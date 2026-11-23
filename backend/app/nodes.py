"""Nœuds Traefik : un nœud = une instance Traefik avec ses réglages, ses membres (rôles) et ses domaines.

Rôles sur un nœud : owner > admin > editor > viewer, plus « dns » (implicite : accès à un domaine du nœud, sans Traefik).
Les administrateurs de la plateforme ont tous les droits sur le nœud « local » (le Traefik de cette machine)."""
import dataclasses
import ipaddress
import re
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit as audit_log
from . import caps as capmod
from . import db, rbac, worker_link
from .audit import audit
from .auth import current_user
from .config import settings
from .perms import Principal, domain_level, DOMAIN_SQL
from .traefik_live import forget as forget_live
from .traefik_store import Store
from . import worker_proto
from .worker_proto import CLOSE_REVOKED

RANK = {"dns": 0, "viewer": 1, "custom": 1, "editor": 2, "admin": 3, "owner": 4}
MEMBER_ROLES = ("admin", "editor", "viewer")  # « owner » ne se donne que par transfert
ROLE_LABEL = {"owner": "Propriétaire", "admin": "Administrateur", "editor": "Éditeur", "viewer": "Lecteur", "dns": "Domaines", "custom": "Personnalisé"}
TOKEN = re.compile(r"^[A-Za-z0-9_.@:-]{1,60}$")
HOSTNAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")

from .crypto import enc as crypto_enc

router = APIRouter(prefix="/api")


def _split(raw: str) -> list[str]:
    return [x for x in (raw or "").split(",") if x]


def local_modules() -> tuple[str, ...]:
    """Ce que le nœud local (le hub) sait faire : toujours Traefik ; Docker seulement s'il est demandé (DOCKER_MANAGE) et que le socket est là."""
    import os
    import stat
    mods = ["traefik"]
    if settings.docker_manage and settings.docker_socket:
        try:
            if stat.S_ISSOCK(os.stat(settings.docker_socket).st_mode):
                mods.append("docker")
        except OSError:
            pass
    return tuple(mods)


def modules_of(row) -> tuple[str, ...]:
    """Modules d'un nœud : calculés pour le local, annoncés par le worker pour un distant."""
    return local_modules() if row["kind"] == "local" else worker_proto.parse_modules(row["modules"])


def node_out(row) -> dict:
    out = {"id": row["id"], "name": row["name"], "description": row["description"], "kind": row["kind"],
           "status": row["status"], "apiUrl": row["api_url"], "entrypoints": _split(row["entrypoints"]),
           "certResolvers": _split(row["cert_resolvers"]), "dnsTarget": row["dns_target"], "connection": "online",
           "metricsEnabled": bool(row["prometheus_url"]), "webhookSet": bool(row["webhook_url"]), "mfaCaps": sorted(capmod.parse_mfa(row["mfa_caps"])),
           "lastSeen": None, "workerVersion": "", "modules": list(modules_of(row)), "addresses": _split(row["addresses"]), "addressesSeen": _split(row["addresses_seen"]),
           "traefikVia": row["traefik_via"] or ""}
    if row["kind"] == "remote":
        w = db.one("SELECT last_seen, version FROM node_workers WHERE node_id=?", (row["id"],))
        out["connection"] = "pending" if not w else "online" if worker_link.online(row["id"]) else "offline"
        if w:
            out.update(lastSeen=w["last_seen"], workerVersion=w["version"])
    return out


# ------------------------------------------------------------------ données de départ (migration)
def seed_local_node() -> None:
    """Crée le nœud « local » à partir du .env au premier démarrage, et y reporte les droits existants."""
    if db.one("SELECT 1 FROM nodes"):
        return
    nid = str(uuid.uuid4())
    db.run("INSERT INTO nodes(id,name,description,kind,api_url,entrypoints,cert_resolvers,dns_target,prometheus_url,created_at) "
           "VALUES(?,?,?,?,?,?,?,?,?,?)",
           (nid, "Principal", "Le Traefik de cette machine", "local", settings.traefik_api, ",".join(settings.entrypoints),
            ",".join(settings.cert_resolvers), settings.default_target or "", settings.prometheus_url, db.now()))
    for u in db.rows("SELECT * FROM users WHERE disabled=0"):
        if u["source"] == "env":
            role = "owner"
        elif u["role"] == "admin":
            continue  # droits complets implicites sur le nœud local
        elif u["traefik_access"] == "scoped":
            role = "editor"
        else:
            continue
        db.run("INSERT INTO node_members(node_id,user_id,role,target_hosts,created_at) VALUES(?,?,?,?,?)",
               (nid, u["id"], role, u["target_hosts"] if role == "editor" else "", db.now()))
    for d in db.rows("SELECT id FROM domains"):
        db.run("INSERT OR IGNORE INTO node_domains(node_id,domain_id) VALUES(?,?)", (nid, d["id"]))


def local_node_id() -> str | None:
    r = db.one("SELECT id FROM nodes WHERE kind='local' ORDER BY created_at LIMIT 1")
    return r["id"] if r else None


def link_domain(node_id: str, domain_id: int) -> None:
    db.run("INSERT OR IGNORE INTO node_domains(node_id,domain_id) VALUES(?,?)", (node_id, domain_id))


