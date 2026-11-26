"""Liaison entre deux hubs : identité, appairage, confirmation des empreintes et canal signé (voir link_proto pour le protocole).

Chaque hub reste seul maître de ses utilisateurs, de ses droits et de ses nœuds : cette couche n'établit que « qui est l'autre hub » et « la liaison
est-elle vivante ». Tout ce que l'autre hub envoie est traité comme une entrée non fiable (taille, types, longueurs contrôlés)."""
import asyncio
import base64
import binascii
import hashlib
import ipaddress
import json
import os
import random
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import websockets
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import APIRouter, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field

from . import crypto, db
from . import link_proto as proto
from .audit import audit
from .httpsafe import NoRedirect

VERSION = os.environ.get("APP_VERSION", "dev")
router = APIRouter(prefix="/api/link")


class LinkError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ identité de ce hub
class Identity:
    def __init__(self, hub_id: str, name: str, key: Ed25519PrivateKey) -> None:
        self.hub_id, self.name, self.key = hub_id, name, key
        self.public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()

    def sign(self, msg: bytes) -> str:
        return base64.b64encode(self.key.sign(msg)).decode()


_ident_lock = threading.Lock()


def identity() -> Identity:
    """L'identité de ce hub, créée au premier besoin. La clé privée est chiffrée par ENCRYPTION_KEY et ne quitte jamais ce hub."""
    with _ident_lock:
        r = db.one("SELECT * FROM hub_identity WHERE id=1")
        if not r:
            key = Ed25519PrivateKey.generate()
            raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
            hub_id = secrets.token_hex(16)
            name = clean_text(os.environ.get("HUB_NAME", ""), 80) or f"Hub {hub_id[:6]}"
            ident = Identity(hub_id, name, key)
            db.run("INSERT INTO hub_identity(id,hub_id,name,public_key,private_key,created_at) VALUES(1,?,?,?,?,?)",
                   (hub_id, name, ident.public, crypto.enc(base64.b64encode(raw).decode()), time.time()))
            return ident
        return Identity(r["hub_id"], r["name"], Ed25519PrivateKey.from_private_bytes(base64.b64decode(crypto.dec(r["private_key"]))))


def rename(name: str) -> str:
    name = clean_text(name, 80)
    if not name:
        raise LinkError("Donnez un nom au hub")
    identity()
    db.run("UPDATE hub_identity SET name=? WHERE id=1", (name,))
    return name


def clean_text(v, limit: int) -> str:
    """Texte venu de l'extérieur : imprimable, sans retour à la ligne, borné."""
    if not isinstance(v, str):
        return ""
    return "".join(c for c in v if c.isprintable()).strip()[:limit]


def verify(public_b64: str, msg: bytes, sig_b64) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64, validate=True)).verify(base64.b64decode(str(sig_b64 or ""), validate=True), msg)
        return True
    except (InvalidSignature, ValueError, binascii.Error, TypeError):
        return False


# ------------------------------------------------------------------ limitation des tentatives (par IP)
_fails: dict[str, tuple[int, float]] = {}


def _locked(ip: str) -> bool:
    return _fails.get(ip, (0, 0))[1] > time.time()


def _fail(ip: str) -> None:
    n = _fails.get(ip, (0, 0))[0] + 1
    _fails[ip] = (n, time.time() + (min(900, 15 * 2 ** (n - 5)) if n >= 5 else 0))


# ------------------------------------------------------------------ les liaisons (base de données)
def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def get(link_id: str):
    return db.one("SELECT * FROM hub_links WHERE id=?", (link_id,))


def all_links() -> list:
    return db.rows("SELECT * FROM hub_links ORDER BY created_at")


