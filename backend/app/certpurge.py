"""Retire des certificats d'acme.json, directement depuis l'interface.

Traefik charge acme.json au démarrage, le garde en mémoire et le réécrit en entier à chaque obtention : une entrée retirée pendant qu'il tourne
est servie quand même, puis revient. L'opération complète est donc : arrêter Traefik, retirer l'entrée, redémarrer Traefik (quelques secondes).
Une copie `acme.json.bak-DATE` est faite avant toute modification (les 5 dernières sont gardées). Les clés privées ne sont jamais renvoyées au
navigateur : l'interface ne reçoit que des noms. Un certificat retiré dont le site existe toujours est redemandé par Traefik à son démarrage
(c'est ainsi qu'on « renouvelle »)."""

import http.client
import json
import os
import shutil
import socket
import time
from pathlib import Path

MAX_FILE = 20 * 1024 * 1024
MAX_REQUESTS = 200
KEEP_BACKUPS = 5



def _s(v, n=253) -> str:
    return v[:n] if isinstance(v, str) else ""


def clean_requests(raw) -> list[dict]:
    """Demandes valides : [{resolver, main, mode}] ; tout ce qui n'a pas la bonne forme est ignoré."""
    out, seen = [], set()
    for r in (raw.get("requests") if isinstance(raw, dict) else None) or []:
        if not isinstance(r, dict):
            continue
        key = (_s(r.get("resolver"), 60), _s(r.get("main")).lower())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        out.append({"resolver": key[0], "main": key[1], "mode": "renew" if r.get("mode") == "renew" else "delete"})
        if len(out) >= MAX_REQUESTS:
            break
    return out


def apply(src: Path, requests: list[dict], dry: bool = False, now: float | None = None) -> dict:
    """Retire de `src` les certificats demandés. Ne touche à rien (ni sauvegarde) si rien ne correspond ou si le fichier est illisible."""
    now = now or time.time()
    res = {"version": 1, "at": now, "removed": [], "missing": [], "backup": None, "error": None, "dryRun": dry}
    try:
        if src.stat().st_size > MAX_FILE:
            raise ValueError("fichier trop gros")
        data = json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("format inattendu")
    except FileNotFoundError:
        res["error"] = f"{src.name} est introuvable dans le dossier monté"
        return res
    except PermissionError:
        res["error"] = f"Droits insuffisants sur {src.name} : lancez l'outil avec l'utilisateur (UID:GID) de Traefik"
        return res
    except (OSError, ValueError):
        res["error"] = f"{src.name} est illisible ou n'est pas un acme.json : rien n'a été modifié"
        return res
    wanted = {(r["resolver"], r["main"]): r for r in requests}
    found = set()
    for resolver, body in data.items():
        if not isinstance(body, dict):
            continue
        for k in ("Certificates", "certificates"):
            certs = body.get(k)
            if not isinstance(certs, list):
                continue
            keep = []
            for c in certs:
                dom = c.get("domain") or c.get("Domain") if isinstance(c, dict) else None
                main = _s((dom or {}).get("main") or (dom or {}).get("Main")).lower() if isinstance(dom, dict) else ""
                key = (resolver, main)
                if key in wanted:
                    found.add(key)
                    res["removed"].append({"resolver": resolver, "main": main, "mode": wanted[key]["mode"]})
                else:
                    keep.append(c)
            body[k] = keep
    res["missing"] = [{"resolver": r, "main": m} for (r, m) in wanted if (r, m) not in found]
    if not found or dry:
        return res
    try:
        backup = src.with_name(f"{src.name}.bak-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}")
        shutil.copy2(src, backup)
        os.chmod(backup, 0o600)
        res["backup"] = backup.name
        old = sorted(src.parent.glob(f"{src.name}.bak-*"))
        for b in old[:-KEEP_BACKUPS]:
            b.unlink(missing_ok=True)
        tmp = src.with_name(f".{src.name}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, src)
    except OSError as e:
        res.update(error=f"Écriture impossible ({e.strerror or 'droits'}) : acme.json n'a pas été modifié", removed=[])
    return res


# ------------------------------------------------------------------ arrêt / démarrage de Traefik (démon Docker)
class _Unix(http.client.HTTPConnection):
    def __init__(self, path: str):
        super().__init__("localhost", timeout=60)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(60)
        self.sock.connect(self._path)


def _docker(sock: str, container: str, action: str) -> None:
    """Arrête ou démarre UN conteneur (c'est le seul appel au démon Docker). 204 / 304 (déjà dans cet état) = succès."""
    if sock.startswith("/"):
        c = _Unix(sock)
    else:   # « hôte:port » d'un proxy de socket
        host, _, port = sock.partition(":")
        c = http.client.HTTPConnection(host, int(port or 2375), timeout=60)
    try:
        c.request("POST", f"/containers/{container}/{action}" + ("?t=30" if action == "stop" else ""))
        r = c.getresponse()
        body = r.read(300).decode("utf-8", "replace")
    except OSError as e:
        raise RuntimeError(f"le démon Docker est injoignable ({e.strerror or e})")
    finally:
        c.close()
    if r.status == 404:
        raise RuntimeError(f"le conteneur « {container} » n'existe pas (variable TRAEFIK_CONTAINER)")
    if r.status not in (204, 304):
        raise RuntimeError(f"Docker a répondu {r.status} {body[:100]}")


def unavailable(src: str | None, sock: str) -> str | None:
    """Pourquoi la gestion directe n'est pas possible (None = possible) : acme.json lisible et modifiable, démon Docker joignable."""
    if not src:
        return "TRAEFIK_CERTS_FILE n'est pas défini"
    p = Path(src)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"{p.name} est introuvable ou illisible"
    if not isinstance(data, dict) or not any(isinstance(b, dict) and ("Certificates" in b or "certificates" in b) for b in data.values()):
        return f"{p.name} n'est pas un acme.json (un relevé du relais ne suffit pas pour modifier)"
    if not (os.access(p, os.W_OK) and os.access(p.parent, os.W_OK)):
        return f"{p.name} est monté en lecture seule (ou ses droits l'interdisent)"
    if sock.startswith("/") and not os.path.exists(sock):
        return "le socket Docker n'est pas monté dans l'interface"
    return None


def run(src: Path, requests: list[dict], sock: str, container: str) -> dict:
    """Arrête Traefik, retire les certificats, REDÉMARRE Traefik — toujours, même si le retrait échoue. Si l'arrêt échoue, rien n'est touché."""
    res = {"version": 1, "at": time.time(), "removed": [], "missing": [], "backup": None, "dryRun": False, "restarted": False, "error": None}
    try:
        _docker(sock, container, "stop")
    except RuntimeError as e:
        res["error"] = f"Traefik n'a pas pu être arrêté ({e}) : rien n'a été modifié."
        return res
    try:
        res.update(apply(src, requests))
    except Exception as e:   # noqa: BLE001 — Traefik doit repartir quoi qu'il arrive
        res["error"] = f"Erreur inattendue ({type(e).__name__}) : acme.json n'a pas été modifié"
    try:
        _docker(sock, container, "start")
        res["restarted"] = True
    except RuntimeError as e:
        res["error"] = (res["error"] + " · " if res["error"] else "") + f"Traefik n'a PAS redémarré ({e}) : lancez « docker start {container} »"
    return res