# ------------------------------------------------------------------ rôle d'un utilisateur sur un nœud
def _has_domain(p: Principal, node_id: str) -> bool:
    return bool(db.one(
        "SELECT 1 FROM node_domains nd JOIN domains d ON d.id=nd.domain_id JOIN credentials c ON c.id=d.credential_id "
        "WHERE nd.node_id=? AND (c.owner_id=? OR EXISTS(SELECT 1 FROM domain_access a WHERE a.domain_id=d.id AND a.user_id=?)) LIMIT 1",
        (node_id, p.id, p.id)))


def role_for(p: Principal, node) -> tuple[str | None, str, int | None]:
    """(rôle, cibles autorisées, session d'urgence) de `p` sur ce nœud ; (None, '', None) s'il n'y a aucun accès."""
    if node["kind"] == "local" and p.is_admin:
        return "owner", "", None
    m = db.one("SELECT role, target_hosts FROM node_members WHERE node_id=? AND user_id=? AND (expires_at IS NULL OR expires_at>?)",
               (node["id"], p.id, time.time()))
    if m:
        return m["role"], m["target_hosts"], None
    if p.is_admin:  # accès d'urgence en cours : droits d'administrateur du nœud, le temps de la session
        s = db.one("SELECT id FROM emergency_sessions WHERE node_id=? AND admin_id=? AND ended_at IS NULL AND expires_at>? "
                   "ORDER BY id DESC LIMIT 1", (node["id"], p.id, time.time()))
        if s:
            return "admin", "", s["id"]
    if _has_domain(p, node["id"]):
        return "dns", "", None
    if rbac.grants_for(p.id, node):   # aucun rôle historique, mais une attribution (rôle personnalisé, directe ou par groupe)
        return "custom", "", None
    return None, "", None


@dataclass
class NodeCtx:
    node: dict    # node_out()
    role: str
    p: Principal  # l'utilisateur, avec le contexte de ce nœud (rôle, cibles autorisées)
    emergency: dict | None = None  # {id, expires} si l'accès se fait par une session d'urgence

    @property
    def id(self) -> str:
        return self.node["id"]

    @property
    def store(self) -> Store:
        return store_for(self.node)

    @property
    def caps(self) -> frozenset:
        return self.p.caps

    @property
    def rank(self) -> int:
        """Rang pour « ne pas toucher à plus haut que soi » : un accès personnalisé qui administre les membres vaut un éditeur."""
        return 2 if self.role == "custom" and "node.members.edit" in self.p.caps else RANK[self.role]

    def can(self, cap: str) -> bool:
        return cap in self.p.caps

    def need(self, cap: str) -> None:
        """403 si le droit manque. Le message dit lequel, pour que la personne sache quoi demander."""
        if cap not in self.p.caps:
            if cap in self.p.mfa_blocked:
                raise HTTPException(403, f"« {capmod.CATALOG[cap]['label']} » exige la double authentification sur ce nœud : activez-la dans « Mon compte » (menu → votre nom)")
            raise HTTPException(403, f"Droit manquant : « {capmod.CATALOG[cap]['label']} »")

    def audit(self, action: str, detail: str = "") -> None:
        audit(self.p.username, action, detail, node=self.id, session=self.p.emergency)


_stores: dict[str, Store] = {}


def store_for(node: dict):
    if node["kind"] == "remote":
        return worker_link.remote_store(node["id"])
    if node["id"] not in _stores:
        from .traefik_store import store  # le magasin du dossier monté : un seul verrou pour ce dossier
        _stores[node["id"]] = store
    return _stores[node["id"]]


def _make_ctx(p: Principal, row) -> NodeCtx | None:
    role, hosts, session = role_for(p, row)
    if role is None:
        return None
    extra = rbac.extra_caps(p.id, row) if role != "owner" else frozenset()
    base = capmod.PRESETS.get(role, frozenset())
    if extra and role in ("dns", "custom"):   # des droits attribués en plus : la personne devient « personnalisée » (le DNS reste acquis)
        role = "custom"
    granted = capmod.closure(base | extra)
    blocked: frozenset = frozenset()
    required = capmod.parse_mfa(row["mfa_caps"])
    if required and not p.two_factor:   # ce nœud exige la double authentification pour ces droits : sans elle, ils n'existent pas (et ce qui en dépend non plus)
        blocked = frozenset(granted & required)
        if blocked:
            kept = capmod.closure(granted - blocked)
            blocked = frozenset(granted - kept)
            granted = kept
    scoped = dataclasses.replace(p, node_id=row["id"], node_role=role, emergency=session, caps=granted, mfa_blocked=blocked,
                                 target_hosts=[h.strip() for h in hosts.split(",") if h.strip()])
    em = None
    if session:
        em = {"id": session, "expires": db.one("SELECT expires_at FROM emergency_sessions WHERE id=?", (session,))["expires_at"]}
    return NodeCtx(node_out(row), role, scoped, em)


def accessible_nodes(p: Principal) -> list[NodeCtx]:
    out = []
    for row in db.rows("SELECT * FROM nodes WHERE deleted_at IS NULL ORDER BY created_at"):
        ctx = _make_ctx(p, row)
        if ctx:
            out.append(ctx)
    return out


def _ctx_by_id(p: Principal, nid: str) -> NodeCtx:
    row = db.one("SELECT * FROM nodes WHERE id=? AND deleted_at IS NULL", (nid,))
    ctx = _make_ctx(p, row) if row else None
    if not ctx:
        raise HTTPException(404, "Nœud introuvable")  # on ne révèle pas l'existence d'un nœud étranger
    return ctx


