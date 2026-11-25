"""Sauvegardes complètes du hub : créer, lister, télécharger, téléverser, restaurer, supprimer. Réservé aux super-administrateurs de la plateforme.

Garde-fous : le téléchargement et la restauration exigent un code de double authentification (une sauvegarde contient tout : utilisateurs, secrets chiffrés, configuration) ;
la restauration fait d'abord une sauvegarde de l'état actuel, demande de retaper le nom de l'archive, et ne remplace rien avant le redémarrage."""
import base64
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import backup
from .admin_nodes import need_second_factor
from .audit import audit
from .config import env
from .perms import Principal, admin_only
from .traefik_store import StoreError

router = APIRouter(prefix="/api/admin/backups", tags=["backups"])
TICKET_TTL = 60
_tickets: dict[str, dict] = {}


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except StoreError as e:
        if getattr(e, "code", ""):
            raise HTTPException(e.status, {"message": str(e), "code": e.code})
        raise HTTPException(e.status, str(e))


@router.get("")
def list_backups(p: Principal = Depends(admin_only)):
    sizes = {c: backup.estimate([c]) for c in backup.COMPONENTS}
    return {"backups": backup.listing(), "free": backup.disk_free(), "restartPolicy": backup.restart_policy(), "hasEnvKey": bool(env("ENCRYPTION_KEY")),
            "components": [{"id": c, "label": l, "bytes": sizes[c]} for c, l in backup.COMPONENTS.items()]}


class CreateBody(BaseModel):
    components: list[str] = Field(min_length=1, max_length=10)
    label: str = Field(default="", max_length=80)
    passphrase: str = Field(default="", max_length=200)
    includeKey: bool = False
    code: str = Field(default="", max_length=20)   # exigé seulement pour inclure la clé de chiffrement


@router.post("")
def create(body: CreateBody, p: Principal = Depends(admin_only)):
    _wrap(backup.validate, body.components, body.passphrase, body.includeKey)
    if body.includeKey:
        _wrap(need_second_factor, p, body.code)
    jid = _wrap(backup.start_job, backup.create, body.components, p.username, body.label, body.passphrase, body.includeKey)
    audit(p.username, "admin.backup.create", f"{', '.join(body.components)}{' · protégée par mot de passe' if body.passphrase else ''}{' · avec la clé' if body.includeKey else ''}")
    return {"job": jid}


@router.get("/jobs/{jid}")
def job(jid: str, p: Principal = Depends(admin_only)):
    return _wrap(backup.job, jid)


class UploadBody(BaseModel):
    filename: str = Field(max_length=200)
    b64: str = Field(default="", max_length=3_000_000)   # un morceau de 2 Mo (≈ 2,8 Mo en base64) : l'API refuse les corps de plus de 4 Mo
    offset: int = Field(default=0, ge=0)
    last: bool = False
    abort: bool = False


@router.post("/upload")
def upload(body: UploadBody, p: Principal = Depends(admin_only)):
    if body.abort:
        backup.abort_upload(body.filename)
        return {"ok": True}
    try:
        data = base64.b64decode(body.b64, validate=True)
    except ValueError:
        raise HTTPException(422, "Morceau illisible")
    r = _wrap(backup.upload_chunk, body.filename, body.offset, data, body.last, p.username)
    if r.get("done"):
        audit(p.username, "admin.backup.upload", r["name"])
    return r


class TicketBody(BaseModel):
    code: str = Field(max_length=20)


@router.post("/{name}/ticket")
def ticket(name: str, body: TicketBody, p: Principal = Depends(admin_only)):
    """Autorise UN téléchargement (60 secondes) après un code de double authentification : le navigateur télécharge ensuite directement le fichier, sans le charger en mémoire."""
    _wrap(need_second_factor, p, body.code)
    _wrap(backup.path_of, name)
    now = time.time()
    for k in [k for k, t in _tickets.items() if t["exp"] < now]:
        del _tickets[k]
    t = secrets.token_urlsafe(24)
    _tickets[t] = {"name": name, "uid": p.id, "exp": now + TICKET_TTL}
    audit(p.username, "admin.backup.download", name)
    return {"ticket": t}


@router.get("/download")
def download(ticket: str):
    t = _tickets.pop(ticket, None)   # à usage unique
    if not t or t["exp"] < time.time():
        raise HTTPException(403, "Lien de téléchargement expiré : recommencez")
    path = _wrap(backup.path_of, t["name"])
    return FileResponse(path, filename=t["name"], media_type="application/octet-stream")


class InspectBody(BaseModel):
    passphrase: str = Field(default="", max_length=200)


@router.post("/{name}/inspect")
def inspect(name: str, body: InspectBody, p: Principal = Depends(admin_only)):
    return _wrap(backup.inspect, name, body.passphrase)


class RestoreBody(BaseModel):
    code: str = Field(max_length=20)
    passphrase: str = Field(default="", max_length=200)
    components: list[str] = Field(min_length=1, max_length=10)
    confirm: str = Field(max_length=200)   # le nom de l'archive, retapé


def _restore(name: str, passphrase: str, components: list[str], user: str) -> dict:
    backup.create(list(backup.COMPONENTS), user, label="avant-restauration", kind="avant restauration")   # filet de sécurité : l'état actuel, restaurable comme n'importe quelle sauvegarde
    plan = backup.stage_restore(name, passphrase, components, user)
    backup.schedule_restart()
    return plan


@router.post("/{name}/restore")
def restore(name: str, body: RestoreBody, p: Principal = Depends(admin_only)):
    _wrap(need_second_factor, p, body.code)
    if body.confirm.strip() != name:
        raise HTTPException(422, "Retapez exactement le nom de la sauvegarde pour confirmer")
    _wrap(backup.path_of, name)
    audit(p.username, "admin.backup.restore", f"{name} ({', '.join(body.components)})")
    return {"job": _wrap(backup.start_job, _restore, name, body.passphrase, body.components, p.username)}


@router.delete("/{name}")
def delete(name: str, p: Principal = Depends(admin_only)):
    _wrap(backup.delete, name)
    audit(p.username, "admin.backup.delete", name)
    return {"ok": True}
