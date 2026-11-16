"""Côté hub : enrôlement des workers, connexion WebSocket authentifiée et magasin de fichiers distant.

Un worker est le seul à toucher aux fichiers de son Traefik ; le hub lui envoie des requêtes (liste fermée, voir
worker_proto.OPS) et traite tout ce qu'il renvoie comme une entrée non fiable : taille, types et champs sont contrôlés."""
import asyncio
import base64
import binascii
import concurrent.futures
import copy
import hashlib
import json
import secrets
import threading
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import accesslog, db, events
from . import worker_proto as proto
from .audit import audit
from .traefik_store import StoreError, Store

router = APIRouter(prefix="/api/worker")


# ------------------------------------------------------------------ limitation des tentatives (par IP)
_fails: dict[str, tuple[int, float]] = {}


def _locked(ip: str) -> bool:
    return _fails.get(ip, (0, 0))[1] > time.time()


def _fail(ip: str) -> None:
    n = _fails.get(ip, (0, 0))[0] + 1
    _fails[ip] = (n, time.time() + (min(900, 15 * 2 ** (n - 5)) if n >= 5 else 0))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(node_id: str, user_id: int) -> tuple[str, float]:
    """Crée un jeton d'enrôlement à usage unique (le clair n'est jamais stocké) ; les anciens jetons inutilisés sont annulés."""
    db.run("DELETE FROM node_enroll_tokens WHERE node_id=? AND used_at IS NULL", (node_id,))
    token = "enr_" + secrets.token_urlsafe(32)
    expires = time.time() + proto.TOKEN_TTL
    db.run("INSERT INTO node_enroll_tokens(token_hash,node_id,expires_at,created_by) VALUES(?,?,?,?)",
           (hash_token(token), node_id, expires, user_id))
    return token, expires


def _public_key(b64: str) -> Ed25519PublicKey:
    try:
        raw = base64.b64decode(b64, validate=True)
        if len(raw) != 32:
            raise ValueError
        return Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "Clé publique invalide")


class EnrollIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    publicKey: str = Field(min_length=40, max_length=60)
    version: str = Field(default="", max_length=40)


@router.post("/enroll")
def enroll(body: EnrollIn, request: Request):
    ip = request.client.host if request.client else "?"
    if _locked(ip):
        raise HTTPException(429, "Trop de tentatives, réessayez plus tard.")
    _public_key(body.publicKey)
    h = hash_token(body.token.strip())
    t = db.one("SELECT node_id, expires_at, used_at FROM node_enroll_tokens WHERE token_hash=?", (h,))
    if not t or t["used_at"] or t["expires_at"] < time.time():
        _fail(ip)
        raise HTTPException(401, "Jeton invalide, expiré ou déjà utilisé")
    burned = db.conn().execute("UPDATE node_enroll_tokens SET used_at=? WHERE token_hash=? AND used_at IS NULL", (time.time(), h)).rowcount
    if burned != 1:  # deux enrôlements simultanés avec le même jeton : un seul gagne
        _fail(ip)
        raise HTTPException(401, "Jeton invalide, expiré ou déjà utilisé")
    if db.one("SELECT 1 FROM node_workers WHERE node_id=?", (t["node_id"],)):
        raise HTTPException(409, "Ce nœud a déjà un worker : régénérez un jeton depuis l'interface")
    db.run("INSERT INTO node_workers(node_id,public_key,version,enrolled_at) VALUES(?,?,?,?)",
           (t["node_id"], body.publicKey, body.version[:40], time.time()))
    audit("worker", "node.worker.enroll", ip, node=t["node_id"])
    _fails.pop(ip, None)
    return {"nodeId": t["node_id"], "protocol": proto.PROTOCOL}


# ------------------------------------------------------------------ connexions actives
class Link:
    def __init__(self, ws: WebSocket, node_id: str, loop: asyncio.AbstractEventLoop) -> None:
        self.ws, self.node_id, self.loop = ws, node_id, loop
        self.pending: dict[str, asyncio.Future] = {}
        self._send = asyncio.Lock()
        self.feat: dict[str, bool] = {}     # ce que le worker a annoncé : exec (terminal), access (journal d'accès)

    async def request(self, op: str, args: dict, timeout: float) -> dict:
        rid = secrets.token_hex(8)
        fut = self.loop.create_future()
        self.pending[rid] = fut
        try:
            async with self._send:
                await self.ws.send_text(json.dumps({"type": "request", "id": rid, "op": op, "args": args}))
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(rid, None)

    def fail_all(self) -> None:
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("worker déconnecté"))


