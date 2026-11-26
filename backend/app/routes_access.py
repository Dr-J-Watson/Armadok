"""Utilisateurs, comptes OVH (clés API) et partage des domaines."""
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import db
from .audit import audit
from .auth import bump_sessions, check_new_password, hash_pw
from .config import settings
from .crypto import enc
from .ovh_client import OvhError, OvhService, registry
from .dns_providers import DUCK_SUFFIX, LOCAL_SERVERS, PROVIDERS, DuckDnsService, capabilities, clean_duck_name, clean_hostname, clean_server_url, new_service
from .nodes import NodeCtx, link_domain, local_node_id, node_optional, place_domains, scoped
from .ovh_client import RECORD_TYPES
from .perms import Principal, accessible_domains, admin_only, current_user, require_domain

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ utilisateurs (admin)
def _user_out(r) -> dict:
    nodes = [{"id": n["id"], "name": n["name"], "role": n["role"]} for n in db.rows(
        "SELECT n.id, n.name, m.role FROM node_members m JOIN nodes n ON n.id=m.node_id WHERE m.user_id=? ORDER BY n.name", (r["id"],))]
    return {"id": r["id"], "username": r["username"], "role": r["role"], "canAddDomains": bool(r["can_add_domains"]),
            "disabled": bool(r["disabled"]), "source": r["source"], "createdAt": r["created_at"],
            "twoFactor": bool(r["totp_enabled"]), "nodes": nodes, "maxNodes": r["max_nodes"]}


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=10, max_length=200)
    role: str = "user"
    canAddDomains: bool = False
    maxNodes: int = Field(default=0, ge=-1, le=1000)


class UserPatch(BaseModel):
    role: str | None = None
    canAddDomains: bool | None = None
    maxNodes: int | None = Field(default=None, ge=-1, le=1000)
    disabled: bool | None = None


class PwBody(BaseModel):
    password: str = Field(min_length=10, max_length=200)


def _check_user_fields(role):
    if role not in (None, "admin", "user"):
        raise HTTPException(422, "Rôle invalide")


@router.get("/users")
def list_users(_: Principal = Depends(admin_only)):
    return [_user_out(r) for r in db.rows("SELECT * FROM users WHERE source<>'guest' ORDER BY username")]



def _session_changed(uid: int) -> None:
    """Temps réel : les sessions ouvertes de cet utilisateur revérifient leur compte (désactivé = déconnecté aussitôt) et ses nœuds."""
    from . import events
    events.publish("session", user=uid)
    events.publish("nodes")


@router.post("/users")
def create_user(body: UserIn, p: Principal = Depends(admin_only)):
    check_new_password(body.password, body.username)
    _check_user_fields(body.role)
    if db.one("SELECT 1 FROM users WHERE username=?", (body.username,)):
        raise HTTPException(409, "Ce nom d'utilisateur existe déjà")
    uid = db.run("INSERT INTO users(username,password_hash,role,can_add_domains,max_nodes,created_at) VALUES(?,?,?,?,?,?)",
                 (body.username, hash_pw(body.password), body.role, int(body.canAddDomains), body.maxNodes, db.now()))
    audit(p.username, "user.create", f"{body.username} ({body.role})")
    return _user_out(db.one("SELECT * FROM users WHERE id=?", (uid,)))


