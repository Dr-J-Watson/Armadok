"""Sauvegardes d'un nœud : créer, lister, télécharger, téléverser, comparer, restaurer fichier par fichier. Pour les responsables du nœud (droits « node.backup » et « node.restore »).

Le nœud visé est celui de l'en-tête X-Node, comme partout. Si le propriétaire du nœud exige la double authentification pour ces droits (onglet Sécurité), ils n'existent
pas pour qui ne l'a pas activée : le contrôle est celui des droits, pas un code à chaque action. Un super-administrateur n'a aucun accès implicite aux nœuds des autres."""
import base64
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import backup, node_backup
from .nodes import NodeCtx, node_ctx
from .traefik_store import StoreError

router = APIRouter(prefix="/api/node-backups", tags=["node-backups"])
TICKET_TTL = 60
_tickets: dict[str, dict] = {}


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except StoreError as e:
        raise HTTPException(e.status, str(e))


def _need(ctx: NodeCtx, cap: str) -> NodeCtx:
    ctx.need(cap)
    return ctx


def can_backup(ctx: NodeCtx = Depends(node_ctx)) -> NodeCtx:
    return _need(ctx, "node.backup")


def can_restore(ctx: NodeCtx = Depends(node_ctx)) -> NodeCtx:
    return _need(ctx, "node.restore")


@router.get("")
def list_backups(ctx: NodeCtx = Depends(can_backup)):
    m = _wrap(node_backup.machine, ctx)
    return {"backups": node_backup.listing(ctx.id), "online": m["online"], "available": m["components"], "canRestore": ctx.can("node.restore"), "max": node_backup.MAX_PER_NODE,
            "free": backup._free(node_backup.store(ctx.id)), "node": {"id": ctx.id, "name": ctx.node["name"], "kind": ctx.node["kind"]}}


class CreateBody(BaseModel):
    components: list[str] = Field(min_length=1, max_length=10)
    label: str = Field(default="", max_length=80)
    passphrase: str = Field(default="", max_length=200)


@router.post("")
def create(body: CreateBody, ctx: NodeCtx = Depends(can_backup)):
    if body.passphrase and len(body.passphrase) < 8:
        raise HTTPException(422, "Le mot de passe de la sauvegarde doit faire 8 caractères au moins")
    jid = _wrap(node_backup.start_job, ctx.id, node_backup.create, ctx, body.components, ctx.p.username, body.label, body.passphrase)
    ctx.audit("node.backup.create", f"{', '.join(body.components)}{' · protégée par mot de passe' if body.passphrase else ''}")
    return {"job": jid}


@router.get("/jobs/{jid}")
def job(jid: str, ctx: NodeCtx = Depends(can_backup)):
    return _wrap(node_backup.job, ctx.id, jid)


class UploadBody(BaseModel):
    filename: str = Field(max_length=200)
    b64: str = Field(default="", max_length=3_000_000)
    offset: int = Field(default=0, ge=0)
    last: bool = False
    abort: bool = False


@router.post("/upload")
def upload(body: UploadBody, ctx: NodeCtx = Depends(can_backup)):
    d = node_backup.store(ctx.id)
    if body.abort:
        backup.abort_upload(body.filename, d)
        return {"ok": True}
    if body.offset == 0 and len(node_backup.listing(ctx.id)) >= node_backup.MAX_PER_NODE:
        raise HTTPException(409, f"Ce nœud a déjà {node_backup.MAX_PER_NODE} sauvegardes : supprimez-en une avant d'en téléverser")
    try:
        data = base64.b64decode(body.b64, validate=True)
    except ValueError:
        raise HTTPException(422, "Morceau illisible")
    r = _wrap(backup.upload_chunk, body.filename, body.offset, data, body.last, ctx.p.username, d, "node")
    if r.get("done"):
        ctx.audit("node.backup.upload", r["name"])
    return r


@router.post("/{name}/ticket")
def ticket(name: str, ctx: NodeCtx = Depends(can_backup)):
    """Autorise UN téléchargement (60 secondes) : le navigateur télécharge ensuite directement le fichier, sans le charger en mémoire."""
    _wrap(backup.path_of, name, node_backup.store(ctx.id))
    now = time.time()
    for k in [k for k, t in _tickets.items() if t["exp"] < now]:
        del _tickets[k]
    t = secrets.token_urlsafe(24)
    _tickets[t] = {"name": name, "node": ctx.id, "uid": ctx.p.id, "exp": now + TICKET_TTL}
    ctx.audit("node.backup.download", name)
    return {"ticket": t}


@router.get("/download")
def download(ticket: str):
    t = _tickets.pop(ticket, None)   # à usage unique, lié à un nœud
    if not t or t["exp"] < time.time():
        raise HTTPException(403, "Lien de téléchargement expiré : recommencez")
    path = _wrap(backup.path_of, t["name"], node_backup.store(t["node"]))
    return FileResponse(path, filename=t["name"], media_type="application/octet-stream")


class PassBody(BaseModel):
    passphrase: str = Field(default="", max_length=200)


@router.post("/{name}/inspect")
def inspect(name: str, body: PassBody, ctx: NodeCtx = Depends(can_backup)):
    return _wrap(node_backup.inspect, ctx, name, body.passphrase)


@router.post("/{name}/diff")
def diff(name: str, body: PassBody, ctx: NodeCtx = Depends(can_backup)):
    return _wrap(node_backup.diff, ctx, name, body.passphrase)


class FileDiffBody(BaseModel):
    passphrase: str = Field(default="", max_length=200)
    component: str = Field(max_length=40)
    rel: str = Field(max_length=400)


@router.post("/{name}/file-diff")
def file_diff(name: str, body: FileDiffBody, ctx: NodeCtx = Depends(can_backup)):
    return _wrap(node_backup.file_diff, ctx, name, body.passphrase, body.component, body.rel)


class Item(BaseModel):
    component: str = Field(max_length=40)
    rel: str = Field(max_length=400)
    action: str = Field(pattern="^(restore|delete)$")


class RestoreBody(BaseModel):
    passphrase: str = Field(default="", max_length=200)
    items: list[Item] = Field(min_length=1, max_length=node_backup.MAX_ITEMS)
    confirm: str = Field(max_length=200)   # le nom de la sauvegarde, retapé


@router.post("/{name}/restore")
def restore(name: str, body: RestoreBody, ctx: NodeCtx = Depends(can_restore)):
    if body.confirm.strip() != name:
        raise HTTPException(422, "Retapez exactement le nom de la sauvegarde pour confirmer")
    _wrap(backup.path_of, name, node_backup.store(ctx.id))
    jid = _wrap(node_backup.start_job, ctx.id, node_backup.restore, ctx, name, body.passphrase, [i.model_dump() for i in body.items], ctx.p.username)
    ctx.audit("node.backup.restore", f"{name} : {sum(i.action == 'restore' for i in body.items)} fichier(s) restauré(s), {sum(i.action == 'delete' for i in body.items)} retiré(s)")
    return {"job": jid}


@router.delete("/{name}")
def delete(name: str, ctx: NodeCtx = Depends(can_backup)):
    _wrap(backup.delete, name, node_backup.store(ctx.id))
    ctx.audit("node.backup.delete", name)
    return {"ok": True}
