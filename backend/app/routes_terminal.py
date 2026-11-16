"""Terminal dans un conteneur (docker exec), par WebSocket.

Le droit le plus sensible : « docker.exec » (jamais donné par défaut, même aux administrateurs ; la double authentification peut être exigée).
Sur le nœud du hub : flux direct vers Docker. Sur un WORKER : le worker tient la session (docker_exec) et le hub l'interroge par des opérations nommées
(lecture courte répétée) ; le worker doit l'avoir autorisé (WORKER_ALLOW_EXEC=true). Le shell lancé est toujours le même, jamais une commande choisie.
Protocole : le premier message est {"token", "node", "container", "cols", "rows"} ; puis le client envoie {"i": texte} (saisie) ou {"r": [colonnes, lignes]}
(redimensionnement), et reçoit des trames binaires (la sortie du terminal). Fermeture 4401 = jeton absent ou périmé, 4403 = refus, 4501 = non disponible ici."""
import asyncio
import base64
import json
import threading
import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from . import docker_api as api
from . import docker_exec, worker_link
from .config import settings

router = APIRouter()
CLOSE_AUTH, CLOSE_DENIED, CLOSE_UNAVAILABLE, CLOSE_BUSY = 4401, 4403, 4501, 1013
IDLE_LIMIT = 15 * 60
MAX_TOTAL, MAX_PER_USER = 10, 3
MAX_INPUT = 64 * 1024
POLL = 1.0   # secondes d'attente d'une lecture sur un worker
_sessions: dict[int, int] = {}
_lock = threading.Lock()


def _prepare(token: str, node_id: str, container: str):
    """(Synchrone, dans un thread) identité + droits + conteneur. Lève HTTPException si refusé."""
    from .events import _identity
    from .nodes import _ctx_by_id, _guard_suspended
    p, exp = _identity(token)
    ctx = _ctx_by_id(p, node_id)
    _guard_suspended(ctx)
    if "docker" not in (ctx.node.get("modules") or []):
        raise HTTPException(409, "Ce nœud ne gère pas Docker")
    remote = ctx.node["kind"] == "remote"
    ctx.need("docker.exec")
    if remote and not worker_link.feature(ctx.id, "exec"):
        raise HTTPException(501, "Le terminal est désactivé sur ce worker (WORKER_ALLOW_EXEC=true dans son compose) ou le worker est hors ligne")
    from .routes_docker import run
    lst = run(ctx, "d_containers", {"all": True})["containers"]
    c = next((x for x in lst if container in (x["name"], x["id"]) or (len(container) >= 4 and x["fullId"].startswith(container))), None)
    if not c:
        raise HTTPException(404, "Conteneur introuvable")
    if c["state"] != "running":
        raise HTTPException(409, "Le conteneur n'est pas démarré")
    ctx.audit("docker.exec", f"terminal ouvert dans {c['name']}")
    return ctx, c, exp


def _create_exec(cid: str, cols: int, rows: int) -> str:
    return docker_exec.create_exec(cid)


def _resize(exec_id: str, cols: int, rows: int) -> None:
    docker_exec.resize(exec_id, cols, rows)


async def _open_stream(exec_id: str):
    """Ouvre la connexion « détournée » vers le démon : après l'en-tête HTTP, c'est le flux brut du terminal dans les deux sens."""
    reader, writer = await asyncio.open_unix_connection(settings.docker_socket)
    writer.write(docker_exec.start_request(exec_id))
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    if b" 101 " not in head.split(b"\r\n", 1)[0] and b" 200 " not in head.split(b"\r\n", 1)[0]:
        writer.close()
        raise ConnectionError("Docker a refusé d'ouvrir le terminal")
    return reader, writer


