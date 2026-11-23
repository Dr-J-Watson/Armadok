import os

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .config import settings
from . import caps as capmod
from . import org_folders, db
from .nodes import NodeCtx, node_optional, node_traefik
from .perms import LEVELS, Principal, accessible_domains, admin_only, current_user, hosts_min_level, target_allowed
from .traefik_lint import lint
from .traefik_live import live_status
from .traefik_scope import router_hosts, scope_items
from .reorganize import MAX_STEPS, build_plan, is_managed
from .traefik_store import StoreError, suggest_file

router = APIRouter(prefix="/api")

ROUTER_KEYS = {"rule", "entryPoints", "service", "middlewares", "priority", "tls"}


def _known_entrypoints(ctx: NodeCtx, items) -> list[str]:
    eps = set(ctx.node["entrypoints"]) | set(live_status(ctx.node).get("entrypoints", []))
    for it in items:
        if it["kind"] == "routers":
            eps.update((it["spec"] or {}).get("entryPoints") or [])
    return sorted(eps)


def _known_resolvers(ctx: NodeCtx, items) -> list[str]:
    rs = set(ctx.node["certResolvers"])
    for it in items:
        if it["kind"] == "routers":
            r = ((it["spec"] or {}).get("tls") or {}).get("certResolver")
            if r:
                rs.add(r)
    return sorted(rs)


@router.get("/meta")
def meta(ctx: NodeCtx | None = Depends(node_optional), p: Principal = Depends(current_user)):
    out = {"version": os.environ.get("APP_VERSION", "dev"), "user": p.public(), "ovh": {"configured": settings.ovh_configured, "mock": settings.ovh_mock},
           "dnsEnabled": settings.ovh_configured or bool(accessible_domains(p)),
           "entrypoints": [], "certResolvers": [], "traefikApi": False, "defaultTarget": "", "dirs": []}
    if ctx and ctx.p.traefik != "none":
        out["defaultTarget"] = ctx.node["dnsTarget"]
        try:
            items, _ = ctx.store.items()
            out.update(entrypoints=_known_entrypoints(ctx, items), certResolvers=_known_resolvers(ctx, items),
                       traefikApi=live_status(ctx.node).get("available", False),
                       dirs=ctx.store.list_dirs() if ctx.p.traefik == "full" and ctx.can("files.view") else [])
        except StoreError:  # worker pas (encore) connecté : l'interface doit rester utilisable pour l'installer
            out.update(nodeOffline=True, entrypoints=list(ctx.node["entrypoints"]), certResolvers=list(ctx.node["certResolvers"]))
    elif ctx:
        out["defaultTarget"] = ctx.node["dnsTarget"]
    return out


@router.get("/audit")
def get_audit(p: Principal = Depends(admin_only)):
    from . import audit as a
    return a.tail()


# ------------------------------------------------------------------ lecture
@router.get("/traefik/overview")
def overview(ctx: NodeCtx = Depends(node_traefik)):
    p, store = ctx.p, ctx.store
    all_items, errors = store.items()
    items = scope_items(p, [dict(i) for i in all_items])
    full = p.traefik == "full"
    if full:   # chaque famille d'éléments n'est montrée qu'à qui a le droit de la voir
        items = [i for i in items if ctx.can(capmod.prefix_of(i["proto"], i["kind"]) + ".view")]
    fv = full and ctx.can("files.view")
    resolvers = ctx.node["certResolvers"]
    if full:
        shown = {i["id"] for i in items}
        issues = [i for i in lint(all_items, errors, _known_entrypoints(ctx, all_items), resolvers) if not i.get("item") or i["item"] in shown]
    else:
        ids = {i["id"] for i in items}
        issues = [i for i in lint(all_items, {}, _known_entrypoints(ctx, all_items), resolvers) if i["item"] in ids and i["level"] != "info"]
        for i in issues:
            i["file"] = None
    return {"items": items, "files": store.list_files() if fv else [], "dirs": store.list_dirs() if fv else [], "fileErrors": errors if fv else {}, "issues": issues}


