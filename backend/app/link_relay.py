"""Relais de requêtes entre deux hubs liés.

Une personne du hub d'ORIGINE (A) utilise les nœuds du hub d'ACCUEIL (B) depuis l'interface de A : A authentifie la personne, puis transmet sa requête à B par le canal signé.
B rejoue cette requête DANS SES PROPRES ROUTES, au nom du compte invité de cette personne : les droits, le plafond des invités, les politiques 2FA et le journal d'audit sont donc
exactement ceux de B, sans code propre à chaque fonction. A n'affirme que « c'est cette personne » ; il ne dit jamais ce qu'elle peut faire.

Zéro confiance côté B : liaison utilisable et accueil autorisé, invité existant et actif, route dans une liste fermée (HTTP seulement, jamais d'administration, de comptes, de liaisons ni de
WebSocket), tailles bornées, et jamais plus de MAX_PARALLEL requêtes simultanées par liaison."""
import asyncio
import base64
import json
import time

from fastapi import HTTPException
from fastapi.routing import APIRoute

from . import auth, db
from . import hub_link as hl
from . import link_guests as lg
from . import link_proto as proto

MAX_BODY = 3 * 1024 * 1024        # requête ou réponse : au-delà, on refuse clairement plutôt que de saturer le canal
TIMEOUT = 30
MAX_PARALLEL = 8
KEEP_HEADERS = ("content-type", "content-disposition")
_slots: dict[str, asyncio.Semaphore] = {}


def _relayable_routes() -> list[APIRoute]:
    """Les routes de travail sur un nœud qu'un invité peut atteindre. Liste fermée, construite à partir des routeurs eux-mêmes : une nouvelle route de ces routeurs est donc relayable
    (ses droits la protègent), mais rien ne l'est dans les routeurs d'administration, de comptes, de droits, de sauvegardes, de liaisons ou de workers."""
    from . import routes_certs, routes_docker, routes_obs, routes_projects, routes_toggle, routes_traefik
    out = []
    for router in (routes_traefik.router, routes_toggle.router, routes_certs.router, routes_obs.router, routes_docker.router, routes_projects.router):
        for r in router.routes:
            if isinstance(r, APIRoute) and not r.path.startswith("/api/projects/templates"):
                out.append(r)
    return out


_cache: list[APIRoute] | None = None


def allowed(method: str, path: str) -> bool:
    """Cette requête (méthode et chemin, SANS « /api ») peut-elle traverser une liaison ?"""
    global _cache
    if not isinstance(path, str) or not path.startswith("/") or "\\" in path or ".." in path or "//" in path or "?" in path or "#" in path or len(path) > 400 or not path.isprintable():
        return False
    full = "/api" + path
    if full.startswith("/api/projects/templates"):   # les modèles sont ceux de CE hub, pas ceux d'un nœud : jamais à travers une liaison
        return False
    if method == "GET" and (full == "/api/nodes" or (full.startswith("/api/nodes/") and full.count("/") == 3)):
        return True    # la liste de ses nœuds et le détail de l'un d'eux : jamais les routes d'écriture des nœuds
    if _cache is None:
        _cache = _relayable_routes()
    return any(method in (r.methods or ()) and r.path_regex.match(full) for r in _cache)


# ------------------------------------------------------------------ côté accueil : rejouer la requête
async def _asgi_call(method: str, path: str, query: str, headers: list[tuple[bytes, bytes]], body: bytes) -> tuple[int, dict, bytes]:
    """Appelle l'application dans ce processus, sans réseau ni dépendance supplémentaire."""
    from .main import app
    got, state = [], {"status": 500, "headers": {}, "size": 0, "big": False}
    sent_body = False

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            state["status"] = msg["status"]
            state["headers"] = {k.decode().lower(): v.decode("latin-1") for k, v in msg.get("headers", [])}
        elif msg["type"] == "http.response.body":
            chunk = msg.get("body", b"")
            state["size"] += len(chunk)
            if state["size"] > MAX_BODY:
                state["big"] = True
            elif chunk:
                got.append(chunk)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http", "path": "/api" + path, "raw_path": ("/api" + path).encode(),
             "query_string": query.encode(), "root_path": "", "headers": headers, "client": ("127.0.0.1", 0), "server": ("relais", 80)}
    await asyncio.wait_for(app(scope, receive, send), TIMEOUT)
    if state["big"]:
        return 413, {"content-type": "application/json"}, json.dumps({"detail": "Réponse trop volumineuse pour passer par une liaison entre hubs"}).encode()
    return state["status"], state["headers"], b"".join(got)


def _error(status: int, message: str) -> dict:
    return {"status": status, "ctype": "application/json", "body": base64.b64encode(json.dumps({"detail": message}).encode()).decode()}


def _guest_for(link_id: str, msg: dict):
    """Le compte invité visé, ou une erreur claire : liaison, accueil, existence et état sont revérifiés à CHAQUE requête."""
    r = hl.get(link_id)
    if not r or not hl.usable(r) or not r["host_enabled"] or not lg.has_feature(r, proto.RELAY_FEATURE):
        return None, _error(403, "Cette liaison n'accueille pas de requêtes")
    if not hl.within_grace(r):
        return None, _error(403, "Liaison coupée depuis trop longtemps")
    who = msg.get("as") if isinstance(msg.get("as"), dict) else {}
    ref = hl.clean_text(str(who.get("id", "")), 64)
    u = db.one("SELECT * FROM users WHERE guest_link=? AND guest_ref=? AND source='guest'", (link_id, ref)) if ref else None
    if not u or u["disabled"] or u["guest_blocked"] or u["guest_suspended"]:
        return None, _error(403, "Votre compte invité n'est pas actif sur ce hub")
    return u, None