def node_optional(request: Request, p: Principal = Depends(current_user)) -> NodeCtx | None:
    """Nœud visé par l'en-tête X-Node (sinon le premier nœud accessible). None si l'utilisateur n'en a aucun."""
    nid = request.headers.get("x-node")
    if nid:
        ctx = _ctx_by_id(p, nid)
    else:
        nodes = accessible_nodes(p)
        ctx = next((c for c in nodes if c.node["status"] == "active"), nodes[0] if nodes else None)
    if ctx:
        _guard_suspended(ctx)
        scope = request.headers.get("x-node-scope")
        if scope and scope != ctx.id:   # vue Traefik d'un nœud qui publie par ce Traefik : on ne garde que ce qui mène à SES conteneurs
            sc = _ctx_by_id(p, scope)
            if sc.node.get("traefikVia") != ctx.id:
                raise HTTPException(403, "Ce nœud ne publie pas ses sites avec ce Traefik")
            from . import docker_link
            ctx.p = dataclasses.replace(ctx.p, scope_routers=docker_link.scope_router_names(sc, ctx.id))
    return ctx


def _guard_suspended(ctx: NodeCtx) -> None:
    """Un nœud suspendu est fermé à ses membres ; seule une session d'urgence y entre encore."""
    if ctx.node["status"] != "active" and not ctx.emergency:
        raise HTTPException(423, "Ce nœud est suspendu par l'administrateur de la plateforme")


def node_ctx(ctx: NodeCtx | None = Depends(node_optional)) -> NodeCtx:
    if not ctx:
        raise HTTPException(404, "Aucun nœud accessible")
    return ctx


def scoped(ctx: NodeCtx | None = Depends(node_optional), p: Principal = Depends(current_user)) -> Principal:
    """L'utilisateur dans le contexte du nœud courant ; sans nœud, il ne voit aucun domaine."""
    return ctx.p if ctx else dataclasses.replace(p, node_id="", node_role=None)


def node_traefik(ctx: NodeCtx = Depends(node_ctx)) -> NodeCtx:
    """Accès minimal à Traefik sur ce nœud (voir les sites). Les routes précisent ensuite le droit exact avec ctx.need()."""
    if ctx.p.traefik == "none":
        raise HTTPException(403, "Pas d'accès Traefik sur ce nœud")
    return ctx


def node_cap(cap: str):
    """Dépendance : la requête vise un nœud où la personne a ce droit fin."""
    def dep(ctx: NodeCtx = Depends(node_ctx)) -> NodeCtx:
        ctx.need(cap)
        return ctx
    return dep


def _path_ctx(nid: str, p: Principal = Depends(current_user)) -> NodeCtx:
    return _ctx_by_id(p, nid)


def _path_cap(cap: str):
    def dep(ctx: NodeCtx = Depends(_path_ctx)) -> NodeCtx:
        ctx.need(cap)
        _guard_suspended(ctx)
        return ctx
    return dep


def _path_owner(ctx: NodeCtx = Depends(_path_ctx)) -> NodeCtx:
    if ctx.role != "owner":
        raise HTTPException(403, "Réservé au propriétaire du nœud")
    _guard_suspended(ctx)
    return ctx


# ------------------------------------------------------------------ nœuds
def _role_label(c: "NodeCtx") -> str:
    """Rôle affiché : le rôle historique, ou le nom des rôles attribués pour un accès personnalisé."""
    if c.role != "custom":
        return ROLE_LABEL[c.role]
    row = db.one("SELECT * FROM nodes WHERE id=?", (c.id,))
    names = sorted({g["role_name"] for g in rbac.grants_for(c.p.id, row)}) if row else []
    return ", ".join(names) or ROLE_LABEL["custom"]


@router.get("/nodes")
def list_nodes(p: Principal = Depends(current_user)):
    out = []
    for c in accessible_nodes(p):
        info = {k: v for k, v in c.node.items() if k != "apiUrl" or c.can("node.settings")}
        out.append({**info, "role": c.role, "roleLabel": _role_label(c), "caps": sorted(c.caps), "mfaBlocked": sorted(c.p.mfa_blocked), "emergency": c.emergency,
                    "domains": db.one("SELECT COUNT(DISTINCT domain_id) c FROM node_domains WHERE node_id=? OR node_id=?", (c.id, c.node.get("traefikVia") or c.id))["c"],
                    "members": db.one("SELECT COUNT(*) c FROM node_members WHERE node_id=?", (c.id,))["c"]})
    return out


@router.get("/nodes/{nid}")
def get_node(ctx: NodeCtx = Depends(_path_ctx)):
    out = {**ctx.node, "role": ctx.role, "roleLabel": _role_label(ctx), "caps": sorted(ctx.caps), "mfaBlocked": sorted(ctx.p.mfa_blocked), "emergency": ctx.emergency}
    if ctx.can("node.settings"):
        if ctx.node["kind"] == "local":
            out["directory"] = str(settings.dynamic_dir)
    else:
        out.pop("apiUrl", None)
    return out