def public(r) -> dict:
    """La liaison telle que l'interface la voit : jamais le code, ni la clé en clair ; l'empreinte à comparer, et l'état de la connexion."""
    me = identity()
    paired = r["status"] == "paired"
    both = bool(paired and r["confirmed_local"] and r["confirmed_peer"])
    if r["status"] == "revoked":
        state = "revoked"
    elif r["status"] == "invited":
        state = "invited" if (r["code_expires"] or 0) > time.time() else "expired"
    elif r["last_error"].startswith("incompatible"):   # dit tout de suite, avant même la confirmation : inutile de comparer des empreintes si les versions ne se parlent pas
        state = "incompatible"
    elif not both:
        state = "to_confirm" if not r["confirmed_local"] else "wait_peer"
    elif online(r["id"]):
        state = "online"
    else:
        state = "offline"
    return {"id": r["id"], "label": r["label"], "peerName": r["peer_name"], "peerHubId": r["peer_hub_id"], "peerUrl": r["peer_url"], "weCall": bool(r["we_call"]),
            "status": r["status"], "state": state, "confirmedLocal": bool(r["confirmed_local"]), "confirmedPeer": bool(r["confirmed_peer"]),
            "fingerprint": proto.fingerprint(me.public, r["peer_key"]) if r["peer_key"] else "", "graceHours": r["grace_hours"],
            "peerVersion": r["peer_version"], "peerProtocol": r["peer_protocol"], "protocol": r["protocol"], "features": [f for f in r["features"].split(",") if f],
            "lastSeen": r["last_seen"], "lastError": r["last_error"], "codeExpires": r["code_expires"] if r["status"] == "invited" else None,
            "createdAt": r["created_at"], "createdBy": r["created_by"], "revokedAt": r["revoked_at"], "revokedBy": r["revoked_by"]}


def usable(r) -> bool:
    """La liaison peut servir à autre chose qu'à se mettre d'accord : appariée, confirmée PAR LES DEUX administrateurs, protocole commun trouvé."""
    return bool(r and r["status"] == "paired" and r["confirmed_local"] and r["confirmed_peer"])


def within_grace(r, now: float | None = None) -> bool:
    """Une liaison coupée garde ses effets (comptes invités…) pendant le délai de grâce propre à cette liaison, puis plus rien. 0 = tout de suite."""
    if not usable(r):
        return False
    if online(r["id"]):
        return True
    if not r["last_seen"]:
        return False
    return (now if now is not None else time.time()) - r["last_seen"] <= r["grace_hours"] * 3600


def clean_url(raw: str) -> str:
    """Adresse d'un hub à appeler : HTTPS obligatoire (HTTP seulement si LINK_ALLOW_INSECURE=true, pour les essais), pas d'identifiants, pas de chemin."""
    u = urllib.parse.urlsplit((raw or "").strip())
    insecure = os.environ.get("LINK_ALLOW_INSECURE", "").lower() in ("1", "true", "yes")
    if u.scheme not in (("https", "http") if insecure else ("https",)) or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ("", "/"):
        raise LinkError("Adresse invalide : donnez l'adresse HTTPS du hub, sans chemin (ex. https://armadok.exemple.fr)")
    try:
        ip = ipaddress.ip_address(u.hostname)
        if ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            raise LinkError("Adresse non autorisée")
    except ValueError:
        pass
    return f"{u.scheme}://{u.netloc}"


# ------------------------------------------------------------------ appairage
def invite(user: str, label: str = "") -> tuple[dict, str]:
    """Ce hub invite l'autre : un code à usage unique (15 minutes) que l'autre hub présentera. Le code n'est jamais stocké en clair."""
    identity()
    code = "lnk_" + secrets.token_urlsafe(32)
    lid = secrets.token_hex(16)
    db.run("INSERT INTO hub_links(id,we_call,status,label,code_hash,code_expires,created_by,created_at) VALUES(?,0,'invited',?,?,?,?,?)",
           (lid, clean_text(label, 80), hash_code(code), time.time() + proto.CODE_TTL, user, time.time()))
    audit(user, "admin.link.invite", lid)
    return public(get(lid)), code


def renew_invite(link_id: str, user: str) -> tuple[dict, str]:
    r = get(link_id)
    if not r or r["status"] != "invited":
        raise LinkError("Invitation introuvable", 404)
    code = "lnk_" + secrets.token_urlsafe(32)
    db.run("UPDATE hub_links SET code_hash=?, code_expires=? WHERE id=?", (hash_code(code), time.time() + proto.CODE_TTL, link_id))
    audit(user, "admin.link.invite.renew", link_id)
    return public(get(link_id)), code


class PairIn(BaseModel):
    code: str = Field(min_length=10, max_length=200)
    hubId: str = Field(min_length=16, max_length=64)
    name: str = Field(default="", max_length=200)
    publicKey: str = Field(min_length=40, max_length=60)
    version: str = Field(default="", max_length=60)
    protocol: int = Field(ge=1, le=1000)
    minProtocol: int = Field(default=1, ge=1, le=1000)


