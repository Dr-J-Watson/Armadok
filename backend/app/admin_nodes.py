"""Super administrateur : vue de tous les nœuds, accès d'urgence (« break-glass ») et actions rapides.

Garde-fous :
- aucun accès implicite : entrer sur le nœud d'un autre exige un motif, une session limitée dans le temps et un code de
  double authentification (qui doit donc être activée) ;
- le propriétaire en est toujours informé (avis non supprimable, avec la liste des actions faites pendant la session) ;
- les actions rapides exigent aussi un code 2FA, annoncent leur impact avant d'agir et sont journalisées."""
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import audit as audit_log
from . import auth, db, events, worker_link
from .audit import audit
from .perms import Principal, admin_only, current_user
from .traefik_live import forget as forget_live
from .traefik_store import StoreError
from .worker_proto import CLOSE_REVOKED, CLOSE_SUSPENDED

router = APIRouter(prefix="/api")

EMERGENCY_MINUTES = 30
PURGE_AFTER = 30 * 86400  # un nœud supprimé reste récupérable 30 jours


# ------------------------------------------------------------------ outils
def need_second_factor(p: Principal, code: str) -> None:
    """Exige la 2FA activée ET un code valide (à usage unique). Aucune erreur 401 : le front y verrait une session expirée."""
    row = db.one("SELECT totp_enabled FROM users WHERE id=?", (p.id,))
    if not row or not row["totp_enabled"]:
        e = StoreError("Activez d'abord la double authentification de votre compte (menu → votre nom) : elle est exigée pour "
                       "accéder au nœud d'un autre utilisateur ou agir dessus.", 403)
        e.code = "needs_2fa"  # type: ignore[attr-defined]
        raise e
    key = f"2fa:{p.id}"
    auth._check_lock(key)
    if not auth._check_second_factor(p.id, code or ""):
        auth._fail(key)
        raise StoreError("Code de double authentification invalide", 403)
    auth._attempts.pop(key, None)


def _node(nid: str):
    r = db.one("SELECT * FROM nodes WHERE id=?", (nid,))
    if not r:
        raise HTTPException(404, "Nœud introuvable")
    return r


def _owners(nid: str) -> list:
    return db.rows("SELECT u.id, u.username, u.disabled, u.role, u.source FROM node_members m JOIN users u ON u.id=m.user_id "
                   "WHERE m.node_id=? AND m.role='owner'", (nid,))


def notify(node, user_ids: list[int], kind: str, message: str, session: int | None = None) -> None:
    for uid in dict.fromkeys(user_ids):
        db.run("INSERT INTO node_notices(node_id,node_name,user_id,kind,message,session_id,created_at) VALUES(?,?,?,?,?,?,?)",
               (node["id"], node["name"], uid, kind, message[:1500], session, time.time()))
        events.publish("notices", user=uid)   # temps réel : l'avis s'affiche aussitôt chez le destinataire


def _members(nid: str) -> list[int]:
    return [r["user_id"] for r in db.rows("SELECT user_id FROM node_members WHERE node_id=?", (nid,))]


def purge_expired() -> None:
    for r in db.rows("SELECT id FROM nodes WHERE deleted_at IS NOT NULL AND deleted_at < ?", (time.time() - PURGE_AFTER,)):
        _purge(r["id"])


def _purge(nid: str) -> None:
    worker_link.disconnect(nid, CLOSE_REVOKED)
    db.run("DELETE FROM nodes WHERE id=?", (nid,))
    worker_link._stores.pop(nid, None)
    forget_live(nid)


def _suspend(nid: str) -> None:
    db.run("UPDATE nodes SET status='suspended' WHERE id=?", (nid,))
    worker_link.disconnect(nid, CLOSE_SUSPENDED)
    forget_live(nid)


@router.get("/admin/audit/verify")
def verify_audit(p: Principal = Depends(admin_only)):
    """Contrôle d'intégrité du journal d'audit (chaîne de signatures)."""
    return audit_log.verify()


