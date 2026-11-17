"""Worker : relie le dossier dynamique d'un Traefik à un hub (python -m app.worker).

Il ne reçoit aucune connexion : il se connecte lui-même au hub (WebSocket sortant), s'authentifie par signature
(clé Ed25519 générée ici, qui ne quitte jamais la machine) et n'exécute que les opérations de worker_proto.OPS,
uniquement dans son dossier Traefik. Le hub n'a pas besoin de joindre cette machine.

Variables : HUB_URL, ENROLL_TOKEN (premier démarrage seulement), TRAEFIK_DYNAMIC_DIR, DATA_DIR,
            TRAEFIK_API_URL (facultatif : statut en direct), WORKER_ALLOW_INSECURE=1 (tests : autorise http://)."""
import asyncio
import base64
import json
import os
import random
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import worker_proto as proto
from .config import env, settings
from . import worker_log
from .fswatch import snapshot, stamp
from .traefik_live import live_status
from .traefik_store import KINDS, Store, StoreError

VERSION = os.environ.get("APP_VERSION", "dev")
HEALTH_FILE = Path("/tmp/worker.ok")


def log(msg: str) -> None:
    print(f"[worker] {msg}", flush=True)


# ------------------------------------------------------------------ opérations (pures : testables sans réseau)
def _s(a: dict, key: str, optional: bool = False) -> str | None:
    v = a.get(key)
    if v is None and optional:
        return None
    if not isinstance(v, str) or len(v) > proto.MAX_FILE:
        raise StoreError(f"Paramètre invalide : {key}", 422)
    return v


def _steps(raw) -> list[dict]:
    """Valide la forme d'un plan de rangement reçu du hub (les chemins sont revérifiés par le Store)."""
    if not isinstance(raw, list) or not raw or len(raw) > 500:
        raise StoreError("Plan de rangement invalide", 422)
    out = []
    for s in raw:
        ok = isinstance(s, dict) and s.get("kind") in ("file", "blocks", "delete", "rmdir") and isinstance(s.get("from"), str) and isinstance(s.get("to", ""), str)
        if not ok or (s["kind"] == "blocks" and not (isinstance(s.get("ids"), list) and s["ids"] and all(isinstance(x, str) for x in s["ids"]))):
            raise StoreError("Plan de rangement invalide", 422)
        out.append({"kind": s["kind"], "from": s["from"], "to": s.get("to", ""), "ids": s.get("ids", [])})
    return out


def worker_modules() -> tuple[str, ...]:
    """Modules de ce worker (WORKER_MODULES=traefik,docker). Par défaut « traefik » : un worker existant ne change pas."""
    return proto.parse_modules(env("WORKER_MODULES"))


def announced_addresses() -> list[str]:
    """Adresses (IP ou noms) par lesquelles un Traefik d'un AUTRE nœud peut joindre les conteneurs de cette machine (WORKER_ADDRESSES=192.168.1.20,nas.lan)."""
    out = []
    for a in (env("WORKER_ADDRESSES") or "").replace(";", ",").split(","):
        a = a.strip().lower()
        if a and len(a) <= 253 and all(c.isalnum() or c in ".-:" for c in a) and a not in out:
            out.append(a)
    return out[:10]


def exec_allowed() -> bool:
    """Le terminal dans un conteneur n'est ouvert que si l'administrateur du worker l'a voulu."""
    return (env("WORKER_ALLOW_EXEC") or "").lower() in ("1", "true", "yes", "on")