@router.get("/traefik/site/check")
def site_check(name: str, ctx: NodeCtx = Depends(node_traefik)):
    """Test en un clic : DNS, certificat et réponse HTTP des adresses d'un site de ce nœud."""
    from . import sitecheck
    ctx.need("sites.view")
    items, _ = ctx.store.items()
    visible = scope_items(ctx.p, [dict(i) for i in items])
    r = next((i for i in visible if i["kind"] == "routers" and i["proto"] == "http" and i["name"] == name), None)
    if not r:
        raise HTTPException(404, "Site introuvable")
    hosts = sorted(h for h in (router_hosts(r["spec"]) or []) if "*" not in h)
    if not hosts:
        raise HTTPException(422, "Ce site n'a pas d'adresse précise à tester (règle sans Host)")
    return {"name": name, "results": sitecheck.check(hosts)}


# ------------------------------------------------------------------ dossiers de sites (rangement de la liste, voir org_folders)
def _folder_owner(request: Request, ctx: NodeCtx) -> str:
    """À qui appartiennent les dossiers de la vue : au nœud dont on regarde les sites. Un nœud qui publie par le Traefik d'un autre (vue filtrée,
    en-tête X-Node-Scope déjà validé par node_optional) range SES sites dans SES dossiers ; la vue complète de ce Traefik a les siens."""
    scope = request.headers.get("x-node-scope")
    return scope if scope and scope != ctx.id else ctx.id


class SiteFolderBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=500)
    color: str = Field(default="", max_length=12)
    parentId: int | None = None


class SiteRegroupBody(BaseModel):
    routers: list[str] = Field(default_factory=list, max_length=300)
    folders: list[int] = Field(default_factory=list, max_length=200)
    folderId: int | None = None


@router.get("/traefik/site-folders")
def site_folders(request: Request, ctx: NodeCtx = Depends(node_traefik)):
    owner = _folder_owner(request, ctx)
    ctx.need("sites.view")
    return {"folders": org_folders.listing(owner, "sites"), "canManage": ctx.can("sites.edit"), "scoped": owner != ctx.id,
            "assign": {r["router"]: r["folder_id"] for r in db.rows("SELECT router, folder_id FROM site_folder_items WHERE node_id=?", (owner,))}}


@router.post("/traefik/site-folders")
def create_site_folder(body: SiteFolderBody, request: Request, ctx: NodeCtx = Depends(node_traefik)):
    owner = _folder_owner(request, ctx)
    ctx.need("sites.edit")
    fid, name = org_folders.create(owner, "sites", ctx.p.username, body.name, body.description, body.color, body.parentId)
    ctx.audit("site.folder.create", name)
    return {"id": fid}


@router.put("/traefik/site-folders/{fid}")
def edit_site_folder(fid: int, body: SiteFolderBody, request: Request, ctx: NodeCtx = Depends(node_traefik)):
    owner = _folder_owner(request, ctx)
    ctx.need("sites.edit")
    ctx.audit("site.folder.edit", org_folders.edit(owner, "sites", fid, body.name, body.description, body.color, body.parentId))
    return {"ok": True}


@router.delete("/traefik/site-folders/{fid}")
def delete_site_folder(fid: int, request: Request, ctx: NodeCtx = Depends(node_traefik)):
    owner = _folder_owner(request, ctx)
    """Supprime le dossier : ses sites et sous-dossiers remontent d'un niveau (les sites eux-mêmes ne sont pas touchés)."""
    ctx.need("sites.edit")
    name, parent = org_folders.delete(owner, "sites", fid)
    if parent is None:
        db.run("DELETE FROM site_folder_items WHERE node_id=? AND folder_id=?", (owner, fid))
    else:
        db.run("UPDATE site_folder_items SET folder_id=? WHERE node_id=? AND folder_id=?", (parent, owner, fid))
    db.run("DELETE FROM project_folders WHERE id=?", (fid,))
    ctx.audit("site.folder.delete", name)
    return {"ok": True}