# ------------------------------------------------------------------ vue d'ensemble
def _row_out(n) -> dict:
    from .nodes import node_out
    out = node_out(n)
    owners = _owners(n["id"])
    now = time.time()
    em = db.one("SELECT a.id, a.expires_at, u.username FROM emergency_sessions a JOIN users u ON u.id=a.admin_id "
                "WHERE a.node_id=? AND a.ended_at IS NULL AND a.expires_at>? ORDER BY a.id DESC LIMIT 1", (n["id"], now))
    return {k: out[k] for k in ("id", "name", "description", "kind", "status", "connection", "lastSeen", "workerVersion")} | {
        "owners": [{"id": o["id"], "username": o["username"], "disabled": bool(o["disabled"])} for o in owners],
        "members": db.one("SELECT COUNT(*) c FROM node_members WHERE node_id=?", (n["id"],))["c"],
        "domains": db.one("SELECT COUNT(*) c FROM node_domains WHERE node_id=?", (n["id"],))["c"],
        "deletedAt": n["deleted_at"], "purgeAt": (n["deleted_at"] + PURGE_AFTER) if n["deleted_at"] else None,
        "emergency": {"id": em["id"], "expires": em["expires_at"]} if em else None,
    }


@router.get("/admin/nodes")
def list_all(p: Principal = Depends(admin_only)):
    purge_expired()
    return [_row_out(n) for n in db.rows("SELECT * FROM nodes ORDER BY kind='local' DESC, name")]


@router.get("/admin/nodes/{nid}/impact")
def impact(nid: str, p: Principal = Depends(admin_only)):
    """Ce qui serait touché par une action : affiché avant toute confirmation."""
    n = _node(nid)
    owners = _owners(nid)
    domains = [r["name"] for r in db.rows("SELECT d.name FROM node_domains nd JOIN domains d ON d.id=nd.domain_id WHERE nd.node_id=? ORDER BY d.name", (nid,))]
    others = []
    for o in owners:
        others += [r["name"] for r in db.rows("SELECT n.name FROM node_members m JOIN nodes n ON n.id=m.node_id WHERE m.user_id=? AND m.role='owner' "
                                              "AND n.id<>? AND n.deleted_at IS NULL", (o["id"], nid))]
    return {**_row_out(n), "domainNames": domains, "otherNodes": others, "memberNames": [r["username"] for r in db.rows(
        "SELECT u.username FROM node_members m JOIN users u ON u.id=m.user_id WHERE m.node_id=? ORDER BY u.username", (nid,))]}


# ------------------------------------------------------------------ accès d'urgence
class EmergencyIn(BaseModel):
    reason: str = Field(min_length=10, max_length=500)
    code: str = Field(max_length=40)


@router.post("/admin/nodes/{nid}/emergency")
def start_emergency(nid: str, body: EmergencyIn, p: Principal = Depends(admin_only)):
    n = _node(nid)
    if n["kind"] == "local" or n["deleted_at"]:
        raise HTTPException(400, "Ce nœud n'a pas besoin d'accès d'urgence")
    if db.one("SELECT 1 FROM node_members WHERE node_id=? AND user_id=?", (nid, p.id)):
        raise HTTPException(400, "Vous êtes déjà membre de ce nœud")
    need_second_factor(p, body.code)
    now = time.time()
    db.run("UPDATE emergency_sessions SET ended_at=? WHERE node_id=? AND admin_id=? AND ended_at IS NULL", (now, nid, p.id))
    sid = db.run("INSERT INTO emergency_sessions(node_id,admin_id,reason,started_at,expires_at) VALUES(?,?,?,?,?)",
                 (nid, p.id, body.reason.strip(), now, now + EMERGENCY_MINUTES * 60))
    owners = [o["id"] for o in _owners(nid)]
    notify(n, owners, "emergency", f"L'administrateur de la plateforme ({p.username}) est intervenu sur votre nœud « {n['name']} ». "
           f"Motif : {body.reason.strip()}", sid)
    audit(p.username, "admin.emergency.start", body.reason.strip(), node=nid, session=sid)
    return {"id": sid, "expires": now + EMERGENCY_MINUTES * 60}


