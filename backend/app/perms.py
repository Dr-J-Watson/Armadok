"""Droits : rôles, niveaux d'accès aux domaines, accès Traefik délégué (par nœud)."""
import fnmatch
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from fastapi import Depends, HTTPException

from . import db

LEVELS = {"view": 1, "edit": 2, "manage": 3}


@dataclass
class Principal:
    id: int
    username: str
    role: str
    can_add_domains: bool
    traefik_access: str
    target_hosts: list[str] = field(default_factory=list)
    source: str = "local"
    two_factor: bool = False
    max_nodes: int = 0  # nœuds qu'il peut posséder : 0 = aucun, -1 = illimité
    # Contexte de nœud (voir nodes.py) : renseigné quand la requête vise un nœud précis.
    node_id: str | None = None
    node_role: str | None = None  # owner | admin | editor | viewer | dns
    emergency: int | None = None  # identifiant de la session d'accès d'urgence en cours sur ce nœud
    caps: frozenset = frozenset()  # droits fins effectifs sur ce nœud (voir caps.py)
    scope_routers: frozenset | None = None  # vue filtrée d'un Traefik partagé : seuls ces routeurs (et ce qu'ils utilisent) sont montrés (nœud sans Traefik qui publie par celui-ci)
    mfa_blocked: frozenset = frozenset()  # droits retirés sur ce nœud parce qu'il exige la double authentification et que la personne ne l'a pas activée

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def can_add(self) -> bool:
        return self.is_admin or self.can_add_domains

    @property
    def traefik(self) -> str:  # 'full' | 'scoped' | 'none' : portée sur Traefik SUR le nœud courant, déduite des droits
        if self.node_role is None:
            return "none"
        if "traefik.all" in self.caps:
            return "full"
        return "scoped" if "sites.view" in self.caps else "none"

    def can(self, cap: str) -> bool:
        """Droit effectif sur le nœud courant. Hors contexte de nœud, seuls les droits DNS s'appliquent (ils dépendent alors du domaine)."""
        if not self.node_id:
            return cap.startswith("dns.")
        return cap in self.caps

    def public(self) -> dict:
        return {"id": self.id, "username": self.username, "role": self.role, "canAddDomains": self.can_add,
                "source": self.source, "twoFactor": self.two_factor, "maxNodes": -1 if self.is_admin else self.max_nodes}


def principal_from_row(r) -> Principal:
    return Principal(r["id"], r["username"], r["role"], bool(r["can_add_domains"]), r["traefik_access"],
                     [h.strip() for h in (r["target_hosts"] or "").split(",") if h.strip()], r["source"],
                     bool(r["totp_enabled"]), max_nodes=r["max_nodes"])


def domain_level(p: Principal, d) -> str | None:
    """d : ligne domains JOIN credentials (champ cred_owner). Un « lecteur » du nœud ne dépasse jamais la lecture."""
    owner = d["cred_owner"]
    lvl = None
    if owner is None:
        if p.is_admin:
            lvl = "manage"
    elif owner == p.id:
        lvl = "manage"
    if lvl is None:
        g = db.one("SELECT level FROM domain_access WHERE domain_id=? AND user_id=? AND (expires_at IS NULL OR expires_at>?)", (d["id"], p.id, time.time()))
        lvl = g["level"] if g else None
    if lvl and p.node_role == "viewer":
        lvl = "view"
    return lvl


def restrictions(p: Principal, d) -> dict | None:
    """Restrictions d'un accès PARTAGÉ à un domaine (types d'enregistrements, sous-domaines, suppression). None : aucune
    (propriétaire du compte, administrateur de la plateforme pour le compte principal)."""
    if d["cred_owner"] == p.id or (d["cred_owner"] is None and p.is_admin):
        return None
    g = db.one("SELECT types, subs, no_delete FROM domain_access WHERE domain_id=? AND user_id=? AND (expires_at IS NULL OR expires_at>?)", (d["id"], p.id, time.time()))
    if not g:
        return None
    split = lambda raw: [x.strip() for x in (raw or "").replace(";", ",").split(",") if x.strip()]  # noqa: E731
    return {"types": {t.upper() for t in split(g["types"])}, "subs": split(g["subs"]), "no_delete": bool(g["no_delete"])}


