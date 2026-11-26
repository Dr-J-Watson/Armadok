"""Équipes et comptes invités d'un hub lié.

Sens « origine » (ce hub PARTAGE des équipes) : l'administrateur choisit lesquelles de SES équipes (groupes) utilisent la liaison ; leur composition est annoncée à l'autre hub.
Sens « accueil » (ce hub REÇOIT des équipes) : si l'administrateur a autorisé l'accueil, chaque équipe annoncée devient un groupe EXTERNE et chaque membre un compte invité, sans mot
de passe, plafonné (caps.GUEST_FORBIDDEN) et rattaché à la liaison : il disparaît avec elle. Les droits sur les nœuds de CE hub sont donnés à l'équipe par les administrateurs de CE hub,
avec le système de droits habituel : l'autre hub n'affirme que « qui est dans quelle équipe », jamais ce que l'on peut faire ici.

Un invité n'existe que tant que la liaison est vivante ; si elle est coupée, il reste valable le délai de grâce propre à la liaison, puis il est suspendu."""
import hashlib
import json
import re
import time

from . import auth, db, events
from . import hub_link as hl
from .audit import audit

MAX_TEAMS = 100
MAX_GUESTS = 1000
GUEST_PASSWORD = "!invité"   # jamais un hash valide : aucune connexion par mot de passe tant que l'invité n'a pas défini le sien (mode direct)
_sent: dict[str, str] = {}   # dernière composition envoyée à chaque liaison (on ne renvoie que ce qui change)


def has_feature(r, name: str) -> bool:
    return name in (r["features"] or "").split(",")


# ------------------------------------------------------------------ côté origine : ce que l'on annonce
def shared_group_ids(link_id: str) -> list[int]:
    return [r["group_id"] for r in db.rows("SELECT group_id FROM link_shared_groups WHERE link_id=?", (link_id,))]


def set_shared_groups(link_id: str, group_ids: list[int], user: str) -> list[int]:
    r = hl.get(link_id)
    if not r or r["status"] != "paired":
        raise hl.LinkError("Liaison introuvable", 404)
    ids = sorted({int(g) for g in group_ids})
    if len(ids) > MAX_TEAMS:
        raise hl.LinkError(f"{MAX_TEAMS} équipes au plus par liaison")
    known = {x["id"] for x in db.rows("SELECT id FROM groups WHERE link_id=''")}   # on ne repartage jamais une équipe reçue d'un autre hub
    if not set(ids) <= known:
        raise hl.LinkError("Équipe inconnue ou reçue d'un autre hub")
    db.run("DELETE FROM link_shared_groups WHERE link_id=?", (link_id,))
    for g in ids:
        db.run("INSERT INTO link_shared_groups(link_id,group_id) VALUES(?,?)", (link_id, g))
    audit(user, "admin.link.teams", f"{link_id} {len(ids)} équipe(s)")
    _sent.pop(link_id, None)
    sync_out(link_id)
    return ids


def payload(link_id: str) -> dict:
    teams = []
    for g in db.rows("SELECT g.id, g.name FROM groups g JOIN link_shared_groups s ON s.group_id=g.id WHERE s.link_id=? AND g.link_id='' ORDER BY g.id", (link_id,)):
        members = db.rows("SELECT u.id, u.username FROM group_members m JOIN users u ON u.id=m.user_id WHERE m.group_id=? AND u.disabled=0 AND u.source<>'guest' ORDER BY u.id", (g["id"],))
        teams.append({"id": str(g["id"]), "name": g["name"], "members": [{"id": str(m["id"]), "username": m["username"]} for m in members]})
    return {"type": "teams", "teams": teams}


def sync_out(link_id: str) -> None:
    """Annonce la composition des équipes partagées si elle a changé (ou si la connexion vient de s'établir)."""
    ch, r = hl._chans.get(link_id), hl.get(link_id)
    if not ch or not r or not hl.usable(r) or not has_feature(r, "guests"):
        return
    body = payload(link_id)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    if _sent.get(link_id) != digest:
        _sent[link_id] = digest
        ch.post(body)


def forget(link_id: str) -> None:
    _sent.pop(link_id, None)


# ------------------------------------------------------------------ côté accueil : ce que l'on reçoit
def _slug(name: str) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "hub")[:20]