@router.post("/admin/nodes/{nid}/emergency/end")
def end_emergency(nid: str, p: Principal = Depends(admin_only)):
    n = _node(nid)
    s = db.one("SELECT id FROM emergency_sessions WHERE node_id=? AND admin_id=? AND ended_at IS NULL", (nid, p.id))
    if s:
        db.run("UPDATE emergency_sessions SET ended_at=? WHERE id=?", (time.time(), s["id"]))
        audit(p.username, "admin.emergency.end", n["name"], node=nid, session=s["id"])
    return {"ok": True}


# ------------------------------------------------------------------ actions rapides
ACTIONS = {
    "suspend": "Suspendre le nœud",
    "resume": "Reprendre le nœud",
    "revoke_worker": "Couper le worker",
    "ban_owner": "Bannir le propriétaire (ses nœuds sont suspendus)",
    "delete": "Supprimer le nœud (récupérable 30 jours)",
    "delete_and_ban": "Supprimer le nœud et bannir le propriétaire",
    "restore": "Restaurer le nœud supprimé",
    "purge": "Purger définitivement",
    "take_ownership": "Reprendre la propriété du nœud",
    "remove_quota": "Retirer au propriétaire le droit de créer des nœuds",
}


class ActionIn(BaseModel):
    action: str
    code: str = Field(max_length=40)
    note: str = Field(default="", max_length=500)  # message ajouté à l'avis du propriétaire


def _ban(user_id: int, by: str) -> list[str]:
    """Désactive le compte (effet immédiat : il est contrôlé à chaque requête) et suspend les nœuds qu'il possède."""
    db.run("UPDATE users SET disabled=1 WHERE id=?", (user_id,))
    names = []
    for r in db.rows("SELECT n.* FROM node_members m JOIN nodes n ON n.id=m.node_id WHERE m.user_id=? AND m.role='owner' AND n.kind='remote' "
                     "AND n.deleted_at IS NULL", (user_id,)):
        _suspend(r["id"])
        names.append(r["name"])
    return names