_links: dict[str, Link] = {}
_slots: dict[str, threading.BoundedSemaphore] = {}  # requêtes simultanées par nœud : un worker lent ne doit pas monopoliser le hub
MAX_PARALLEL = 4


def online(node_id: str) -> bool:
    return node_id in _links


def disconnect(node_id: str, code: int = proto.CLOSE_REVOKED) -> None:
    """Coupe la connexion d'un worker (révocation, suppression du nœud)."""
    link = _links.get(node_id)
    if link:
        try:
            asyncio.run_coroutine_threadsafe(link.ws.close(code), link.loop)
        except Exception:
            pass


def feature(node_id: str, name: str) -> bool:
    """Fonction facultative annoncée par le worker connecté (« exec », « access »)."""
    link = _links.get(node_id)
    return bool(link and link.feat.get(name))


def node_modules(node_id: str) -> tuple[str, ...]:
    """Modules que le nœud a annoncés (« traefik » par défaut : les workers d'avant les modules)."""
    r = db.one("SELECT modules FROM nodes WHERE id=?", (node_id,))
    return proto.parse_modules(r["modules"] if r else "")


def call(node_id: str, op: str, args: dict | None = None):
    """Exécute une opération sur le worker (appelé depuis les routes synchrones). Lève StoreError en cas d'échec.
    L'opération doit appartenir à un module que CE worker a annoncé : un worker « Docker seul » ne reçoit jamais d'ordre Traefik."""
    if op not in proto.OPS:
        raise ValueError(op)
    if op not in proto.ops_for(node_modules(node_id)):
        raise StoreError("Ce nœud ne gère pas cette fonction (module absent)", 409)
    link = _links.get(node_id)
    if not link:
        raise StoreError("Ce nœud est hors ligne : son worker n'est pas connecté", 503)
    slot = _slots.setdefault(node_id, threading.BoundedSemaphore(MAX_PARALLEL))
    if not slot.acquire(timeout=5):
        raise StoreError("Ce nœud est trop sollicité, réessayez dans un instant", 429)
    fut = asyncio.run_coroutine_threadsafe(link.request(op, args or {}, proto.REQUEST_TIMEOUT), link.loop)
    try:
        msg = fut.result(proto.REQUEST_TIMEOUT + 5)
    except (asyncio.TimeoutError, concurrent.futures.TimeoutError):
        fut.cancel()
        raise StoreError("Le worker n'a pas répondu à temps", 504)
    except (ConnectionError, concurrent.futures.CancelledError, asyncio.CancelledError):
        raise StoreError("Le worker s'est déconnecté pendant la requête", 503)
    finally:
        slot.release()
    if msg.get("ok") is not True:
        err = msg.get("error") if isinstance(msg.get("error"), dict) else {}
        status = err.get("status") if isinstance(err.get("status"), int) and 400 <= err["status"] < 600 else 502
        e = StoreError(str(err.get("message") or "Erreur du worker")[:500], status)
        for k in ("line", "column"):
            if isinstance(err.get(k), int):
                setattr(e, k, err[k])
        raise e
    return msg.get("result")


async def _recv(ws: WebSocket, timeout: float, limit: int = proto.MAX_MESSAGE) -> dict:
    text = await asyncio.wait_for(ws.receive_text(), timeout)
    if len(text) > limit:
        raise ValueError("message trop gros")
    msg = json.loads(text)
    if not isinstance(msg, dict):
        raise ValueError("message invalide")
    return msg