@router.post("/traefik/site-folders/regroup")
def regroup_sites(body: SiteRegroupBody, request: Request, ctx: NodeCtx = Depends(node_traefik)):
    owner = _folder_owner(request, ctx)
    """Range des sites (par nom de routeur) et/ou des dossiers dans un dossier, ou à la racine."""
    ctx.need("sites.edit")
    dest = org_folders.get(owner, "sites", body.folderId)["name"] if body.folderId is not None else "la racine"
    items, _ = ctx.store.items()
    visible = {i["name"] for i in scope_items(ctx.p, [dict(i) for i in items]) if i["kind"] == "routers" and i["proto"] == "http"}
    unknown = [n for n in body.routers if n not in visible]
    if unknown:
        raise HTTPException(404, f"Site introuvable : {unknown[0]}")
    moved = org_folders.move_folders(owner, "sites", body.folders, body.folderId)
    for name in body.routers:
        if body.folderId is None:
            db.run("DELETE FROM site_folder_items WHERE node_id=? AND router=?", (owner, name))
        else:
            db.run("INSERT INTO site_folder_items(node_id,router,folder_id) VALUES(?,?,?) ON CONFLICT(node_id,router) DO UPDATE SET folder_id=excluded.folder_id", (owner, name, body.folderId))
        moved += 1
    if moved:
        ctx.audit("site.folder.move", f"{moved} élément(s) → {dest}")
    return {"moved": moved}


@router.get("/traefik/live")
def traefik_live(ctx: NodeCtx = Depends(node_traefik)):
    live = live_status(ctx.node)
    if ctx.p.traefik == "full" or not live.get("routers"):
        return live
    items, _ = ctx.store.items()   # accès limité à ses domaines : pas la carte des routeurs des autres (noms, fournisseurs, erreurs)
    mine = {i["name"] for i in scope_items(ctx.p, [dict(i) for i in items]) if i["proto"] == "http" and i["kind"] == "routers"}
    keep = {k: v for k, v in live["routers"].items() if k.split("/", 1)[0] == "http" and k.split("/", 1)[-1].split("@", 1)[0] in mine}
    return {**live, "routers": keep}


# ------------------------------------------------------------------ sites (routeur + service) : tous les profils Traefik
class SiteBody(BaseModel):
    name: str
    oldName: str | None = None
    router: dict
    serviceName: str | None = None
    service: dict | None = None
    file: str | None = None