@router.put("/users/{uid}")
def update_user(uid: int, body: UserPatch, p: Principal = Depends(admin_only)):
    r = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not r:
        raise HTTPException(404, "Utilisateur introuvable")
    _check_user_fields(body.role)
    if r["source"] == "env" and (body.role == "user" or body.disabled):
        raise HTTPException(400, "Le compte propriétaire (.env) reste administrateur et actif")
    if uid == p.id and body.disabled:
        raise HTTPException(400, "Vous ne pouvez pas vous désactiver")
    fields = {"role": body.role, "can_add_domains": None if body.canAddDomains is None else int(body.canAddDomains),
              "max_nodes": body.maxNodes, "disabled": None if body.disabled is None else int(body.disabled)}
    sets = {k: v for k, v in fields.items() if v is not None}
    if sets:
        db.run(f"UPDATE users SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), uid))
        if sets.get("disabled"):
            bump_sessions(uid)
    audit(p.username, "user.update", f"{r['username']} {sets}")
    _session_changed(uid)
    return _user_out(db.one("SELECT * FROM users WHERE id=?", (uid,)))


@router.post("/users/{uid}/password")
def reset_password(uid: int, body: PwBody, p: Principal = Depends(admin_only)):
    r = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not r:
        raise HTTPException(404, "Utilisateur introuvable")
    if r["source"] == "env":
        raise HTTPException(400, "Le mot de passe du compte propriétaire se change dans le .env")
    check_new_password(body.password, r["username"])
    db.run("UPDATE users SET password_hash=? WHERE id=?", (hash_pw(body.password), uid))
    bump_sessions(uid)
    audit(p.username, "user.password.reset", r["username"])
    _session_changed(uid)
    return {"ok": True}


@router.post("/users/{uid}/2fa/reset")
def reset_2fa(uid: int, p: Principal = Depends(admin_only)):
    r = db.one("SELECT username FROM users WHERE id=?", (uid,))
    if not r:
        raise HTTPException(404, "Utilisateur introuvable")
    db.run("UPDATE users SET totp_enabled=0, totp_secret=NULL, totp_last_step=0, recovery_codes='' WHERE id=?", (uid,))
    bump_sessions(uid)
    audit(p.username, "user.2fa.reset", r["username"])
    _session_changed(uid)
    return {"ok": True}


@router.delete("/users/{uid}")
def delete_user(uid: int, p: Principal = Depends(admin_only)):
    r = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not r:
        raise HTTPException(404, "Utilisateur introuvable")
    if r["source"] == "env" or uid == p.id:
        raise HTTPException(400, "Ce compte ne peut pas être supprimé")
    owned = [n["name"] for n in db.rows("SELECT n.name FROM node_members m JOIN nodes n ON n.id=m.node_id WHERE m.user_id=? AND m.role='owner'", (uid,))]
    if owned:
        raise HTTPException(409, f"Cet utilisateur possède des nœuds ({', '.join(owned)}) : transférez-les d'abord")
    for c in db.rows("SELECT id FROM credentials WHERE owner_id=?", (uid,)):
        registry.forget(c["id"])
    db.run("DELETE FROM users WHERE id=?", (uid,))
    audit(p.username, "user.delete", r["username"])
    _session_changed(uid)
    return {"ok": True}


_lookups: dict[int, list[float]] = {}


@router.get("/users-directory")
def directory(p: Principal = Depends(current_user)):
    """Les personnes que l'utilisateur connaît déjà (co-membres de ses nœuds, destinataires de ses partages). Un
    administrateur de la plateforme voit tout le monde ; les autres cherchent un compte par son nom exact (/users-lookup)."""
    if p.is_admin:
        return [{"id": r["id"], "username": r["username"]} for r in
                db.rows("SELECT id, username FROM users WHERE disabled=0 AND source<>'guest' AND id<>? ORDER BY username", (p.id,))]
    return [{"id": r["id"], "username": r["username"]} for r in db.rows(
        "SELECT id, username FROM users WHERE disabled=0 AND source<>'guest' AND id<>? AND ("
        " id IN (SELECT m2.user_id FROM node_members m1 JOIN node_members m2 ON m2.node_id=m1.node_id WHERE m1.user_id=?)"
        " OR id IN (SELECT a.user_id FROM domain_access a JOIN domains d ON d.id=a.domain_id JOIN credentials c ON c.id=d.credential_id WHERE c.owner_id=?)"
        ") ORDER BY username LIMIT 300", (p.id, p.id, p.id))]


@router.get("/users-lookup")
def lookup(username: str, p: Principal = Depends(current_user)):
    """Recherche d'un compte par son nom exact, limitée en débit pour ne pas servir à énumérer les comptes."""
    now = time.time()
    recent = [t for t in _lookups.get(p.id, []) if now - t < 60]
    if len(recent) >= 20:
        raise HTTPException(429, "Trop de recherches, réessayez dans une minute")
    _lookups[p.id] = recent + [now]
    r = db.one("SELECT id, username FROM users WHERE username=? AND disabled=0 AND source<>'guest' AND id<>?", (username.strip(), p.id))
    if not r:
        raise HTTPException(404, "Utilisateur introuvable")
    return {"id": r["id"], "username": r["username"]}


# ------------------------------------------------------------------ domaine principal (.env)
_last_sync = 0.0


def sync_primary(force: bool = False) -> None:
    """Le compte du .env : ses zones deviennent des domaines « système » (gérés par les administrateurs)."""
    global _last_sync
    if not settings.ovh_configured or (not force and time.time() - _last_sync < 300):
        return
    cred = db.one("SELECT id FROM credentials WHERE source='env'")
    if not cred:
        cid = db.run("INSERT INTO credentials(label,endpoint,owner_id,source,created_at) VALUES(?,?,NULL,'env',?)",
                     ("Compte principal", settings.ovh_endpoint, db.now()))
    else:
        cid = cred["id"]
    try:
        names = registry.get(cid).zone_names()
    except OvhError:
        return
    _last_sync = time.time()
    have = {r["name"].lower(): r["id"] for r in db.rows("SELECT id, name FROM domains WHERE credential_id=?", (cid,))}
    local = local_node_id()
    for n in names:
        if n.lower() not in have and not db.one("SELECT 1 FROM domains WHERE name=?", (n,)):
            did = db.run("INSERT INTO domains(name, credential_id) VALUES(?,?)", (n, cid))
            if local:
                link_domain(local, did)
    for n, did in have.items():
        if n not in [x.lower() for x in names]:
            db.run("DELETE FROM domains WHERE id=?", (did,))


# ------------------------------------------------------------------ comptes OVH
class CredIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    endpoint: str = "ovh-eu"
    applicationKey: str = Field(min_length=1)
    applicationSecret: str = Field(min_length=1)
    consumerKey: str = Field(min_length=1)


class CredPatch(BaseModel):
    label: str | None = None
    token: str | None = None
    url: str | None = None
    username: str | None = None
    password: str | None = None
    insecureTls: bool | None = None
    applicationKey: str | None = None
    applicationSecret: str | None = None
    consumerKey: str | None = None


class ImportBody(BaseModel):
    zones: list[str]


def _need_can_add(p: Principal, ctx: NodeCtx | None = None):
    """Ajouter un compte DNS ou un domaine : le droit global « ajouter des domaines », OU administrer le nœud visé
    (son propriétaire gère son instance comme il l'entend). Un compte DNS appartient à l'utilisateur : il n'exige aucun nœud.
    Le domaine rejoint le nœud visé s'il l'administre ; sinon il attend d'être rattaché à un nœud (voir target_node)."""
    if not (p.can_add or (ctx and ctx.can("node.domains.link"))):
        raise HTTPException(403, "Vous n'avez pas le droit d'ajouter des comptes ou des domaines (réservé aux administrateurs d'un nœud, ou aux comptes autorisés)")


def _own_cred(p: Principal, cid: int):
    r = db.one("SELECT * FROM credentials WHERE id=? AND owner_id=?", (cid, p.id))
    if not r:
        raise HTTPException(404, "Compte introuvable")
    return r


def _ovh_only(r):
    if r["provider"] != "ovh":
        raise HTTPException(400, f"Cette opération n'existe que pour les comptes OVH (ici : {PROVIDERS[r['provider']]['label']})")


def _cred_out(r, with_domains=True, node_id: str | None = None) -> dict:
    """Un compte DNS. Avec `node_id`, seuls les domaines de CE nœud (et ceux qui ne sont sur aucun nœud) sont listés ;
    `elsewhere` compte ceux qui ne sont que sur d'autres nœuds : on ne les montre pas ici, mais on le dit (supprimer le compte les retire aussi)."""
    out = {"id": r["id"], "label": r["label"], "endpoint": r["endpoint"], "source": r["source"], "system": r["owner_id"] is None,
           "provider": r["provider"], "providerLabel": PROVIDERS[r["provider"]]["label"]}
    if r["provider"] in LOCAL_SERVERS:   # l'adresse du serveur n'est pas un secret ; l'identifiant et le mot de passe ne sortent jamais
        out["url"] = r["endpoint"]
        out["insecureTls"] = bool(json.loads(r["options"] or "{}").get("insecureTls")) if r["options"] else False
    if with_domains:
        nodeless = [d["name"] for d in db.rows("SELECT name FROM domains WHERE credential_id=? AND id NOT IN (SELECT domain_id FROM node_domains) ORDER BY name", (r["id"],))]
        if node_id is None:
            out["domains"] = [d["name"] for d in db.rows("SELECT name FROM domains WHERE credential_id=? ORDER BY name", (r["id"],))]
        else:
            here = [d["name"] for d in db.rows("SELECT name FROM domains WHERE credential_id=? AND id IN (SELECT domain_id FROM node_domains WHERE node_id=?)", (r["id"], node_id))]
            out["domains"] = sorted(set(here) | set(nodeless))
            out["here"] = len(here)
            out["elsewhere"] = db.one("SELECT COUNT(*) c FROM domains WHERE credential_id=? AND id IN (SELECT domain_id FROM node_domains) "
                                      "AND id NOT IN (SELECT domain_id FROM node_domains WHERE node_id=?)", (r["id"], node_id))["c"]
        out["nodeless"] = nodeless
    return out


def _available(svc: OvhService) -> list[dict]:
    names = svc.zone_names()
    taken = {r["name"].lower() for r in db.rows("SELECT name FROM domains")}
    return [{"name": n, "available": n.lower() not in taken} for n in names]


@router.get("/credentials")
def list_credentials(all: bool = False, p: Principal = Depends(current_user), ctx: NodeCtx | None = Depends(node_optional)):
    """Les comptes DNS de l'utilisateur (et, pour un administrateur, le compte principal, sans ses clés), sur LE NŒUD COURANT :
    un compte dont tous les domaines sont rattachés à d'autres nœuds n'apparaît pas ici (la page propose de les importer).
    Un compte sans domaine, ou dont les domaines ne sont sur aucun nœud, reste visible. Sans nœud accessible : tous les comptes.
    `all=1` : tous les comptes de l'utilisateur, sans le filtre du nœud (pour choisir à quel compte ajouter un domaine)."""
    nid = None if all or not ctx else ctx.id
    rows = [r for r in db.rows("SELECT * FROM credentials WHERE owner_id=? ORDER BY label", (p.id,))]
    if p.is_admin:
        rows = [r for r in db.rows("SELECT * FROM credentials WHERE source='env'")] + rows
    out = [_cred_out(r, node_id=nid) for r in rows]
    return [c for c in out if nid is None or c["here"] > 0 or c["elsewhere"] == 0]


@router.post("/credentials")
def add_credential(body: CredIn, p: Principal = Depends(current_user), ctx: NodeCtx | None = Depends(node_optional)):
    _need_can_add(p, ctx)
    tmp = OvhService({"id": 0, "source": "user", "endpoint": body.endpoint, "app_key": enc(body.applicationKey),
                      "app_secret": enc(body.applicationSecret), "consumer_key": enc(body.consumerKey)})
    zones = _available(tmp)  # teste les clés : lève une erreur si invalides
    cid = db.run("INSERT INTO credentials(label,endpoint,app_key,app_secret,consumer_key,owner_id,source,created_at) VALUES(?,?,?,?,?,?,'user',?)",
                 (body.label, body.endpoint, enc(body.applicationKey), enc(body.applicationSecret), enc(body.consumerKey), p.id, db.now()))
    audit(p.username, "credential.add", body.label)
    return {**_cred_out(db.one("SELECT * FROM credentials WHERE id=?", (cid,))), "zones": zones}


@router.put("/credentials/{cid}")
def edit_credential(cid: int, body: CredPatch, p: Principal = Depends(current_user)):
    r = _own_cred(p, cid)
    sets = {}
    if body.label:
        sets["label"] = body.label
    if r["provider"] == "duckdns" and body.token:
        sets["secret"] = enc(body.token.strip())
    elif r["provider"] in LOCAL_SERVERS:
        if body.url:
            sets["endpoint"] = clean_server_url(body.url, r["provider"])
        if body.username:
            sets["app_key"] = enc(body.username.strip())
        if body.password:
            sets["secret"] = enc(body.password)
        if body.insecureTls is not None:
            sets["options"] = json.dumps({"insecureTls": bool(body.insecureTls)})
    elif r["provider"] == "ovh":
        for k, col in (("applicationKey", "app_key"), ("applicationSecret", "app_secret"), ("consumerKey", "consumer_key")):
            if getattr(body, k):
                sets[col] = enc(getattr(body, k))
    if sets:
        db.run(f"UPDATE credentials SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), cid))
        registry.forget(cid)
        if r["provider"] == "ovh" and len(sets) > (1 if "label" in sets else 0):
            registry.get(cid).zone_names()  # re-teste
        if r["provider"] in LOCAL_SERVERS and len(sets) > (1 if "label" in sets else 0):
            registry.get(cid).test()
    audit(p.username, "credential.edit", r["label"])
    return _cred_out(db.one("SELECT * FROM credentials WHERE id=?", (cid,)))


@router.get("/credentials/{cid}/zones")
def credential_zones(cid: int, p: Principal = Depends(current_user)):
    _ovh_only(_own_cred(p, cid))
    return _available(registry.get(cid))


@router.post("/credentials/{cid}/import")
def import_zones(cid: int, body: ImportBody, p: Principal = Depends(current_user), ctx: NodeCtx | None = Depends(node_optional)):
    _need_can_add(p, ctx)
    _ovh_only(_own_cred(p, cid))
    valid = {z["name"].lower(): z for z in _available(registry.get(cid))}
    added, skipped, ids = [], [], []
    for z in body.zones:
        info = valid.get(z.lower())
        if info and info["available"]:
            ids.append(db.run("INSERT INTO domains(name, credential_id) VALUES(?,?)", (info["name"], cid)))
            added.append(info["name"])
        else:
            skipped.append(z)
    node = place_domains(p, ctx, ids)
    audit(p.username, "domain.import", ", ".join(added), node=node)
    return {"added": added, "skipped": skipped}


@router.get("/dns-providers")
def dns_providers(p: Principal = Depends(current_user)):
    return [{"id": k, **v} for k, v in PROVIDERS.items()]


class DuckIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    token: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9-]+$")
    domains: list[str] = Field(min_length=1, max_length=20)
    ip: str | None = Field(default=None, max_length=45)


def _duck_names(raw_names: list[str], svc: DuckDnsService, ip: str | None) -> tuple[list[str], list[str]]:
    """Noms valides et libres, vérifiés auprès de DuckDNS (l'appel ne change rien si le nom pointe déjà à l'adresse donnée)."""
    names = []
    for raw in raw_names:
        n = clean_duck_name(raw)
        if not n:
            raise HTTPException(422, f"Nom DuckDNS invalide : « {raw} » (lettres, chiffres et tirets, ex. monsite ou monsite{DUCK_SUFFIX})")
        if n not in names:
            names.append(n)
    taken = {r["name"].lower() for r in db.rows("SELECT name FROM domains")}
    skipped = [n for n in names if n in taken]
    names = [n for n in names if n not in taken]
    if not names:
        raise HTTPException(409, "Ces noms sont déjà utilisés dans l'interface")
    for n in names:
        ips = svc.current_ip(n)
        use = ip or (ips["v4"] or ips["v6"] or [None])[0]
        if not use:
            raise HTTPException(422, f"« {n} » n'a pas encore d'adresse : indiquez l'IP à utiliser")
        svc.update(n, use)  # lève une erreur si le jeton ou le nom est refusé
    return names, skipped


@router.post("/credentials/duckdns")
def add_duckdns(body: DuckIn, p: Principal = Depends(current_user), ctx: NodeCtx | None = Depends(node_optional)):
    """Un jeton DuckDNS + les noms xxx.duckdns.org à gérer."""
    _need_can_add(p, ctx)
    names, skipped = _duck_names(body.domains, DuckDnsService({"secret": enc(body.token)}), body.ip)
    cid = db.run("INSERT INTO credentials(label,endpoint,secret,provider,owner_id,source,created_at) VALUES(?,?,?,?,?,'user',?)",
                 (body.label, "duckdns", enc(body.token), "duckdns", p.id, db.now()))
    ids = [db.run("INSERT INTO domains(name, credential_id) VALUES(?,?)", (n, cid)) for n in names]
    node = place_domains(p, ctx, ids)
    audit(p.username, "credential.add", f"{body.label} (DuckDNS: {', '.join(names)})", node=node)
    return {**_cred_out(db.one("SELECT * FROM credentials WHERE id=?", (cid,))), "added": names, "skipped": skipped}


class DuckNames(BaseModel):
    domains: list[str] = Field(min_length=1, max_length=20)
    ip: str | None = Field(default=None, max_length=45)


@router.post("/credentials/{cid}/duckdns-names")
def add_duck_names(cid: int, body: DuckNames, p: Principal = Depends(current_user), ctx: NodeCtx | None = Depends(node_optional)):
    _need_can_add(p, ctx)
    r = _own_cred(p, cid)
    if r["provider"] != "duckdns":
        raise HTTPException(400, "Ce compte n'est pas un compte DuckDNS")
    names, skipped = _duck_names(body.domains, registry.get(cid), body.ip)
    ids = [db.run("INSERT INTO domains(name, credential_id) VALUES(?,?)", (n, cid)) for n in names]
    node = place_domains(p, ctx, ids)
    audit(p.username, "domain.import", ", ".join(names), node=node)
    return {"added": names, "skipped": skipped}


class AdGuardIn(BaseModel):
    url: str = Field(min_length=1, max_length=200)
    username: str = Field(default="", max_length=100)   # Pi-hole n'a pas d'identifiant : seulement un mot de passe
    password: str = Field(min_length=1, max_length=200)
    insecureTls: bool = False


class NamesIn(BaseModel):
    names: list[str] = Field(min_length=1, max_length=50)


class AdGuardNew(AdGuardIn):
    label: str = Field(min_length=1, max_length=60)
    domains: list[str] = Field(default_factory=list, max_length=50)


def _server_service(provider: str, b: AdGuardIn):
    if provider == "adguard" and not b.username.strip():
        raise HTTPException(422, "Indiquez l'identifiant d'AdGuard Home")
    return new_service({"id": 0, "provider": provider, "endpoint": clean_server_url(b.url, provider), "app_key": enc(b.username.strip()), "secret": enc(b.password),
                        "options": json.dumps({"insecureTls": b.insecureTls})})


def _clean_names(raw_names: list[str]) -> tuple[list[str], list[str]]:
    taken = {r["name"].lower() for r in db.rows("SELECT name FROM domains")}
    added, skipped = [], []
    for raw in raw_names:
        n = clean_hostname(raw)
        if not n or n in taken or n in added:
            skipped.append(raw)
        else:
            added.append(n)
    return added, skipped


def _test_server(provider: str, body: AdGuardIn, p: Principal, ctx):
    _need_can_add(p, ctx)
    return _server_service(provider, body).test()


def _add_server(provider: str, body: AdGuardNew, p: Principal, ctx):
    """Un serveur DNS local (adresse + identifiants) et, si on veut, les domaines dont il gère les noms."""
    _need_can_add(p, ctx)
    svc = _server_service(provider, body)
    info = svc.test()   # refuse tout de suite une adresse ou des identifiants faux
    names, skipped = _clean_names(body.domains)
    if body.domains and not names:
        raise HTTPException(422, "Aucun nom valide ou disponible (ex. maison.exemple.fr ou home.lan, un point au minimum)")
    cid = db.run("INSERT INTO credentials(label,endpoint,app_key,secret,options,provider,owner_id,source,created_at) VALUES(?,?,?,?,?,?,?,'user',?)",
                 (body.label, svc.cred["endpoint"], svc.cred["app_key"], svc.cred["secret"], svc.cred["options"], provider, p.id, db.now()))
    ids = [db.run("INSERT INTO domains(name, credential_id) VALUES(?,?)", (n, cid)) for n in names]
    node = place_domains(p, ctx, ids)
    audit(p.username, "credential.add", f"{body.label} ({PROVIDERS[provider]['label']}{': ' + ', '.join(names) if names else ''})", node=node)
    return {**_cred_out(db.one("SELECT * FROM credentials WHERE id=?", (cid,))), "added": names, "skipped": skipped, "info": info}


@router.post("/credentials/adguard/test")
def test_adguard(body: AdGuardIn, p: Principal = Depends(current_user), ctx: NodeCtx | None = Depends(node_optional)):
    """Essaie la connexion sans rien enregistrer."""
    return _test_server("adguard", body, p, ctx)


@router.post("/credentials/adguard")
def add_adguard(body: AdGuardNew, p: Principal = Depends(current_user), ctx: NodeCtx | None = Depends(node_optional)):
    return _add_server("adguard", body, p, ctx)


@router.post("/credentials/pihole/test")
def test_pihole(body: AdGuardIn, p: Principal = Depends(current_user), ctx: NodeCtx | None = Depends(node_optional)):
    return _test_server("pihole", body, p, ctx)


@router.post("/credentials/pihole")
def add_pihole(body: AdGuardNew, p: Principal = Depends(current_user), ctx: NodeCtx | None = Depends(node_optional)):
    return _add_server("pihole", body, p, ctx)


@router.post("/credentials/{cid}/test")
def test_credential(cid: int, p: Principal = Depends(current_user)):
    r = _own_cred(p, cid)
    if r["provider"] not in LOCAL_SERVERS:
        raise HTTPException(400, "Le test de connexion n'existe que pour les serveurs DNS locaux (AdGuard Home, Pi-hole)")
    return registry.get(cid).test()


@router.post("/credentials/{cid}/names")
def add_server_names(cid: int, body: NamesIn, p: Principal = Depends(current_user), ctx: NodeCtx | None = Depends(node_optional)):
    """Domaines supplémentaires gérés par un serveur DNS local déjà enregistré."""
    _need_can_add(p, ctx)
    r = _own_cred(p, cid)
    if r["provider"] not in LOCAL_SERVERS:
        raise HTTPException(400, "Ce compte ne gère pas de domaines saisis à la main")
    added, skipped = _clean_names(body.names)
    if not added:
        raise HTTPException(422, "Aucun nom valide ou disponible (ex. maison.exemple.fr ou home.lan, un point au minimum)")
    ids = [db.run("INSERT INTO domains(name, credential_id) VALUES(?,?)", (n, cid)) for n in added]
    node = place_domains(p, ctx, ids)
    audit(p.username, "domain.import", ", ".join(added), node=node)
    return {"added": added, "skipped": skipped}


@router.post("/domains/local")
def add_local_domains(body: NamesIn, p: Principal = Depends(current_user), ctx: NodeCtx | None = Depends(node_optional)):
    """Domaines résolus par un DNS local : aucun compte, aucune clé, rien n'est publié. Réservé à ceux qui peuvent ajouter
    des domaines, ou qui administrent le nœud visé."""
    _need_can_add(p, ctx)
    added, skipped = _clean_names(body.names)
    if not added:
        raise HTTPException(422, "Aucun nom valide ou disponible (ex. maison.exemple.fr ou home.lan, un point au minimum)")
    cred = db.one("SELECT id FROM credentials WHERE owner_id=? AND provider='local'", (p.id,))
    cid = cred["id"] if cred else db.run("INSERT INTO credentials(label,endpoint,provider,owner_id,source,created_at) VALUES('DNS local','local','local',?,'user',?)", (p.id, db.now()))
    ids = [db.run("INSERT INTO domains(name, credential_id) VALUES(?,?)", (n, cid)) for n in added]
    node = place_domains(p, ctx, ids)
    audit(p.username, "domain.local.add", ", ".join(added), node=node)
    return {"added": added, "skipped": skipped}


@router.delete("/credentials/{cid}")
def delete_credential(cid: int, p: Principal = Depends(current_user)):
    r = _own_cred(p, cid)
    db.run("DELETE FROM credentials WHERE id=?", (cid,))
    registry.forget(cid)
    audit(p.username, "credential.delete", r["label"])
    return {"ok": True}


# ------------------------------------------------------------------ domaines & partage
@router.get("/domains")
def list_domains(p: Principal = Depends(scoped)):
    sync_primary()
    out = []
    for name, v in accessible_domains(p).items():
        d = v["row"]
        owner = db.one("SELECT username FROM users WHERE id=?", (d["cred_owner"],)) if d["cred_owner"] else None
        out.append({"name": d["name"], "level": v["level"], "system": d["cred_owner"] is None,
                    "provider": d["provider"], "capabilities": capabilities(d["provider"]),
                    "owner": owner["username"] if owner else None,
                    "account": d["cred_label"] if v["level"] == "manage" else None,
                    "shared": db.one("SELECT COUNT(*) c FROM domain_access WHERE domain_id=?", (d["id"],))["c"] if v["level"] == "manage" else None})
    return out


class Grant(BaseModel):
    userId: int
    level: str
    types: str = Field(default="", max_length=200)       # types d'enregistrements autorisés (vide : tous)
    subs: str = Field(default="", max_length=400)        # sous-domaines autorisés, motifs « dev, *.dev » (vide : tous)
    noDelete: bool = False                                # interdit de supprimer des enregistrements
    expiresAt: float | None = None                        # accès temporaire


class AccessBody(BaseModel):
    grants: list[Grant]


@router.get("/domains/{name}/access")
def get_access(name: str, p: Principal = Depends(scoped)):
    d, _ = require_domain(p, name, "manage")
    return {"grants": [{"userId": r["user_id"], "username": r["username"], "level": r["level"], "types": r["types"], "subs": r["subs"],
                        "noDelete": bool(r["no_delete"]), "expiresAt": r["expires_at"]} for r in
                       db.rows("SELECT a.user_id, u.username, a.level, a.types, a.subs, a.no_delete, a.expires_at FROM domain_access a JOIN users u ON u.id=a.user_id WHERE a.domain_id=? ORDER BY u.username", (d["id"],))]}


@router.put("/domains/{name}/access")
def set_access(name: str, body: AccessBody, p: Principal = Depends(scoped)):
    if not p.can("dns.share"):
        raise HTTPException(403, "Droit manquant : « Partager l'accès à un domaine »")
    d, _ = require_domain(p, name, "manage")
    seen = {}
    for g in body.grants:
        if g.level not in ("view", "edit"):
            raise HTTPException(422, "Niveau invalide (view / edit)")
        u = db.one("SELECT id FROM users WHERE id=?", (g.userId,))
        if not u:
            raise HTTPException(422, "Utilisateur inconnu")
        if g.expiresAt is not None and g.expiresAt <= time.time():
            raise HTTPException(422, "La date de fin d'un accès est déjà passée")
        types = ",".join(t.strip().upper() for t in g.types.replace(";", ",").split(",") if t.strip())
        bad = [t for t in types.split(",") if t and t not in RECORD_TYPES]
        if bad:
            raise HTTPException(422, f"Type d'enregistrement inconnu : {bad[0]}")
        if g.userId != p.id and g.userId != d["cred_owner"]:
            seen[g.userId] = (g.level, types, ",".join(x.strip() for x in g.subs.replace(";", ",").split(",") if x.strip()), int(g.noDelete), g.expiresAt)
    db.run("DELETE FROM domain_access WHERE domain_id=?", (d["id"],))
    for uid, (lvl, types, subs, nodel, until) in seen.items():
        db.run("INSERT INTO domain_access(domain_id,user_id,level,types,subs,no_delete,expires_at) VALUES(?,?,?,?,?,?,?)", (d["id"], uid, lvl, types, subs, nodel, until))
    audit(p.username, "domain.access", f"{name}: {len(seen)} utilisateur(s)")
    return {"ok": True}


@router.delete("/domains/{name}")
def remove_domain(name: str, p: Principal = Depends(scoped)):
    d, _ = require_domain(p, name, "manage")
    if d["cred_owner"] != p.id:
        raise HTTPException(400, "Seul le propriétaire peut retirer un domaine (les domaines du compte principal suivent le .env)")
    db.run("DELETE FROM domains WHERE id=?", (d["id"],))
    audit(p.username, "domain.remove", name)
    return {"ok": True}