def handle(store: Store, op: str, a: dict, api_url: str = "", modules=None):
    """Exécute UNE opération autorisée par les modules de ce worker et renvoie un résultat JSON ; lève StoreError en cas d'erreur métier."""
    allowed = proto.ops_for(modules if modules is not None else ("traefik",))
    if op not in allowed or not isinstance(a, dict):
        raise StoreError("Opération non autorisée", 400)
    if op in proto.OPS_BY_MODULE["docker"]:
        if op.startswith(("d_exec_", "p_exec_")) and not exec_allowed():
            raise StoreError("Le terminal est désactivé sur ce worker (WORKER_ALLOW_EXEC=true pour l'autoriser)", 403)
        from . import host_ops   # seulement si ce worker gère Docker
        return host_ops.handle(op, a)
    if op == "items":
        items, errors = store.items()
        return {"items": items, "errors": errors}
    if op == "list_files":
        return store.list_files()
    if op == "list_dirs":
        return store.list_dirs()
    if op == "read":
        return store.read(_s(a, "path"))
    if op == "write":
        content = _s(a, "content")
        if len(content.encode()) > proto.MAX_FILE:
            raise StoreError("Fichier trop gros", 413)
        if a.get("create") is True and len(store.list_files()) >= proto.MAX_FILES:
            raise StoreError(f"Trop de fichiers sur ce nœud ({proto.MAX_FILES} maximum)", 409)
        store.write(_s(a, "path"), content, create=a.get("create") is True)
        return True
    if op == "delete":
        store.delete(_s(a, "path"))
        return True
    if op == "move":
        store.move(_s(a, "src"), _s(a, "dst"))
        return True
    if op == "mkdir":
        store.make_dir(_s(a, "path"))
        return True
    if op == "rmdir":
        store.remove_dir(_s(a, "path"))
        return True
    if op == "move_dir":
        store.move_dir(_s(a, "src"), _s(a, "dst"))
        return True
    if op == "certs":
        return store.certs()
    if op == "toggle":
        ids, files = a.get("ids", []), a.get("files", [])
        if not (isinstance(ids, list) and isinstance(files, list) and len(ids) + len(files) <= 500 and all(isinstance(x, str) for x in ids + files)):
            raise StoreError("Paramètres invalides", 422)
        tag = a.get("tag")
        return store.toggle(ids, files, a.get("enable") is True, tag if isinstance(tag, str) and len(tag) <= 200 else None,
                            a.get("reconcile") is not False, a.get("dry") is True)
    if op == "reorganize":
        return store.reorganize(_steps(a.get("steps")), a.get("clean") is not False)
    if op == "history":
        return store.history(_s(a, "path"))
    if op == "history_content":
        return store.history_content(_s(a, "path"), _s(a, "id"))
    if op in ("upsert", "delete_item"):
        kind = (_s(a, "proto"), _s(a, "kind"))
        if kind not in KINDS:
            raise StoreError("Type d'élément inconnu", 422)
        if op == "delete_item":
            return store.delete_item(kind[0], kind[1], _s(a, "name"))
        spec = a.get("spec")
        if not isinstance(spec, dict):
            raise StoreError("Paramètre invalide : spec", 422)
        if len(json.dumps(spec)) > proto.MAX_FILE:
            raise StoreError("Élément trop gros", 413)
        if a.get("file") and len(store.list_files()) >= proto.MAX_FILES and _s(a, "file") not in {f["path"] for f in store.list_files()}:
            raise StoreError(f"Trop de fichiers sur ce nœud ({proto.MAX_FILES} maximum)", 409)
        return store.upsert(kind[0], kind[1], _s(a, "name"), spec, _s(a, "file", True), _s(a, "old_name", True))
    return live_status({"id": "worker", "kind": "local", "apiUrl": api_url})  # op == "live"


def error_payload(e: Exception) -> dict:
    if isinstance(e, StoreError):
        out = {"message": str(e), "status": e.status}
        for k in ("line", "column"):
            if isinstance(getattr(e, k, None), int):
                out[k] = getattr(e, k)
        return out
    log(f"erreur interne : {type(e).__name__}: {e}")
    return {"message": "Erreur interne du worker", "status": 500}  # on ne renvoie jamais les détails au hub


# ------------------------------------------------------------------ identité et enrôlement
class Identity:
    def __init__(self, node_id: str, key: Ed25519PrivateKey) -> None:
        self.node_id, self.key = node_id, key

    @staticmethod
    def public_b64(key: Ed25519PrivateKey) -> str:
        raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.b64encode(raw).decode()

    def sign(self, nonce: str) -> str:
        return base64.b64encode(self.key.sign(proto.auth_message(self.node_id, nonce))).decode()


def identity_path() -> Path:
    return settings.data_dir / "identity.json"


def load_identity(hub: str) -> Identity | None:
    try:
        d = json.loads(identity_path().read_text())
        if d.get("hub") != hub:
            raise SystemExit(f"Ce worker est enrôlé auprès de {d.get('hub')} et non de {hub} : supprimez {identity_path()} pour le réenrôler.")
        return Identity(d["nodeId"], Ed25519PrivateKey.from_private_bytes(base64.b64decode(d["privateKey"])))
    except (OSError, ValueError, KeyError):
        return None