def _scoped_checks(ctx: NodeCtx, body: SiteBody, items: list[dict], visible: list[dict]) -> None:
    p = ctx.p
    vis = {(i["kind"], i["name"]): i for i in visible}
    existing = vis.get(("routers", body.oldName or body.name))
    if (body.oldName or body.name) in {i["name"] for i in items if i["proto"] == "http" and i["kind"] == "routers"} and not existing:
        raise HTTPException(403, "Ce routeur existe mais vous n'y avez pas accès")
    if existing and not existing["editable"]:
        raise HTTPException(403, "Vous n'avez que le droit de lecture sur ce site")
    extra = set(body.router) - ROUTER_KEYS
    if extra:
        raise HTTPException(403, f"Options non autorisées : {', '.join(sorted(extra))}")
    tls = body.router.get("tls") or {}
    if set(tls) - {"certResolver", "options"}:
        raise HTTPException(403, "Options TLS non autorisées")
    hosts = router_hosts(body.router)
    if not hosts:
        raise HTTPException(422, "La règle doit se limiter à des hôtes précis (Host(`…`)) de vos domaines")
    lvl = hosts_min_level(p, hosts)
    if not lvl or LEVELS[lvl] < LEVELS["edit"]:
        bad = [h for h in hosts if not hosts_min_level(p, [h]) or LEVELS[hosts_min_level(p, [h])] < LEVELS["edit"]]
        raise HTTPException(403, f"Domaine non autorisé pour : {', '.join(bad)}")
    mws = {i["name"] for i in items if i["proto"] == "http" and i["kind"] == "middlewares"}
    for m in body.router.get("middlewares") or []:
        if m not in mws:
            raise HTTPException(422, f"Middleware inconnu : {m}")
    known = set(_known_entrypoints(ctx, items))
    for ep in body.router.get("entryPoints") or []:
        if ep not in known:
            raise HTTPException(422, f"EntryPoint inconnu : {ep}")
    sname = body.serviceName or body.name
    if body.service is not None:
        if set(body.service) != {"loadBalancer"} or set(body.service["loadBalancer"]) - {"servers", "passHostHeader"}:
            raise HTTPException(403, "Seul un load balancer simple (serveurs) est autorisé")
        for s in body.service["loadBalancer"].get("servers") or []:
            if set(s) != {"url"} or not target_allowed(p, str(s["url"])):
                raise HTTPException(403, f"Cible non autorisée : {s.get('url')} (demandez à un administrateur)")
        cur = vis.get(("services", sname))
        if sname in {i["name"] for i in items if i["proto"] == "http" and i["kind"] == "services"} and not (cur and cur["editable"]):
            raise HTTPException(403, f"Le service « {sname} » existe déjà et n'est pas à vous")
        if body.router.get("service") != sname:
            raise HTTPException(422, "Le routeur doit pointer vers ce service")
    elif ("services", body.router.get("service")) not in vis:
        raise HTTPException(403, "Service inconnu ou non autorisé")


@router.put("/traefik/site")
def save_site(body: SiteBody, ctx: NodeCtx = Depends(node_traefik)):
    p, store = ctx.p, ctx.store
    items, _ = store.items()
    sname = body.serviceName or body.name
    names = {(i["kind"], i["name"]) for i in items if i["proto"] == "http"}
    existing = next((i for i in items if i["id"] == f"http/routers/{body.oldName or body.name}"), None)
    ctx.need("sites.edit" if existing else "sites.create")
    if p.traefik == "scoped":
        _scoped_checks(ctx, body, items, scope_items(p, [dict(i) for i in items]))   # ces contrôles couvrent aussi la propriété du service
    elif body.service is not None:
        ctx.need("services.edit" if ("services", sname) in names else "services.create")   # « tout Traefik » ne dispense pas du droit sur les services : sinon sites.create réécrirait le service d'un autre site
    file = (existing["file"] if existing else None) or body.file
    if not file:
        file = suggest_file(items, store.list_dirs(), "http", "routers", body.name)
    if p.traefik == "scoped" and not existing and file in {f["path"] for f in store.list_files()}:
        raise HTTPException(409, "Un fichier de ce nom existe déjà : choisissez un autre nom")
    if body.service is not None:
        store.upsert("http", "services", sname, body.service, file=file if ("services", sname) not in names else None)
    store.upsert("http", "routers", body.name, body.router, file=file, old_name=body.oldName if body.oldName != body.name else None)
    ctx.audit("traefik.site.save", f"{body.name} -> {file}")
    return {"file": file}


class SiteDel(BaseModel):
    name: str
    deleteService: bool = True