def _user_part(name: str) -> str:
    return (re.sub(r"[^A-Za-z0-9._+-]", "_", hl.clean_text(name, 64)).strip("._") or "invite")[:48]


def _allowed(r) -> bool:
    return bool(r and r["status"] == "paired" and hl.usable(r) and hl.within_grace(r))


def apply_in(link_id: str, msg: dict) -> dict:
    """L'autre hub annonce ses équipes. Rien n'est créé si l'accueil n'est pas autorisé ici, si la liaison n'est pas confirmée des deux côtés, ou si les données sont hors limites."""
    r = hl.get(link_id)
    if not r or not hl.usable(r) or not r["host_enabled"] or not has_feature(r, "guests"):
        return {"type": "teams_ack", "accepted": False}
    raw = msg.get("teams")
    if not isinstance(raw, list) or len(raw) > MAX_TEAMS:
        return {"type": "teams_ack", "accepted": False, "reason": "données invalides"}
    teams, people = [], {}
    for t in raw:
        if not isinstance(t, dict) or not isinstance(t.get("members"), list):
            return {"type": "teams_ack", "accepted": False, "reason": "données invalides"}
        tid, name = hl.clean_text(str(t.get("id", "")), 64), hl.clean_text(t.get("name"), 50)
        if not tid or not name:
            continue
        ids = []
        for m in t["members"]:
            if not isinstance(m, dict):
                continue
            uid, uname = hl.clean_text(str(m.get("id", "")), 64), _user_part(m.get("username", ""))
            if uid and uname:
                people.setdefault(uid, uname)
                ids.append(uid)
        teams.append((tid, name, ids))
    if len(people) > MAX_GUESTS:
        return {"type": "teams_ack", "accepted": False, "reason": "trop de comptes"}
    hub = r["peer_name"] or "hub"
    suffix = _slug(hub)
    allowed = _allowed(r)
    # 1. les invités
    uid_of: dict[str, int] = {}
    for ref, uname in people.items():
        row = db.one("SELECT id FROM users WHERE guest_link=? AND guest_ref=?", (link_id, ref))
        if row:
            uid_of[ref] = row["id"]
            continue
        name = f"{uname}@{suffix}"
        if db.one("SELECT 1 FROM users WHERE username=?", (name,)):   # jamais prendre la place d'un compte existant
            name = f"{uname}@{suffix}~{hashlib.sha256(ref.encode()).hexdigest()[:5]}"
            if db.one("SELECT 1 FROM users WHERE username=?", (name,)):
                continue
        uid_of[ref] = db.run("INSERT INTO users(username,password_hash,role,can_add_domains,traefik_access,disabled,source,guest_link,guest_ref,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (name, GUEST_PASSWORD, "user", 0, "none", 0 if allowed else 1, "guest", link_id, ref, time.time()))
        audit("lien", "admin.link.guest.create", f"{name} ({link_id})")
    # 2. les équipes et leur composition
    keep_groups = set()
    for tid, name, ids in teams:
        g = db.one("SELECT id FROM groups WHERE link_id=? AND peer_ref=?", (link_id, tid))
        label = f"{name} · {hub}"[:80]
        if g:
            gid = g["id"]
            db.run("UPDATE groups SET name=? WHERE id=?", (label, gid))
        else:
            gid = db.run("INSERT INTO groups(name,description,owner_id,created_at,link_id,peer_ref) VALUES(?,?,?,?,?,?)",
                         (label, f"Équipe « {name} » du hub {hub} (composition gérée par ce hub)", None, time.time(), link_id, tid))
        keep_groups.add(gid)
        want = {uid_of[i] for i in ids if i in uid_of}
        have = {x["user_id"] for x in db.rows("SELECT user_id FROM group_members WHERE group_id=?", (gid,))}
        for u in have - want:
            db.run("DELETE FROM group_members WHERE group_id=? AND user_id=?", (gid, u))
        for u in want - have:
            db.run("INSERT INTO group_members(group_id,user_id) VALUES(?,?)", (gid, u))
    # 3. ce qui a disparu côté origine disparaît ici (équipe retirée = droits retirés ; personne sortie de toutes les équipes = compte supprimé)
    for g in db.rows("SELECT id FROM groups WHERE link_id=?", (link_id,)):
        if g["id"] not in keep_groups:
            db.run("DELETE FROM grants WHERE subject_type='group' AND subject_id=?", (g["id"],))
            db.run("DELETE FROM groups WHERE id=?", (g["id"],))
    keep_users = set(uid_of.values())
    for u in db.rows("SELECT id FROM users WHERE guest_link=?", (link_id,)):
        if u["id"] not in keep_users:
            _delete_guest(u["id"])
    events.publish("nodes")
    return {"type": "teams_ack", "accepted": True, "teams": len(teams), "guests": len(keep_users)}


def _delete_guest(uid: int) -> None:
    auth.bump_sessions(uid)
    db.run("DELETE FROM group_members WHERE user_id=?", (uid,))
    db.run("DELETE FROM grants WHERE subject_type='user' AND subject_id=?", (uid,))
    db.run("DELETE FROM users WHERE id=? AND source='guest'", (uid,))
    events.publish("session", user=uid)


def purge(link_id: str) -> None:
    """La liaison est coupée pour de bon (révoquée, accueil retiré) : plus aucune équipe ni aucun invité de ce hub."""
    for g in db.rows("SELECT id FROM groups WHERE link_id=?", (link_id,)):
        db.run("DELETE FROM grants WHERE subject_type='group' AND subject_id=?", (g["id"],))
        db.run("DELETE FROM groups WHERE id=?", (g["id"],))
    for u in db.rows("SELECT id FROM users WHERE guest_link=?", (link_id,)):
        _delete_guest(u["id"])
    forget(link_id)
    events.publish("nodes")


def enforce() -> None:
    """Appelé régulièrement : applique le délai de grâce (suspension des invités quand la liaison est coupée depuis trop longtemps, reprise quand elle revient)
    et supprime les invités des liaisons révoquées, disparues ou dont l'accueil est retiré."""
    links = {r["id"]: r for r in hl.all_links()}
    for g in db.rows("SELECT DISTINCT guest_link l FROM users WHERE source='guest'"):
        r = links.get(g["l"])
        if not r or r["status"] != "paired" or not r["host_enabled"]:
            purge(g["l"])
            continue
        if _allowed(r):
            for u in db.rows("SELECT id FROM users WHERE guest_link=? AND guest_suspended=1 AND guest_blocked=0", (g["l"],)):
                db.run("UPDATE users SET disabled=0, guest_suspended=0 WHERE id=?", (u["id"],))
                events.publish("session", user=u["id"])
        else:
            for u in db.rows("SELECT id FROM users WHERE guest_link=? AND guest_suspended=0", (g["l"],)):
                db.run("UPDATE users SET disabled=1, guest_suspended=1 WHERE id=?", (u["id"],))
                auth.bump_sessions(u["id"])
                events.publish("session", user=u["id"])
                audit("lien", "admin.link.guest.suspend", f"{u['id']} ({g['l']})")


# ------------------------------------------------------------------ pour l'administrateur
def guests_of(link_id: str) -> list[dict]:
    out = []
    for u in db.rows("SELECT * FROM users WHERE guest_link=? ORDER BY username", (link_id,)):
        teams = [x["name"] for x in db.rows("SELECT g.name FROM groups g JOIN group_members m ON m.group_id=g.id WHERE m.user_id=? AND g.link_id=?", (u["id"], link_id))]
        out.append({"id": u["id"], "username": u["username"], "ref": u["guest_ref"], "teams": teams, "blocked": bool(u["guest_blocked"]), "suspended": bool(u["guest_suspended"]),
                    "direct": bool(u["guest_direct"]), "twoFactor": bool(u["totp_enabled"])})
    return out


def set_blocked(link_id: str, uid: int, blocked: bool, user: str) -> dict:
    u = db.one("SELECT * FROM users WHERE id=? AND guest_link=?", (uid, link_id))
    if not u:
        raise hl.LinkError("Invité introuvable", 404)
    r = hl.get(link_id)
    if blocked:
        db.run("UPDATE users SET guest_blocked=1, disabled=1 WHERE id=?", (uid,))
        auth.bump_sessions(uid)
    else:
        db.run("UPDATE users SET guest_blocked=0, disabled=?, guest_suspended=? WHERE id=?", (0 if _allowed(r) else 1, 0 if _allowed(r) else 1, uid))
    events.publish("session", user=uid)
    audit(user, "admin.link.guest.block" if blocked else "admin.link.guest.unblock", f"{u['username']} ({link_id})")
    return next(g for g in guests_of(link_id) if g["id"] == uid)
