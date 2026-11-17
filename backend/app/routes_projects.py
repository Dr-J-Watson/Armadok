"""Projets Compose d'un nœud : découverte, import, vue d'ensemble (services, conteneurs, sites Traefik, visiteurs).

Un projet est un DOSSIER sur la machine (le disque reste la source de vérité) : l'interface n'en garde que la référence (chemin, état, étiquettes).
« Découvert » = vu dans les racines autorisées, jamais modifié ; « géré » = importé ; « ignoré » = écarté de la liste. Désimporter ne touche ni au disque ni aux conteneurs."""
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from . import compose_convert, crypto, db, project_templates, docker_link, gitssh, imagecheck, labels_yaml, linkage, project_ops, worker_link
from .config import settings
from .nodes import NodeCtx, node_ctx
from .routes_docker import run
from .traefik_store import StoreError

router = APIRouter(prefix="/api/projects", tags=["projects"])


def proj_cap(cap: str):
    """Dépendance : le nœud gère Docker ET la personne a ce droit fin sur les projets."""
    def dep(ctx: NodeCtx = Depends(node_ctx)) -> NodeCtx:
        if "docker" not in (ctx.node.get("modules") or []):
            raise HTTPException(409, "Ce nœud ne gère pas Docker")
        ctx.need(cap)
        return ctx
    return dep


def _row(ctx: NodeCtx, pid: int):
    r = db.one("SELECT * FROM projects WHERE id=? AND node_id=?", (pid, ctx.id))
    if not r:
        raise HTTPException(404, "Projet introuvable")
    return r


def assign(entries: list[dict], containers: list[dict]) -> dict[str, list[dict]]:
    """Conteneurs de chaque projet. D'abord par le dossier de travail déclaré par Compose (certain) ; sinon par le nom du projet, seulement si UN SEUL
    dossier connu porte ce nom (sans quoi on ne devine pas)."""
    out: dict[str, list[dict]] = {}
    taken = set()
    for c in containers:
        wd = os.path.normpath(c["workingDir"]) if c["workingDir"] else ""
        if wd and any(wd == e["path"] for e in entries):
            out.setdefault(wd, []).append(c)
            taken.add(c["id"])
    names: dict[str, list[dict]] = {}
    for e in entries:
        names.setdefault(e["composeProject"], []).append(e)
    for c in containers:
        if c["id"] in taken or not c["project"] or c["workingDir"]:
            continue
        cand = names.get(c["project"], [])
        if len(cand) == 1:
            out.setdefault(cand[0]["path"], []).append(c)
    return out


def _slim(c: dict) -> dict:
    return {"id": c["id"], "name": c["name"], "state": c["state"], "health": c["health"], "image": c["image"], "status": c["status"],
            "ports": [p for p in c["ports"] if p["public"]], "protected": c.get("self") and "self" or ""}


def summarize(ctx: NodeCtx, e: dict, cs: list[dict], links: dict[str, list[dict]], row=None, access: bool = True) -> dict:
    ctxs = getattr(links, "ctxs", {})
    services = []
    seen = set()
    for s in e["services"]:
        mine = [c for c in cs if c["service"] == s["name"]]
        seen.update(c["id"] for c in mine)
        run_n = sum(1 for c in mine if c["state"] == "running")
        state = "absent" if not mine else "running" if run_n == len(mine) else "partial" if run_n else "stopped"
        lk = [l for c in mine for l in links.get(c["name"], [])]
        done = bool(mine) and not run_n and all(str(c.get("status") or "").startswith("Exited (0)") for c in mine)   # service « init » / ponctuel terminé normalement : arrêté exprès
        services.append({**s, "state": state, "done": done, "containers": [_slim(c) for c in mine], "links": lk, "hosts": linkage.hosts_of(lk)})
    extra = [c for c in cs if c["id"] not in seen]
    expected = [s for s in services if not s["profiles"] and not s["done"]]
    running = sum(1 for s in expected if s["state"] == "running")
    status = "absent" if not cs else "running" if expected and running == len(expected) else "stopped" if not any(c["state"] == "running" for c in cs) else "partial"
    alerts = []
    if e["error"]:
        alerts.append({"level": "err", "text": e["error"]})
    for s in services:
        if any(c["health"] == "unhealthy" for c in s["containers"]):
            alerts.append({"level": "err", "text": f"Le service « {s['name']} » est défaillant"})
        elif status == "partial" and s["state"] in ("stopped", "absent") and not s["profiles"] and not s["done"]:
            alerts.append({"level": "warn", "text": f"Le service « {s['name']} » ne tourne pas"})
    hosts = sorted({h for s in services for h in s["hosts"]} | {h for c in extra for l in links.get(c["name"], []) for h in l["hosts"]})
    return {"id": row["id"] if row else None, "path": e["path"], "name": e["name"], "composeProject": e["composeProject"], "file": e["file"], "error": e["error"],
            "state": row["state"] if row else "discovered", "tags": [t for t in (row["tags"] if row else "").split(",") if t], "services": services, "extra": [_slim(c) for c in extra],
            "status": status, "running": running, "total": len(expected), "alerts": alerts, "hosts": hosts, "containerIds": [c["id"] for c in cs],
            "access": docker_link.access_for(ctx, [l for s in services for l in s["links"]] + [l for c in extra for l in links.get(c["name"], [])], ctxs, totals=True) if access and hosts else None,
            "hasEnv": e["hasEnv"], "hasMake": e.get("hasMake", False), "hasGit": e["hasGit"], "build": e["build"], "mtime": e["mtime"], "missing": False}


@router.get("")
def projects(ctx: NodeCtx = Depends(proj_cap("projects.view"))):
    info = run(ctx, "p_info")
    scan = run(ctx, "p_discover")
    containers = run(ctx, "d_containers", {"all": True})["containers"]
    links = docker_link.link_containers(ctx, containers)
    rows = {r["path"]: r for r in db.rows("SELECT * FROM projects WHERE node_id=?", (ctx.id,))}
    entries = scan["projects"]
    mine = assign(entries, containers)
    out = {"managed": [], "discovered": [], "ignored": []}
    found = set()
    for e in entries:
        found.add(e["path"])
        row = rows.get(e["path"])
        s = summarize(ctx, e, mine.get(e["path"], []), links, row, access=bool(row and row["state"] == "managed"))
        out[{"managed": "managed", "ignored": "ignored"}.get(row["state"] if row else "", "discovered")].append(s)
    for path, row in rows.items():   # géré mais plus trouvé : dossier supprimé ou racine retirée
        if path not in found and row["state"] == "managed":
            out["managed"].append({"id": row["id"], "path": path, "name": row["name"], "state": "managed", "missing": True, "services": [], "extra": [], "hosts": [], "alerts": [{"level": "err", "text": "Le dossier de ce projet est introuvable"}],
                                   "status": "absent", "running": 0, "total": 0, "tags": [t for t in row["tags"].split(",") if t], "containerIds": [], "access": None})
    return {**out, "roots": info["roots"], "configuredRoots": info["configuredRoots"], "compose": info["compose"], "truncated": scan["truncated"],
            "canImport": ctx.can("projects.import"), "canDeploy": ctx.can("projects.deploy"), "canGit": ctx.can("projects.git") and ctx.can("projects.import"), "node": ctx.id}


class PathsBody(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=100)
    tags: str = Field(default="", max_length=200)


def _tags(raw: str) -> str:
    return ",".join(dict.fromkeys(t.strip()[:30] for t in raw.split(",") if t.strip()))[:200]


def _mark(ctx: NodeCtx, body: PathsBody, state: str) -> dict:
    done, skipped = [], []
    for raw in body.paths:
        e = run(ctx, "p_project", {"dir": raw})
        row = db.one("SELECT id, state FROM projects WHERE node_id=? AND path=?", (ctx.id, e["path"]))
        if row and row["state"] == state:
            skipped.append(e["name"])
            continue
        if row:
            db.run("UPDATE projects SET state=? WHERE id=?", (state, row["id"]))
        else:
            db.run("INSERT INTO projects(node_id,path,name,state,tags,created_by,created_at) VALUES(?,?,?,?,?,?,?)", (ctx.id, e["path"], e["name"], state, _tags(body.tags), ctx.p.username, db.now()))
        done.append(e["name"])
    if done:
        ctx.audit(f"project.{'import' if state == 'managed' else 'ignore'}", ", ".join(done))
    return {"done": done, "skipped": skipped}


class CreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=61)
    files: dict[str, str] = Field(min_length=1, max_length=30)
    tags: str = Field(default="", max_length=200)


@router.post("/create")
def create_project(body: CreateBody, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    """Crée un projet neuf (dossier + fichiers de départ, par exemple une pile Traefik) dans la racine des projets et l'ajoute à la liste."""
    ctx.need("projects.edit")
    if any(project_ops.is_env(r) for r in body.files):
        ctx.need("projects.env")
    made = run(ctx, "p_create", {"name": body.name, "files": body.files})
    ctx.audit("project.create", made["name"])
    r = _mark(ctx, PathsBody(paths=[made["path"]], tags=body.tags), "managed")
    row = db.one("SELECT id FROM projects WHERE node_id=? AND path=?", (ctx.id, made["path"]))
    return {**made, **r, "id": row["id"] if row else None}


@router.post("/import")
def import_(body: PathsBody, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    return _mark(ctx, body, "managed")


@router.post("/ignore")
def ignore(body: PathsBody, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    return _mark(ctx, body, "ignored")


@router.delete("/{pid}")
def unimport(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    """Retire le projet de la liste. Ni le disque ni les conteneurs ne sont touchés."""
    row = _row(ctx, pid)
    db.run("DELETE FROM projects WHERE id=?", (pid,))
    ctx.audit("project.unimport", row["name"])
    return {"ok": True}


def _choice(row) -> list[str]:
    """Fichiers compose choisis pour ce projet (liste vide = automatique)."""
    try:
        v = json.loads(row["compose_files"] or "[]")
    except (ValueError, KeyError, TypeError):
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def _last_files(path: str, cs: list[dict]) -> list[str]:
    """Fichiers compose du dernier lancement, relus sur les conteneurs du projet (label com.docker.compose.project.config_files), restreints à son dossier."""
    for c in sorted(cs, key=lambda c: c.get("state") != "running"):
        raw = c.get("configFiles") or ""
        names = []
        for part in raw.split(","):
            pp = part.strip()
            if pp.startswith(path.rstrip("/") + "/") and "/" not in pp[len(path.rstrip("/")) + 1:]:
                names.append(pp.rsplit("/", 1)[1])
        if names:
            return names
    return []


@router.get("/{pid}")
def detail(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.view"))):
    row = _row(ctx, pid)
    choice = _choice(row)
    e = run(ctx, "p_project", {"dir": row["path"], "files": choice})
    containers = run(ctx, "d_containers", {"all": True})["containers"]
    others = [x for x in run(ctx, "p_discover")["projects"] if x["path"] != e["path"]]
    cs = assign([e] + others, containers).get(e["path"], [])
    links = docker_link.link_containers(ctx, containers)
    s = summarize(ctx, e, cs, links, row, access=False)
    last = _last_files(e["path"], cs)
    s["compose"] = {"candidates": e.get("composeFiles", []), "default": e.get("defaultFiles", []), "choice": choice, "last": last,
                    "effective": choice or e.get("defaultFiles", []), "running": any(c["state"] == "running" for c in cs)}
    s["access"] = docker_link.access_for(ctx, [l for sv in s["services"] for l in sv["links"]], getattr(links, "ctxs", {}))
    s["perms"] = {"import": ctx.can("projects.import"), "deploy": ctx.can("projects.deploy"), "files": ctx.can("projects.files"), "edit": ctx.can("projects.edit"), "env": ctx.can("projects.env"),
                  "git": ctx.can("projects.git"), "delete": ctx.can("projects.delete"), "logs": ctx.can("docker.logs"), "control": ctx.can("docker.control"), "exec": ctx.can("docker.exec")}
    return s


# ------------------------------------------------------------------ fichiers du projet
def _dir(ctx: NodeCtx, pid: int) -> tuple[str, dict]:
    row = _row(ctx, pid)
    return row["path"], dict(row)


def _need_file(ctx: NodeCtx, rel: str, write: bool) -> None:
    """Le .env contient des secrets : il a son propre droit ; le reste suit « voir » / « modifier » les fichiers."""
    ctx.need("projects.env" if project_ops.is_env(rel) else "projects.edit" if write else "projects.files")


@router.get("/{pid}/tree")
def tree(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.files"))):
    d, _ = _dir(ctx, pid)
    r = run(ctx, "p_tree", {"dir": d})
    if not ctx.can("projects.env"):   # sans le droit, les fichiers .env ne sont même pas listés
        r["entries"] = [e for e in r["entries"] if not (e["type"] == "file" and e.get("env"))]
    return r


@router.get("/{pid}/file")
def read_file(pid: int, rel: str, ctx: NodeCtx = Depends(proj_cap("projects.view"))):
    _need_file(ctx, rel, False)
    d, _ = _dir(ctx, pid)
    return run(ctx, "p_read", {"dir": d, "rel": rel})


class FileBody(BaseModel):
    rel: str = Field(max_length=300)
    content: str
    baseHash: str = Field(default="", max_length=40)
    create: bool = False


@router.put("/{pid}/file")
def write_file(pid: int, body: FileBody, ctx: NodeCtx = Depends(proj_cap("projects.view"))):
    _need_file(ctx, body.rel, True)
    d, row = _dir(ctx, pid)
    r = run(ctx, "p_write", {"dir": d, "rel": body.rel, "content": body.content, "baseHash": body.baseHash, "create": body.create})
    ctx.audit("project.file.create" if body.create else "project.file.edit", f"{row['name']} : {body.rel}")   # jamais le contenu
    return r


@router.delete("/{pid}/file")
def delete_file(pid: int, rel: str, ctx: NodeCtx = Depends(proj_cap("projects.view"))):
    _need_file(ctx, rel, True)
    d, row = _dir(ctx, pid)
    r = run(ctx, "p_delete_file", {"dir": d, "rel": rel})
    ctx.audit("project.file.delete", f"{row['name']} : {rel}")
    return r


class MkdirBody(BaseModel):
    rel: str = Field(max_length=300)


@router.post("/{pid}/mkdir")
def make_dir(pid: int, body: MkdirBody, ctx: NodeCtx = Depends(proj_cap("projects.edit"))):
    d, row = _dir(ctx, pid)
    r = run(ctx, "p_mkdir", {"dir": d, "rel": body.rel})
    ctx.audit("project.dir.create", f"{row['name']} : {body.rel}")
    return r


class MoveBody(BaseModel):
    src: str = Field(max_length=300)
    dst: str = Field(max_length=300)


@router.post("/{pid}/move")
def move_path(pid: int, body: MoveBody, ctx: NodeCtx = Depends(proj_cap("projects.edit"))):
    """Renomme ou déplace un fichier ou un dossier du projet. Toucher à un nom « .env » demande le droit sur les secrets."""
    if project_ops.is_env(body.src) or project_ops.is_env(body.dst):
        ctx.need("projects.env")
    d, row = _dir(ctx, pid)
    r = run(ctx, "p_move", {"dir": d, "src": body.src, "dst": body.dst})
    ctx.audit("project.file.move", f"{row['name']} : {body.src} → {body.dst}")
    return r


@router.delete("/{pid}/dir")
def delete_dir(pid: int, rel: str, ctx: NodeCtx = Depends(proj_cap("projects.edit"))):
    d, row = _dir(ctx, pid)
    r = run(ctx, "p_delete_dir", {"dir": d, "rel": rel, "canEnv": ctx.can("projects.env")})
    ctx.audit("project.dir.delete", f"{row['name']} : {rel} ({r.get('files', 0)} fichier(s))")
    return r


@router.get("/{pid}/history")
def history(pid: int, rel: str, ctx: NodeCtx = Depends(proj_cap("projects.view"))):
    _need_file(ctx, rel, False)
    d, _ = _dir(ctx, pid)
    return run(ctx, "p_history", {"dir": d, "rel": rel})


@router.get("/{pid}/history/{vid}")
def history_version(pid: int, vid: str, rel: str, ctx: NodeCtx = Depends(proj_cap("projects.view"))):
    _need_file(ctx, rel, False)
    d, _ = _dir(ctx, pid)
    return run(ctx, "p_history_content", {"dir": d, "rel": rel, "vid": vid})


class ValidateBody(BaseModel):
    content: str


@router.post("/{pid}/validate")
def validate(pid: int, body: ValidateBody, ctx: NodeCtx = Depends(proj_cap("projects.files"))):
    d, _ = _dir(ctx, pid)
    return run(ctx, "p_validate", {"dir": d, "content": body.content})


# ------------------------------------------------------------------ déploiement
LAUNCHED_ACTIONS = ("down", "stop", "start", "restart", "ps")


class ComposeBody(BaseModel):
    action: str = Field(max_length=20)
    services: list[str] = Field(default_factory=list, max_length=50)
    build: bool = False
    pull: bool = False
    noCache: bool = False
    volumes: bool = False
    files: list[str] = Field(default_factory=list, max_length=10)   # fichiers compose de cette commande ; vide = choix du projet, sinon automatique


@router.post("/{pid}/compose")
def compose(pid: int, body: ComposeBody, ctx: NodeCtx = Depends(proj_cap("projects.deploy"))):
    if body.action == "config":
        ctx.need("projects.env")      # « compose config » affiche les valeurs des variables
    if body.volumes:
        ctx.need("projects.delete")   # « down --volumes » détruit les données
    d, row = _dir(ctx, pid)
    files = body.files or _choice(row)
    if not files and body.action in LAUNCHED_ACTIONS:   # arrêter / redémarrer / supprimer : avec ce qui a servi au dernier lancement, pas forcément le défaut
        cs = [c for c in run(ctx, "d_containers", {"all": True})["containers"] if c.get("workingDir") and os.path.normpath(c["workingDir"]) == os.path.normpath(d)]
        files = _last_files(d, cs)
    r = run(ctx, "p_compose", {"dir": d, "action": body.action, "services": body.services, "build": body.build, "pull": body.pull, "noCache": body.noCache, "volumes": body.volumes, "files": files})
    ctx.audit(f"project.compose.{body.action}", f"{row['name']}{' : ' + ', '.join(body.services) if body.services else ''}{' (+ volumes)' if body.volumes else ''}{' [' + ' + '.join(files) + ']' if len(files) > 1 else ''}")
    return r


class ComposeFilesBody(BaseModel):
    files: list[str] = Field(default_factory=list, max_length=10)   # vide = automatique


@router.put("/{pid}/compose-files")
def set_compose_files(pid: int, body: ComposeFilesBody, ctx: NodeCtx = Depends(proj_cap("projects.deploy"))):
    """Mémorise les fichiers compose à utiliser pour ce projet (par exemple le principal + un override). Vide : automatique."""
    d, row = _dir(ctx, pid)
    if body.files:
        run(ctx, "p_project", {"dir": d, "files": body.files})   # refuse un nom inconnu
    db.run("UPDATE projects SET compose_files=? WHERE id=?", (json.dumps(body.files) if body.files else "", pid))
    ctx.audit("project.compose.files", f"{row['name']} : {' + '.join(body.files) or 'automatique'}")
    return {"ok": True, "files": body.files}


@router.get("/{pid}/make")
def make_targets(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.view"))):
    d, _ = _dir(ctx, pid)
    r = run(ctx, "p_make_targets", {"dir": d})
    r["canRun"] = ctx.can("projects.deploy") and ctx.can("docker.exec") and (ctx.node["kind"] != "remote" or bool(worker_link.feature(ctx.id, "exec")))
    return r


class MakeBody(BaseModel):
    target: str = Field(min_length=1, max_length=100)


@router.post("/{pid}/make")
def make_run(pid: int, body: MakeBody, ctx: NodeCtx = Depends(proj_cap("projects.deploy"))):
    """Lance une cible du Makefile du projet. Équivaut à exécuter des commandes dans le dossier : réservé au droit du terminal."""
    ctx.need("docker.exec")
    if ctx.node["kind"] == "remote" and not worker_link.feature(ctx.id, "exec"):
        raise HTTPException(409, "Ce worker n'autorise pas l'exécution de commandes (WORKER_ALLOW_EXEC=true)")
    d, row = _dir(ctx, pid)
    r = run(ctx, "p_exec_make_run", {"dir": d, "target": body.target})
    ctx.audit("project.make", f"{row['name']} : make {body.target}")
    return r


@router.get("/{pid}/jobs/{jid}")
def job(pid: int, jid: str, since: int = 0, ctx: NodeCtx = Depends(proj_cap("projects.view"))):
    _dir(ctx, pid)
    if not (ctx.can("projects.deploy") or ctx.can("projects.files")):
        raise HTTPException(403, "Droit manquant pour lire la sortie des commandes")
    return run(ctx, "p_job", {"id": jid, "since": since})


class DestroyBody(BaseModel):
    confirm: str = Field(max_length=200)


@router.post("/{pid}/destroy")
def destroy(pid: int, body: DestroyBody, ctx: NodeCtx = Depends(proj_cap("projects.delete"))):
    """Supprime le dossier du projet sur le disque. Refusé tant qu'un de ses conteneurs tourne (faire « Down » d'abord)."""
    d, row = _dir(ctx, pid)
    e = run(ctx, "p_project", {"dir": d})
    containers = run(ctx, "d_containers", {"all": True})["containers"]
    others = [x for x in run(ctx, "p_discover")["projects"] if x["path"] != e["path"]]
    if any(c["state"] == "running" for c in assign([e] + others, containers).get(e["path"], [])):
        raise HTTPException(409, "Des conteneurs de ce projet tournent encore : faites d'abord « Down »")
    run(ctx, "p_destroy", {"dir": d, "confirm": body.confirm})
    db.run("DELETE FROM projects WHERE id=?", (pid,))
    ctx.audit("project.destroy", f"{row['name']} ({d})")
    return {"ok": True}


# ------------------------------------------------------------------ labels Traefik ⇄ fichier
def _yaml(obj) -> str:
    y = YAML(typ="safe")
    y.default_flow_style = False
    y.width = 4096
    buf = io.StringIO()
    y.dump(obj, buf)
    return buf.getvalue()


def _items_yaml(items: list[dict]) -> str:
    http: dict = {}
    for i in items:
        http.setdefault(i["kind"], {})[i["name"]] = i["spec"]
    return _yaml({"http": http}) if http else ""


def _traefik_items(ctx: NodeCtx) -> list[dict]:
    if "traefik" not in (ctx.node.get("modules") or []):
        return []
    try:
        return ctx.store.items()[0]
    except Exception:  # noqa: BLE001
        return []


@router.get("/{pid}/labels")
def labels(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.files"))):
    """Les labels Traefik de chaque service du compose : lus comme des règles, convertis en fichier, et contrôlés contre les fichiers de Traefik."""
    d, row = _dir(ctx, pid)
    e = run(ctx, "p_project", {"dir": d})
    items = _traefik_items(ctx)
    declared, out = {}, []
    for s in e["services"]:
        if not s["traefik"]:
            continue
        declared[s["name"]] = s["traefik"]
        conv = labels_yaml.labels_to_items(s["traefik"], target=s["containerName"] or s["name"])
        out.append({"service": s["name"], "labels": s["traefik"], "items": conv["items"], "yaml": _items_yaml(conv["items"]), "warnings": conv["warnings"],
                    "disabled": str(s["traefik"].get("traefik.enable", "")).lower() == "false"})
    checks = labels_yaml.check_labels(declared, items)
    if declared and "traefik" in (ctx.node.get("modules") or []):
        prov = docker_link.traefik_provider(ctx, run(ctx, "d_containers", {"all": True})["containers"])
        if prov["docker"] is False:
            checks.insert(0, {"level": "err", "service": "", "text": "Traefik ne lit pas les labels Docker (le fournisseur Docker n'est pas activé : --providers.docker=true) : ces labels ne servent à rien. Utilisez « Créer dans Traefik », ou activez le fournisseur Docker"})
        elif prov["docker"] and prov["exposedByDefault"] is False:
            for svc, labels in declared.items():
                if str(labels.get("traefik.enable", "")).lower() != "true":
                    checks.append({"level": "warn", "service": svc, "text": "Traefik n'expose pas les conteneurs par défaut : sans « traefik.enable=true », ce service est ignoré"})
    tfiles = []
    if "traefik" in (ctx.node.get("modules") or []):
        try:
            tfiles = sorted(f["path"] for f in ctx.store.list_files() if not f["disabled"])
        except Exception:  # noqa: BLE001
            tfiles = []
    return {"services": out, "checks": checks, "traefik": "traefik" in (ctx.node.get("modules") or []), "traefikFiles": tfiles,
            "canApply": ctx.can("sites.create") and ctx.can("traefik.all"), "canEditCompose": ctx.can("projects.edit")}


class ApplyBody(BaseModel):
    service: str = Field(max_length=100)
    file: str = Field(default="", max_length=200)      # fichier de Traefik où écrire ; vide = un fichier « zz-<projet>-<service>.yml »
    removeLabels: bool = False                          # retire ensuite les labels traefik.* du compose (sinon la règle existerait deux fois)


@router.post("/{pid}/labels/apply")
def labels_apply(pid: int, body: ApplyBody, ctx: NodeCtx = Depends(proj_cap("projects.files"))):
    """Écrit dans Traefik (fichier « zz-… ») les éléments équivalents aux labels d'un service. Refusé si un élément du même nom existe déjà."""
    ctx.need("sites.create")
    ctx.need("traefik.all")
    d, row = _dir(ctx, pid)
    e = run(ctx, "p_project", {"dir": d})
    svc = next((s for s in e["services"] if s["name"] == body.service and s["traefik"]), None)
    if not svc:
        raise HTTPException(404, "Ce service n'a pas de labels Traefik")
    conv = labels_yaml.labels_to_items(svc["traefik"], target=svc["containerName"] or svc["name"])
    if any(w.startswith(("Routeur", "Service")) and "à compléter" in w for w in conv["warnings"]):
        raise HTTPException(422, "Conversion incomplète : " + " ".join(w for w in conv["warnings"] if "à compléter" in w))
    existing = {(i["kind"], i["name"]) for i in _traefik_items(ctx) if i["proto"] == "http"}
    clash = [f"{i['kind']}/{i['name']}" for i in conv["items"] if (i["kind"], i["name"]) in existing]
    if clash:
        raise HTTPException(409, "Existe déjà dans Traefik : " + ", ".join(clash))
    fname = body.file.strip() or re.sub(r"[^a-zA-Z0-9_.-]", "-", f"zz-{row['name']}-{svc['name']}.yml")
    if body.file.strip() and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./ -]{0,150}\.ya?ml", fname):
        raise HTTPException(422, "Nom de fichier invalide (un chemin relatif se terminant par .yml ou .yaml)")
    try:
        for i in conv["items"]:
            ctx.store.upsert("http", i["kind"], i["name"], i["spec"], fname)
    except StoreError as ex:
        raise HTTPException(ex.status, str(ex))
    removed: list[str] = []
    if body.removeLabels:
        ctx.need("projects.edit")
        removed = _edit_compose_service(ctx, d, e, svc["name"], lambda t: labels_yaml.remove_traefik_labels(t, svc["name"]))
    ctx.audit("project.labels.apply", f"{row['name']}/{svc['name']} → {fname}{' (labels retirés du compose)' if removed else ''}")
    return {"file": fname, "items": [f"{i['kind']}/{i['name']}" for i in conv["items"]], "removedLabels": removed}


class FromSiteBody(BaseModel):
    router: str = Field(max_length=200)


@router.post("/labels/from-site")
def labels_from_site(body: FromSiteBody, ctx: NodeCtx = Depends(proj_cap("projects.files"))):
    """Un site de Traefik (routeur + son service + ses middlewares) → les labels à coller dans un compose."""
    ctx.need("sites.view")
    from .traefik_scope import scope_items
    items = scope_items(ctx.p, _traefik_items(ctx))
    http = [i for i in items if i["proto"] == "http"]
    r = next((i for i in http if i["kind"] == "routers" and i["name"] == body.router), None)
    if not r:
        raise HTTPException(404, "Site introuvable")
    pick = [r]
    sname = str((r["spec"] or {}).get("service") or "").split("@")[0]
    pick += [i for i in http if i["kind"] == "services" and i["name"] == sname]
    pick += [i for i in http if i["kind"] == "middlewares" and i["name"] in {str(m).split("@")[0] for m in (r["spec"] or {}).get("middlewares") or []}]
    conv = labels_yaml.items_to_labels(pick, compose=True)
    lines = [f"{k}={v}" for k, v in conv["labels"].items()]
    return {"labels": conv["labels"], "text": _yaml({"labels": lines}), "warnings": conv["warnings"], "router": body.router}


def _compose_services(ctx: NodeCtx, d: str, e: dict) -> list[dict]:
    """Les services de TOUS les fichiers compose du dossier : [{file, service, containerName, image, ports, hasTraefik}]."""
    out = []
    for f in e.get("composeFiles") or []:
        try:
            txt = run(ctx, "p_read", {"dir": d, "rel": f})["content"]
        except HTTPException:
            continue
        for sv in project_ops.parse_compose(txt)["services"]:
            out.append({"file": f, "service": sv["name"], "containerName": sv["containerName"], "image": sv["image"], "ports": sv["ports"], "hasTraefik": bool(sv["traefik"])})
    return out


def _edit_compose_service(ctx: NodeCtx, d: str, e: dict, service: str, edit) -> list[str]:
    """Applique `edit(texte) -> (nouveau texte, clés)` au fichier compose qui définit ce service (l'historique du fichier est gardé par p_write)."""
    f = next((x["file"] for x in _compose_services(ctx, d, e) if x["service"] == service and x["hasTraefik"]), None) or \
        next((x["file"] for x in _compose_services(ctx, d, e) if x["service"] == service), None)
    if not f:
        raise HTTPException(404, "Service introuvable dans les fichiers compose")
    cur = run(ctx, "p_read", {"dir": d, "rel": f})
    try:
        new, keys = edit(cur["content"])
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    if new != cur["content"]:
        run(ctx, "p_write", {"dir": d, "rel": f, "content": new, "baseHash": cur["hash"]})
    return keys


def _pick_site(ctx: NodeCtx, router: str) -> tuple[list[dict], dict]:
    ctx.need("sites.view")
    from .traefik_scope import scope_items
    items = scope_items(ctx.p, _traefik_items(ctx))
    http = [i for i in items if i["proto"] == "http"]
    r = next((i for i in http if i["kind"] == "routers" and i["name"] == router), None)
    if not r:
        raise HTTPException(404, "Site introuvable")
    pick = [r]
    sname = str((r["spec"] or {}).get("service") or "").split("@")[0]
    svc = next((i for i in http if i["kind"] == "services" and i["name"] == sname), None)
    if svc:
        pick.append(svc)
    pick += [i for i in http if i["kind"] == "middlewares" and i["name"] in {str(m).split("@")[0] for m in (r["spec"] or {}).get("middlewares") or []}]
    return pick, r


class SitePlanBody(BaseModel):
    router: str = Field(max_length=200)


@router.post("/{pid}/labels/from-site/plan")
def labels_from_site_plan(pid: int, body: SitePlanBody, ctx: NodeCtx = Depends(proj_cap("projects.files"))):
    """Un site de Traefik (fichier) → des labels dans CE projet : quels labels, dans quel fichier compose et quel service (suggestion argumentée), et ce qu'il faut savoir avant."""
    d, row = _dir(ctx, pid)
    pick, r = _pick_site(ctx, body.router)
    conv = labels_yaml.items_to_labels(pick, compose=True)
    labels = {"traefik.enable": "true", **conv["labels"]}
    urls = [str(sv.get("url")) for i in pick if i["kind"] == "services" for sv in ((i["spec"] or {}).get("loadBalancer") or {}).get("servers") or [] if isinstance(sv, dict) and sv.get("url")]
    e = run(ctx, "p_project", {"dir": d})
    ranked = labels_yaml.suggest_service(urls, _compose_services(ctx, d, e))
    checks = []
    if "traefik" in (ctx.node.get("modules") or []):
        prov = docker_link.traefik_provider(ctx, run(ctx, "d_containers", {"all": True})["containers"])
        if prov["docker"] is False:
            checks.append({"level": "err", "text": "Traefik ne lit pas les labels Docker (--providers.docker=true absent de sa configuration) : ces labels ne serviraient à rien tant qu'il n'est pas activé."})
        if prov["found"] and prov["networks"]:
            checks.append({"level": "info", "text": f"Le service doit être sur un réseau que Traefik partage ({', '.join(prov['networks'])}) : sinon Traefik ne pourra pas l'atteindre."})
    if not urls:
        checks.append({"level": "warn", "text": "Ce site n'a pas d'adresse de cible lisible : choisissez le service vous-même."})
    elif not any(c["score"] for c in ranked):
        checks.append({"level": "warn", "text": f"Aucun service de ce projet ne ressemble à la cible du site ({urls[0]}) : choisissez le bon, ou publiez depuis le conteneur."})
    return {"router": body.router, "rule": str((r["spec"] or {}).get("rule") or ""), "targets": urls, "labels": labels, "warnings": conv["warnings"], "checks": checks,
            "candidates": ranked[:60], "suggest": ranked[0] if ranked and ranked[0]["score"] else None, "files": e.get("composeFiles") or [],
            "canApply": ctx.can("projects.edit"), "canDisable": ctx.can("sites.toggle")}


class SiteApplyBody(BaseModel):
    router: str = Field(max_length=200)
    service: str = Field(max_length=100)
    file: str = Field(max_length=200)           # fichier compose choisi (parmi ceux du dossier)
    overwrite: bool = False                     # remplace un label déjà présent avec une autre valeur
    disableSite: bool = False                   # met le site en pause dans Traefik (sinon il serait servi deux fois) ; réversible


@router.post("/{pid}/labels/from-site/apply")
def labels_from_site_apply(pid: int, body: SiteApplyBody, ctx: NodeCtx = Depends(proj_cap("projects.edit"))):
    d, row = _dir(ctx, pid)
    if body.disableSite:
        ctx.need("sites.toggle")
    pick, r = _pick_site(ctx, body.router)
    e = run(ctx, "p_project", {"dir": d})
    if body.file not in (e.get("composeFiles") or []):
        raise HTTPException(422, "Ce fichier compose n'est pas dans le projet")
    conv = labels_yaml.items_to_labels(pick, compose=True)
    labels = {"traefik.enable": "true", **conv["labels"]}
    cur = run(ctx, "p_read", {"dir": d, "rel": body.file})
    try:
        new, added, conflicts = labels_yaml.set_service_labels(cur["content"], body.service, labels, body.overwrite)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    if conflicts and not body.overwrite:
        raise HTTPException(409, "Ces labels existent déjà avec une autre valeur : " + ", ".join(conflicts[:6]) + (" …" if len(conflicts) > 6 else "") + ". Cochez « remplacer » pour les écraser.")
    if new != cur["content"]:
        run(ctx, "p_write", {"dir": d, "rel": body.file, "content": new, "baseHash": cur["hash"]})
    disabled = False
    if body.disableSite:
        try:
            ctx.store.toggle([r["id"]], [], False)
            disabled = True
        except StoreError as ex:
            raise HTTPException(ex.status, f"Labels écrits, mais le site n'a pas pu être mis en pause : {ex}")
    ctx.audit("project.labels.from_site", f"{row['name']}/{body.service} ({body.file}) ← {body.router}{' ; site en pause' if disabled else ''}")
    return {"file": body.file, "service": body.service, "added": added, "disabled": disabled, "note": "Redéployez le projet pour que les labels soient pris en compte."}


# ------------------------------------------------------------------ modèles de projet
def _tpl_visible(r, ctx: NodeCtx) -> bool:
    return bool(r["shared"]) or r["created_by"] == ctx.p.username or ctx.p.is_admin


def _tpl_row_out(r) -> dict:
    return {"id": str(r["id"]), "name": r["name"], "description": r["description"], "builtin": False, "createdBy": r["created_by"], "files": _tpl_files(r), "shared": bool(r["shared"])}


def _tpl_all(ctx: NodeCtx) -> list[dict]:
    out = [{**b, "createdBy": "", "shared": True} for b in project_templates.BUILTIN]
    for r in db.rows("SELECT * FROM project_templates ORDER BY name COLLATE NOCASE"):
        if _tpl_visible(r, ctx):
            out.append(_tpl_row_out(r))
    return out


def _tpl_files(row) -> dict:
    try:
        return json.loads(crypto.dec(row["data"]))
    except (ValueError, TypeError):
        return {}


def _tpl_get(tid: str, ctx: NodeCtx) -> dict:
    for t in project_templates.BUILTIN:
        if t["id"] == tid:
            return {**t, "createdBy": "", "shared": True}
    row = db.one("SELECT * FROM project_templates WHERE id=?", (tid,)) if tid.isdigit() else None
    if not row or not _tpl_visible(row, ctx):   # un modèle privé n'existe pas pour les autres
        raise HTTPException(404, "Modèle introuvable")
    return _tpl_row_out(row)


def _tpl_out(t: dict, ctx: NodeCtx, full: bool = False) -> dict:
    files = t["files"]
    out = {"id": t["id"], "name": t["name"], "description": t["description"], "builtin": t["builtin"], "createdBy": t["createdBy"], "files": sorted(files),
           "shared": t["shared"], "mine": (not t["builtin"]) and (t["createdBy"] == ctx.p.username or ctx.p.is_admin), "vars": project_templates.variables(files)}
    if full:
        out["preview"] = {k: v[:12000] for k, v in files.items() if k != ".env" and len(v) <= 200000}
    return out


@router.get("/templates/list")
def templates_list(ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    return {"templates": [_tpl_out(t, ctx) for t in _tpl_all(ctx)], "canCreate": ctx.can("projects.import") and ctx.can("projects.edit")}


@router.get("/templates/{tid}")
def template_detail(tid: str, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    return _tpl_out(_tpl_get(tid, ctx), ctx, full=True)


class TemplateSave(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    projectId: int
    files: list[str] = Field(min_length=1, max_length=project_templates.MAX_FILES)
    keepSecrets: list[str] = Field(default_factory=list, max_length=100)   # clés du .env dont la valeur est gardée (les autres secrets sont vidés)
    keepAllSecrets: bool = False                                          # garde la valeur de tous les secrets du .env (modèle PRIVÉ seulement)
    shared: bool = False                                                  # visible des autres (secrets toujours vidés) ; sinon privé
    blankKeys: list[str] = Field(default_factory=list, max_length=200)     # clés du .env à vider en plus


@router.post("/templates")
def template_save(body: TemplateSave, ctx: NodeCtx = Depends(proj_cap("projects.files"))):
    """Enregistre un projet existant comme modèle : les fichiers choisis, le .env avec ses secrets vidés (sauf ceux qu'on garde explicitement)."""
    ctx.need("projects.import")
    d, row = _dir(ctx, body.projectId)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Le nom ne peut pas être vide")
    if db.one("SELECT 1 FROM project_templates WHERE lower(name)=lower(?)", (name,)) or any(b["name"].lower() == name.lower() for b in project_templates.BUILTIN):
        raise HTTPException(409, "Un modèle porte déjà ce nom")
    files: dict[str, str] = {}
    for rel in dict.fromkeys(body.files):
        if project_ops.is_env(rel):
            ctx.need("projects.env")
        else:
            ctx.need("projects.files")
        files[rel] = run(ctx, "p_read", {"dir": d, "rel": rel})["content"]
    if ".env" in files:
        keep = set() if body.shared else set(body.keepSecrets) | ({v["key"] for v in project_templates.parse_env(files[".env"])} if body.keepAllSecrets else set())   # un modèle partagé ne garde jamais de secret
        blank = set(body.blankKeys) | {v["key"] for v in project_templates.parse_env(files[".env"]) if v["secret"] and v["key"] not in keep}
        files[".env"] = project_templates.blank_secrets(files[".env"], blank, secrets_too=False)
    for rel in list(files):                       # le nom du projet devient un paramètre du modèle
        files[rel] = files[rel].replace(row["name"], "{{name}}") if rel != ".env" and len(row["name"]) >= 4 else files[rel]
    try:
        clean = project_templates.validate_files(files)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    now = db.now()
    cur = db.run("INSERT INTO project_templates(name,description,data,created_by,created_at,updated_at,shared) VALUES(?,?,?,?,?,?,?)",
                 (name, body.description.strip(), crypto.enc(json.dumps(clean)), ctx.p.username, now, now, 1 if body.shared else 0))
    ctx.audit("project.template.save", f"{name} ← {row['name']} ({'partagé' if body.shared else 'privé'})")
    tid = str(cur)
    return {"id": tid, "name": name, "files": sorted(clean)}


class TemplatePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    shared: bool | None = None   # privé ⇄ partagé ; en passant à « partagé », les valeurs des secrets sont vidées pour de bon


def _tpl_owned(tid: str, ctx: NodeCtx) -> dict:
    t = _tpl_get(tid, ctx)
    if t["builtin"]:
        raise HTTPException(403, "Les modèles intégrés ne se modifient pas")
    if t["createdBy"] != ctx.p.username and not ctx.p.is_admin:
        raise HTTPException(403, "Seul son auteur (ou un administrateur) modifie ce modèle")
    return t


@router.put("/templates/{tid}")
def template_update(tid: str, body: TemplatePatch, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    t = _tpl_owned(tid, ctx)
    name = (body.name if body.name is not None else t["name"]).strip()
    if name.lower() != t["name"].lower() and (db.one("SELECT 1 FROM project_templates WHERE lower(name)=lower(?)", (name,)) or any(b["name"].lower() == name.lower() for b in project_templates.BUILTIN)):
        raise HTTPException(409, "Un modèle porte déjà ce nom")
    sets, args = ["name=?", "description=?", "updated_at=?"], [name, (body.description if body.description is not None else t["description"]).strip(), db.now()]
    if body.shared is not None and body.shared != t["shared"]:
        sets.append("shared=?")
        args.append(1 if body.shared else 0)
        if body.shared and ".env" in t["files"]:   # partager = ne plus garder aucun secret
            files = dict(t["files"])
            files[".env"] = project_templates.blank_secrets(files[".env"])
            sets.append("data=?")
            args.append(crypto.enc(json.dumps(files)))
    db.run(f"UPDATE project_templates SET {', '.join(sets)} WHERE id=?", (*args, int(tid)))
    ctx.audit("project.template.update", f"{name}{' → ' + ('partagé' if body.shared else 'privé') if body.shared is not None else ''}")
    return {"ok": True}


@router.delete("/templates/{tid}")
def template_delete(tid: str, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    t = _tpl_owned(tid, ctx)
    db.run("DELETE FROM project_templates WHERE id=?", (int(tid),))
    ctx.audit("project.template.delete", t["name"])
    return {"ok": True}


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=61)
    values: dict[str, str] = Field(default_factory=dict, max_length=200)


@router.post("/templates/{tid}/create")
def template_create(tid: str, body: TemplateCreate, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    """Crée un projet neuf à partir d'un modèle : `{{name}}` remplacé, le .env rempli avec les valeurs saisies. Rien n'est démarré."""
    ctx.need("projects.edit")
    t = _tpl_get(tid, ctx)
    if ".env" in t["files"]:
        ctx.need("projects.env")
    known = {v["key"] for v in project_templates.variables(t["files"])}
    files = project_templates.render(t["files"], body.name, {k: v for k, v in body.values.items() if k in known})
    made = run(ctx, "p_create", {"name": body.name, "files": files})
    ctx.audit("project.template.use", f"{t['name']} → {made['name']}")
    r = _mark(ctx, PathsBody(paths=[made["path"]], tags=""), "managed")
    row = db.one("SELECT id FROM projects WHERE node_id=? AND path=?", (ctx.id, made["path"]))
    return {**made, **r, "id": row["id"] if row else None}


# ------------------------------------------------------------------ conteneur lancé à la main (docker run) → projet compose
def _convertible(ctx: NodeCtx, name: str) -> tuple[dict, dict]:
    ctx.need("projects.env")   # la conversion lit les VALEURS des variables d'environnement (elles vont dans un .env)
    lst = run(ctx, "d_containers", {"all": True})["containers"]
    c = next((x for x in lst if name in (x["name"], x["id"])), None)
    if not c:
        raise HTTPException(404, "Conteneur introuvable")
    spec = run(ctx, "d_spec", {"id": c["fullId"]})
    if spec.get("composeProject"):
        raise HTTPException(409, f"Ce conteneur fait déjà partie du projet compose « {spec['composeProject']} »")
    if spec.get("self"):
        raise HTTPException(409, "C'est le conteneur d'Armadok lui-même : il se gère avec son propre docker-compose.yml")
    return c, spec


@router.get("/from-container/list")
def convertible_list(ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    """Les conteneurs qui ne viennent d'aucun projet compose (lancés avec docker run, Portainer, etc.) : candidats à la conversion."""
    lst = run(ctx, "d_containers", {"all": True})["containers"]
    return {"containers": [{"name": c["name"], "image": c["image"], "state": c["state"], "status": c["status"], "ports": [p for p in c["ports"] if p["public"]][:4],
                            "protected": "self" if c.get("self") else ("traefik" if c["name"] == settings.traefik_container else "")}
                           for c in lst if not c["project"] and not c.get("self")],
            "canConvert": ctx.can("projects.import") and ctx.can("projects.edit") and ctx.can("projects.env")}


class FromContainerPreview(BaseModel):
    container: str = Field(min_length=1, max_length=200)


@router.post("/from-container/preview")
def from_container_preview(body: FromContainerPreview, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    c, spec = _convertible(ctx, body.container)
    conv = compose_convert.convert(spec)
    return {"container": c["name"], "image": spec["image"], "running": spec["running"], "name": compose_convert.service_name(c["name"]), "compose": conv["compose"],
            "envKeys": conv["envKeys"], "warnings": conv["warnings"], "volumes": conv["volumes"], "networks": conv["networks"],
            "protected": "traefik" if c["name"] == settings.traefik_container else "", "canReplace": ctx.can("projects.deploy") and ctx.can("docker.control")}


class FromContainerBody(BaseModel):
    container: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=61)      # dossier du nouveau projet
    replace: bool = False                                # arrête l'ancien conteneur (renommé, gardé pour revenir en arrière) et déploie le projet
    confirm: str = Field(default="", max_length=200)    # le nom du dossier, exigé pour remplacer le conteneur de Traefik


@router.post("/from-container")
def from_container(body: FromContainerBody, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    """Crée un projet (docker-compose.yml + .env) à partir d'un conteneur existant. Avec `replace`, l'ancien conteneur est arrêté et renommé (jamais supprimé)."""
    ctx.need("projects.edit")
    if body.replace:
        ctx.need("projects.deploy")
        ctx.need("docker.control")
    c, spec = _convertible(ctx, body.container)
    if body.replace and c["name"] == settings.traefik_container and body.confirm != body.name:
        raise HTTPException(409, "C'est le conteneur de Traefik : le remplacer coupe l'accès à tous vos sites quelques secondes. Retapez le nom du dossier pour confirmer")
    conv = compose_convert.convert(spec, project=compose_convert.service_name(body.name))
    files = {"docker-compose.yml": conv["compose"]}
    if conv["env"]:
        files[".env"] = conv["env"]
    made = run(ctx, "p_create", {"name": body.name, "files": files})
    ctx.audit("project.from_container", f"{c['name']} → {made['name']}")
    r = _mark(ctx, PathsBody(paths=[made["path"]], tags=""), "managed")
    row = db.one("SELECT id FROM projects WHERE node_id=? AND path=?", (ctx.id, made["path"]))
    out = {**made, **r, "id": row["id"] if row else None, "warnings": conv["warnings"], "envKeys": conv["envKeys"], "oldContainer": c["name"], "job": None}
    if body.replace:
        old = f"{c['name']}-avant-armadok"
        try:
            if spec["running"]:
                run(ctx, "d_action", {"id": c["fullId"], "action": "stop"})
            run(ctx, "d_rename", {"id": c["fullId"], "name": old})
        except HTTPException as ex:
            raise HTTPException(ex.status_code, f"Projet créé, mais l'ancien conteneur n'a pas pu être arrêté ou renommé : {ex.detail}")
        out["oldContainer"] = old
        out["job"] = run(ctx, "p_compose", {"dir": made["path"], "action": "up"})
        ctx.audit("project.from_container.replace", f"{c['name']} → {old}, déploiement de {made['name']}")
    return out


# ------------------------------------------------------------------ Git
def _cred(pid: int) -> tuple[str, str]:
    r = db.one("SELECT username, token FROM project_git WHERE project_id=?", (pid,))
    return (r["username"], crypto.reveal(r["token"])) if r and r["token"] else ("", "")


def _ssh_key(node_id: str, pid: int | None = None) -> tuple[str, str, str]:
    """(clé privée, clé publique, origine) à utiliser pour git : celle du projet d'abord, sinon celle du nœud. Vide si aucune."""
    if pid is not None:
        r = db.one("SELECT private, public FROM project_ssh WHERE project_id=?", (pid,))
        if r:
            return crypto.reveal(r["private"]), r["public"], "project"
    r = db.one("SELECT private, public FROM node_git_key WHERE node_id=?", (node_id,))
    return (crypto.reveal(r["private"]), r["public"], "node") if r else ("", "", "")


def _git_args(pid: int, **extra) -> dict:
    u, t = _cred(pid)
    row = db.one("SELECT node_id FROM projects WHERE id=?", (pid,))
    return {"user": u, "token": t, "sshKey": _ssh_key(row["node_id"], pid)[0] if row else "", **extra}


@router.get("/{pid}/git")
def git_state(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    d, _ = _dir(ctx, pid)
    st = run(ctx, "p_git_status", {"dir": d})
    u, t = _cred(pid)
    out = {"status": st, "hasToken": bool(t), "username": u, "commits": [], "canDeploy": ctx.can("projects.deploy")}
    _, pub, src = _ssh_key(ctx.id, pid)
    out["ssh"] = {"source": src or None, "publicKey": pub, "fingerprint": gitssh.fingerprint(pub) if pub else ""}
    h = db.one("SELECT deploy, last_at, last_result FROM project_hook WHERE project_id=?", (pid,))
    out["hook"] = {"set": bool(h), "deploy": bool(h and h["deploy"]), "lastAt": h["last_at"] if h else None, "lastResult": h["last_result"] if h else "", "path": f"/api/hooks/git/{pid}"}
    if st["isRepo"]:
        out["commits"] = run(ctx, "p_git_log", {"dir": d, "n": 15})["commits"]
    return out


@router.post("/{pid}/git/fetch")
def git_fetch(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    d, _ = _dir(ctx, pid)
    return run(ctx, "p_git_status", {"dir": d, **_git_args(pid), "fetch": True})


class GitRun(BaseModel):
    action: str = Field(max_length=20)
    ref: str = Field(default="", max_length=120)


@router.post("/{pid}/git/run")
def git_run(pid: int, body: GitRun, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    """pull / fetch / checkout (retour à une version) en tâche de fond. Pour redéployer ensuite, l'interface enchaîne « Déployer » (droit projects.deploy)."""
    d, row = _dir(ctx, pid)
    r = run(ctx, "p_git_run", {"dir": d, "action": body.action, "ref": body.ref, **_git_args(pid)})
    ctx.audit(f"project.git.{body.action}", f"{row['name']}{' : ' + body.ref if body.ref else ''}")
    return r


class GitCred(BaseModel):
    username: str = Field(default="", max_length=100)
    token: str = Field(min_length=1, max_length=500)


@router.put("/{pid}/git/credentials")
def git_cred_set(pid: int, body: GitCred, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    """Jeton d'accès pour un dépôt privé en https. Chiffré en base ; il n'est jamais renvoyé, ni écrit dans .git/config."""
    _, row = _dir(ctx, pid)
    db.run("INSERT INTO project_git(project_id,username,token,updated_by,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET username=excluded.username, token=excluded.token, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
           (pid, body.username.strip(), crypto.enc(body.token), ctx.p.username, db.now()))
    ctx.audit("project.git.credentials", row["name"])
    return {"ok": True}


@router.delete("/{pid}/git/credentials")
def git_cred_del(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    _, row = _dir(ctx, pid)
    db.run("DELETE FROM project_git WHERE project_id=?", (pid,))
    ctx.audit("project.git.credentials.remove", row["name"])
    return {"ok": True}


# --- clé SSH de déploiement : par projet, ou par nœud (utilisée pour cloner et pour les projets sans clé propre)
def _gen_key(label: str, who: str) -> tuple[str, str, str]:
    return gitssh.generate(f"armadok-{label}")


@router.post("/{pid}/git/ssh-key")
def git_ssh_new(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    """Génère (ou remplace) la clé SSH de déploiement de ce projet. La partie publique se donne au dépôt ; la privée reste chiffrée ici."""
    _, row = _dir(ctx, pid)
    private, public, fp = _gen_key(re.sub(r"[^A-Za-z0-9._-]", "-", row["name"])[:30], ctx.p.username)
    db.run("INSERT INTO project_ssh(project_id,private,public,created_by,created_at) VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET private=excluded.private, public=excluded.public, created_by=excluded.created_by, created_at=excluded.created_at",
           (pid, crypto.enc(private), public, ctx.p.username, db.now()))
    ctx.audit("project.git.ssh_key", f"{row['name']} {fp}")
    return {"source": "project", "publicKey": public, "fingerprint": fp}


@router.delete("/{pid}/git/ssh-key")
def git_ssh_del(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    _, row = _dir(ctx, pid)
    db.run("DELETE FROM project_ssh WHERE project_id=?", (pid,))
    ctx.audit("project.git.ssh_key.remove", row["name"])
    return {"ok": True}


@router.get("/git/node-key")
def node_key_get(ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    r = db.one("SELECT public, created_at FROM node_git_key WHERE node_id=?", (ctx.id,))
    return {"set": bool(r), "publicKey": r["public"] if r else "", "fingerprint": gitssh.fingerprint(r["public"]) if r else "", "createdAt": r["created_at"] if r else None}


@router.post("/git/node-key")
def node_key_new(ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    """Clé SSH du nœud : sert à cloner un dépôt privé en SSH, et aux projets qui n'ont pas de clé propre."""
    private, public, fp = _gen_key("node", ctx.p.username)
    db.run("INSERT INTO node_git_key(node_id,private,public,created_by,created_at) VALUES(?,?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET private=excluded.private, public=excluded.public, created_by=excluded.created_by, created_at=excluded.created_at",
           (ctx.id, crypto.enc(private), public, ctx.p.username, db.now()))
    ctx.audit("project.git.node_key", fp)
    return {"set": True, "publicKey": public, "fingerprint": fp, "createdAt": db.now()}


@router.delete("/git/node-key")
def node_key_del(ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    db.run("DELETE FROM node_git_key WHERE node_id=?", (ctx.id,))
    ctx.audit("project.git.node_key.remove")
    return {"ok": True}


# --- déclenchement par le dépôt (webhook) : le dépôt prévient, l'interface récupère (et redéploie si c'est demandé)
class HookBody(BaseModel):
    deploy: bool = False


@router.post("/{pid}/git/hook")
def git_hook_new(pid: int, body: HookBody, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    """Crée (ou renouvelle) le secret du webhook. Il n'est montré QU'UNE fois : à coller dans le dépôt (GitHub : « Secret » ; GitLab : « Secret token »)."""
    _, row = _dir(ctx, pid)
    if body.deploy:
        ctx.need("projects.deploy")
    secret = secrets.token_urlsafe(32)
    db.run("INSERT INTO project_hook(project_id,secret,deploy,created_by,created_at) VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET secret=excluded.secret, deploy=excluded.deploy, created_by=excluded.created_by, created_at=excluded.created_at, last_at=NULL, last_result=''",
           (pid, crypto.enc(secret), 1 if body.deploy else 0, ctx.p.username, db.now()))
    ctx.audit("project.git.hook", f"{row['name']}{' (redéploie)' if body.deploy else ''}")
    return {"secret": secret, "path": f"/api/hooks/git/{pid}", "deploy": body.deploy}


@router.put("/{pid}/git/hook")
def git_hook_set(pid: int, body: HookBody, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    _, row = _dir(ctx, pid)
    if body.deploy:
        ctx.need("projects.deploy")
    if not db.one("SELECT 1 FROM project_hook WHERE project_id=?", (pid,)):
        raise HTTPException(404, "Aucun webhook sur ce projet")
    db.run("UPDATE project_hook SET deploy=? WHERE project_id=?", (1 if body.deploy else 0, pid))
    ctx.audit("project.git.hook.update", f"{row['name']} redéploie={body.deploy}")
    return {"ok": True}


@router.delete("/{pid}/git/hook")
def git_hook_del(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    _, row = _dir(ctx, pid)
    db.run("DELETE FROM project_hook WHERE project_id=?", (pid,))
    ctx.audit("project.git.hook.remove", row["name"])
    return {"ok": True}


class CloneBody(BaseModel):
    url: str = Field(max_length=500)
    name: str = Field(max_length=80)
    branch: str = Field(default="", max_length=100)
    username: str = Field(default="", max_length=100)
    token: str = Field(default="", max_length=500)


@router.post("/git/clone")
def git_clone(body: CloneBody, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    """Clone un dépôt dans la racine des projets (le projet apparaît ensuite dans « Découverts »). Le jeton n'est PAS gardé."""
    ctx.need("projects.import")
    ssh = _ssh_key(ctx.id)[0] if not body.url.lower().startswith(("http://", "https://")) else ""   # adresse SSH : la clé du nœud, si elle existe
    r = run(ctx, "p_git_clone", {"url": body.url, "name": body.name, "branch": body.branch, "user": body.username, "token": body.token, "sshKey": ssh})
    ctx.audit("project.git.clone", f"{body.name} ← {project_ops._strip_url(body.url)}")
    return r


@router.get("/clone-jobs/{jid}")
def clone_job(jid: str, since: int = 0, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    return run(ctx, "p_job", {"id": jid, "since": since})


# ------------------------------------------------------------------ suivi des images
@router.get("/{pid}/images")
def images_state(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.view"))):
    d, row = _dir(ctx, pid)
    rows = imagecheck.compute(lambda op, a: run(ctx, op, a), ctx.id, d)
    return {"images": rows, "track": bool(row["track_images"]), "canManage": ctx.can("projects.git"), "hours": settings.image_check_hours, "webhook": bool(db.one("SELECT webhook_url FROM nodes WHERE id=? AND webhook_url<>''", (ctx.id,)))}


@router.post("/{pid}/images/check")
def images_check(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    """Interroge les registres (sans rien télécharger) pour savoir s'il existe de nouvelles versions."""
    d, row = _dir(ctx, pid)
    rows = imagecheck.compute(lambda op, a: run(ctx, op, a), ctx.id, d, fetch=True)
    ctx.audit("project.images.check", row["name"])
    return {"images": rows, "track": bool(row["track_images"])}


class TrackBody(BaseModel):
    enabled: bool


@router.put("/{pid}/images/track")
def images_track(pid: int, body: TrackBody, ctx: NodeCtx = Depends(proj_cap("projects.git"))):
    """Vérification quotidienne de ce projet, avec une notification (webhook du nœud) quand une nouvelle version apparaît."""
    _, row = _dir(ctx, pid)
    db.run("UPDATE projects SET track_images=? WHERE id=?", (1 if body.enabled else 0, pid))
    ctx.audit("project.images.track", f"{row['name']} : {'activé' if body.enabled else 'désactivé'}")
    return {"track": body.enabled}


# ------------------------------------------------------------------ webhook public : le dépôt prévient, l'interface récupère
hooks_router = APIRouter(prefix="/api/hooks", tags=["hooks"])
HOOK_MIN_GAP = 10          # secondes entre deux déclenchements d'un même projet
HOOK_MAX_BODY = 1_000_000
_hook_last: dict[int, float] = {}
_hook_fails: dict[str, list[float]] = {}


def _hook_locked(ip: str) -> bool:
    now = time.time()
    lst = [t for t in _hook_fails.get(ip, []) if now - t < 600]
    _hook_fails[ip] = lst
    return len(lst) >= 10


def _hook_valid(secret: str, body: bytes, headers) -> bool:
    """GitHub : X-Hub-Signature-256 (HMAC-SHA256 du corps) · Gitea / Gogs : X-Gitea-Signature / X-Gogs-Signature (HMAC en hexadécimal) · GitLab : X-Gitlab-Token (le secret)."""
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    sig = headers.get("x-hub-signature-256") or ""
    if sig.startswith("sha256=") and hmac.compare_digest(sig[7:], mac):
        return True
    for h in ("x-gitea-signature", "x-gogs-signature"):
        if headers.get(h) and hmac.compare_digest(headers[h], mac):
            return True
    tok = headers.get("x-gitlab-token") or ""
    return bool(tok) and hmac.compare_digest(tok, secret)


def _node_call(node: dict, op: str, args: dict):
    from . import host_ops, worker_link
    return worker_link.call(node["id"], op, args) if node["kind"] == "remote" else host_ops.handle(op, args)


def _hook_follow(node: dict, pid: int, name: str, path: str, jid: str, deploy: bool) -> None:
    """Suit la récupération jusqu'au bout, puis (si demandé) relance « up -d » ; garde le résultat et le journalise."""
    from .audit import audit
    result = "échec de la récupération"
    try:
        since, code = 0, None
        for _ in range(600):
            time.sleep(1)
            j = _node_call(node, "p_job", {"id": jid, "since": since})
            since = j["next"]
            if j["done"]:
                code = j["code"]
                break
        if code == 0:
            result = "récupéré"
            if deploy:
                d = _node_call(node, "p_compose", {"dir": path, "action": "up"})
                for _ in range(1800):
                    time.sleep(1)
                    j = _node_call(node, "p_job", {"id": d["job"], "since": 0})
                    if j["done"]:
                        result = "récupéré et redéployé" if j["code"] == 0 else "récupéré, mais le redéploiement a échoué"
                        break
                else:
                    result = "récupéré, redéploiement trop long"
        elif code is not None:
            result = f"échec de la récupération (code {code})"
    except Exception as e:  # noqa: BLE001 - un nœud injoignable ne doit pas laisser le projet sans résultat
        result = f"interrompu : {str(e)[:120]}"
    db.run("UPDATE project_hook SET last_result=? WHERE project_id=?", (result, pid))
    audit("webhook", "project.git.hook.result", f"{name} : {result}", node=node["id"])


@hooks_router.post("/git/{pid}")
async def git_hook(pid: int, request: Request):
    import asyncio
    from .audit import audit
    ip = request.client.host if request.client else "?"
    if _hook_locked(ip):
        raise HTTPException(429, "Trop de tentatives")
    if int(request.headers.get("content-length") or 0) > HOOK_MAX_BODY:
        raise HTTPException(413, "Corps trop gros")
    body = await request.body()
    if len(body) > HOOK_MAX_BODY:
        raise HTTPException(413, "Corps trop gros")
    row = await asyncio.to_thread(db.one, "SELECT h.secret, h.deploy, p.id, p.node_id, p.path, p.name FROM project_hook h JOIN projects p ON p.id=h.project_id "
                                         "JOIN nodes n ON n.id=p.node_id WHERE p.id=? AND p.state='managed' AND n.deleted_at IS NULL AND n.status='active'", (pid,))
    secret = crypto.reveal(row["secret"]) if row else ""
    if not row or not secret or not _hook_valid(secret, body, request.headers):
        _hook_fails.setdefault(ip, []).append(time.time())
        audit("webhook", "project.git.hook.refused", f"projet {pid} depuis {ip}")
        raise HTTPException(401, "Signature invalide")                      # même réponse pour un projet inconnu : rien n'est révélé
    if request.headers.get("x-github-event") == "ping":
        return {"ok": True, "pong": True}
    now = time.time()
    if now - _hook_last.get(pid, 0) < HOOK_MIN_GAP:
        return {"ok": True, "skipped": "trop rapproché"}
    _hook_last[pid] = now
    node = dict(db.one("SELECT id, kind FROM nodes WHERE id=?", (row["node_id"],)))
    try:
        user, token = _cred(pid)
        key = _ssh_key(node["id"], pid)[0]
        r = await asyncio.to_thread(_node_call, node, "p_git_run", {"dir": row["path"], "action": "pull", "user": user, "token": token, "sshKey": key})
    except (StoreError, Exception) as e:  # noqa: BLE001
        msg = str(e)[:150]
        db.run("UPDATE project_hook SET last_at=?, last_result=? WHERE project_id=?", (now, f"refusé : {msg}", pid))
        audit("webhook", "project.git.hook.error", f"{row['name']} : {msg}", node=node["id"])
        raise HTTPException(502, "Récupération impossible")
    db.run("UPDATE project_hook SET last_at=?, last_result='en cours' WHERE project_id=?", (now, pid))
    audit("webhook", "project.git.hook", f"{row['name']} depuis {ip}", node=node["id"])
    threading.Thread(target=_hook_follow, args=(node, pid, row["name"], row["path"], r["job"], bool(row["deploy"])), name=f"hook-{pid}", daemon=True).start()
    return {"ok": True, "job": r["job"]}
