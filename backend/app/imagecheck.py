"""Suivi des images d'un projet : pour chaque image de son compose, est-elle à jour, a-t-on téléchargé mieux sans redéployer, existe-t-il une nouvelle version ?

compute() est partagé par la page (à la demande) et par la vérification quotidienne (qui prévient par le webhook du nœud). `run(op, args)` exécute une opération
de la machine (le hub l'appelle pour son nœud, via le canal pour un worker)."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import db, registry
from .alerts import send_webhook
from .audit import audit
from .config import settings
from .crypto import reveal
from .events import publish


def _cached(node_id: str, keys: list[str]) -> dict:
    return {r["ref"]: r for r in db.rows(f"SELECT * FROM image_checks WHERE node_id=? AND ref IN ({','.join('?' for _ in keys)})", (node_id, *keys))} if keys else {}


def compute(run, node_id: str, path: str, fetch: bool = False) -> list[dict]:
    """Une ligne par service qui a une image : {service, image, status, local, remote, error, checkedAt, needsRedeploy}."""
    proj = run("p_project", {"dir": path})
    images = run("d_images", {})["images"]
    containers = run("d_containers", {"all": True})["containers"]
    rows, wanted = [], {}
    for s in proj["services"]:
        if not s["image"]:
            continue
        ref = registry.parse_ref(s["image"])
        rows.append({"service": s["name"], "image": s["image"], "ref": ref})
        if ref and not ref["digest"]:
            wanted[ref["key"]] = ref
    if fetch and wanted:
        with ThreadPoolExecutor(max_workers=6) as pool:
            res = dict(zip(wanted, pool.map(lambda r: registry.remote_digest(r), wanted.values())))
        for key, (dig, err) in res.items():
            old = db.one("SELECT notified FROM image_checks WHERE node_id=? AND ref=?", (node_id, key))
            db.run("INSERT INTO image_checks(node_id,ref,remote_digest,error,checked_at,notified) VALUES(?,?,?,?,?,?) ON CONFLICT(node_id,ref) DO UPDATE SET remote_digest=excluded.remote_digest, "
                   "error=excluded.error, checked_at=excluded.checked_at", (node_id, key, dig or "", err, time.time(), old["notified"] if old else ""))
    cache = _cached(node_id, list(wanted))
    out = []
    for r in rows:
        ref = r["ref"]
        if not ref:
            out.append({"service": r["service"], "image": r["image"], "status": "unresolved", "local": None, "remote": None, "error": "Nom d'image variable (${…}) : non vérifié", "checkedAt": None, "needsRedeploy": False})
            continue
        local = registry.local_info(ref, images)
        c = cache.get(ref["key"])
        status = registry.status_of(ref, local, (c["remote_digest"] or None) if c else None, c["error"] if c else "")
        mine = [x for x in containers if x["service"] == r["service"] and x["workingDir"] == proj["path"]] or [x for x in containers if x["service"] == r["service"] and x["project"] == proj["composeProject"]]
        out.append({"service": r["service"], "image": r["image"], "status": status, "local": local["id"] if local else None, "remote": ((c["remote_digest"] or "")[7:19] or None) if c else None,
                    "error": (c["error"] if c else "") if status in ("unknown", "absent") else "", "checkedAt": c["checked_at"] if c else None,
                    "needsRedeploy": bool(local and any(x["imageId"] != local["id"] for x in mine)), "key": ref["key"]})
    return out


def _notify_updates(node: dict, project: str, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        if r["status"] != "update":
            continue
        c = db.one("SELECT remote_digest, notified FROM image_checks WHERE node_id=? AND ref=?", (node["id"], r["key"]))
        if not c or c["notified"] == c["remote_digest"]:
            continue
        msg = f"🔔 Mise à jour disponible · {node['name']} · {project} · {r['image']}"
        audit("système", "project.image.update", f"{project}/{r['service']} : {r['image']}", node=node["id"])
        url = reveal(node.get("webhook_url"))
        if url:
            threading.Thread(target=send_webhook, args=(url, {"text": msg, "content": msg, "node": node["name"], "project": project, "image": r["image"], "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}), daemon=True).start()
        db.run("UPDATE image_checks SET notified=? WHERE node_id=? AND ref=?", (c["remote_digest"], node["id"], r["key"]))
        n += 1
    if n:
        publish("projects", node["id"])
    return n


def check_node(node: dict, run) -> int:
    """Vérifie les projets suivis d'un nœud ; renvoie le nombre de notifications envoyées."""
    sent = 0
    for p in db.rows("SELECT * FROM projects WHERE node_id=? AND state='managed' AND track_images=1", (node["id"],)):
        try:
            sent += _notify_updates(node, p["name"], compute(run, node["id"], p["path"], fetch=True))
        except Exception:  # noqa: BLE001 - un projet illisible ne doit pas arrêter les autres
            continue
    return sent


_started = False


def start() -> None:
    global _started
    if _started or settings.image_check_hours <= 0:
        return
    _started = True

    def loop():
        time.sleep(180)
        while True:
            try:
                from . import host_ops
                from .nodes import local_modules
                if "docker" in local_modules():
                    for row in db.rows("SELECT * FROM nodes WHERE kind='local' AND status='active' AND deleted_at IS NULL"):
                        check_node(dict(row), host_ops.handle)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(settings.image_check_hours * 3600)
    threading.Thread(target=loop, name="image-watch", daemon=True).start()