@router.post("/pair")
def pair(body: PairIn, request: Request):
    """Appelé par l'AUTRE hub avec le code : on enregistre sa clé et on lui donne la nôtre. Rien n'est actif tant que les deux administrateurs n'ont pas confirmé."""
    ip = request.client.host if request.client else "?"
    if _locked(ip):
        raise HTTPException(429, "Trop de tentatives, réessayez plus tard.")
    me = identity()
    if not proto.valid_hub_id(body.hubId) or not proto.b64_ok(body.publicKey, 32):
        _fail(ip)
        raise HTTPException(422, "Identité invalide")
    h = hash_code(body.code.strip())
    r = db.one("SELECT * FROM hub_links WHERE code_hash=? AND status='invited'", (h,))
    if not r or (r["code_expires"] or 0) < time.time():
        _fail(ip)
        raise HTTPException(401, "Code invalide, expiré ou déjà utilisé")
    if body.hubId == me.hub_id:
        raise HTTPException(409, "Un hub ne peut pas se lier à lui-même")
    if db.one("SELECT 1 FROM hub_links WHERE peer_hub_id=? AND status='paired'", (body.hubId,)):
        raise HTTPException(409, "Ces deux hubs sont déjà liés : révoquez la liaison existante pour en refaire une")
    burned = db.conn().execute("UPDATE hub_links SET code_hash=NULL, code_expires=NULL, status='paired', peer_hub_id=?, peer_name=?, peer_key=?, peer_version=?, peer_protocol=? "
                               "WHERE id=? AND code_hash=? AND status='invited'",
                               (body.hubId, clean_text(body.name, 80), body.publicKey, clean_text(body.version, 40), body.protocol, r["id"], h)).rowcount
    if burned != 1:   # deux appairages simultanés avec le même code : un seul gagne
        _fail(ip)
        raise HTTPException(401, "Code invalide, expiré ou déjà utilisé")
    audit("lien", "admin.link.paired", f"{ip} {r['id']}")
    _fails.pop(ip, None)
    return {"linkId": r["id"], "hubId": me.hub_id, "name": me.name, "publicKey": me.public, "version": VERSION, "protocol": proto.PROTOCOL, "minProtocol": proto.MIN_PROTOCOL}


def _post_json(url: str, body: dict) -> tuple[int, dict]:
    """POST JSON sans jamais suivre une redirection (le code ne doit pas partir ailleurs). Remplaçable dans les tests."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=20) as resp:   # noqa: S310 (URL validée par clean_url, schéma https)
            return resp.status, json.loads(resp.read(65536) or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read(65536) or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LinkError(f"Hub injoignable : {getattr(e, 'reason', e)}", 502)


def join(user: str, url: str, code: str, label: str = "") -> dict:
    """Ce hub rejoint celui qui a émis le code : il l'appelle, échange les clés, et sera aussi celui qui entretient la connexion."""
    url = clean_url(url)
    me = identity()
    status, data = _post_json(url + "/api/link/pair", {"code": code.strip(), "hubId": me.hub_id, "name": me.name, "publicKey": me.public, "version": VERSION,
                                                       "protocol": proto.PROTOCOL, "minProtocol": proto.MIN_PROTOCOL})
    if status != 200:
        detail = data.get("detail") if isinstance(data, dict) else ""
        raise LinkError(f"Le hub a refusé : {detail}" if isinstance(detail, str) and detail else f"Le hub a refusé la liaison ({status})", 400 if status < 500 else 502)
    try:
        lid, hid, key = str(data["linkId"]), str(data["hubId"]), str(data["publicKey"])
        peer_proto = int(data["protocol"])
        peer_min = int(data.get("minProtocol", 1))
    except (KeyError, ValueError, TypeError):
        raise LinkError("Réponse invalide de l'autre hub", 502)
    if not (proto.valid_hub_id(hid) and proto.valid_hub_id(lid) and proto.b64_ok(key, 32)) or hid == me.hub_id:
        raise LinkError("Identité invalide de l'autre hub", 502)
    if get(lid) or db.one("SELECT 1 FROM hub_links WHERE peer_hub_id=? AND status='paired'", (hid,)):
        raise LinkError("Une liaison avec ce hub existe déjà", 409)
    db.run("INSERT INTO hub_links(id,peer_hub_id,peer_name,peer_key,peer_url,we_call,status,label,peer_version,peer_protocol,created_by,created_at) VALUES(?,?,?,?,?,1,'paired',?,?,?,?,?)",
           (lid, hid, clean_text(data.get("name"), 80), key, url, clean_text(label, 80), clean_text(data.get("version"), 40), peer_proto, user, time.time()))
    audit(user, "admin.link.join", f"{lid} {url}")
    reconcile_soon()
    return public(get(lid))