@router.websocket("/ws")
async def worker_ws(ws: WebSocket):
    ip = ws.client.host if ws.client else "?"
    await ws.accept()
    if _locked(ip):
        await ws.close(proto.CLOSE_TOO_MANY)
        return
    link: Link | None = None
    node_id = ""
    try:
        hello = await _recv(ws, 10, 4096)  # avant authentification : messages minuscules
        node_id = str(hello.get("nodeId") or "")
        row = db.one("SELECT w.public_key, n.status, n.deleted_at FROM node_workers w JOIN nodes n ON n.id=w.node_id WHERE w.node_id=?",
                     (node_id,)) if hello.get("type") == "hello" else None
        if not row or hello.get("protocol") != proto.PROTOCOL:
            _fail(ip)
            await ws.close(proto.CLOSE_UNAUTHORIZED)
            return
        if row["status"] != "active" or row["deleted_at"]:  # suspendu ou supprimé : connu, mais mis à l'écart
            await ws.close(proto.CLOSE_SUSPENDED)
            return
        nonce = secrets.token_urlsafe(32)
        await ws.send_text(json.dumps({"type": "challenge", "nonce": nonce}))
        auth = await _recv(ws, 10, 4096)
        try:
            sig = base64.b64decode(str(auth.get("signature") or ""), validate=True)
            Ed25519PublicKey.from_public_bytes(base64.b64decode(row["public_key"])).verify(sig, proto.auth_message(node_id, nonce))
        except (InvalidSignature, ValueError, binascii.Error):
            _fail(ip)
            audit("worker", "node.worker.auth_failed", ip, node=node_id)
            await ws.close(proto.CLOSE_UNAUTHORIZED)
            return
        _fails.pop(ip, None)
        link = Link(ws, node_id, asyncio.get_running_loop())
        old = _links.get(node_id)
        _links[node_id] = link
        if old:  # même identité connectée deux fois : reconnexion légitime ou machine clonée, dans les deux cas on garde la plus récente
            audit("worker", "node.worker.replaced", ip, node=node_id)
            old.fail_all()
            try:
                await old.ws.close(proto.CLOSE_REPLACED)
            except Exception:
                pass
        version = str(hello.get("version") or "")[:40]
        db.run("UPDATE node_workers SET version=?, last_seen=? WHERE node_id=?", (version, time.time(), node_id))
        mods = ",".join(proto.parse_modules(hello.get("modules")))   # absent chez un ancien worker : « traefik »
        if mods != ",".join(node_modules(node_id)):
            db.run("UPDATE nodes SET modules=? WHERE id=?", (mods, node_id))
            audit("worker", "node.worker.modules", mods, node=node_id)
            events.publish("nodes")
        link.feat = {"exec": hello.get("exec") is True and "docker" in mods, "access": hello.get("access") is True and "traefik" in mods}
        accesslog.remote_hello(node_id, link.feat["access"])
        await ws.send_text(json.dumps({"type": "ready"}))
        audit("worker", "node.worker.connect", f"{ip} {version}", node=node_id)
        last_write = time.time()
        while True:
            msg = await _recv(ws, proto.IDLE_LIMIT)
            kind = msg.get("type")
            if kind == "response":
                fut = link.pending.get(str(msg.get("id")))
                if fut and not fut.done():
                    fut.set_result(msg)
            elif kind == "changed":   # le worker signale un changement sur disque (fichier modifié à la main, relevé des certificats)
                remote_store(node_id)._cache.clear()
                events.publish("certs" if msg.get("what") == "certs" else "traefik", node_id)
                if msg.get("what") != "certs":
                    events.publish("live", node_id)
            elif kind == "access" and link.feat.get("access"):   # lignes du journal d'accès du worker : analysées et rangées sous ce nœud
                await asyncio.to_thread(accesslog.ingest_remote, node_id, msg.get("lines"))
            elif kind == "ping":
                if time.time() - last_write > 30:
                    db.run("UPDATE node_workers SET last_seen=? WHERE node_id=?", (time.time(), node_id))
                    last_write = time.time()
                async with link._send:
                    await ws.send_text('{"type":"pong"}')
    except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, RuntimeError, KeyError):
        pass
    finally:
        if link:
            link.fail_all()
            if _links.get(node_id) is link:
                del _links[node_id]
                try:
                    db.run("UPDATE node_workers SET last_seen=? WHERE node_id=?", (time.time(), node_id))
                except Exception:
                    pass
                events.publish("node", node_id)    # le nœud passe « hors ligne » à l'écran
                events.publish("nodes")


# ------------------------------------------------------------------ magasin de fichiers distant
_TTL = 3.0
_WRITES = ("write", "delete", "move", "upsert", "delete_item", "mkdir", "rmdir", "move_dir", "reorganize", "toggle")


def _str(v, default: str = "") -> str:
    return v if isinstance(v, str) else default


def _item(it) -> dict | None:
    """Un élément renvoyé par le worker : on ne garde que des champs de types attendus."""
    if not isinstance(it, dict) or not isinstance(it.get("spec"), dict):
        return None
    if any(not isinstance(it.get(k), str) or not it[k] for k in ("id", "proto", "kind", "name", "file")):
        return None
    if it["proto"] not in ("http", "tcp", "udp", "tls"):
        return None
    t = it.get("type")
    return {"id": it["id"], "proto": it["proto"], "kind": it["kind"], "name": it["name"], "file": it["file"],
            "spec": it["spec"], "type": t if isinstance(t, str) else None, "disabled": it.get("disabled") is True,
            "pausedBy": it["pausedBy"][:200] if isinstance(it.get("pausedBy"), str) else None}