def _clean_list(values: list[str], label: str) -> str:
    out = []
    for v in values:
        v = v.strip()
        if not v:
            continue
        if not TOKEN.match(v):
            raise HTTPException(422, f"{label} : « {v} » n'est pas un nom valide")
        if v not in out:
            out.append(v)
    return ",".join(out)


def _check_dns_target(v: str) -> str:
    v = v.strip()
    if not v:
        return ""
    try:
        ipaddress.ip_address(v)
    except ValueError:
        if not HOSTNAME.match(v):
            raise HTTPException(422, "La cible DNS doit être une adresse IP ou un nom d'hôte")
    return v


def _check_api_url(v: str, msg: str = "L'URL de l'API Traefik doit ressembler à http://traefik:8080", query: bool = False) -> str:
    v = v.strip().rstrip("/")
    if not v:
        return ""
    u = urlparse(v)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.fragment or (u.query and not query):
        raise HTTPException(422, msg)
    return v


_ADDR = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|[0-9A-Fa-f:]{2,45})$")


def _clean_addresses(raw: list[str]) -> str:
    """Adresses (IP ou noms) de la machine d'un nœud, en minuscules, sans doublon : elles servent à reconnaître, dans les services d'un Traefik, les conteneurs de ce nœud."""
    out = []
    for a in raw:
        a = (a or "").strip().lower()
        if not a:
            continue
        if not _ADDR.match(a):
            raise HTTPException(422, f"Adresse invalide : « {a[:60]} » (une IP ou un nom d'hôte, sans http:// ni port)")
        if a not in out:
            out.append(a)
    return ",".join(out)


class NodePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=200)
    entrypoints: list[str] | None = None
    certResolvers: list[str] | None = None
    dnsTarget: str | None = Field(default=None, max_length=253)
    apiUrl: str | None = Field(default=None, max_length=300)
    prometheusUrl: str | None = Field(default=None, max_length=300)
    webhookUrl: str | None = Field(default=None, max_length=500)
    addresses: list[str] | None = Field(default=None, max_length=20)
    traefikVia: str | None = Field(default=None, max_length=64)   # nœud dont le Traefik publie les sites de ce nœud ; vide = aucun