# ------------------------------------------------------------------ actions de l'administrateur
def confirm(link_id: str, user: str) -> dict:
    """« J'ai comparé l'empreinte avec l'autre administrateur : elle est identique. » Les deux côtés doivent le faire."""
    r = get(link_id)
    if not r or r["status"] != "paired":
        raise LinkError("Liaison introuvable", 404)
    db.run("UPDATE hub_links SET confirmed_local=1 WHERE id=?", (link_id,))
    audit(user, "admin.link.confirm", link_id)
    _push_state(link_id)
    return public(get(link_id))


def update(link_id: str, user: str, grace_hours: int | None = None, label: str | None = None, host: bool | None = None, direct: bool | None = None) -> dict:
    r = get(link_id)
    if not r:
        raise LinkError("Liaison introuvable", 404)
    if host is not None and bool(host) != bool(r["host_enabled"]):
        db.run("UPDATE hub_links SET host_enabled=? WHERE id=?", (1 if host else 0, link_id))
        audit(user, "admin.link.host", f"{link_id} {'accueil autorisé' if host else 'accueil retiré'}")
        _push_state(link_id)
        if not host:
            from . import link_guests
            link_guests.purge(link_id)
    if direct is not None and bool(direct) != bool(r["direct_enabled"]):
        db.run("UPDATE hub_links SET direct_enabled=? WHERE id=?", (1 if direct else 0, link_id))
        audit(user, "admin.link.direct", f"{link_id} {'connexion directe autorisée' if direct else 'connexion directe retirée'}")
    if grace_hours is not None:
        if not 0 <= grace_hours <= proto.MAX_GRACE_HOURS:
            raise LinkError(f"Le délai de grâce va de 0 à {proto.MAX_GRACE_HOURS} heures")
        db.run("UPDATE hub_links SET grace_hours=? WHERE id=?", (grace_hours, link_id))
        audit(user, "admin.link.grace", f"{link_id} {grace_hours}h")
    if label is not None:
        db.run("UPDATE hub_links SET label=? WHERE id=?", (clean_text(label, 80), link_id))
    return public(get(link_id))


def revoke(link_id: str, user: str) -> dict:
    """Coupe la liaison des deux côtés : la connexion est fermée et l'autre hub est prévenu (ou le saura à sa prochaine connexion)."""
    r = get(link_id)
    if not r or r["status"] == "revoked":
        raise LinkError("Liaison introuvable", 404)
    db.run("UPDATE hub_links SET status='revoked', revoked_at=?, revoked_by=?, code_hash=NULL, code_expires=NULL WHERE id=?", (time.time(), user, link_id))
    audit(user, "admin.link.revoke", link_id)
    from . import link_guests
    link_guests.purge(link_id)
    ch = _chans.get(link_id)
    if ch:   # prévenu tout de suite ; sinon (connexion tombée) le hub appelant retentera de le lui dire pendant sept jours
        ch.post({"type": "revoke"})
        ch.close_later()
        db.run("UPDATE hub_links SET peer_notified=1 WHERE id=?", (link_id,))
    reconcile_soon()
    return public(get(link_id))


def delete(link_id: str, user: str) -> None:
    r = get(link_id)
    if not r:
        raise LinkError("Liaison introuvable", 404)
    if r["status"] == "paired":
        raise LinkError("Révoquez d'abord la liaison", 409)
    db.run("DELETE FROM hub_links WHERE id=?", (link_id,))
    audit(user, "admin.link.delete", link_id)


# ------------------------------------------------------------------ canal : état commun aux deux rôles
class Channel:
    """Une connexion vivante vers l'autre hub, vue du même côté que l'on ait appelé ou été appelé."""

    def __init__(self, link_id: str, loop: asyncio.AbstractEventLoop, send, close) -> None:
        self.link_id, self.loop, self._send, self._close = link_id, loop, send, close
        self.lock = asyncio.Lock()

    async def send(self, msg: dict) -> None:
        async with self.lock:
            await self._send(json.dumps(msg))

    def post(self, msg: dict) -> None:
        """Depuis du code synchrone : envoie sans attendre."""
        try:
            asyncio.run_coroutine_threadsafe(self.send(msg), self.loop)
        except Exception:
            pass

    def close_later(self) -> None:
        async def go():
            await asyncio.sleep(0.5)
            try:
                await self._close()
            except Exception:
                pass
        try:
            asyncio.run_coroutine_threadsafe(go(), self.loop)
        except Exception:
            pass


