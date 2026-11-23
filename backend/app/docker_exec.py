"""Terminal dans un conteneur (docker exec) : la partie « machine Docker ».

Un WORKER ne peut pas tenir un flux ouvert avec le navigateur : le hub lui parle par requêtes. Il tient donc les sessions de terminal ici, et le hub
les interroge par des opérations nommées : ouvrir, lire (attente courte), écrire, redimensionner, fermer. Le shell lancé est toujours le même
(`SHELL`) : le hub ne peut pas demander une autre commande. Le worker exige en plus WORKER_ALLOW_EXEC=true (voir worker.handle).
Le hub, lui, garde son flux asynchrone (routes_terminal) pour son propre nœud et réutilise `SHELL`, `create_exec`, `resize` d'ici."""
import base64
import binascii
import secrets
import select
import socket
import threading
import time

from . import docker_api as api
from .config import settings
from .docker_api import DockerError, ident
from .traefik_store import StoreError

SHELL = ["/bin/sh", "-c", "command -v bash >/dev/null 2>&1 && exec bash || exec sh"]
MAX_SESSIONS = 5
IDLE_LIMIT = 15 * 60
MAX_READ = 64 * 1024
MAX_INPUT = 64 * 1024
MAX_WAIT = 2.0

_lock = threading.Lock()
_sessions: dict[str, "_Session"] = {}


def create_exec(cid: str) -> str:
    r = api.request("POST", f"/containers/{ident(cid)}/exec", body={"AttachStdin": True, "AttachStdout": True, "AttachStderr": True, "Tty": True,
                                                                       "Cmd": SHELL, "Env": ["TERM=xterm-256color"]})
    return str((r or {}).get("Id") or "")


def resize(exec_id: str, cols: int, rows: int) -> None:
    try:
        api.request("POST", f"/exec/{ident(exec_id)}/resize", {"h": max(1, min(rows, 500)), "w": max(1, min(cols, 500))})
    except DockerError:
        pass


def start_request(exec_id: str) -> bytes:
    body = b'{"Detach":false,"Tty":true}'
    return (b"POST /exec/%b/start HTTP/1.1\r\nHost: docker\r\nContent-Type: application/json\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Length: %d\r\n\r\n%b"
            % (ident(exec_id).encode(), len(body), body))


class _Session:
    def __init__(self, sock: socket.socket, exec_id: str, name: str, first: bytes) -> None:
        self.sock, self.exec_id, self.name = sock, exec_id, name
        self.pending = bytearray(first)
        self.last = time.time()
        self.eof = False
        self.io = threading.Lock()   # une seule lecture à la fois sur la socket


def _connect(exec_id: str) -> tuple[socket.socket, bytes]:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
    try:
        s.connect(settings.docker_socket)
        s.sendall(start_request(exec_id))
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = s.recv(4096)
            if not chunk or len(head) > 16384:
                raise DockerError("Docker a refusé d'ouvrir le terminal", 502)
            head += chunk
        line = head.split(b"\r\n", 1)[0]
        if b" 101 " not in line and b" 200 " not in line:
            raise DockerError("Docker a refusé d'ouvrir le terminal", 502)
        return s, head.split(b"\r\n\r\n", 1)[1]
    except (OSError, DockerError):
        s.close()
        raise


def _reap(now: float) -> None:
    for sid, s in list(_sessions.items()):
        if now - s.last > IDLE_LIMIT:
            _drop(sid)


def _drop(sid: str) -> None:
    with _lock:
        s = _sessions.pop(sid, None)
    if s:
        try:
            s.sock.close()
        except OSError:
            pass


def _get(a: dict) -> "_Session":
    s = _sessions.get(str(a.get("sid") or ""))
    if not s:
        raise StoreError("Terminal fermé ou inconnu", 404)
    s.last = time.time()
    return s


def d_exec_open(a: dict) -> dict:
    cid = str(a.get("id") or "")
    info = api.request("GET", f"/containers/{ident(cid)}/json")
    if not (info or {}).get("State", {}).get("Running"):
        raise StoreError("Le conteneur n'est pas démarré", 409)
    name = str(info.get("Name") or cid).lstrip("/")[:100]
    with _lock:
        _reap(time.time())
        if len(_sessions) >= MAX_SESSIONS:
            raise StoreError("Trop de terminaux ouverts sur ce nœud", 429)
    exec_id = create_exec(info["Id"])
    sock, first = _connect(exec_id)
    resize(exec_id, int(a.get("cols") or 80), int(a.get("rows") or 24))
    sid = secrets.token_urlsafe(12)
    with _lock:
        _sessions[sid] = _Session(sock, exec_id, name, first)
    return {"sid": sid, "name": name}


def d_exec_read(a: dict) -> dict:
    s = _get(a)
    wait = max(0.0, min(float(a.get("wait") or 0), MAX_WAIT))
    with s.io:
        if not s.pending and not s.eof:
            try:
                if select.select([s.sock], [], [], wait)[0]:
                    chunk = s.sock.recv(MAX_READ)
                    if chunk:
                        s.pending += chunk
                    else:
                        s.eof = True
            except (OSError, ValueError):
                s.eof = True
        data, s.pending = bytes(s.pending[:MAX_READ]), s.pending[MAX_READ:]
    out = {"data": base64.b64encode(data).decode(), "eof": s.eof and not s.pending}
    if out["eof"]:
        _drop(str(a.get("sid")))
    return out


def d_exec_write(a: dict) -> dict:
    s = _get(a)
    try:
        data = base64.b64decode(str(a.get("data") or ""), validate=True)
    except (binascii.Error, ValueError):
        raise StoreError("Saisie invalide", 422)
    if len(data) > MAX_INPUT:
        raise StoreError("Saisie trop longue", 413)
    try:
        s.sock.sendall(data)
    except OSError:
        raise StoreError("Terminal fermé", 410)
    return {"ok": True}


def d_exec_resize(a: dict) -> dict:
    s = _get(a)
    resize(s.exec_id, int(a.get("cols") or 80), int(a.get("rows") or 24))
    return {"ok": True}


def d_exec_close(a: dict) -> dict:
    _drop(str(a.get("sid") or ""))
    return {"ok": True}


OPS = {"d_exec_open": d_exec_open, "d_exec_read": d_exec_read, "d_exec_write": d_exec_write, "d_exec_resize": d_exec_resize, "d_exec_close": d_exec_close}
