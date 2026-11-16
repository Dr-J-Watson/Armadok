"""Page « Droits » : rôles (ensembles de droits), groupes et attributions. Voir rbac.py et caps.py pour le modèle."""
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import caps as capmod
from . import db, events, rbac
from .audit import audit
from .auth import current_user
from .nodes import ROLE_LABEL, NodeCtx, _ctx_by_id, accessible_nodes
from .perms import Principal

router = APIRouter(prefix="/api/rights")


# ------------------------------------------------------------------ ce que la personne peut administrer
def _admin_ctxs(p: Principal) -> list[NodeCtx]:
    return [c for c in accessible_nodes(p) if c.can("node.members.edit")]


def _need_admin(p: Principal) -> list[NodeCtx]:
    ctxs = _admin_ctxs(p)
    if not ctxs and not p.is_admin:
        raise HTTPException(403, "Vous n'administrez aucun nœud : pas de droits à gérer ici")
    return ctxs


def _ceiling(p: Principal, ctxs: list[NodeCtx]) -> frozenset:
    """Droits qu'on peut mettre dans un rôle : jamais plus que ce qu'on a (réunion sur les nœuds qu'on administre)."""
    out: set[str] = set()
    for c in ctxs:
        out |= c.caps
    return frozenset(out) - capmod.OWNER_ONLY


def _visible_role(p: Principal, rid: int):
    r = db.one("SELECT * FROM roles WHERE id=?", (rid,))
    if not r or not (r["builtin"] or r["owner_id"] == p.id or p.is_admin):
        raise HTTPException(404, "Rôle introuvable")
    return r


def _role_out(r) -> dict:
    caps_ = sorted(rbac.role_caps(r))
    used = db.one("SELECT COUNT(*) c FROM grants WHERE role_id=?", (r["id"],))["c"]
    owner = db.one("SELECT username FROM users WHERE id=?", (r["owner_id"],)) if r["owner_id"] else None
    return {"id": r["id"], "name": r["name"], "description": r["description"], "caps": caps_, "builtin": r["builtin"],
            "predefined": bool(r["builtin"]), "owner": owner["username"] if owner else None, "uses": used,
            "legacy": r["builtin"] in ROLE_LABEL}


def _notify_users(user_ids) -> None:
    for uid in dict.fromkeys(user_ids):
        events.publish("session", user=uid)
    events.publish("nodes")


def _subject_users(stype: str, sid: int) -> list[int]:
    if stype == "user":
        return [sid]
    return [r["user_id"] for r in db.rows("SELECT user_id FROM group_members WHERE group_id=?", (sid,))]


# ------------------------------------------------------------------ catalogue et rôles
@router.get("/catalog")
def catalog(p: Principal = Depends(current_user)):
    return capmod.catalog()


@router.get("/roles")
def roles(p: Principal = Depends(current_user)):
    rows = db.rows("SELECT * FROM roles ORDER BY builtin IS NULL, id")
    return [_role_out(r) for r in rows if r["builtin"] or r["owner_id"] == p.id or p.is_admin]


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=200)
    caps: list[str] = Field(max_length=100)


def _check_role_body(p: Principal, body: RoleIn, ctxs: list[NodeCtx], keep_id: int | None = None) -> str:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Le nom du rôle est obligatoire")
    dup = db.one("SELECT id FROM roles WHERE name=? AND id<>?", (name, keep_id or 0))
    if dup:
        raise HTTPException(409, "Un rôle porte déjà ce nom")
    unknown = [c for c in body.caps if c not in capmod.CATALOG]
    if unknown:
        raise HTTPException(422, f"Droit inconnu : {unknown[0]}")
    over = set(body.caps) - _ceiling(p, ctxs) if not p.is_admin else set(body.caps) & capmod.OWNER_ONLY
    if over:
        raise HTTPException(403, "Vous ne pouvez pas mettre dans un rôle des droits que vous n'avez pas vous-même : "
                            + ", ".join(capmod.CATALOG[c]["label"] for c in sorted(over)[:3]))
    return json.dumps(sorted(capmod.closure(body.caps)))