_chans: dict[str, Channel] = {}


def online(link_id: str) -> bool:
    return link_id in _chans


def public_url() -> str:
    identity()
    return db.one("SELECT public_url FROM hub_identity WHERE id=1")["public_url"]


def set_public_url(url: str) -> str:
    """L'adresse par laquelle les utilisateurs ouvrent l'interface de CE hub (annoncée à l'autre hub pour ses liens)."""
    url = clean_url(url) if (url or "").strip() else ""
    identity()
    db.run("UPDATE hub_identity SET public_url=? WHERE id=1", (url,))
    return url


def my_state(r) -> dict:
    me = identity()
    return {"type": "state", "confirmed": bool(r["confirmed_local"]), "name": me.name, "version": VERSION, "protocol": proto.PROTOCOL,
            "minProtocol": proto.MIN_PROTOCOL, "features": list(proto.FEATURES), "hosting": bool(r["host_enabled"]), "publicUrl": public_url()}


def _push_state(link_id: str) -> None:
    ch, r = _chans.get(link_id), get(link_id)
    if ch and r:
        ch.post(my_state(r))


def _apply_state(link_id: str, msg: dict) -> None:
    """L'autre hub annonce son état : on ne retient que des champs bornés, et seulement ce qui informe l'interface (jamais de droits)."""
    r = get(link_id)
    if not r or r["status"] != "paired":
        return
    name = clean_text(msg.get("name"), 80) or r["peer_name"]
    version = clean_text(msg.get("version"), 40)
    try:
        ui = clean_url(msg.get("publicUrl") or "") if msg.get("publicUrl") else ""
    except LinkError:
        ui = ""
    hosting = 1 if msg.get("hosting") is True else 0
    db.run("UPDATE hub_links SET confirmed_peer=?, peer_name=?, peer_version=?, peer_hosting=?, peer_ui_url=?, last_seen=? WHERE id=?",
           (1 if msg.get("confirmed") is True else 0, name, version, hosting, ui, time.time(), link_id))
    if hosting != r["peer_hosting"]:   # l'autre hub vient d'accepter (ou de refuser) nos équipes : on les lui renvoie
        from . import link_guests
        link_guests.forget(link_id)
        link_guests.sync_out(link_id)


def _apply_revoke(link_id: str, by: str) -> None:
    r = get(link_id)
    if r and r["status"] != "revoked":
        db.run("UPDATE hub_links SET status='revoked', revoked_at=?, revoked_by=? WHERE id=?", (time.time(), by, link_id))
        audit("lien", "admin.link.revoked_by_peer", link_id)
        from . import link_guests
        link_guests.purge(link_id)
        reconcile_soon()


def _record_session(link_id: str, negotiated: int, features: list[str], peer: dict) -> None:
    db.run("UPDATE hub_links SET protocol=?, features=?, peer_protocol=?, peer_version=?, last_seen=?, last_error='' WHERE id=?",
           (negotiated, ",".join(features), int(peer.get("protocol") or 0), clean_text(peer.get("version"), 40), time.time(), link_id))


def _record_incompatible(link_id: str, peer: dict) -> None:
    msg = (f"incompatible : ce hub parle les protocoles {proto.MIN_PROTOCOL} à {proto.PROTOCOL} (version {VERSION}), l'autre {peer.get('minProtocol')} à {peer.get('protocol')} "
           f"(version {clean_text(peer.get('version'), 40) or '?'}) : mettez à jour le plus ancien des deux")
    db.run("UPDATE hub_links SET last_error=?, protocol=0, last_seen=?, peer_protocol=?, peer_version=? WHERE id=?",
           (msg[:400], time.time(), int(peer.get("protocol") or 0) if isinstance(peer.get("protocol"), int) else 0, clean_text(peer.get("version"), 40), link_id))


