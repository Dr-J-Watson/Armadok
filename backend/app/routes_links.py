"""Administration des liaisons entre hubs (réservée aux super-administrateurs ; chaque action qui engage la sécurité exige un code de double authentification)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import hub_link as links
from . import link_guests as guests
from . import db
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
    url: str = Field(default="", max_length=300)   # adresse de l'interface de ce hub, annoncée à l'autre pour ses liens (facultative)


class JoinIn(Code):
    url: str = Field(default="", max_length=300)
    invitation: str = Field(min_length=10, max_length=600)   # le code seul, ou « adresse#code » tel que l'autre hub l'affiche
    label: str = Field(default="", max_length=80)


class PatchIn(Code):
    graceHours: int | None = Field(default=None, ge=0, le=proto.MAX_GRACE_HOURS)
    label: str | None = Field(default=None, max_length=80)
    hostEnabled: bool | None = None      # ce hub accueille les équipes de l'autre (comptes invités)
    directEnabled: bool | None = None    # les invités de l'autre hub peuvent définir un mot de passe propre à ce hub


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    publicUrl: str | None = Field(default=None, max_length=300)   # adresse de l'interface de CE hub (annoncée à l'autre pour ses liens)


class TeamsIn(Code):
    groupIds: list[int] = Field(max_length=guests.MAX_TEAMS)


class BlockIn(Code):
    blocked: bool


def _identity() -> dict:
    me = links.identity()
    return {"hubId": me.hub_id, "name": me.name, "publicUrl": links.public_url(), "version": links.VERSION, "protocol": proto.PROTOCOL, "minProtocol": proto.MIN_PROTOCOL, "features": list(proto.FEATURES)}


@router.get("")
def overview(p: Principal = Depends(admin_only)):
    return {"identity": _identity(), "links": [links.public(r) for r in links.all_links()], "maxGraceHours": proto.MAX_GRACE_HOURS, "defaultGraceHours": proto.DEFAULT_GRACE_HOURS}


@router.get("/guests")
def all_guests(p: Principal = Depends(admin_only)):
    """Tous les invités des hubs liés, par liaison : ce que la page « Équipes » montre à côté des comptes locaux."""
    out = []
    for r in links.all_links():
        gs = guests.guests_of(r["id"])
        if gs:
            out.append({"linkId": r["id"], "hub": r["peer_name"], "guests": gs})
    return out


@router.put("/identity")
def set_name(body: NameIn, p: Principal = Depends(admin_only)):
    _wrap(links.rename, body.name)
    if body.publicUrl is not None:
        _wrap(links.set_public_url, body.publicUrl)
    return _identity()


@router.post("/invite")
def invite(body: InviteIn, p: Principal = Depends(admin_only)):
    need_second_factor(p, body.code)
    link, code = _wrap(links.invite, p.username, body.label)
    if body.url and not links.public_url():
        try:
            links.set_public_url(body.url)
        except links.LinkError:
            pass
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
    if body.graceHours is not None or body.hostEnabled is not None or body.directEnabled is not None:   # ce qui engage la sécurité exige un code
        need_second_factor(p, body.code)
    return _wrap(links.update, lid, p.username, body.graceHours, body.label, body.hostEnabled, body.directEnabled)


@router.get("/{lid}/teams")
def teams(lid: str, p: Principal = Depends(admin_only)):
    r = links.get(lid)
    if not r:
        raise HTTPException(404, "Liaison introuvable")
    shared = set(guests.shared_group_ids(lid))
    mine = [{"id": g["id"], "name": g["name"], "members": db.one("SELECT COUNT(*) c FROM group_members m JOIN users u ON u.id=m.user_id WHERE m.group_id=? AND u.source<>'guest'", (g["id"],))["c"],
             "shared": g["id"] in shared} for g in db.rows("SELECT id, name FROM groups WHERE link_id='' ORDER BY name")]
    received = [{"id": g["id"], "name": g["name"], "members": [m["username"] for m in db.rows("SELECT u.username FROM group_members x JOIN users u ON u.id=x.user_id WHERE x.group_id=? ORDER BY u.username", (g["id"],))],
                 "grants": db.one("SELECT COUNT(*) c FROM grants WHERE subject_type='group' AND subject_id=?", (g["id"],))["c"]} for g in db.rows("SELECT id, name FROM groups WHERE link_id=? ORDER BY name", (lid,))]
    return {"mine": mine, "received": received, "peerHosting": bool(r["peer_hosting"]), "hostEnabled": bool(r["host_enabled"])}


@router.put("/{lid}/teams")
def set_teams(lid: str, body: TeamsIn, p: Principal = Depends(admin_only)):
    need_second_factor(p, body.code)
    return {"shared": _wrap(guests.set_shared_groups, lid, body.groupIds, p.username)}


@router.get("/{lid}/guests")
def guest_list(lid: str, p: Principal = Depends(admin_only)):
    if not links.get(lid):
        raise HTTPException(404, "Liaison introuvable")
    return guests.guests_of(lid)


@router.post("/{lid}/guests/{uid}/block")
def block(lid: str, uid: int, body: BlockIn, p: Principal = Depends(admin_only)):
    if not body.blocked:   # rendre l'accès à quelqu'un qu'on avait écarté : on s'assure que c'est bien l'administrateur
        need_second_factor(p, body.code)
    return _wrap(guests.set_blocked, lid, uid, body.blocked, p.username)


@router.post("/{lid}/revoke")
def revoke(lid: str, body: Code, p: Principal = Depends(admin_only)):
    need_second_factor(p, body.code)
    return _wrap(links.revoke, lid, p.username)


@router.delete("/{lid}")
def delete(lid: str, p: Principal = Depends(admin_only)):
    _wrap(links.delete, lid, p.username)
    return {"ok": True}