@router.post("/roles")
def create_role(body: RoleIn, p: Principal = Depends(current_user)):
    ctxs = _need_admin(p)
    caps_json = _check_role_body(p, body, ctxs)
    rid = db.run("INSERT INTO roles(name,description,caps,owner_id,created_at) VALUES(?,?,?,?,?)",
                 (body.name.strip(), body.description.strip(), caps_json, p.id, time.time()))
    audit(p.username, "rights.role.create", body.name.strip())
    return _role_out(db.one("SELECT * FROM roles WHERE id=?", (rid,)))


@router.put("/roles/{rid}")
def update_role(rid: int, body: RoleIn, p: Principal = Depends(current_user)):
    r = _visible_role(p, rid)
    if r["builtin"]:
        raise HTTPException(400, "Un rôle prédéfini ne se modifie pas : dupliquez-le pour en faire un rôle à vous")
    ctxs = _need_admin(p)
    caps_json = _check_role_body(p, body, ctxs, rid)
    db.run("UPDATE roles SET name=?, description=?, caps=? WHERE id=?", (body.name.strip(), body.description.strip(), caps_json, rid))
    users = [u for g in db.rows("SELECT subject_type, subject_id FROM grants WHERE role_id=?", (rid,)) for u in _subject_users(g["subject_type"], g["subject_id"])]
    _notify_users(users)
    audit(p.username, "rights.role.update", body.name.strip())
    return _role_out(db.one("SELECT * FROM roles WHERE id=?", (rid,)))


@router.post("/roles/{rid}/duplicate")
def duplicate_role(rid: int, p: Principal = Depends(current_user)):
    r = _visible_role(p, rid)
    ctxs = _need_admin(p)
    base, n, name = r["name"], 2, f"{r['name']} (copie)"
    while db.one("SELECT 1 FROM roles WHERE name=?", (name,)):
        name, n = f"{base} (copie {n})", n + 1
    caps_ = sorted(rbac.role_caps(r) - capmod.OWNER_ONLY)
    if not p.is_admin:
        caps_ = sorted(set(caps_) & _ceiling(p, ctxs))   # on ne copie que ce qu'on a soi-même
    new = db.run("INSERT INTO roles(name,description,caps,owner_id,created_at) VALUES(?,?,?,?,?)",
                 (name, r["description"], json.dumps(caps_), p.id, time.time()))
    audit(p.username, "rights.role.create", f"{name} (copie de {r['name']})")
    return _role_out(db.one("SELECT * FROM roles WHERE id=?", (new,)))


@router.delete("/roles/{rid}")
def delete_role(rid: int, p: Principal = Depends(current_user)):
    r = _visible_role(p, rid)
    if r["builtin"]:
        raise HTTPException(400, "Un rôle prédéfini ne se supprime pas")
    if r["owner_id"] != p.id and not p.is_admin:
        raise HTTPException(403, "Ce rôle appartient à quelqu'un d'autre")
    n = db.one("SELECT COUNT(*) c FROM grants WHERE role_id=?", (rid,))["c"]
    if n:
        raise HTTPException(409, f"Ce rôle est attribué {n} fois : retirez d'abord ces attributions")
    db.run("DELETE FROM roles WHERE id=?", (rid,))
    audit(p.username, "rights.role.delete", r["name"])
    return {"ok": True}


# ------------------------------------------------------------------ groupes
def _my_group(p: Principal, gid: int):
    g = db.one("SELECT * FROM groups WHERE id=?", (gid,))
    if not g or not (g["owner_id"] == p.id or p.is_admin):
        raise HTTPException(404, "Groupe introuvable")
    return g


def _group_out(g) -> dict:
    members = db.rows("SELECT u.id, u.username FROM group_members m JOIN users u ON u.id=m.user_id WHERE m.group_id=? ORDER BY u.username", (g["id"],))
    return {"id": g["id"], "name": g["name"], "description": g["description"], "members": [{"id": m["id"], "username": m["username"]} for m in members],
            "grants": db.one("SELECT COUNT(*) c FROM grants WHERE subject_type='group' AND subject_id=?", (g["id"],))["c"]}


@router.get("/groups")
def groups(p: Principal = Depends(current_user)):
    rows = db.rows("SELECT * FROM groups ORDER BY name")
    return [_group_out(g) for g in rows if g["owner_id"] == p.id or p.is_admin]


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=200)


