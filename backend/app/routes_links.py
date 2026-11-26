"""Administration des liaisons entre hubs (réservée aux super-administrateurs ; chaque action qui engage la sécurité exige un code de double authentification)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import hub_link as links
from . import link_proto as proto
from .admin_nodes import need_second_factor
from .perms import Principal, admin_only

router = APIRouter(prefix="/api/admin/links", tags=["links"])


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except links.LinkError as e:
        raise HTTPException(e.status, str(e))


class Code(BaseModel):
    code: str = Field(default="", max_length=32)   # code de double authentification de l'administrateur


class InviteIn(Code):
    label: str = Field(default="", max_length=80)


class JoinIn(Code):
    url: str = Field(default="", max_length=300)
    invitation: str = Field(min_length=10, max_length=600)   # le code seul, ou « adresse#code » tel que l'autre hub l'affiche
    label: str = Field(default="", max_length=80)


class PatchIn(Code):
    graceHours: int | None = Field(default=None, ge=0, le=proto.MAX_GRACE_HOURS)
    label: str | None = Field(default=None, max_length=80)


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _identity() -> dict:
    me = links.identity()
    return {"hubId": me.hub_id, "name": me.name, "version": links.VERSION, "protocol": proto.PROTOCOL, "minProtocol": proto.MIN_PROTOCOL, "features": list(proto.FEATURES)}


@router.get("")
def overview(p: Principal = Depends(admin_only)):
    return {"identity": _identity(), "links": [links.public(r) for r in links.all_links()], "maxGraceHours": proto.MAX_GRACE_HOURS, "defaultGraceHours": proto.DEFAULT_GRACE_HOURS}


@router.put("/identity")
def set_name(body: NameIn, p: Principal = Depends(admin_only)):
    _wrap(links.rename, body.name)
    return _identity()


@router.post("/invite")
def invite(body: InviteIn, p: Principal = Depends(admin_only)):
    need_second_factor(p, body.code)
    link, code = _wrap(links.invite, p.username, body.label)
    return {"link": link, "code": code}


@router.post("/{lid}/invite/renew")
def renew(lid: str, body: Code, p: Principal = Depends(admin_only)):
    need_second_factor(p, body.code)
    link, code = _wrap(links.renew_invite, lid, p.username)
    return {"link": link, "code": code}


@router.post("/join")
def join(body: JoinIn, p: Principal = Depends(admin_only)):
    need_second_factor(p, body.code)
    url, _, code = body.invitation.strip().partition("#") if "#" in body.invitation else (body.url, "", body.invitation)
    return _wrap(links.join, p.username, url or body.url, code, body.label)


@router.post("/{lid}/confirm")
def confirm(lid: str, body: Code, p: Principal = Depends(admin_only)):
    need_second_factor(p, body.code)
    return _wrap(links.confirm, lid, p.username)


@router.patch("/{lid}")
def patch(lid: str, body: PatchIn, p: Principal = Depends(admin_only)):
    if body.graceHours is not None:
        need_second_factor(p, body.code)
    return _wrap(links.update, lid, p.username, body.graceHours, body.label)


@router.post("/{lid}/revoke")
def revoke(lid: str, body: Code, p: Principal = Depends(admin_only)):
    need_second_factor(p, body.code)
    return _wrap(links.revoke, lid, p.username)


@router.delete("/{lid}")
def delete(lid: str, p: Principal = Depends(admin_only)):
    _wrap(links.delete, lid, p.username)
    return {"ok": True}