@router.put("/nodes/{nid}")
def update_node(body: NodePatch, ctx: NodeCtx = Depends(_path_cap("node.settings"))):
    sets: dict[str, str] = {}
    if body.name is not None:
        sets["name"] = body.name.strip()
        if not sets["name"]:
            raise HTTPException(422, "Le nom ne peut pas être vide")
    if body.description is not None:
        sets["description"] = body.description.strip()
    if body.entrypoints is not None:
        sets["entrypoints"] = _clean_list(body.entrypoints, "EntryPoint")
    if body.certResolvers is not None:
        sets["cert_resolvers"] = _clean_list(body.certResolvers, "Résolveur de certificats")
    if body.dnsTarget is not None:
        sets["dns_target"] = _check_dns_target(body.dnsTarget)
    if body.addresses is not None:
        sets["addresses"] = _clean_addresses(body.addresses)
    if body.traefikVia is not None:
        via = body.traefikVia.strip()
        if via:
            if via == ctx.id:
                raise HTTPException(422, "Un nœud ne peut pas passer par son propre Traefik ici : c'est déjà le cas s'il a Traefik")
            other = next((c for c in accessible_nodes(ctx.p) if c.id == via), None)
            if not other or "traefik" not in (other.node.get("modules") or []) or not other.can("sites.view"):
                raise HTTPException(422, "Ce nœud n'existe pas, n'a pas Traefik, ou vous ne pouvez pas voir ses sites")
        sets["traefik_via"] = via
    if body.apiUrl is not None and ctx.node["kind"] == "remote":
        raise HTTPException(400, "L'URL de l'API Traefik d'un nœud distant se règle sur son worker (TRAEFIK_API_URL)")
    if body.apiUrl is not None and body.apiUrl.strip().rstrip("/") != ctx.node["apiUrl"]:
        # Le hub appelle cette URL lui-même : seul un administrateur de la plateforme peut la choisir (anti-SSRF).
        if not ctx.p.is_admin:
            raise HTTPException(403, "Seul un administrateur de la plateforme peut changer l'URL de l'API Traefik")
        sets["api_url"] = _check_api_url(body.apiUrl)
    for field, col, what in (("prometheusUrl", "prometheus_url", "Prometheus"), ("webhookUrl", "webhook_url", "du webhook")):
        v = getattr(body, field)
        if v is None:
            continue
        v = v.strip().rstrip("/") if field == "prometheusUrl" else v.strip()
        if v and not ctx.p.is_admin:   # le serveur appelle lui-même cette adresse : réservé aux administrateurs de la plateforme (anti-SSRF)
            raise HTTPException(403, f"Seul un administrateur de la plateforme peut définir l'adresse {what}")
        checked = _check_api_url(v, f"L'adresse {what} doit ressembler à http://…", query=field == "webhookUrl") if v else ""
        sets[col] = crypto_enc(checked) if (field == "webhookUrl" and checked) else checked   # le webhook contient souvent un jeton : chiffré en base
        if field == "prometheusUrl" and v and ctx.node["kind"] == "remote":
            raise HTTPException(400, "Prometheus n'est pris en charge que sur le nœud principal pour l'instant")
    if sets:
        db.run(f"UPDATE nodes SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), ctx.id))
        forget_live(ctx.id)
        ctx.audit("node.update", ", ".join(sorted(sets)))
    return get_node(_ctx_by_id(ctx.p, ctx.id))


# ------------------------------------------------------------------ double authentification exigée pour certains droits
class MfaPolicy(BaseModel):
    caps: list[str] = Field(default_factory=list, max_length=100)


@router.get("/nodes/{nid}/mfa-policy")
def get_mfa_policy(ctx: NodeCtx = Depends(_path_ctx)):
    if not (ctx.role == "owner" or ctx.can("node.settings")):
        raise HTTPException(403, "Réservé à ceux qui règlent le nœud")
    required = capmod.parse_mfa(db.one("SELECT mfa_caps FROM nodes WHERE id=?", (ctx.id,))["mfa_caps"])
    members = []
    for r in db.rows("SELECT u.username, u.totp_enabled, m.role FROM node_members m JOIN users u ON u.id=m.user_id WHERE m.node_id=? AND u.disabled=0", (ctx.id,)):
        members.append({"username": r["username"], "role": r["role"], "twoFactor": bool(r["totp_enabled"])})
    return {"caps": sorted(required), "recommended": sorted(capmod.MFA_SENSITIVE), "members": members, "mine": ctx.p.two_factor}


@router.put("/nodes/{nid}/mfa-policy")
def set_mfa_policy(body: MfaPolicy, ctx: NodeCtx = Depends(_path_ctx)):
    """Choisit les droits qui exigent la double authentification sur ce nœud. Réservé au propriétaire (ou à un administrateur de la plateforme).
    Garde-fou : pour EXIGER la 2FA, il faut l'avoir soi-même (on ne se verrouille pas) ; la retirer (liste vide) reste toujours possible."""
    if not (ctx.role == "owner" or ctx.p.is_admin):
        raise HTTPException(403, "Réservé au propriétaire du nœud")
    _guard_suspended(ctx)
    unknown = [k for k in body.caps if k not in capmod.CATALOG]
    if unknown:
        raise HTTPException(422, f"Droit inconnu : {unknown[0]}")
    keys = sorted(set(body.caps))
    if keys and not ctx.p.two_factor:
        raise HTTPException(403, "Activez d'abord la double authentification de votre propre compte : sinon vous perdriez ces droits vous-même.")
    db.run("UPDATE nodes SET mfa_caps=? WHERE id=?", (capmod.dump_mfa(keys), ctx.id))
    ctx.audit("node.mfa.policy", ", ".join(keys) if keys else "aucune exigence")
    from . import events
    events.publish("nodes")
    return {"caps": keys}


# ------------------------------------------------------------------ création, worker et suppression
def _owned_remote(user_id: int) -> int:
    return db.one("SELECT COUNT(*) c FROM node_members m JOIN nodes n ON n.id=m.node_id "
                  "WHERE m.user_id=? AND m.role='owner' AND n.kind='remote' AND n.deleted_at IS NULL", (user_id,))["c"]


def _check_quota(max_nodes: int, user_id: int, who: str = "Vous") -> None:
    if max_nodes == 0:
        raise HTTPException(403, f"{who} n'avez pas le droit de créer des nœuds" if who == "Vous" else f"{who} ne peut pas posséder de nœud")
    if max_nodes > 0 and _owned_remote(user_id) >= max_nodes:
        raise HTTPException(403, f"Quota atteint : {max_nodes} nœud{'s' if max_nodes > 1 else ''} maximum")


class NodeNew(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    dnsTarget: str = Field(default="", max_length=253)
    entrypoints: list[str] = Field(default_factory=lambda: ["web", "websecure"], max_length=30)
    certResolvers: list[str] = Field(default_factory=list, max_length=30)


@router.post("/nodes")
def create_node(body: NodeNew, p: Principal = Depends(current_user)):
    _check_quota(-1 if p.is_admin else p.max_nodes, p.id)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Le nom ne peut pas être vide")
    nid = str(uuid.uuid4())
    db.run("INSERT INTO nodes(id,name,description,kind,entrypoints,cert_resolvers,dns_target,created_at) VALUES(?,?,?,?,?,?,?,?)",
           (nid, name, body.description.strip(), "remote", _clean_list(body.entrypoints, "EntryPoint"),
            _clean_list(body.certResolvers, "Résolveur de certificats"), _check_dns_target(body.dnsTarget), db.now()))
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?, 'owner', ?)", (nid, p.id, db.now()))
    token, expires = worker_link.issue_token(nid, p.id)
    audit(p.username, "node.create", name, node=nid)
    return {**node_out(db.one("SELECT * FROM nodes WHERE id=?", (nid,))), "role": "owner", "token": token, "tokenExpires": expires}


@router.post("/nodes/{nid}/worker/token")
def new_worker_token(ctx: NodeCtx = Depends(_path_cap("node.connection"))):
    """Nouveau jeton d'enrôlement. Un worker déjà enrôlé est révoqué : à utiliser pour reconnecter ou remplacer une machine."""
    if ctx.node["kind"] != "remote":
        raise HTTPException(400, "Le nœud de cette machine n'a pas de worker")
    revoked = bool(db.one("SELECT 1 FROM node_workers WHERE node_id=?", (ctx.id,)))
    db.run("DELETE FROM node_workers WHERE node_id=?", (ctx.id,))
    worker_link.disconnect(ctx.id, CLOSE_REVOKED)
    token, expires = worker_link.issue_token(ctx.id, ctx.p.id)
    ctx.audit("node.worker.token", "worker révoqué" if revoked else "")
    return {"token": token, "tokenExpires": expires, "revoked": revoked}


@router.delete("/nodes/{nid}")
def delete_node(ctx: NodeCtx = Depends(_path_owner)):
    if ctx.node["kind"] != "remote":
        raise HTTPException(400, "Le nœud de cette machine ne peut pas être supprimé")
    worker_link.disconnect(ctx.id, CLOSE_REVOKED)
    db.run("DELETE FROM nodes WHERE id=?", (ctx.id,))
    worker_link._stores.pop(ctx.id, None)
    forget_live(ctx.id)
    audit(ctx.p.username, "node.delete", ctx.node["name"], node=ctx.id)
    return {"ok": True}


@router.get("/nodes/{nid}/audit")
def node_audit(scope: str = "node", ctx: NodeCtx = Depends(_path_cap("node.audit"))):
    """Par défaut, ce qui s'est passé SUR ce nœud. « platform » (admins de la plateforme) : connexions, comptes, DNS… qui ne dépendent d'aucun nœud."""
    if scope == "platform":
        if ctx.p.role != "admin":
            raise HTTPException(403, "Réservé aux administrateurs de la plateforme")
        return audit_log.tail(300, platform=True)
    entries = audit_log.tail(300, node=ctx.id, include_unscoped=ctx.node["kind"] == "local")
    if not ctx.can("security.ips.reveal"):   # le journal cite des adresses de visiteurs (blocages, alertes) : même règle que partout, masquées sans ce droit
        from .alerts import mask_text
        entries = [{**e, "detail": mask_text(e["detail"])} if isinstance(e.get("detail"), str) else e for e in entries]
    return entries


# ------------------------------------------------------------------ membres
def _members(nid: str) -> list[dict]:
    return [{"userId": r["user_id"], "username": r["username"], "role": r["role"], "roleLabel": ROLE_LABEL[r["role"]],
             "targetHosts": r["target_hosts"], "disabled": bool(r["disabled"]), "expiresAt": r["expires_at"]}
            for r in db.rows("SELECT m.user_id, u.username, u.disabled, m.role, m.target_hosts, m.expires_at FROM node_members m "
                             "JOIN users u ON u.id=m.user_id WHERE m.node_id=? ORDER BY m.role='owner' DESC, u.username", (nid,))]


@router.get("/nodes/{nid}/members")
def list_members(ctx: NodeCtx = Depends(_path_cap("node.members.view"))):
    implicit = [{"userId": r["id"], "username": r["username"], "role": "owner", "roleLabel": ROLE_LABEL["owner"],
                 "targetHosts": "", "implicit": True, "disabled": False}
                for r in db.rows("SELECT id, username FROM users WHERE role='admin' AND disabled=0 ORDER BY username")] \
        if ctx.node["kind"] == "local" else []
    members = _members(ctx.id)
    via: dict[int, dict] = {}   # personnes qui n'ont accès que par une attribution (page « Droits »)
    node_row = db.one("SELECT * FROM nodes WHERE id=?", (ctx.id,))
    for g in db.rows("SELECT g.*, r.name AS role_name FROM grants g JOIN roles r ON r.id=g.role_id WHERE g.node_id=? OR g.node_id IS NULL", (ctx.id,)):
        if g["node_id"] is None and not (g["granted_by"] and rbac.owns_node(g["granted_by"], node_row)):
            continue
        if g["expires_at"] and g["expires_at"] <= time.time():
            continue
        subjects = [g["subject_id"]] if g["subject_type"] == "user" else [x["user_id"] for x in db.rows("SELECT user_id FROM group_members WHERE group_id=?", (g["subject_id"],))]
        for uid in subjects:
            u = db.one("SELECT username, disabled FROM users WHERE id=?", (uid,))
            if u and not any(m["userId"] == uid for m in members):
                e = via.setdefault(uid, {"userId": uid, "username": u["username"], "role": "custom", "roleLabel": [], "targetHosts": "", "disabled": bool(u["disabled"]),
                                         "viaGrant": True, "expiresAt": g["expires_at"]})
                e["roleLabel"].append(g["role_name"])
    members += [{**e, "roleLabel": ", ".join(sorted(set(e["roleLabel"])))} for e in via.values()]
    have = {m["userId"] for m in members}
    return {"members": members + [i for i in implicit if i["userId"] not in have],
            "assignable": [r for r in MEMBER_ROLES if RANK[r] < RANK[ctx.role]]}


class MemberIn(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    role: str
    targetHosts: str = Field(default="", max_length=500)
    expiresAt: float | None = None   # accès temporaire : le membre perd son rôle à cette date


class MemberPatch(BaseModel):
    role: str | None = None
    targetHosts: str | None = Field(default=None, max_length=500)
    expiresAt: float | None = None
    clearExpiry: bool = False


def _check_assignable(ctx: NodeCtx, role: str) -> None:
    if role not in MEMBER_ROLES:
        raise HTTPException(422, "Rôle invalide")
    if RANK[role] >= ctx.rank:  # plafond de délégation : on ne donne jamais plus que ce qu'on a
        raise HTTPException(403, "Vous ne pouvez pas donner un rôle égal ou supérieur au vôtre")
    over = capmod.PRESETS[role] - ctx.caps
    if over:
        raise HTTPException(403, "Vous ne pouvez pas donner des droits que vous n'avez pas vous-même : " + ", ".join(capmod.CATALOG[k]["label"] for k in sorted(over)[:3]))


def _clean_hosts(raw: str) -> str:
    return ",".join(h.strip() for h in raw.replace(";", ",").split(",") if h.strip())


@router.post("/nodes/{nid}/members")
def add_member(body: MemberIn, ctx: NodeCtx = Depends(_path_cap("node.members.add"))):
    _check_assignable(ctx, body.role)
    u = db.one("SELECT id, role, disabled FROM users WHERE username=?", (body.username.strip(),))
    if not u or u["disabled"]:
        raise HTTPException(404, "Utilisateur introuvable")
    if ctx.node["kind"] == "local" and u["role"] == "admin":
        raise HTTPException(400, "Cet administrateur a déjà tous les droits sur ce nœud")
    if db.one("SELECT 1 FROM node_members WHERE node_id=? AND user_id=?", (ctx.id, u["id"])):
        raise HTTPException(409, "Cet utilisateur est déjà membre du nœud")
    if body.expiresAt is not None and body.expiresAt <= time.time():
        raise HTTPException(422, "La date de fin est déjà passée")
    db.run("INSERT INTO node_members(node_id,user_id,role,target_hosts,created_at,expires_at) VALUES(?,?,?,?,?,?)",
           (ctx.id, u["id"], body.role, _clean_hosts(body.targetHosts), db.now(), body.expiresAt))
    ctx.audit("node.member.add", f"{body.username} ({body.role})" + (" jusqu'au " + time.strftime("%d/%m/%Y %H:%M", time.localtime(body.expiresAt)) if body.expiresAt else ""))
    return {"ok": True}


def _target_member(ctx: NodeCtx, uid: int):
    m = db.one("SELECT m.*, u.username FROM node_members m JOIN users u ON u.id=m.user_id WHERE m.node_id=? AND m.user_id=?",
               (ctx.id, uid))
    if not m:
        raise HTTPException(404, "Membre introuvable")
    if m["role"] == "owner" or RANK[m["role"]] >= ctx.rank:
        raise HTTPException(403, "Vous ne pouvez pas modifier ce membre")
    return m


@router.put("/nodes/{nid}/members/{uid}")
def update_member(uid: int, body: MemberPatch, ctx: NodeCtx = Depends(_path_cap("node.members.edit"))):
    m = _target_member(ctx, uid)
    sets = {}
    if body.role is not None:
        _check_assignable(ctx, body.role)
        sets["role"] = body.role
    if body.targetHosts is not None:
        sets["target_hosts"] = _clean_hosts(body.targetHosts)
    if body.expiresAt is not None:
        if body.expiresAt <= time.time():
            raise HTTPException(422, "La date de fin est déjà passée")
        sets["expires_at"] = body.expiresAt
    elif body.clearExpiry:
        sets["expires_at"] = None
    if sets:
        db.run(f"UPDATE node_members SET {', '.join(k + '=?' for k in sets)} WHERE node_id=? AND user_id=?", (*sets.values(), ctx.id, uid))
        ctx.audit("node.member.update", f"{m['username']} {sets}")
    return {"ok": True}


@router.delete("/nodes/{nid}/members/{uid}")
def remove_member(uid: int, ctx: NodeCtx = Depends(_path_ctx)):
    if uid == ctx.p.id:  # quitter un nœud est toujours possible, sauf pour son propriétaire
        if ctx.role == "owner":
            raise HTTPException(400, "Le propriétaire doit d'abord transférer le nœud")
        row = db.one("SELECT 1 FROM node_members WHERE node_id=? AND user_id=?", (ctx.id, uid))
        if not row:
            raise HTTPException(404, "Membre introuvable")
        m = {"username": ctx.p.username}
    else:
        ctx.need("node.members.remove")
        _guard_suspended(ctx)
        m = _target_member(ctx, uid)
    db.run("DELETE FROM node_members WHERE node_id=? AND user_id=?", (ctx.id, uid))
    ctx.audit("node.member.remove", m["username"])
    return {"ok": True}


class TransferIn(BaseModel):
    userId: int


@router.post("/nodes/{nid}/transfer")
def transfer(body: TransferIn, ctx: NodeCtx = Depends(_path_owner)):
    if body.userId == ctx.p.id:
        raise HTTPException(400, "Vous êtes déjà propriétaire")
    u = db.one("SELECT id, username, disabled, role, max_nodes FROM users WHERE id=?", (body.userId,))
    if not u or u["disabled"]:
        raise HTTPException(404, "Utilisateur introuvable")
    if ctx.node["kind"] == "remote":  # le nœud compte dans le quota de son nouveau propriétaire
        _check_quota(-1 if u["role"] == "admin" else u["max_nodes"], u["id"], u["username"])
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?, 'owner', ?) "
           "ON CONFLICT(node_id,user_id) DO UPDATE SET role='owner'", (ctx.id, u["id"], db.now()))
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?, 'admin', ?) "
           "ON CONFLICT(node_id,user_id) DO UPDATE SET role='admin'", (ctx.id, ctx.p.id, db.now()))
    ctx.audit("node.transfer", u["username"])
    return {"ok": True}


