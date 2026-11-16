"""Rôles, groupes et attributions (RBAC) au-dessus des droits fins de `caps.py`.

 - Un RÔLE est un ensemble nommé de droits. Les rôles prédéfinis (propriétaire, administrateur, éditeur, lecteur, domaines, opérateur,
   auditeur) existent toujours et ne se modifient pas ; chacun peut les dupliquer pour en faire un rôle à soi.
 - Un GROUPE réunit des personnes pour leur attribuer les mêmes droits d'un coup.
 - Une ATTRIBUTION donne un rôle à une personne ou à un groupe, sur UN nœud ou sur « tous mes nœuds » (ceux dont l'attributeur est
   propriétaire), éventuellement jusqu'à une date.

Les rôles historiques d'un nœud (table node_members) restent la façon de rattacher quelqu'un à un nœud ; les attributions s'y ajoutent."""
import json
import time

from . import caps, db

# Rôles prédéfinis supplémentaires (en plus des cinq historiques, dont les droits viennent de caps.PRESETS)
_EXTRA = {
    "operator": ("Opérateur", "Voit les sites et peut les mettre en pause ou les réactiver, sans rien modifier",
                 {"sites.view", "sites.toggle", "certs.view", "dns.view"}),
    "auditor": ("Auditeur", "Lecture seule de tout : sites, éléments, fichiers, DNS, membres et activité",
                {k for k in caps.ALL if k.endswith(".view") or k in {"dns.export", "node.audit", "traefik.all"}}),
}
_LEGACY = {"owner": ("Propriétaire", "Tous les droits sur le nœud, dont le transférer ou le supprimer"),
           "admin": ("Administrateur", "Tous les droits sur le nœud, sauf le transférer ou le supprimer"),
           "editor": ("Éditeur", "Crée et modifie des sites sur ses domaines (sans fichiers bruts ni administration)"),
           "viewer": ("Lecteur", "Lecture seule des sites, des certificats et du DNS"),
           "dns": ("Domaines", "Gère seulement le DNS des domaines qui lui sont partagés")}
BUILTIN_PRESETS: dict[str, frozenset] = {**{k: caps.PRESETS[k] for k in _LEGACY}, **{k: frozenset(v[2]) for k, v in _EXTRA.items()}}


def seed_roles() -> None:
    """Crée (ou remet à jour) les rôles prédéfinis ; appelée à chaque démarrage."""
    meta = {**{k: v for k, v in _LEGACY.items()}, **{k: (v[0], v[1]) for k, v in _EXTRA.items()}}
    for key, (name, desc) in meta.items():
        body = json.dumps(sorted(caps.effective_closure(BUILTIN_PRESETS[key])))
        r = db.one("SELECT id FROM roles WHERE builtin=?", (key,))
        if r:
            db.run("UPDATE roles SET name=?, description=?, caps=? WHERE id=?", (name, desc, body, r["id"]))
        else:
            db.run("INSERT INTO roles(name,description,caps,builtin,created_at) VALUES(?,?,?,?,?)", (name, desc, body, key, time.time()))


def role_caps(row) -> frozenset:
    try:
        return caps.closure(json.loads(row["caps"] or "[]"))
    except ValueError:
        return frozenset()


def user_groups(user_id: int) -> list[int]:
    return [r["group_id"] for r in db.rows("SELECT group_id FROM group_members WHERE user_id=?", (user_id,))]


def owns_node(user_id: int, node) -> bool:
    if node["kind"] == "local":
        u = db.one("SELECT role FROM users WHERE id=?", (user_id,))
        if u and u["role"] == "admin":
            return True
    return bool(db.one("SELECT 1 FROM node_members WHERE node_id=? AND user_id=? AND role='owner'", (node["id"], user_id)))


def grants_for(user_id: int, node, now: float | None = None) -> list:
    """Attributions (non expirées) qui s'appliquent à cette personne sur ce nœud : directes ou par groupe, nœud précis ou « tous mes nœuds »."""
    now = time.time() if now is None else now
    gids = user_groups(user_id)
    marks = ",".join("?" * len(gids)) or "NULL"
    rows = db.rows(
        "SELECT g.*, r.caps, r.name AS role_name, r.builtin FROM grants g JOIN roles r ON r.id=g.role_id "
        f"WHERE (g.expires_at IS NULL OR g.expires_at>?) AND ((g.subject_type='user' AND g.subject_id=?) OR (g.subject_type='group' AND g.subject_id IN ({marks}))) "
        "AND (g.node_id=? OR g.node_id IS NULL)", (now, user_id, *gids, node["id"]))
    return [g for g in rows if g["node_id"] == node["id"] or (g["node_id"] is None and g["granted_by"] and owns_node(g["granted_by"], node))]


def extra_caps(user_id: int, node, now: float | None = None) -> frozenset:
    out: set[str] = set()
    for g in grants_for(user_id, node, now):
        out |= role_caps(g)
    return frozenset(out)


def next_expiry(after: float) -> float | None:
    """Prochaine échéance d'une attribution (pour prévenir les écrans à ce moment-là)."""
    r = db.one("SELECT MIN(expires_at) m FROM grants WHERE expires_at>?", (after,))
    return r["m"] if r else None