@router.post("/traefik/site/delete")
def delete_site(body: SiteDel, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("sites.delete")
    p, store = ctx.p, ctx.store
    items, _ = store.items()
    visible = scope_items(p, [dict(i) for i in items])
    r = next((i for i in visible if i["id"] == f"http/routers/{body.name}"), None)
    if not r or not r["editable"]:
        raise HTTPException(403, "Suppression non autorisée")
    svc = (r["spec"] or {}).get("service")
    store.delete_item("http", "routers", body.name)
    if body.deleteService and svc:
        users = [i for i in items if i["proto"] == "http" and i["kind"] == "routers" and i["name"] != body.name and (i["spec"] or {}).get("service") == svc]
        s = next((i for i in visible if i["id"] == f"http/services/{svc}"), None)
        if not users and s and s["editable"]:
            store.delete_item("http", "services", svc)
    ctx.audit("traefik.site.delete", body.name)
    return {"ok": True}


# ------------------------------------------------------------------ éditeur complet (administrateurs)
class ItemBody(BaseModel):
    proto: str
    kind: str
    name: str
    spec: dict
    file: str | None = None
    oldName: str | None = None


@router.put("/traefik/item")
def put_item(body: ItemBody, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("traefik.all")
    items, _ = ctx.store.items()
    exists = any(i["id"] == f"{body.proto}/{body.kind}/{body.oldName or body.name}" for i in items)
    ctx.need(capmod.prefix_of(body.proto, body.kind) + (".edit" if exists else ".create"))
    target = body.file
    if not target and not exists:
        target = suggest_file(items, ctx.store.list_dirs(), body.proto, body.kind, body.name)
    file = ctx.store.upsert(body.proto, body.kind, body.name, body.spec, target, body.oldName)
    ctx.audit("traefik.item.save", f"{body.proto}/{body.kind}/{body.name} -> {file}")
    return {"file": file}


@router.delete("/traefik/item")
def del_item(proto: str, kind: str, name: str, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("traefik.all")
    ctx.need(capmod.prefix_of(proto, kind) + ".delete")
    file = ctx.store.delete_item(proto, kind, name)
    ctx.audit("traefik.item.delete", f"{proto}/{kind}/{name} ({file})")
    return {"file": file}


class FileBody(BaseModel):
    path: str
    content: str
    create: bool = False


@router.get("/traefik/file")
def get_file(path: str, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("files.view")
    return {"path": path, "content": ctx.store.read(path)}


@router.put("/traefik/file")
def put_file(body: FileBody, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("files.create" if body.create else "files.edit")
    ctx.store.write(body.path, body.content, create=body.create)
    ctx.audit("traefik.file.create" if body.create else "traefik.file.save", body.path)
    return {"ok": True}


@router.delete("/traefik/file")
def del_file(path: str, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("files.delete")
    ctx.store.delete(path)
    ctx.audit("traefik.file.delete", path)
    return {"ok": True}


class MoveBody(BaseModel):
    src: str
    dst: str


@router.post("/traefik/file/move")
def move_file(body: MoveBody, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("files.move")
    ctx.store.move(body.src, body.dst)
    ctx.audit("traefik.file.move", f"{body.src} -> {body.dst}")
    return {"ok": True}


@router.get("/traefik/placement")
def placement(proto: str, kind: str, name: str = "", ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("traefik.all")
    items, _ = ctx.store.items()
    return {"file": suggest_file(items, ctx.store.list_dirs(), proto, kind, name)}


def _empty_files(store, items: list[dict], errors: dict[str, str]) -> list[str]:
    """Fichiers actifs sans aucun contenu (ni bloc, ni autre clé YAML) : des restes inutiles."""
    with_items = {i["file"] for i in items}
    out = []
    for f in store.list_files():
        if f["disabled"] or f["path"] in with_items or f["path"] in errors or len(out) >= 50:
            continue
        try:
            if not len(store.parse_text(store.read(f["path"]))):
                out.append(f["path"])
        except Exception:  # noqa: BLE001 - un fichier illisible n'est pas « vide »
            continue
    return out


def _empty_dirs(store) -> list[str]:
    """Dossiers sans aucun fichier dessous (seul le plus haut de chaque branche vide est cité : il emporte les autres)."""
    paths = [f["path"] for f in store.list_files()]
    empty = [d for d in store.list_dirs() if not any(p.startswith(d + "/") for p in paths)]
    return [d for d in empty if not any(d.startswith(e + "/") for e in empty)]


@router.get("/traefik/reorganize")
def reorganize_plan(scope: str = "root", layout: str = "sites", sub: bool = False, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("files.reorganize")
    items, errors = ctx.store.items()
    return build_plan(items, ctx.store.list_dirs(), errors, "all" if scope == "all" else "root",
                      layout if layout in ("keep", "split") else "sites", sub,
                      existing=[f["path"] for f in ctx.store.list_files()], empty=_empty_files(ctx.store, items, errors), empty_dirs=_empty_dirs(ctx.store))


class ReorgStep(BaseModel):
    kind: str
    # `from` est un mot réservé de Python
    src: str = Field(alias="from")
    to: str = ""
    ids: list[str] = []


class ReorgBody(BaseModel):
    steps: list[ReorgStep] = Field(min_length=1, max_length=MAX_STEPS)
    clean: bool = True


@router.post("/traefik/reorganize")
def reorganize_apply(body: ReorgBody, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("files.reorganize")
    steps = [{"kind": s.kind, "from": s.src, "to": s.to, "ids": s.ids} for s in body.steps]
    if any(s["kind"] not in ("file", "blocks", "delete", "rmdir") or (s["kind"] == "blocks" and not s["ids"]) or (s["kind"] in ("file", "blocks") and not s["to"]) for s in steps):
        raise HTTPException(422, "Plan de rangement invalide")
    if any(is_managed(s["from"]) or (s["to"] and is_managed(s["to"])) for s in steps):
        raise HTTPException(422, "Les fichiers dont le nom commence par « zz » sont gérés par l'interface : le rangement n'y touche pas")
    done = ctx.store.reorganize(steps, body.clean)
    ctx.audit("traefik.reorganize", f"{done['files']} fichier(s), {done['blocks']} bloc(s), {done.get('deleted', 0)} fichier(s) et {done.get('dirs', 0)} dossier(s) supprimé(s)")
    return done


class DirBody(BaseModel):
    path: str


@router.post("/traefik/dir")
def create_dir(body: DirBody, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("files.create")
    ctx.store.make_dir(body.path)
    ctx.audit("traefik.dir.create", body.path)
    return {"ok": True}


@router.post("/traefik/dir/move")
def move_dir(body: MoveBody, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("files.move")
    ctx.store.move_dir(body.src, body.dst)
    ctx.audit("traefik.dir.move", f"{body.src} -> {body.dst}")
    return {"ok": True}


@router.delete("/traefik/dir")
def delete_dir(path: str, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("files.delete")
    ctx.store.remove_dir(path)
    ctx.audit("traefik.dir.delete", path)
    return {"ok": True}


class ValidateBody(BaseModel):
    content: str


@router.post("/traefik/validate")
def validate(body: ValidateBody, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("files.view")
    try:
        ctx.store.parse_text(body.content)
    except StoreError as e:
        return {"ok": False, "message": str(e), "line": getattr(e, "line", 1), "column": getattr(e, "column", 1)}
    return {"ok": True}


@router.get("/traefik/history")
def file_history(path: str, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("history.view")
    return ctx.store.history(path)


@router.get("/traefik/history/content")
def file_history_content(path: str, id: str, ctx: NodeCtx = Depends(node_traefik)):
    ctx.need("history.view")
    return {"content": ctx.store.history_content(path, id)}


class HtBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


@router.post("/traefik/htpasswd")
def htpasswd(body: HtBody, ctx: NodeCtx = Depends(node_traefik)):
    if not (ctx.can("middlewares.create") or ctx.can("middlewares.edit")):
        ctx.need("middlewares.create")
    if ":" in body.username:
        raise HTTPException(400, "Le nom d'utilisateur ne peut pas contenir ':'")
    h = bcrypt.hashpw(body.password.encode()[:72], bcrypt.gensalt(12)).decode().replace("$2b$", "$2a$", 1)
    return {"entry": f"{body.username}:{h}"}