def check_record(p: Principal, d, ftype: str, sub: str, op: str) -> None:
    """Refuse un enregistrement hors des types / sous-domaines autorisés pour cette personne sur ce domaine, ou une suppression interdite."""
    r = restrictions(p, d)
    if not r:
        return
    t = (ftype or "").upper()
    if r["types"] and t not in r["types"]:
        raise HTTPException(403, f"Type d'enregistrement {t} non autorisé sur ce domaine pour vous (autorisés : {', '.join(sorted(r['types']))})")
    label = (sub or "").lower() or "@"
    if r["subs"] and not any(fnmatch.fnmatch(label, pat.lower()) for pat in r["subs"]):
        raise HTTPException(403, f"Sous-domaine « {label} » non autorisé pour vous sur ce domaine (autorisés : {', '.join(r['subs'])})")
    if op == "delete" and r["no_delete"]:
        raise HTTPException(403, "Vous n'avez pas le droit de supprimer d'enregistrements sur ce domaine")


DOMAIN_SQL = ("SELECT d.id, d.name, d.credential_id, c.owner_id AS cred_owner, c.label AS cred_label, "
              "c.source AS cred_source, c.provider AS provider FROM domains d JOIN credentials c ON c.id = d.credential_id")


def node_domain_rows(p: Principal):
    """Domaines du nœud courant (tous les domaines si la requête ne vise aucun nœud)."""
    if p.node_id is None:
        return db.rows(DOMAIN_SQL + " ORDER BY d.name")
    return db.rows(DOMAIN_SQL + " WHERE d.id IN (SELECT domain_id FROM node_domains WHERE node_id=?) ORDER BY d.name", (p.node_id,))


def accessible_domains(p: Principal) -> dict[str, dict]:
    out = {}
    for d in node_domain_rows(p):
        lvl = domain_level(p, d)
        if lvl:
            out[d["name"].lower()] = {"row": d, "level": lvl}
    return out


def require_domain(p: Principal, name: str, need: str = "view"):
    d = db.one(DOMAIN_SQL + " WHERE d.name = ?", (name,))
    if d and p.node_id is not None and not db.one("SELECT 1 FROM node_domains WHERE node_id=? AND domain_id=?", (p.node_id, d["id"])):
        d = None  # le domaine n'est pas sur ce nœud
    lvl = domain_level(p, d) if d else None
    if not lvl:
        raise HTTPException(404, "Domaine inconnu")  # 404 : on ne révèle pas l'existence
    if LEVELS[lvl] < LEVELS[need]:
        raise HTTPException(403, "Droits insuffisants sur ce domaine")
    return d, lvl


def match_domain(host: str, domains: dict[str, dict]) -> str | None:
    host = host.lower()
    best = None
    for z in domains:
        if (host == z or host.endswith("." + z)) and (best is None or len(z) > len(best)):
            best = z
    return best


def hosts_min_level(p: Principal, hosts) -> str | None:
    """Niveau minimal sur l'ensemble des hôtes (None si l'un n'est dans aucun domaine accessible)."""
    doms = accessible_domains(p)
    lv = 9
    for h in hosts:
        z = match_domain(h, doms)
        if not z:
            return None
        lv = min(lv, LEVELS[doms[z]["level"]])
    return {1: "view", 2: "edit", 3: "manage"}.get(lv) if hosts else None


def target_allowed(p: Principal, url_or_addr: str) -> bool:
    if p.traefik == "full":
        return True
    raw = url_or_addr if "://" in url_or_addr else "//" + url_or_addr
    host = (urlparse(raw).hostname or "").lower()
    return bool(host) and any(fnmatch.fnmatch(host, pat.lower()) for pat in p.target_hosts)


from .auth import current_user  # noqa: E402  (évite l'import circulaire)


def admin_only(p: Principal = Depends(current_user)) -> Principal:
    if not p.is_admin:
        raise HTTPException(403, "Réservé aux administrateurs")
    return p