async def _recv(ws_recv, timeout: float, limit: int = proto.MAX_MESSAGE) -> dict:
    text = await asyncio.wait_for(ws_recv(), timeout)
    if isinstance(text, bytes):
        raise ValueError("message binaire")
    if len(text) > limit:
        raise ValueError("message trop gros")
    msg = json.loads(text)
    if not isinstance(msg, dict):
        raise ValueError("message invalide")
    return msg


def _int(v, default: int = 0) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 1000 else default


# ------------------------------------------------------------------ canal : côté appelé (reçoit la connexion de l'autre hub)
@router.websocket("/ws")
async def link_ws(ws: WebSocket):
    ip = ws.client.host if ws.client else "?"
    await ws.accept()
    if _locked(ip):
        await ws.close(proto.CLOSE_TOO_MANY)
        return
    chan: Channel | None = None
    link_id = ""
    try:
        hello = await _recv(ws.receive_text, 10, 4096)   # avant authentification : messages minuscules
        me = identity()
        peer = str(hello.get("hubId") or "")
        nc = str(hello.get("nonce") or "")
        r = None
        if hello.get("type") == "hello" and proto.valid_hub_id(peer) and 16 <= len(nc) <= 100:
            r = db.one("SELECT * FROM hub_links WHERE peer_hub_id=? AND we_call=0 AND status IN ('paired','revoked') ORDER BY created_at DESC LIMIT 1", (peer,))
        if not r:   # inconnu : même réponse que pour une signature fausse, on ne révèle pas quels hubs sont connus
            _fail(ip)
            await ws.close(proto.CLOSE_UNAUTHORIZED)
            return
        link_id = r["id"]
        ns = secrets.token_urlsafe(32)
        await ws.send_text(json.dumps({"type": "challenge", "hubId": me.hub_id, "nonce": ns, "signature": me.sign(proto.auth_message("srv", me.hub_id, peer, nc, ns))}))
        auth = await _recv(ws.receive_text, 10, 4096)
        if auth.get("type") != "auth" or not verify(r["peer_key"], proto.auth_message("cli", peer, me.hub_id, nc, ns), auth.get("signature")):
            _fail(ip)
            audit("lien", "admin.link.auth_failed", f"{ip} {link_id}")
            await ws.close(proto.CLOSE_UNAUTHORIZED)
            return
        _fails.pop(ip, None)
        if r["status"] == "revoked":   # il s'est authentifié : il peut apprendre que la liaison est coupée
            await ws.send_text(json.dumps({"type": "revoke"}))
            await ws.close(proto.CLOSE_REVOKED)
            return
        mine, theirs_max, theirs_min = proto.PROTOCOL, _int(hello.get("protocol")), _int(hello.get("minProtocol"), 1)
        negotiated = proto.negotiate(mine, proto.MIN_PROTOCOL, theirs_max, theirs_min)
        if negotiated is None:
            _record_incompatible(link_id, {"protocol": theirs_max, "minProtocol": theirs_min, "version": hello.get("version")})
            await ws.send_text(json.dumps({"type": "incompatible", "protocol": mine, "minProtocol": proto.MIN_PROTOCOL, "version": VERSION}))
            await ws.close(proto.CLOSE_INCOMPATIBLE)
            return
        chan = Channel(link_id, asyncio.get_running_loop(), ws.send_text, ws.close)
        old = _chans.get(link_id)
        _chans[link_id] = chan
        if old:   # même hub connecté deux fois : on garde la plus récente
            audit("lien", "admin.link.replaced", link_id)
            try:
                await old._close()
            except Exception:
                pass
        _record_session(link_id, negotiated, proto.common_features(proto.FEATURES, hello.get("features")), {"protocol": theirs_max, "version": hello.get("version")})
        await chan.send({"type": "ready", "protocol": negotiated, "features": list(proto.FEATURES), "state": my_state(get(link_id))})
        audit("lien", "admin.link.connect", f"{ip} {link_id}")
        await _serve_channel(chan, ws.receive_text)
    except (asyncio.TimeoutError, ValueError, json.JSONDecodeError):
        pass
    except Exception as e:   # noqa: BLE001 : connexion coupée par l'autre côté, ou fermée par nous
        if type(e).__name__ not in ("WebSocketDisconnect", "ConnectionClosedOK", "ConnectionClosedError", "RuntimeError"):
            raise
    finally:
        if chan and _chans.get(link_id) is chan:
            _chans.pop(link_id, None)
            if link_id:
                db.run("UPDATE hub_links SET last_seen=? WHERE id=?", (time.time(), link_id))
        try:
            await ws.close()
        except Exception:
            pass