@router.post("/groups")
def create_group(body: GroupIn, p: Principal = Depends(current_user)):
    _need_admin(p)
    if db.one("SELECT 1 FROM groups WHERE name=? AND owner_id=?", (body.name.strip(), p.id)):
        raise HTTPException(409, "Vous avez déjà un groupe de ce nom")
    gid = db.run("INSERT INTO groups(name,description,owner_id,created_at) VALUES(?,?,?,?)", (body.name.strip(), body.description.strip(), p.id, time.time()))
    audit(p.username, "rights.group.create", body.name.strip())
    return _group_out(db.one("SELECT * FROM groups WHERE id=?", (gid,)))


@router.put("/groups/{gid}")
def update_group(gid: int, body: GroupIn, p: Principal = Depends(current_user)):
    g = _my_group(p, gid)
    db.run("UPDATE groups SET name=?, description=? WHERE id=?", (body.name.strip(), body.description.strip(), gid))
    audit(p.username, "rights.group.update", g["name"])
    return _group_out(db.one("SELECT * FROM groups WHERE id=?", (gid,)))


class MembersIn(BaseModel):
    userIds: list[int] = Field(max_length=500)


@router.put("/groups/{gid}/members")
def set_group_members(gid: int, body: MembersIn, p: Principal = Depends(current_user)):
    g = _my_group(p, gid)
    ids = set(body.userIds)
    known = {r["id"] for r in db.rows("SELECT id FROM users WHERE disabled=0")}
    if not ids <= known:
        raise HTTPException(422, "Utilisateur inconnu")
    before = {r["user_id"] for r in db.rows("SELECT user_id FROM group_members WHERE group_id=?", (gid,))}
    db.run("DELETE FROM group_members WHERE group_id=?", (gid,))
    for uid in ids:
        db.run("INSERT INTO group_members(group_id,user_id) VALUES(?,?)", (gid, uid))
    _notify_users(before ^ ids)
    audit(p.username, "rights.group.members", f"{g['name']} : {len(ids)} membre(s)")
    return _group_out(g)


@router.delete("/groups/{gid}")
def delete_group(gid: int, p: Principal = Depends(current_user)):
    g = _my_group(p, gid)
    users = _subject_users("group", gid)
    db.run("DELETE FROM grants WHERE subject_type='group' AND subject_id=?", (gid,))
    db.run("DELETE FROM groups WHERE id=?", (gid,))
    _notify_users(users)
    audit(p.username, "rights.group.delete", g["name"])
    return {"ok": True}


# ------------------------------------------------------------------ attributions
def _grant_out(g, nodes: dict) -> dict:
    subj = (db.one("SELECT username n FROM users WHERE id=?", (g["subject_id"],)) if g["subject_type"] == "user"
            else db.one("SELECT name n FROM groups WHERE id=?", (g["subject_id"],)))
    by = db.one("SELECT username FROM users WHERE id=?", (g["granted_by"],)) if g["granted_by"] else None
    role = db.one("SELECT id, name, builtin FROM roles WHERE id=?", (g["role_id"],))
    return {"id": g["id"], "subjectType": g["subject_type"], "subjectId": g["subject_id"], "subject": subj["n"] if subj else "(supprimé)",
            "roleId": role["id"], "role": role["name"], "node": nodes.get(g["node_id"], g["node_id"]) if g["node_id"] else None, "nodeId": g["node_id"],
            "allMine": g["node_id"] is None, "expiresAt": g["expires_at"], "by": by["username"] if by else None, "createdAt": g["created_at"],
            "expired": bool(g["expires_at"] and g["expires_at"] <= time.time())}


def _grants_visible(p: Principal) -> list:
    admin_nodes = {c.id for c in _admin_ctxs(p)}
    rows = db.rows("SELECT * FROM grants ORDER BY created_at DESC")
    return [g for g in rows if g["granted_by"] == p.id or (g["node_id"] and g["node_id"] in admin_nodes) or p.is_admin]


@router.get("/grants")
def grants(p: Principal = Depends(current_user)):
    _need_admin(p)
    names = {r["id"]: r["name"] for r in db.rows("SELECT id, name FROM nodes WHERE deleted_at IS NULL")}
    out = [_grant_out(g, names) for g in _grants_visible(p)]
    # rôles historiques du nœud (membres) : affichés avec les attributions, gérés depuis la page du nœud
    for c in _admin_ctxs(p):
        for m in db.rows("SELECT m.user_id, m.role, m.expires_at, m.created_at, u.username FROM node_members m JOIN users u ON u.id=m.user_id WHERE m.node_id=?", (c.id,)):
            out.append({"id": f"m:{c.id}:{m['user_id']}", "subjectType": "user", "subjectId": m["user_id"], "subject": m["username"], "roleId": None,
                        "role": ROLE_LABEL[m["role"]], "node": c.node["name"], "nodeId": c.id, "allMine": False, "expiresAt": m["expires_at"], "by": None,
                        "createdAt": m["created_at"], "expired": False, "membership": True})
    return out