async def _serve_remote(ws: WebSocket, ctx, c: dict, cols: int, rows: int, exp: float) -> None:
    """Terminal d'un worker : le hub ouvre la session, lit en boucle (attente courte côté worker) et écrit chaque saisie."""
    from .routes_docker import run
    opened = await asyncio.to_thread(run, ctx, "d_exec_open", {"id": c["fullId"], "cols": cols, "rows": rows})
    sid = opened["sid"]
    await ws.send_text('{"t":"ready"}')
    last = [time.time()]

    async def down() -> None:
        while True:
            res = await asyncio.to_thread(run, ctx, "d_exec_read", {"sid": sid, "wait": POLL})
            data = base64.b64decode(res.get("data") or "")
            if data:
                await ws.send_bytes(data)
            if res.get("eof"):
                return

    async def up() -> None:
        while True:
            msg = json.loads(await ws.receive_text())
            last[0] = time.time()
            if isinstance(msg.get("i"), str) and len(msg["i"]) <= MAX_INPUT:
                await asyncio.to_thread(run, ctx, "d_exec_write", {"sid": sid, "data": base64.b64encode(msg["i"].encode()).decode()})
            elif isinstance(msg.get("r"), list) and len(msg["r"]) == 2:
                await asyncio.to_thread(run, ctx, "d_exec_resize", {"sid": sid, "cols": int(msg["r"][0]), "rows": int(msg["r"][1])})

    async def watch() -> None:
        while True:
            await asyncio.sleep(10)
            if time.time() - last[0] > IDLE_LIMIT or time.time() >= exp:
                await ws.close(CLOSE_AUTH)
                return

    tasks = [asyncio.create_task(down()), asyncio.create_task(up()), asyncio.create_task(watch())]
    try:
        done, rest = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in rest:
            t.cancel()
        for t in done:
            t.exception()
    finally:
        try:
            await asyncio.to_thread(run, ctx, "d_exec_close", {"sid": sid})
        except Exception:  # noqa: BLE001 - le worker referme de lui-même une session inactive
            pass


@router.websocket("/api/docker/exec")
async def exec_ws(ws: WebSocket):
    await ws.accept()
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), 8))
        ctx, c, exp = await asyncio.to_thread(_prepare, str(first.get("token") or ""), str(first.get("node") or ""), str(first.get("container") or ""))
    except HTTPException as e:
        await ws.close(CLOSE_UNAVAILABLE if e.status_code in (501, 409) else CLOSE_DENIED if e.status_code in (403, 404) else CLOSE_AUTH, reason=str(e.detail)[:100])
        return
    except Exception:  # noqa: BLE001 - jeton absent, invalide, délai dépassé : on ne détaille pas
        await ws.close(CLOSE_AUTH)
        return
    uid = ctx.p.id
    with _lock:
        if _sessions.get(uid, 0) >= MAX_PER_USER or sum(_sessions.values()) >= MAX_TOTAL:
            await ws.close(CLOSE_BUSY, reason="Trop de terminaux ouverts")
            return
        _sessions[uid] = _sessions.get(uid, 0) + 1
    writer = None
    sid = None
    try:
        cols, rows = int(first.get("cols") or 80), int(first.get("rows") or 24)
        if ctx.node["kind"] == "remote":
            sid = await _serve_remote(ws, ctx, c, cols, rows, exp)
            return
        exec_id = await asyncio.to_thread(_create_exec, c["fullId"], cols, rows)
        reader, writer = await _open_stream(exec_id)
        await asyncio.to_thread(_resize, exec_id, cols, rows)
        await ws.send_text('{"t":"ready"}')
        last = [time.time()]

        async def down() -> None:       # terminal → navigateur
            while True:
                data = await reader.read(8192)
                if not data:
                    return
                await ws.send_bytes(data)

        async def up() -> None:         # navigateur → terminal
            while True:
                msg = json.loads(await ws.receive_text())
                last[0] = time.time()
                if isinstance(msg.get("i"), str) and len(msg["i"]) <= MAX_INPUT:
                    writer.write(msg["i"].encode())
                    await writer.drain()
                elif isinstance(msg.get("r"), list) and len(msg["r"]) == 2:
                    await asyncio.to_thread(_resize, exec_id, int(msg["r"][0]), int(msg["r"][1]))

        async def watch() -> None:      # inactivité ou jeton périmé : on ferme
            while True:
                await asyncio.sleep(10)
                if time.time() - last[0] > IDLE_LIMIT or time.time() >= exp:
                    await ws.close(CLOSE_AUTH)
                    return

        tasks = [asyncio.create_task(down()), asyncio.create_task(up()), asyncio.create_task(watch())]
        done, rest = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in rest:
            t.cancel()
        for t in done:
            t.exception()
    except (WebSocketDisconnect, RuntimeError, OSError, ConnectionError, ValueError, api.DockerError, HTTPException):
        pass
    finally:
        if writer:
            writer.close()
        with _lock:
            _sessions[uid] = max(0, _sessions.get(uid, 1) - 1)
        try:
            await asyncio.to_thread(ctx.audit, "docker.exec.end", f"terminal fermé dans {c['name']}")
        except Exception:  # noqa: BLE001
            pass
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