async def _serve_channel(chan: Channel, recv) -> None:
    """Boucle commune : l'autre hub parle (état, ping, révocation) ; tout type inconnu est ignoré (versions futures)."""
    from . import link_guests
    link_guests.forget(chan.link_id)

    async def syncer():
        while True:
            try:
                link_guests.sync_out(chan.link_id)
            except Exception:   # noqa: BLE001 : un incident de synchro ne doit jamais couper la liaison
                pass
            await asyncio.sleep(15)

    sync = asyncio.create_task(syncer())
    try:
        await _loop_channel(chan, recv)
    finally:
        sync.cancel()


async def _loop_channel(chan: Channel, recv) -> None:
    while True:
        msg = await _recv(recv, proto.IDLE_LIMIT)
        kind = msg.get("type")
        if kind == "ping":
            await chan.send({"type": "pong"})
        elif kind == "state":
            _apply_state(chan.link_id, msg)
        elif kind == "teams":
            from . import link_guests
            await chan.send(link_guests.apply_in(chan.link_id, msg))
        elif kind == "revoke":
            _apply_revoke(chan.link_id, "l'autre hub")
            return


# ------------------------------------------------------------------ canal : côté appelant (ce hub appelle l'autre et entretient la connexion)
def ws_url(base: str) -> str:
    return ("wss://" if base.startswith("https://") else "ws://") + base.split("://", 1)[1] + "/api/link/ws"


async def client_handshake(send, recv, me: Identity, peer_id: str, peer_key: str, features=proto.FEATURES) -> dict:
    """Poignée de main côté appelant, sur un canal abstrait (testable sans réseau). Rend le message « ready » ou lève une erreur typée."""
    nc = secrets.token_urlsafe(32)
    await send(json.dumps({"type": "hello", "hubId": me.hub_id, "nonce": nc, "protocol": proto.PROTOCOL, "minProtocol": proto.MIN_PROTOCOL, "version": VERSION,
                           "features": list(features)}))
    ch = await _recv(recv, 15, 4096)
    if ch.get("type") != "challenge" or ch.get("hubId") != peer_id or not isinstance(ch.get("nonce"), str) or not 16 <= len(ch["nonce"]) <= 100:
        raise ConnectionError("réponse inattendue de l'autre hub")
    if not verify(peer_key, proto.auth_message("srv", peer_id, me.hub_id, nc, ch["nonce"]), ch.get("signature")):   # l'appelé prouve QUI il est avant que l'on ne réponde
        raise PermissionError("identité de l'autre hub non conforme à la clé enregistrée à l'appairage")
    await send(json.dumps({"type": "auth", "signature": me.sign(proto.auth_message("cli", me.hub_id, peer_id, nc, ch["nonce"]))}))
    ready = await _recv(recv, 15)
    if ready.get("type") in ("revoke", "incompatible"):
        return ready
    if ready.get("type") != "ready":
        raise ConnectionError("authentification refusée")
    return ready


async def _caller_session(r) -> None:
    me = identity()
    link_id = r["id"]
    async with websockets.connect(ws_url(r["peer_url"]), max_size=proto.MAX_MESSAGE, ping_interval=None, open_timeout=15) as ws:
        ready = await client_handshake(ws.send, ws.recv, me, r["peer_hub_id"], r["peer_key"])
        if r["status"] == "revoked":   # c'est NOUS qui avons coupé : on le dit à l'autre hub, une fois, puis on se tait
            if ready.get("type") == "ready":
                await ws.send(json.dumps({"type": "revoke"}))
                await asyncio.sleep(0.3)
            if ready.get("type") in ("ready", "revoke"):
                db.run("UPDATE hub_links SET peer_notified=1 WHERE id=?", (link_id,))
            return
        if ready.get("type") == "revoke":
            _apply_revoke(link_id, "l'autre hub")
            return
        if ready.get("type") == "incompatible":
            _record_incompatible(link_id, ready)
            return
        negotiated = proto.negotiate(proto.PROTOCOL, proto.MIN_PROTOCOL, _int(ready.get("protocol")), proto.MIN_PROTOCOL)
        if negotiated is None or negotiated != _int(ready.get("protocol")):
            raise ConnectionError("protocole annoncé non reconnu")
        st = ready.get("state") if isinstance(ready.get("state"), dict) else {}
        _record_session(link_id, negotiated, proto.common_features(proto.FEATURES, ready.get("features")), {"protocol": st.get("protocol"), "version": st.get("version")})
        chan = Channel(link_id, asyncio.get_running_loop(), ws.send, ws.close)
        _chans[link_id] = chan
        _apply_state(link_id, st)
        await chan.send(my_state(get(link_id)))

        async def pinger():
            while True:
                await chan.send({"type": "ping"})
                await asyncio.sleep(proto.PING_EVERY)

        ping = asyncio.create_task(pinger())
        try:
            await _serve_channel(chan, ws.recv)
        finally:
            ping.cancel()
            if _chans.get(link_id) is chan:
                _chans.pop(link_id, None)
                db.run("UPDATE hub_links SET last_seen=? WHERE id=?", (time.time(), link_id))