class GrantIn(BaseModel):
    subjectType: str
    subjectId: int
    roleId: int
    nodeId: str | None = None     # None : tous les nœuds dont l'attributeur est propriétaire
    expiresAt: float | None = None


@router.post("/grants")
def create_grant(body: GrantIn, p: Principal = Depends(current_user)):
    ctxs = _need_admin(p)
    if body.subjectType not in ("user", "group"):
        raise HTTPException(422, "Type de destinataire invalide")
    if body.subjectType == "user":
        u = db.one("SELECT id, disabled FROM users WHERE id=?", (body.subjectId,))
        if not u or u["disabled"]:
            raise HTTPException(404, "Utilisateur introuvable")
    else:
        _my_group(p, body.subjectId)
    role = _visible_role(p, body.roleId)
    if role["builtin"] == "owner":
        raise HTTPException(400, "Le rôle de propriétaire ne s'attribue pas : il se transfère depuis la page du nœud")
    if body.expiresAt is not None and body.expiresAt <= time.time():
        raise HTTPException(422, "La date de fin est déjà passée")
    rcaps = rbac.role_caps(role)
    if body.nodeId:
        ctx = _ctx_by_id(p, body.nodeId)
        ctx.need("node.members.edit")
        targets = [ctx]
    else:
        owned = [c for c in accessible_nodes(p) if c.role == "owner"]
        if not owned:
            raise HTTPException(403, "« Tous mes nœuds » suppose d'être propriétaire d'au moins un nœud")
        targets = owned
    for c in targets:   # plafond : on ne donne jamais plus que ce qu'on a sur ce nœud
        over = rcaps - c.caps
        if over:
            raise HTTPException(403, f"Sur « {c.node['name']} », vous ne pouvez pas donner : " + ", ".join(capmod.CATALOG[k]["label"] for k in sorted(over)[:3]))
    if body.subjectType == "user":
        for c in targets:
            if db.one("SELECT 1 FROM node_members WHERE node_id=? AND user_id=? AND role='owner'", (c.id, body.subjectId)):
                raise HTTPException(400, "Cette personne est déjà propriétaire du nœud")
    gid = db.run("INSERT INTO grants(subject_type,subject_id,role_id,node_id,granted_by,expires_at,created_at) VALUES(?,?,?,?,?,?,?)",
                 (body.subjectType, body.subjectId, role["id"], body.nodeId, p.id, body.expiresAt, time.time()))
    _notify_users(_subject_users(body.subjectType, body.subjectId))
    audit(p.username, "rights.grant.add", f"{body.subjectType}#{body.subjectId} ← {role['name']} sur {body.nodeId or 'tous mes nœuds'}", node=body.nodeId)
    return {"id": gid}


@router.delete("/grants/{gid}")
def delete_grant(gid: str, p: Principal = Depends(current_user)):
    if gid.startswith("m:"):   # rôle historique d'un nœud
        _, nid, uid = gid.split(":")
        ctx = _ctx_by_id(p, nid)
        ctx.need("node.members.remove")
        m = db.one("SELECT role FROM node_members WHERE node_id=? AND user_id=?", (nid, int(uid)))
        if not m or m["role"] == "owner":
            raise HTTPException(400, "Ce membre ne peut pas être retiré ici")
        db.run("DELETE FROM node_members WHERE node_id=? AND user_id=?", (nid, int(uid)))
        _notify_users([int(uid)])
        ctx.audit("node.member.remove", f"user#{uid} (page Droits)")
        return {"ok": True}
    g = db.one("SELECT * FROM grants WHERE id=?", (int(gid),))
    if not g or g not in _grants_visible(p):
        raise HTTPException(404, "Attribution introuvable")
    if g["node_id"]:
        ctx = _ctx_by_id(p, g["node_id"])
        if g["granted_by"] != p.id:
            ctx.need("node.members.remove")
    elif g["granted_by"] != p.id and not p.is_admin:
        raise HTTPException(403, "Cette attribution « tous mes nœuds » appartient à quelqu'un d'autre")
    db.run("DELETE FROM grants WHERE id=?", (g["id"],))
    _notify_users(_subject_users(g["subject_type"], g["subject_id"]))
    audit(p.username, "rights.grant.remove", f"{g['subject_type']}#{g['subject_id']}", node=g["node_id"])
    return {"ok": True}