async def handle(link_id: str, msg: dict) -> dict:
    """Une requête venue de l'autre hub : renvoie le message « res » à lui retourner."""
    rid = str(msg.get("id", ""))[:64]
    sem = _slots.setdefault(link_id, asyncio.Semaphore(MAX_PARALLEL))
    if sem.locked():
        return {"type": "res", "id": rid, **_error(429, "Trop de requêtes simultanées sur cette liaison")}
    async with sem:
        if msg.get("op") == "setup":   # un invité demande le lien pour définir son propre mot de passe ici (mode direct)
            who = msg.get("as") if isinstance(msg.get("as"), dict) else {}
            try:
                token = await asyncio.to_thread(lg.issue_setup, link_id, hl.clean_text(str(who.get("id", "")), 64))
            except hl.LinkError as e:
                return {"type": "res", "id": rid, **_error(e.status, str(e))}
            return {"type": "res", "id": rid, "status": 200, "ctype": "application/json", "body": base64.b64encode(json.dumps({"token": token}).encode()).decode()}
        method, path, node = str(msg.get("method", "")).upper(), msg.get("path"), hl.clean_text(str(msg.get("node", "")), 64)
        guest, err = await asyncio.to_thread(_guest_for, link_id, msg)
        if err:
            return {"type": "res", "id": rid, **err}
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE") or not allowed(method, path):
            return {"type": "res", "id": rid, **_error(403, "Cette action n'est pas disponible à travers une liaison")}
        query = msg.get("query") if isinstance(msg.get("query"), str) and len(msg["query"]) <= 2000 and msg["query"].isprintable() else ""
        try:
            body = base64.b64decode(msg.get("body") or "", validate=True)
        except Exception:
            return {"type": "res", "id": rid, **_error(400, "Requête invalide")}
        if len(body) > MAX_BODY:
            return {"type": "res", "id": rid, **_error(413, "Requête trop volumineuse pour une liaison")}
        token = auth._token(guest["id"], "access", 60)
        headers = [(b"host", b"relais"), (b"authorization", f"Bearer {token}".encode()), (b"accept", b"application/json, */*")]
        ctype = hl.clean_text(msg.get("ctype"), 120)
        if ctype:
            headers.append((b"content-type", ctype.encode()))
        if body:
            headers.append((b"content-length", str(len(body)).encode()))
        if node:
            headers.append((b"x-node", node.encode()))
        try:
            status, hdrs, out = await _asgi_call(method, path, query, headers, body)
        except asyncio.TimeoutError:
            return {"type": "res", "id": rid, **_error(504, "Le hub n'a pas répondu à temps")}
        except Exception:   # noqa: BLE001 : jamais de détail interne vers l'autre hub
            return {"type": "res", "id": rid, **_error(502, "Erreur interne du hub")}
        return {"type": "res", "id": rid, "status": status, "ctype": hdrs.get("content-type", ""), "disposition": hdrs.get("content-disposition", ""), "body": base64.b64encode(out).decode()}


# ------------------------------------------------------------------ côté origine : envoyer la requête
def can_use(user_id: int, link_id: str) -> bool:
    """La personne fait partie d'une équipe que CE hub partage sur cette liaison (c'est ce qui décide qui voit l'encart de l'autre hub)."""
    return bool(db.one("SELECT 1 FROM group_members m JOIN link_shared_groups s ON s.group_id=m.group_id WHERE s.link_id=? AND m.user_id=?", (link_id, user_id)))


async def call(link_id: str, user, method: str, path: str, query: str, node: str, body: bytes, ctype: str, op: str = "") -> dict:
    r = hl.get(link_id)
    if not r or not hl.usable(r):
        raise HTTPException(404, "Liaison introuvable")
    if not can_use(user.id, link_id):
        raise HTTPException(403, "Vous n'avez pas accès à cette liaison")
    if not lg.has_feature(r, proto.RELAY_FEATURE):
        raise HTTPException(409, "Les deux hubs n'ont pas une version compatible pour le relais : mettez à jour le plus ancien des deux")
    ch = hl._chans.get(link_id)
    if not ch:
        raise HTTPException(503, "Le hub lié est hors ligne")
    if not op and not allowed(method, path):
        raise HTTPException(403, "Cette action n'est pas disponible à travers une liaison")
    if len(body) > MAX_BODY:
        raise HTTPException(413, "Requête trop volumineuse pour une liaison")
    msg = {"type": "req", "id": f"{int(time.time() * 1000)}-{id(body) % 100000}", "as": {"id": str(user.id), "username": user.username}, "method": method, "path": path,
           "query": query, "node": node, "ctype": ctype, "body": base64.b64encode(body).decode(), **({"op": op} if op else {})}
    try:
        loop = getattr(ch, "loop", None)
        if loop is None or loop is asyncio.get_running_loop():
            return await ch.request(msg, TIMEOUT)
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(ch.request(msg, TIMEOUT), loop))
    except asyncio.TimeoutError:
        raise HTTPException(504, "Le hub lié n'a pas répondu à temps")
    except (ConnectionError, OSError):
        raise HTTPException(503, "Le hub lié s'est déconnecté pendant la requête")
