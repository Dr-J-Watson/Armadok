"""Suivi des images d'un projet : pour chaque image de son compose, est-elle à jour, a-t-on téléchargé mieux sans redéployer, existe-t-il une nouvelle version ?

compute() est partagé par la page (à la demande) et par la vérification quotidienne (qui prévient par le webhook du nœud). `run(op, args)` exécute une opération
de la machine (le hub l'appelle pour son nœud, via le canal pour un worker)."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import db, project_ops, registry, schedule
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
        if "$" in s["image"] and not s.get("imageRaw"):   # un worker plus ancien n'a pas lu le .env : au moins les valeurs par défaut ${VAR:-défaut}
            res = project_ops.interpolate(s["image"], {})
            if res != s["image"]:
                s = {**s, "imageRaw": s["image"], "image": res}
        ref = registry.parse_ref(s["image"])
        rows.append({"service": s["name"], "image": s["image"], "imageRaw": s.get("imageRaw"), "ref": ref})
        if ref and not ref["digest"]:
            wanted[ref["key"]] = ref
    if fetch and wanted:
        def look(r):
            dig, err = registry.remote_digest(r)
            tags = registry.list_tags(r)[0] if registry._ver(r["tag"]) else []   # un tag qui n'est pas un numéro (latest, dev) n'a pas de version suivante
            return dig, err, registry.newer_version(r["tag"], tags) or ""
        with ThreadPoolExecutor(max_workers=6) as pool:
            res = dict(zip(wanted, pool.map(look, wanted.values())))
        for key, (dig, err, newer) in res.items():
            old = db.one("SELECT notified, notified_tag FROM image_checks WHERE node_id=? AND ref=?", (node_id, key))
            db.run("INSERT INTO image_checks(node_id,ref,remote_digest,error,checked_at,notified,latest_tag,notified_tag) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(node_id,ref) DO UPDATE SET "
                   "remote_digest=excluded.remote_digest, error=excluded.error, checked_at=excluded.checked_at, latest_tag=excluded.latest_tag",
                   (node_id, key, dig or "", err, time.time(), old["notified"] if old else "", newer, old["notified_tag"] if old else ""))
    cache = _cached(node_id, list(wanted))
    out = []
    for r in rows:
        ref = r["ref"]
        if not ref:
            out.append({"service": r["service"], "image": r["image"], "status": "unresolved", "local": None, "remote": None, "error": "Nom d'image variable (${…}) : non vérifié", "checkedAt": None, "needsRedeploy": False, "packageUrl": None, "newerTag": None, "newerImage": None})
            continue
        local = registry.local_info(ref, images)
        c = cache.get(ref["key"])
        status = registry.status_of(ref, local, (c["remote_digest"] or None) if c else None, c["error"] if c else "")
        mine = [x for x in containers if x["service"] == r["service"] and x["workingDir"] == proj["path"]] or [x for x in containers if x["service"] == r["service"] and x["project"] == proj["composeProject"]]
        out.append({"service": r["service"], "image": r["image"], "status": status, "local": local["id"] if local else None, "remote": ((c["remote_digest"] or "")[7:19] or None) if c else None,
                    "error": (c["error"] if c else "") if status in ("unknown", "absent") else "", "checkedAt": c["checked_at"] if c else None,
                    "needsRedeploy": bool(local and any(x["imageId"] != local["id"] for x in mine)), "key": ref["key"],
                    "packageUrl": registry.package_url(ref), "imageRaw": r.get("imageRaw"), "newerTag": (c["latest_tag"] or None) if c else None,
                    "newerImage": (f"{r['image'].rsplit(':', 1)[0] if ':' in r['image'].rsplit('/', 1)[-1] else r['image']}:{c['latest_tag']}" if c and c["latest_tag"] else None)})
    return out


def _notify_updates(node: dict, project: str, rows: list[dict]) -> int:
    """Une notification par nouveauté : l'empreinte a changé (même tag) ou une version plus haute est apparue. Chacune n'est annoncée qu'une fois."""
    n = 0
    for r in rows:
        c = db.one("SELECT remote_digest, notified, latest_tag, notified_tag FROM image_checks WHERE node_id=? AND ref=?", (node["id"], r.get("key") or ""))
        if not c:
            continue
        msgs = []
        if r["status"] == "update" and c["notified"] != c["remote_digest"]:
            msgs.append((f"🔔 Mise à jour disponible · {node['name']} · {project} · {r['image']}", "digest"))
        if c["latest_tag"] and c["notified_tag"] != c["latest_tag"]:
            msgs.append((f"🔔 Nouvelle version {c['latest_tag']} · {node['name']} · {project} · {r['image']}", "version"))
        for msg, what in msgs:
            audit("système", "project.image.update", f"{project}/{r['service']} : {r['image']}" + (f" → {c['latest_tag']}" if what == "version" else ""), node=node["id"])
            url = reveal(node.get("webhook_url"))
            if url:
                threading.Thread(target=send_webhook, args=(url, {"text": msg, "content": msg, "node": node["name"], "project": project, "image": r["image"], "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}), daemon=True).start()
            db.run("UPDATE image_checks SET notified=?, notified_tag=? WHERE node_id=? AND ref=?",
                   (c["remote_digest"] if what == "digest" else c["notified"], c["latest_tag"] if what == "version" else c["notified_tag"], node["id"], r["key"]))
            c = db.one("SELECT remote_digest, notified, latest_tag, notified_tag FROM image_checks WHERE node_id=? AND ref=?", (node["id"], r["key"]))
            n += 1
    if n:
        publish("projects", node["id"])
    return n


def project_schedule(p) -> list[dict]:
    """Les règles de planning d'un projet : les siennes, sinon un intervalle par défaut (IMAGE_CHECK_HOURS)."""
    try:
        return schedule.validate(json.loads(p["image_schedule"])) if p["image_schedule"] else schedule.default_rules(settings.image_check_hours)
    except (ValueError, TypeError, KeyError):
        return schedule.default_rules(settings.image_check_hours)


def check_project(node: dict, run, p) -> int:
    try:
        return _notify_updates(node, p["name"], compute(run, node["id"], p["path"], fetch=True))
    except Exception:  # noqa: BLE001 - un projet illisible ne doit pas arrêter les autres
        return 0


def check_node(node: dict, run, only_due: bool = False, now: float | None = None) -> int:
    """Vérifie les projets suivis d'un nœud (seulement ceux dont l'échéance est passée si only_due) ; renvoie le nombre de notifications envoyées."""
    now = now or time.time()
    sent = 0
    for p in db.rows("SELECT * FROM projects WHERE node_id=? AND state='managed' AND track_images=1", (node["id"],)):
        if only_due:
            if p["image_last_run"] is None:   # suivi activé avant les plannings : on part de maintenant
                db.run("UPDATE projects SET image_last_run=? WHERE id=?", (now, p["id"]))
                continue
            nxt = schedule.next_run(project_schedule(p), p["image_last_run"])
            if nxt is None or nxt > now or now - p["image_last_run"] < schedule.MIN_GAP:
                continue
            db.run("UPDATE projects SET image_last_run=? WHERE id=?", (now, p["id"]))
        sent += check_project(node, run, p)
    return sent


_started = False


def start() -> None:
    global _started
    if _started or settings.image_check_hours <= 0:
        return
    _started = True

    def loop():
        time.sleep(20)
        while True:
            try:
                from . import host_ops
                from .nodes import local_modules
                if "docker" in local_modules():
                    for row in db.rows("SELECT * FROM nodes WHERE kind='local' AND status='active' AND deleted_at IS NULL"):
                        check_node(dict(row), host_ops.handle, only_due=True)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(5)   # les plannings ont la précision de la seconde
    threading.Thread(target=loop, name="image-watch", daemon=True).start()