def enroll(hub: str, token: str) -> Identity:
    key = Ed25519PrivateKey.generate()
    body = json.dumps({"token": token.strip(), "publicKey": Identity.public_b64(key), "version": VERSION}).encode()
    req = urllib.request.Request(hub + "/api/worker/enroll", data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 (HUB_URL est choisie par l'administrateur du worker)
            node_id = json.load(r)["nodeId"]
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.load(e).get("detail", "")
        except Exception:
            pass
        raise SystemExit(f"Enrôlement refusé ({e.code}) : {detail or e.reason}")
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit(f"Hub injoignable ({hub}) : {e}")
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    path = identity_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # créé en 0600 d'emblée : jamais lisible par les autres, même un instant
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"hub": hub, "nodeId": node_id, "privateKey": base64.b64encode(raw).decode()}))
    os.chmod(path, 0o600)
    log(f"enrôlé : nœud {node_id}, identité enregistrée dans {path}")
    return Identity(node_id, key)


# ------------------------------------------------------------------ connexion
def ws_url(hub: str) -> str:
    return ("wss://" if hub.startswith("https://") else "ws://") + hub.split("://", 1)[1] + "/api/worker/ws"


async def session(hub: str, ident: Identity, store: Store, api_url: str) -> None:
    async with websockets.connect(ws_url(hub), max_size=proto.MAX_MESSAGE, ping_interval=None, open_timeout=15) as ws:
        send_lock = asyncio.Lock()

        async def send(msg: dict) -> None:
            async with send_lock:
                await ws.send(json.dumps(msg))

        await send({"type": "hello", "nodeId": ident.node_id, "version": VERSION, "protocol": proto.PROTOCOL, "modules": list(worker_modules()),
                    "exec": "docker" in worker_modules() and exec_allowed(), "addresses": announced_addresses(),
                    "access": "traefik" in worker_modules() and worker_log.available()})
        challenge = json.loads(await asyncio.wait_for(ws.recv(), 15))
        if challenge.get("type") != "challenge":
            raise ConnectionError("réponse inattendue du hub")
        await send({"type": "auth", "signature": ident.sign(str(challenge.get("nonce")))})
        if json.loads(await asyncio.wait_for(ws.recv(), 15)).get("type") != "ready":
            raise ConnectionError("authentification refusée")
        log("connecté au hub, en attente de commandes")
        HEALTH_FILE.touch()

        async def pinger() -> None:
            while True:
                await send({"type": "ping"})
                await asyncio.sleep(proto.PING_EVERY)

        async def run(msg: dict) -> None:
            rid = str(msg.get("id"))[:40]
            try:
                result = await asyncio.to_thread(handle, store, str(msg.get("op")), msg.get("args") or {}, api_url, worker_modules())
                out = {"type": "response", "id": rid, "ok": True, "result": result}
            except Exception as e:  # noqa: BLE001
                out = {"type": "response", "id": rid, "ok": False, "error": error_payload(e)}
            await send(out)

        async def watcher() -> None:
            """Signale au hub tout changement sur disque (fichier modifié à la main, relevé des certificats) : il met son écran à jour."""
            last, last_cert = snapshot(store.root), stamp(settings.certs_file)
            while True:
                await asyncio.sleep(2)
                snap, cert = await asyncio.to_thread(snapshot, store.root), stamp(settings.certs_file)
                if snap != last:
                    last = snap
                    await send({"type": "changed"})
                if cert != last_cert:
                    last_cert = cert
                    await send({"type": "changed", "what": "certs"})

        async def shipper() -> None:
            """Envoie au hub les nouvelles lignes du journal d'accès (la position n'est enregistrée qu'une fois la ligne partie)."""
            reader = worker_log.Reader()
            while True:
                lines = await asyncio.to_thread(reader.read)
                if lines:
                    await send({"type": "access", "lines": lines})
                reader.commit()
                await asyncio.sleep(0.2 if len(lines) >= worker_log.BATCH_LINES else 3)

        ping = asyncio.create_task(pinger())
        watch = asyncio.create_task(watcher())
        ship = asyncio.create_task(shipper()) if "traefik" in worker_modules() and worker_log.available() else None
        tasks: set[asyncio.Task] = set()
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "request":
                    t = asyncio.create_task(run(msg))
                    tasks.add(t)
                    t.add_done_callback(tasks.discard)
                elif msg.get("type") == "pong":
                    HEALTH_FILE.touch()
        finally:
            ping.cancel()
            watch.cancel()
            if ship:
                ship.cancel()
            for t in tasks:
                t.cancel()