# ------------------------------------------------------------------ domaines du nœud
def _managed_domains(p: Principal) -> list:
    """Domaines sur lesquels l'utilisateur est gestionnaire, tous nœuds confondus (pas de contexte de nœud)."""
    base = dataclasses.replace(p, node_id=None, node_role=None)
    return [d for d in db.rows(DOMAIN_SQL + " ORDER BY d.name") if domain_level(base, d) == "manage"]


@router.get("/nodes/{nid}/domains")
def node_domains(ctx: NodeCtx = Depends(_path_cap("node.domains.view"))):
    """Domaines rattachés à ce nœud, avec leur gestionnaire (les clés OVH restent chez lui)."""
    sql = ("SELECT d.name, c.label, c.owner_id, u.username FROM node_domains nd JOIN domains d ON d.id=nd.domain_id "
           "JOIN credentials c ON c.id=d.credential_id LEFT JOIN users u ON u.id=c.owner_id WHERE nd.node_id=? ORDER BY d.name")
    out = [{"name": r["name"], "account": r["label"], "owner": r["username"], "system": r["owner_id"] is None, "mine": r["owner_id"] == ctx.p.id, "inherited": ""}
           for r in db.rows(sql, (ctx.id,))]
    via = ctx.node.get("traefikVia") or ""
    if via:   # les sites de ce nœud passent par le Traefik d'un autre : ses domaines valent aussi ici (en lecture seule, gérés depuis ce nœud)
        other = next((c for c in accessible_nodes(ctx.p) if c.id == via and c.can("node.domains.view")), None)
        have = {d["name"] for d in out}
        if other:
            out += [{"name": r["name"], "account": r["label"], "owner": r["username"], "system": r["owner_id"] is None, "mine": r["owner_id"] == ctx.p.id, "inherited": other.node["name"]}
                    for r in db.rows(sql, (via,)) if r["name"] not in have]
    return out


