"""Projets Compose d'un nœud : découverte, import, vue d'ensemble (services, conteneurs, sites Traefik, visiteurs).

Un projet est un DOSSIER sur la machine (le disque reste la source de vérité) : l'interface n'en garde que la référence (chemin, état, étiquettes).
« Découvert » = vu dans les racines autorisées, jamais modifié ; « géré » = importé ; « ignoré » = écarté de la liste. Désimporter ne touche ni au disque ni aux conteneurs."""
import hashlib
import hmac
import io
import os
import re
import secrets
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from . import crypto, db, docker_link, gitssh, imagecheck, labels_yaml, linkage, project_ops
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
        services.append({**s, "state": state, "containers": [_slim(c) for c in mine], "links": lk, "hosts": linkage.hosts_of(lk)})
    extra = [c for c in cs if c["id"] not in seen]
    expected = [s for s in services if not s["profiles"]]
    running = sum(1 for s in expected if s["state"] == "running")
    status = "absent" if not cs else "running" if expected and running == len(expected) else "stopped" if not any(c["state"] == "running" for c in cs) else "partial"
    alerts = []
    if e["error"]:
        alerts.append({"level": "err", "text": e["error"]})
    for s in services:
        if any(c["health"] == "unhealthy" for c in s["containers"]):
            alerts.append({"level": "err", "text": f"Le service « {s['name']} » est défaillant"})
        elif status == "partial" and s["state"] in ("stopped", "absent") and not s["profiles"]:
            alerts.append({"level": "warn", "text": f"Le service « {s['name']} » ne tourne pas"})
    hosts = sorted({h for s in services for h in s["hosts"]} | {h for c in extra for l in links.get(c["name"], []) for h in l["hosts"]})
    return {"id": row["id"] if row else None, "path": e["path"], "name": e["name"], "composeProject": e["composeProject"], "file": e["file"], "error": e["error"],
            "state": row["state"] if row else "discovered", "tags": [t for t in (row["tags"] if row else "").split(",") if t], "services": services, "extra": [_slim(c) for c in extra],
            "status": status, "running": running, "total": len(expected), "alerts": alerts, "hosts": hosts, "containerIds": [c["id"] for c in cs],
            "access": docker_link.access_for(ctx, [l for s in services for l in s["links"]] + [l for c in extra for l in links.get(c["name"], [])], ctxs, totals=True) if access and hosts else None,
            "hasEnv": e["hasEnv"], "hasGit": e["hasGit"], "build": e["build"], "mtime": e["mtime"], "missing": False}


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


@router.get("/{pid}")
def detail(pid: int, ctx: NodeCtx = Depends(proj_cap("projects.view"))):
    row = _row(ctx, pid)
    e = run(ctx, "p_project", {"dir": row["path"]})
    containers = run(ctx, "d_containers", {"all": True})["containers"]
    others = [x for x in run(ctx, "p_discover")["projects"] if x["path"] != e["path"]]
    cs = assign([e] + others, containers).get(e["path"], [])
    links = docker_link.link_containers(ctx, containers)
    s = summarize(ctx, e, cs, links, row, access=False)
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
class ComposeBody(BaseModel):
    action: str = Field(max_length=20)
    services: list[str] = Field(default_factory=list, max_length=50)
    build: bool = False
    pull: bool = False
    noCache: bool = False
    volumes: bool = False


@router.post("/{pid}/compose")
def compose(pid: int, body: ComposeBody, ctx: NodeCtx = Depends(proj_cap("projects.deploy"))):
    if body.action == "config":
        ctx.need("projects.env")      # « compose config » affiche les valeurs des variables
    if body.volumes:
        ctx.need("projects.delete")   # « down --volumes » détruit les données
    d, row = _dir(ctx, pid)
    r = run(ctx, "p_compose", {"dir": d, "action": body.action, "services": body.services, "build": body.build, "pull": body.pull, "noCache": body.noCache, "volumes": body.volumes})
    ctx.audit(f"project.compose.{body.action}", f"{row['name']}{' : ' + ', '.join(body.services) if body.services else ''}{' (+ volumes)' if body.volumes else ''}")
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
    return {"services": out, "checks": checks, "traefik": "traefik" in (ctx.node.get("modules") or []),
            "canApply": ctx.can("sites.create") and ctx.can("traefik.all")}


class ApplyBody(BaseModel):
    service: str = Field(max_length=100)


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
    fname = re.sub(r"[^a-zA-Z0-9_.-]", "-", f"zz-{row['name']}-{svc['name']}.yml")
    try:
        for i in conv["items"]:
            ctx.store.upsert("http", i["kind"], i["name"], i["spec"], fname)
    except StoreError as ex:
        raise HTTPException(ex.status, str(ex))
    ctx.audit("project.labels.apply", f"{row['name']}/{svc['name']} → {fname}")
    return {"file": fname, "items": [f"{i['kind']}/{i['name']}" for i in conv["items"]]}


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