class RemoteStore:
    """Même interface que traefik_store.Store, mais les fichiers sont ceux de la machine du worker."""

    parse_text = staticmethod(Store.parse_text)

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self._cache: dict[str, tuple[float, object]] = {}

    def _call(self, op: str, **args):
        try:
            return call(self.node_id, op, args)
        finally:
            if op in _WRITES:
                self._cache.clear()

    def _cached(self, op: str, build):
        hit = self._cache.get(op)
        if hit and time.time() - hit[0] < _TTL:
            return copy.deepcopy(hit[1])
        value = build()
        self._cache[op] = (time.time(), value)
        return copy.deepcopy(value)

    def items(self) -> tuple[list[dict], dict[str, str]]:
        def build():
            r = self._call("items")
            if not isinstance(r, dict) or not isinstance(r.get("items"), list) or not isinstance(r.get("errors"), dict):
                raise StoreError("Réponse invalide du worker", 502)
            items = [i for i in map(_item, r["items"]) if i]
            errors = {str(k)[:300]: str(v)[:500] for k, v in r["errors"].items()}
            return items, errors
        return self._cached("items", build)

    def list_files(self) -> list[dict]:
        def build():
            r = self._call("list_files")
            if not isinstance(r, list):
                raise StoreError("Réponse invalide du worker", 502)
            return [{"path": f["path"], "size": f.get("size") if isinstance(f.get("size"), int) else 0,
                     "mtime": f.get("mtime") if isinstance(f.get("mtime"), (int, float)) else 0, "disabled": f.get("disabled") is True}
                    for f in r if isinstance(f, dict) and isinstance(f.get("path"), str)]
        return self._cached("list_files", build)

    def list_dirs(self) -> list[str]:
        r = self._call("list_dirs")
        return [d for d in r if isinstance(d, str)] if isinstance(r, list) else []

    def read(self, rel: str) -> str:
        return _str(self._call("read", path=rel))

    def write(self, rel: str, content: str, create: bool = False) -> None:
        self._call("write", path=rel, content=content, create=create)

    def delete(self, rel: str) -> None:
        self._call("delete", path=rel)

    def move(self, src: str, dst: str) -> None:
        self._call("move", src=src, dst=dst)

    def make_dir(self, rel: str) -> None:
        self._call("mkdir", path=rel)

    def remove_dir(self, rel: str) -> None:
        self._call("rmdir", path=rel)

    def move_dir(self, src: str, dst: str) -> None:
        self._call("move_dir", src=src, dst=dst)

    def certs(self) -> dict:
        from .certs import clean
        r = self._call("certs")
        if not isinstance(r, dict):
            return {"configured": False, "error": None, "certs": [], "updated": None}
        return {"configured": r.get("configured") is True, "error": r["error"][:300] if isinstance(r.get("error"), str) else None,
                "certs": clean(r.get("certs")), "updated": r["updated"] if isinstance(r.get("updated"), (int, float)) else None}

    def toggle(self, ids: list[str], files: list[str], enable: bool, tag: str | None = None, reconcile: bool = True, dry: bool = False) -> dict:
        r = self._call("toggle", ids=ids, files=files, enable=enable, tag=tag, reconcile=reconcile, dry=dry)
        return r if isinstance(r, dict) else {}

    def reorganize(self, steps: list[dict], clean: bool = True) -> dict:
        r = self._call("reorganize", steps=steps, clean=clean)
        return {"blocks": r.get("blocks", 0), "files": r.get("files", 0), "deleted": r.get("deleted", 0), "dirs": r.get("dirs", 0)} if isinstance(r, dict) else {}

    def history(self, rel: str) -> list[dict]:
        r = self._call("history", path=rel)
        return [{"id": h["id"], "size": h.get("size") if isinstance(h.get("size"), int) else 0,
                 "mtime": h.get("mtime") if isinstance(h.get("mtime"), (int, float)) else 0, "deleted": bool(h.get("deleted"))}
                for h in (r if isinstance(r, list) else []) if isinstance(h, dict) and isinstance(h.get("id"), str)]

    def history_content(self, rel: str, bid: str) -> str:
        return _str(self._call("history_content", path=rel, id=bid))

    def upsert(self, proto_: str, kind: str, name: str, spec: dict, file: str | None = None, old_name: str | None = None) -> str:
        return _str(self._call("upsert", proto=proto_, kind=kind, name=name, spec=spec, file=file, old_name=old_name))

    def delete_item(self, proto_: str, kind: str, name: str) -> str:
        return _str(self._call("delete_item", proto=proto_, kind=kind, name=name))

    def live(self) -> dict:
        r = self._call("live")
        return r if isinstance(r, dict) else {"available": False}


_stores: dict[str, RemoteStore] = {}


def remote_store(node_id: str) -> RemoteStore:
    if node_id not in _stores:
        _stores[node_id] = RemoteStore(node_id)
    return _stores[node_id]
