"""Client minimal de l'API Docker (socket Unix), sans dépendance : http.client suffit.

Ne fait que parler à Docker ; aucune décision de droits ici. Toute erreur devient une DockerError(status, message) que les routes traduisent."""
import http.client
import json
import socket
from urllib.parse import quote, urlencode

from .config import settings


class DockerError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class _Conn(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def request(method: str, path: str, params: dict | None = None, body=None, timeout: float = 20, raw: bool = False, ok=(200, 201, 204, 304)):
    """Appelle l'API Docker. Renvoie le JSON décodé (ou les octets si raw, ou None si la réponse est vide)."""
    sock = settings.docker_socket
    if not sock:
        raise DockerError("Docker n'est pas branché sur ce nœud (aucun socket)", 503)
    url = path + (("?" + urlencode(params, doseq=True)) if params else "")
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn = _Conn(sock, timeout)
    try:
        conn.request(method, url, body=json.dumps(body) if body is not None else None, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
    except (OSError, http.client.HTTPException) as e:
        raise DockerError(f"Docker ne répond pas : {e}", 503)
    finally:
        conn.close()
    if resp.status not in ok:
        msg = ""
        try:
            msg = (json.loads(data) or {}).get("message", "")
        except ValueError:
            msg = data[:200].decode("utf-8", "replace")
        raise DockerError(msg or f"Docker a répondu {resp.status}", 404 if resp.status == 404 else 409 if resp.status == 409 else 502)
    if raw:
        return data
    if not data:
        return {"notModified": True} if resp.status == 304 else None
    try:
        return json.loads(data)
    except ValueError:
        return data.decode("utf-8", "replace")


def ident(value: str) -> str:
    """Identifiant ou nom de ressource sûr à mettre dans une URL (jamais de « / » ni de « ? » venus de l'extérieur)."""
    v = str(value or "")
    if not v or len(v) > 200 or any(c in v for c in "/?#\\ \x00") or v.startswith("-"):
        raise DockerError("Identifiant invalide", 422)
    return quote(v, safe="")


def demux(data: bytes) -> list[tuple[str, str]]:
    """Journal d'un conteneur : flux multiplexé (en-têtes de 8 octets) ou texte brut (conteneur avec TTY). Renvoie [("out"|"err", texte)]."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(data)
    if n >= 8 and data[0] in (0, 1, 2) and data[1:4] == b"\x00\x00\x00":
        while i + 8 <= n:
            kind, size = data[i], int.from_bytes(data[i + 4:i + 8], "big")
            chunk = data[i + 8:i + 8 + size]
            out.append(("err" if kind == 2 else "out", chunk.decode("utf-8", "replace")))
            i += 8 + size
    else:
        out.append(("out", data.decode("utf-8", "replace")))
    return out