@router.get("/nodes/{nid}/importable")
def importable(ctx: NodeCtx = Depends(_path_cap("node.domains.link"))):
    """Domaines que l'utilisateur gère déjà ailleurs et peut rattacher à ce nœud (sans dupliquer les clés OVH)."""
    if not ctx.p.can_add:
        return []
    linked = {r["domain_id"] for r in db.rows("SELECT domain_id FROM node_domains WHERE node_id=?", (ctx.id,))}
    out = []
    for d in _managed_domains(ctx.p):
        if d["id"] in linked:
            continue
        where = [r["name"] for r in db.rows("SELECT n.name FROM node_domains nd JOIN nodes n ON n.id=nd.node_id WHERE nd.domain_id=?", (d["id"],))]
        out.append({"name": d["name"], "account": d["cred_label"], "accountId": d["credential_id"], "provider": d["provider"],
                    "mine": d["cred_owner"] == ctx.p.id, "nodes": where})
    return out


class ImportIn(BaseModel):
    names: list[str] = Field(max_length=200)


@router.post("/nodes/{nid}/domains")
def import_domains(body: ImportIn, ctx: NodeCtx = Depends(_path_cap("node.domains.link"))):
    if not ctx.p.can_add:
        raise HTTPException(403, "Vous n'avez pas le droit d'ajouter des domaines")
    mine = {d["name"].lower(): d for d in _managed_domains(ctx.p)}
    added, skipped = [], []
    for n in body.names:
        d = mine.get(n.lower())
        if d:
            link_domain(ctx.id, d["id"])
            added.append(d["name"])
        else:
            skipped.append(n)
    if added:
        ctx.audit("node.domain.import", ", ".join(added))
    return {"added": added, "skipped": skipped}