@router.post("/admin/nodes/{nid}/actions")
def run_action(nid: str, body: ActionIn, p: Principal = Depends(admin_only)):
    n = _node(nid)
    if body.action not in ACTIONS:
        raise HTTPException(422, "Action inconnue")
    if n["kind"] == "local":
        raise HTTPException(400, "Le nœud de cette machine ne peut pas être suspendu ni supprimé")
    deleted = bool(n["deleted_at"])
    if body.action == "restore" and not deleted:
        raise HTTPException(400, "Ce nœud n'est pas supprimé")
    if body.action == "purge" and not deleted:
        raise HTTPException(400, "Supprimez d'abord le nœud : la purge n'agit que sur un nœud déjà supprimé")
    if deleted and body.action not in ("restore", "purge"):
        raise HTTPException(400, "Ce nœud est supprimé : restaurez-le ou purgez-le")
    owners = _owners(nid)
    if body.action in ("ban_owner", "delete_and_ban", "remove_quota"):
        if not owners:
            raise HTTPException(400, "Ce nœud n'a pas de propriétaire")
        for o in owners:
            if o["role"] == "admin" or o["source"] == "env":
                raise HTTPException(400, f"{o['username']} est administrateur de la plateforme : retirez-lui ce rôle avant")
    need_second_factor(p, body.code)
    note = f" Message : {body.note.strip()}" if body.note.strip() else ""
    owner_ids = [o["id"] for o in owners]
    name = n["name"]
    detail = name
    a = body.action
    if a == "suspend":
        _suspend(nid)
        notify(n, owner_ids + _members(nid), "suspended", f"Votre nœud « {name} » a été suspendu par l'administrateur de la plateforme.{note}")
    elif a == "resume":
        db.run("UPDATE nodes SET status='active' WHERE id=?", (nid,))
        notify(n, owner_ids, "resumed", f"Votre nœud « {name} » est de nouveau actif.{note}")
    elif a == "revoke_worker":
        db.run("DELETE FROM node_workers WHERE node_id=?", (nid,))
        worker_link.disconnect(nid, CLOSE_REVOKED)
        notify(n, owner_ids, "worker_revoked", f"Le worker de votre nœud « {name} » a été coupé par l'administrateur de la plateforme : "
               f"générez un nouveau jeton dans « Connexion » pour le réinstaller.{note}")
    elif a in ("delete", "delete_and_ban"):
        db.run("UPDATE nodes SET deleted_at=?, status='suspended' WHERE id=?", (time.time(), nid))
        worker_link.disconnect(nid, CLOSE_SUSPENDED)
        forget_live(nid)
        notify(n, owner_ids, "deleted", f"Votre nœud « {name} » a été supprimé par l'administrateur de la plateforme.{note}")
        notify(n, [m for m in _members(nid) if m not in owner_ids], "deleted", f"Le nœud « {name} » a été supprimé par l'administrateur de la plateforme.")
        if a == "delete_and_ban":
            others = []
            for o in owners:
                others += _ban(o["id"], p.username)
                notify(n, [o["id"]], "banned", f"Votre compte a été désactivé par l'administrateur de la plateforme.{note}")
            detail += " + propriétaire banni" + (f" (nœuds suspendus : {', '.join(others)})" if others else "")
    elif a == "ban_owner":
        others = []
        for o in owners:
            others += _ban(o["id"], p.username)
            notify(n, [o["id"]], "banned", f"Votre compte a été désactivé par l'administrateur de la plateforme.{note}")
        detail += f" : propriétaire banni (nœuds suspendus : {', '.join(others) or 'aucun'})"
    elif a == "restore":
        db.run("UPDATE nodes SET deleted_at=NULL, status='active' WHERE id=?", (nid,))
        notify(n, owner_ids, "restored", f"Votre nœud « {name} » a été restauré.{note}")
    elif a == "purge":
        audit(p.username, "admin.node.purge", name, node=nid)
        _purge(nid)
        return {"ok": True}
    elif a == "take_ownership":
        for o in owners:
            db.run("UPDATE node_members SET role='viewer' WHERE node_id=? AND user_id=?", (nid, o["id"]))
        db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?, 'owner', ?) "
               "ON CONFLICT(node_id,user_id) DO UPDATE SET role='owner'", (nid, p.id, time.time()))
        notify(n, owner_ids, "ownership_taken", f"L'administrateur de la plateforme a repris la propriété de votre nœud « {name} » : "
               f"vous y avez désormais un rôle de lecteur.{note}")
    elif a == "remove_quota":
        for o in owners:
            db.run("UPDATE users SET max_nodes=0 WHERE id=?", (o["id"],))
            notify(n, [o["id"]], "quota", f"Vous ne pouvez plus créer de nouveaux nœuds (vos nœuds existants ne sont pas touchés).{note}")
    audit(p.username, f"admin.node.{a}", detail, node=nid)
    return {"ok": True}


# ------------------------------------------------------------------ avis aux propriétaires
def _notice_out(r) -> dict:
    actions = []
    if r["session_id"]:
        actions = [{"ts": e["ts"], "action": e["action"], "detail": e["detail"]}
                   for e in reversed(audit_log.tail(300, node=r["node_id"], session=r["session_id"]))
                   if e["action"] != "admin.emergency.start"]
    s = db.one("SELECT started_at, expires_at, ended_at FROM emergency_sessions WHERE id=?", (r["session_id"],)) if r["session_id"] else None
    return {"id": r["id"], "nodeName": r["node_name"], "kind": r["kind"], "message": r["message"], "createdAt": r["created_at"],
            "ackedAt": r["acked_at"], "actions": actions, "session": dict(s) if s else None}


@router.get("/notices")
def my_notices(all: bool = False, p: Principal = Depends(current_user)):
    where = "" if all else "AND acked_at IS NULL"
    return [_notice_out(r) for r in db.rows(f"SELECT * FROM node_notices WHERE user_id=? {where} ORDER BY created_at DESC LIMIT 100", (p.id,))]


@router.post("/notices/ack")
def ack_notices(p: Principal = Depends(current_user)):
    """L'avis peut être acquitté, jamais supprimé : il reste consultable avec ses actions."""
    db.run("UPDATE node_notices SET acked_at=? WHERE user_id=? AND acked_at IS NULL", (time.time(), p.id))
    return {"ok": True}
