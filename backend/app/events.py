"""Temps réel : le serveur SIGNALE ce qui a changé (« traefik », « nodes », « domains »…), le navigateur recharge ce qu'il affiche.

Aucune donnée ne circule dans ces messages (seulement un sujet et un identifiant de nœud) : le navigateur redemande les données par
l'API habituelle, qui applique les droits. Un utilisateur ne reçoit les signaux d'un nœud que s'il y a accès.

Connexion : WebSocket /api/events ; le premier message est {"token": <jeton d'accès>}. Fermeture 4401 = jeton absent ou périmé
(le navigateur en obtient un nouveau et se reconnecte)."""
import asyncio
import hashlib
import json
import threading
import time

from fastapi import APIRouter, WebSocket

from . import db
from .config import settings
from .fswatch import snapshot, stamp

router = APIRouter()

NODE_TOPICS = {"traefik", "live", "certs", "node", "audit", "alerts", "security"}   # propres à un nœud
MAX_PER_USER = 10
MAX_TOTAL = 400
COALESCE = 0.15       # secondes : plusieurs signaux rapprochés n'en font qu'un
PING_EVERY = 25
CLOSE_AUTH = 4401


class Conn:
    def __init__(self, ws: WebSocket, loop: asyncio.AbstractEventLoop, p) -> None:
        self.ws, self.loop, self.p = ws, loop, p
        self.nodes: set[str] = set()
        self.pending: set[tuple[str, str | None]] = set()
        self.wake = asyncio.Event()

    def allows(self, topic: str, node: str | None, user: int | None) -> bool:
        if topic in NODE_TOPICS:
            return (self.p.is_admin if node is None else node in self.nodes)
        if topic == "users":
            return bool(self.p.is_admin)
        if topic == "notices":
            return user is None or user == self.p.id
        if topic == "session":   # « votre compte a changé » : seulement pour l'utilisateur concerné
            return user == self.p.id
        return True   # « nodes », « domains » : liste de ce que l'utilisateur peut voir, de toute façon refiltrée par l'API

    def push(self, topic: str, node: str | None) -> None:   # toujours appelée dans la boucle asyncio
        self.pending.add((topic, node))
        self.wake.set()


_conns: set[Conn] = set()
_lock = threading.Lock()


def publish(topic: str, node: str | None = None, user: int | None = None) -> None:
    """Signale un changement. Utilisable depuis n'importe quel thread ; sans effet si personne n'écoute."""
    with _lock:
        snap = list(_conns)
    for c in snap:
        if c.allows(topic, node, user):
            try:
                c.loop.call_soon_threadsafe(c.push, topic, node)
            except RuntimeError:   # boucle fermée
                pass


def listeners() -> int:
    return len(_conns)


def _topics_for(action: str, node: str | None) -> list[tuple[str, str | None]]:
    """Ce qu'une entrée du journal d'audit change à l'écran."""
    out: list[tuple[str, str | None]] = [("audit", node)]
    head = action.split(".")[0]
    if head == "traefik":
        out += [("traefik", node), ("live", node)]
    elif head == "node":
        out += [("node", node), ("nodes", None), ("traefik", node)]
    elif head in ("credential", "domain", "dns", "sync"):
        out += [("domains", None)]
    elif head == "user":
        out += [("users", None), ("domains", None)]
    elif head == "rights":   # rôles, groupes, attributions : les droits de personnes changent
        out += [("nodes", None)] + ([("node", node)] if node else [])
    elif head == "admin":    # accès d'urgence, suspension, purge : l'état des nœuds change pour tout le monde (bandeaux, listes)
        out += [("nodes", None), ("node", node)]
    return out


def on_audit(action: str, node: str | None) -> None:
    if node and action.startswith("traefik."):   # le prochain état de Traefik doit être relu, pas servi depuis le cache de 5 s
        from .traefik_live import forget
        forget(node)
    for topic, n in _topics_for(action, node):
        publish(topic, n)


def _identity(token: str):
    from .auth import _decode, _load
    data = _decode(token, "access")
    return _load(int(data["sub"])), float(data["exp"])


def _reload(uid: int):
    from .auth import _load
    return _load(uid)


def _node_ids(p) -> set[str]:
    from .nodes import accessible_nodes
    return {c.id for c in accessible_nodes(p)}