@router.delete("/nodes/{nid}/domains/{name}")
def unlink_domain(name: str, ctx: NodeCtx = Depends(_path_cap("node.domains.link"))):
    d = db.one("SELECT d.id FROM domains d JOIN node_domains nd ON nd.domain_id=d.id WHERE d.name=? AND nd.node_id=?", (name, ctx.id))
    if not d:
        raise HTTPException(404, "Domaine introuvable sur ce nœud")
    db.run("DELETE FROM node_domains WHERE node_id=? AND domain_id=?", (ctx.id, d["id"]))
    ctx.audit("node.domain.unlink", name)
    return {"ok": True}


def target_node(p: Principal, ctx: NodeCtx | None) -> str | None:
    """Nœud qui recevra un domaine ajouté : celui visé si l'utilisateur l'administre, sinon le nœud local s'il y a accès.
    None : aucun nœud où le déposer. Le compte DNS et son domaine existent quand même (ils appartiennent à l'utilisateur) ;
    le domaine sera rattaché plus tard à un nœud (« Ce nœud → Domaines → importer »)."""
    if ctx and ctx.can("node.domains.link"):
        return ctx.id
    local = local_node_id()
    if local and (p.is_admin or any(c.id == local for c in accessible_nodes(p))):
        return local
    return None


def place_domains(p: Principal, ctx: NodeCtx | None, domain_ids: list[int]) -> str | None:
    """Rattache des domaines tout juste ajoutés au nœud choisi par `target_node` (aucun : ils restent à leur propriétaire)."""
    nid = target_node(p, ctx)
    if nid:
        for did in domain_ids:
            link_domain(nid, did)
    return nid