async def serve(hub: str, ident: Identity, store: Store, api_url: str) -> None:
    attempt = 0
    while True:
        started = time.time()
        try:
            await session(hub, ident, store, api_url)
            log("connexion fermée par le hub")
        except websockets.exceptions.ConnectionClosed as e:
            code = e.rcvd.code if e.rcvd else 0
            log({proto.CLOSE_UNAUTHORIZED: "identité refusée par le hub (worker révoqué ? réenrôlez-le avec un nouveau jeton)",
                 proto.CLOSE_REVOKED: "worker révoqué par le hub", proto.CLOSE_REPLACED: "remplacé par une autre connexion de ce worker",
                 proto.CLOSE_TOO_MANY: "trop de tentatives, le hub me met en attente",
                 proto.CLOSE_SUSPENDED: "nœud suspendu par l'administrateur de la plateforme (je réessaierai)"}.get(code, f"connexion perdue ({code})"))
        except (OSError, ConnectionError, asyncio.TimeoutError, websockets.exceptions.WebSocketException, ValueError) as e:
            log(f"connexion impossible : {type(e).__name__}: {e}")
        attempt = 0 if time.time() - started > 60 else attempt + 1
        delay = min(60, 2 ** min(attempt, 6)) + random.random() * 2
        log(f"nouvelle tentative dans {delay:.0f} s")
        await asyncio.sleep(delay)


def check_health() -> int:
    try:
        return 0 if time.time() - HEALTH_FILE.stat().st_mtime < 90 else 1
    except OSError:
        return 1


def _owner_hint(path) -> str:
    """Propriétaire réel du dossier : c'est exactement la valeur à donner à PUID:PGID (ou à user:)."""
    try:
        st = os.stat(path)
    except OSError:
        return " Le dossier n'existe pas : le volume ou le chemin monté est-il le bon ?"
    return f" Ce dossier appartient à {st.st_uid}:{st.st_gid} (droits {oct(st.st_mode & 0o777)[2:]}) : mettez PUID={st.st_uid} et PGID={st.st_gid}, ou ajoutez les droits d'écriture au groupe."


def main() -> None:
    if "--health" in sys.argv:
        raise SystemExit(check_health())
    hub = (env("HUB_URL") or "").rstrip("/")
    if not hub.startswith(("https://", "http://")):
        raise SystemExit("HUB_URL est requise (ex : https://traefik-ui.example.com)")
    if hub.startswith("http://") and env("WORKER_ALLOW_INSECURE") != "1":
        raise SystemExit("HUB_URL doit être en https:// : les échanges avec le hub ne doivent pas circuler en clair.")
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if "docker" in worker_modules():
        if not settings.docker_socket or not os.path.exists(settings.docker_socket):
            raise SystemExit(f"Le module « docker » est activé mais le socket Docker est introuvable ({settings.docker_socket or 'DOCKER_SOCKET vide'}) : montez /var/run/docker.sock dans ce conteneur.")
        if not os.access(settings.docker_socket, os.R_OK | os.W_OK):
            raise SystemExit(f"Le socket Docker ({settings.docker_socket}) n'est pas accessible à l'utilisateur {os.getuid()}:{os.getgid()} : ajoutez le groupe du socket dans PGROUPS (stat -c %g {settings.docker_socket}).")
        if not settings.project_roots:
            log("PROJECT_ROOTS est vide : la page Projets de ce nœud restera vide (indiquez les dossiers des projets compose, montés à l'identique).")
    for label, path in (("des données", settings.data_dir),) + ((("de Traefik", settings.dynamic_dir),) if "traefik" in worker_modules() else ()):
        if not (path.is_dir() and os.access(path, os.W_OK | os.X_OK)):
            raise SystemExit(f"Le dossier {label} ({path}) est introuvable ou non modifiable par l'utilisateur {os.getuid()}:{os.getgid()}.{_owner_hint(path)}")
    ident = load_identity(hub)
    if ident is None:
        token = env("ENROLL_TOKEN")
        if not token:
            raise SystemExit("Aucune identité : renseignez ENROLL_TOKEN avec le jeton affiché dans l'interface du hub.")
        ident = enroll(hub, token)
    settings.harden_data_dir()
    store = Store()
    api_url = (env("TRAEFIK_API_URL") or "").rstrip("/")
    if "traefik" in worker_modules():
        log(f"journal d'accès : {'lu et envoyé au hub (' + settings.access_log + ')' if worker_log.available() else 'non branché (TRAEFIK_ACCESS_LOG absent ou illisible) : pas de statistiques de visiteurs pour ce nœud'}")
    log(f"nœud {ident.node_id} · modules {','.join(worker_modules())} · dossier {settings.dynamic_dir} · hub {hub}")
    # PID 1 d'un conteneur ignore SIGTERM sans gestionnaire : « docker restart » attendait 10 s puis tuait le worker. On s'arrête tout de suite, proprement.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        asyncio.run(serve(hub, ident, store, api_url))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