@router.websocket("/api/events")
async def events_ws(ws: WebSocket):
    await ws.accept()
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), 8))
        p, exp = await asyncio.to_thread(_identity, str(first.get("token") or ""))
    except Exception:  # noqa: BLE001 - jeton absent, invalide, délai dépassé : on ne détaille pas
        await ws.close(CLOSE_AUTH)
        return
    with _lock:
        mine = sum(1 for c in _conns if c.p.id == p.id)
        if mine >= MAX_PER_USER or len(_conns) >= MAX_TOTAL:
            await ws.close(1013)
            return
    c = Conn(ws, asyncio.get_running_loop(), p)
    c.nodes = await asyncio.to_thread(_node_ids, p)
    with _lock:
        _conns.add(c)
    try:
        await ws.send_text('{"t":"ready"}')

        async def reader() -> None:
            while True:
                await ws.receive_text()   # le client n'envoie que des ping ; on ne s'en sert que pour détecter la déconnexion

        async def writer() -> None:
            while True:
                try:
                    await asyncio.wait_for(c.wake.wait(), PING_EVERY)
                except asyncio.TimeoutError:
                    if time.time() >= exp:
                        await ws.close(CLOSE_AUTH)
                        return
                    await ws.send_text('{"t":"ping"}')
                    continue
                await asyncio.sleep(COALESCE)
                c.wake.clear()
                items = sorted(c.pending, key=lambda x: (x[0], x[1] or ""))
                c.pending.clear()
                if any(t == "session" for t, _ in items):           # compte modifié, désactivé ou supprimé : on revérifie tout de suite
                    try:
                        c.p = await asyncio.to_thread(_reload, c.p.id)
                    except Exception:  # noqa: BLE001 - désactivé ou supprimé : la session s'arrête
                        await ws.close(CLOSE_AUTH)
                        return
                if any(t in ("nodes", "node", "session") for t, _ in items):   # l'accès aux nœuds a pu changer
                    try:
                        c.nodes = await asyncio.to_thread(_node_ids, c.p)
                    except Exception:  # noqa: BLE001
                        pass
                await ws.send_text(json.dumps({"t": "changed", "topics": [{"t": t, "node": n} for t, n in items]}))
                if time.time() >= exp:
                    await ws.close(CLOSE_AUTH)
                    return

        tasks = [asyncio.create_task(reader()), asyncio.create_task(writer())]
        done, rest = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in rest:
            t.cancel()
        for t in done:
            t.exception()   # lu pour ne pas laisser « Task exception was never retrieved » dans les journaux (déconnexion normale)
    except Exception:  # noqa: BLE001 - déconnexion du client
        pass
    finally:
        with _lock:
            _conns.discard(c)


# ------------------------------------------------------------------ surveillance côté serveur
_started = False


def start_watchers() -> None:
    """Thread qui détecte ce que personne ne « signale » : fichiers modifiés à la main, relevé des certificats, état de Traefik."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_watch, name="events-watch", daemon=True).start()


def _live_hash(node: dict) -> str:
    from .traefik_live import live_status
    d = live_status(node)
    routers = {k: (v.get("status"), v.get("error")) for k, v in (d.get("routers") or {}).items()}
    return hashlib.sha1(json.dumps([d.get("available"), d.get("version"), sorted(routers.items())], default=str).encode()).hexdigest()


def _watch() -> None:
    last_files = last_cert = None
    last_tick = time.time()
    live: dict[str, str] = {}
    tick = 0
    while True:
        time.sleep(2)
        tick += 1
        try:
            if not listeners():
                last_files = last_cert = None   # personne n'écoute : on repart d'un état neuf au prochain venu
                last_tick = time.time()
                live.clear()
                continue
            local = db.one("SELECT id FROM nodes WHERE kind='local' AND deleted_at IS NULL ORDER BY created_at LIMIT 1")
            if local:
                snap = snapshot(settings.dynamic_dir)
                if last_files is not None and snap != last_files:
                    publish("traefik", local["id"])
                    publish("live", local["id"])
                last_files = snap
                if settings.certs_file:
                    st = stamp(settings.certs_file)
                    if last_cert is not None and st != last_cert:
                        publish("certs", local["id"])
                    last_cert = st
            for r in db.rows("SELECT node_id FROM emergency_sessions WHERE ended_at IS NULL AND expires_at>? AND expires_at<=?", (last_tick, time.time())):
                publish("nodes")                  # un accès d'urgence vient d'expirer : bandeaux et droits changent sans action de personne
                publish("node", r["node_id"])
            for r in db.rows("SELECT subject_type, subject_id FROM grants WHERE expires_at>? AND expires_at<=?", (last_tick, time.time())):
                publish("nodes")                  # une attribution temporaire vient de prendre fin
                for uid in ([r["subject_id"]] if r["subject_type"] == "user" else [x["user_id"] for x in db.rows("SELECT user_id FROM group_members WHERE group_id=?", (r["subject_id"],))]):
                    publish("session", user=uid)
            for r in db.rows("SELECT user_id FROM node_members WHERE expires_at>? AND expires_at<=?", (last_tick, time.time())):
                publish("nodes")                  # un accès temporaire à un nœud vient de se terminer
                publish("session", user=r["user_id"])
            last_tick = time.time()
            if tick % 3 == 0:   # état de Traefik : toutes les ~6 s, seulement pour les nœuds regardés
                from .nodes import node_out
                watched = set().union(*(c.nodes for c in list(_conns))) if _conns else set()
                for row in db.rows("SELECT * FROM nodes WHERE deleted_at IS NULL AND status='active'"):
                    if row["id"] not in watched:
                        continue
                    node = node_out(row)
                    if node["kind"] == "local" and not node.get("apiUrl"):
                        continue
                    try:
                        h = _live_hash(node)
                    except Exception:  # noqa: BLE001
                        continue
                    if live.get(row["id"]) not in (None, h):
                        publish("live", row["id"])
                    live[row["id"]] = h
        except Exception:  # noqa: BLE001 - la surveillance ne doit jamais s'arrêter
            continue