# ------------------------------------------------------------------ utilisateurs : qui a quels droits
@router.get("/users")
def users(p: Principal = Depends(current_user)):
    ctxs = _need_admin(p)
    node_ids = [c.id for c in ctxs]
    seen: dict[int, dict] = {}

    def add(uid, username, role, node, source):
        u = seen.setdefault(uid, {"id": uid, "username": username, "platformRole": role, "access": []})
        entry = {"node": node, "source": source}
        if entry not in u["access"]:   # propriétaire à la fois comme membre et comme administrateur de la plateforme : une seule ligne
            u["access"].append(entry)
    for c in ctxs:
        for m in db.rows("SELECT m.user_id, m.role, u.username, u.role AS prole FROM node_members m JOIN users u ON u.id=m.user_id WHERE m.node_id=?", (c.id,)):
            add(m["user_id"], m["username"], m["prole"], c.node["name"], ROLE_LABEL[m["role"]])
    if db.one("SELECT 1 FROM nodes WHERE kind='local'"):   # administrateurs de la plateforme : propriétaires implicites du nœud local
        for c in ctxs:
            if c.node["kind"] == "local":
                for a in db.rows("SELECT id, username, role FROM users WHERE role='admin' AND disabled=0"):
                    add(a["id"], a["username"], a["role"], c.node["name"], ROLE_LABEL["owner"])
    for g in db.rows("SELECT * FROM grants"):
        if g["node_id"] and g["node_id"] not in node_ids and g["granted_by"] != p.id and not p.is_admin:
            continue
        role = db.one("SELECT name FROM roles WHERE id=?", (g["role_id"],))["name"]
        node = db.one("SELECT name FROM nodes WHERE id=?", (g["node_id"],)) if g["node_id"] else None
        label = node["name"] if node else "tous mes nœuds"
        via = ""
        if g["subject_type"] == "group":
            via = " (groupe " + (db.one("SELECT name FROM groups WHERE id=?", (g["subject_id"],)) or {"name": "?"})["name"] + ")"
        for uid in _subject_users(g["subject_type"], g["subject_id"]):
            u = db.one("SELECT username, role FROM users WHERE id=?", (uid,))
            if u:
                add(uid, u["username"], u["role"], label, role + via)
    return sorted(seen.values(), key=lambda u: u["username"].lower())


@router.get("/users/{uid}")
def user_detail(uid: int, p: Principal = Depends(current_user)):
    """Droits effectifs d'une personne sur chacun des nœuds que l'on administre, avec l'origine de chaque droit."""
    ctxs = _need_admin(p)
    u = db.one("SELECT id, username, role, disabled FROM users WHERE id=?", (uid,))
    if not u:
        raise HTTPException(404, "Utilisateur introuvable")
    target = Principal(u["id"], u["username"], u["role"], False, "none")
    out = []
    for c in ctxs:
        row = db.one("SELECT * FROM nodes WHERE id=?", (c.id,))
        from .nodes import _make_ctx
        tc = _make_ctx(target, row)
        if not tc:
            continue
        sources = [{"role": ROLE_LABEL.get(tc.role, tc.role) if tc.role != "custom" else None, "kind": "node"}] if tc.role != "custom" else []
        for g in rbac.grants_for(uid, row):
            via = None
            if g["subject_type"] == "group":
                via = (db.one("SELECT name FROM groups WHERE id=?", (g["subject_id"],)) or {"name": "?"})["name"]
            sources.append({"role": g["role_name"], "kind": "grant", "via": via, "expiresAt": g["expires_at"], "id": g["id"]})
        out.append({"node": c.node["name"], "nodeId": c.id, "role": tc.role, "caps": sorted(tc.caps), "sources": sources})
    return {"id": u["id"], "username": u["username"], "disabled": bool(u["disabled"]), "nodes": out}