async def _caller(link_id: str) -> None:
    attempt = 0
    while True:
        r = get(link_id)
        if not r or not r["we_call"] or not r["peer_url"] or not (r["status"] == "paired" or (r["status"] == "revoked" and not r["peer_notified"])):
            return
        started = time.time()
        try:
            await _caller_session(r)
        except PermissionError as e:   # la clé de l'autre ne correspond plus : on n'insiste pas à toute vitesse, et on le dit
            db.run("UPDATE hub_links SET last_error=? WHERE id=?", (f"identité refusée : {e}"[:400], link_id))
            attempt = max(attempt, 6)
        except websockets.exceptions.ConnectionClosed as e:
            code = e.rcvd.code if e.rcvd else 0
            msg = {proto.CLOSE_UNAUTHORIZED: "refusé par l'autre hub (liaison inconnue ou signature refusée)", proto.CLOSE_TOO_MANY: "l'autre hub me met en attente (trop de tentatives)",
                   proto.CLOSE_INCOMPATIBLE: "incompatible : versions de protocole sans recouvrement"}.get(code, "")
            if msg and get(link_id) and get(link_id)["status"] == "paired" and code != proto.CLOSE_INCOMPATIBLE:
                db.run("UPDATE hub_links SET last_error=? WHERE id=?", (msg, link_id))
        except (OSError, ConnectionError, asyncio.TimeoutError, websockets.exceptions.WebSocketException, ValueError, json.JSONDecodeError) as e:
            db.run("UPDATE hub_links SET last_error=? WHERE id=?", (f"injoignable : {type(e).__name__}"[:400], link_id))
        attempt = 0 if time.time() - started > 60 else attempt + 1
        await asyncio.sleep(min(60, 2 ** min(attempt, 6)) + random.random() * 2)


# ------------------------------------------------------------------ gestionnaire des connexions sortantes
_loop: asyncio.AbstractEventLoop | None = None
_tasks: dict[str, asyncio.Task] = {}
_started = False
_wake: asyncio.Event | None = None


def reconcile_soon() -> None:
    if _loop and _wake:
        _loop.call_soon_threadsafe(_wake.set)


async def _manage() -> None:
    global _wake
    _wake = asyncio.Event()
    while True:
        try:
            from . import link_guests
            link_guests.enforce()
        except Exception:   # noqa: BLE001 : l'application du délai de grâce ne doit jamais arrêter le gestionnaire
            pass
        want = {r["id"] for r in db.rows("SELECT id FROM hub_links WHERE we_call=1 AND peer_url!='' AND (status='paired' OR (status='revoked' AND peer_notified=0 AND revoked_at>?))",
                                          (time.time() - 7 * 86400,))}
        for lid in list(_tasks):
            if lid not in want or _tasks[lid].done():
                t = _tasks.pop(lid)
                if not t.done():
                    t.cancel()
        for lid in want:
            if lid not in _tasks:
                _tasks[lid] = asyncio.create_task(_caller(lid))
        try:
            await asyncio.wait_for(_wake.wait(), 5)
        except asyncio.TimeoutError:
            pass
        _wake.clear()


def start() -> None:
    """Lance le gestionnaire des liaisons dont CE hub est l'appelant (un fil et sa boucle) ; inactif tant qu'il n'y en a aucune."""
    global _loop, _started
    if _started:
        return
    _started = True

    def run():
        global _loop
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _loop.run_until_complete(_manage())

    threading.Thread(target=run, name="hub-links", daemon=True).start()
