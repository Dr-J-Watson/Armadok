from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import sync
from .audit import audit
from .dns_providers import PROVIDERS, DuckDnsService, capabilities
from .ovh_client import RECORD_TYPES, OvhError, registry
from .nodes import NodeCtx, node_optional, scoped
from . import caps as capmod
from .perms import Principal, accessible_domains, check_record, require_domain

router = APIRouter(prefix="/api")


class RecordBody(BaseModel):
    fieldType: str
    subDomain: str = ""
    target: str
    ttl: int = 0


class BulkBody(BaseModel):
    create: list[RecordBody] = []
    delete: list[int] = []


class ImportBody(BaseModel):
    zoneFile: str


class RestoreBody(BaseModel):
    date: str


def _svc(p: Principal, zone: str, need: str, records: bool = True, cap: str | None = None, checks: list | None = None):
    """checks : [(type, sous-domaine, opération)] à vérifier contre les restrictions de l'accès partagé à ce domaine."""
    if cap and not p.can(cap):   # droit fin du nœud (hors contexte de nœud, seul le niveau sur le domaine compte)
        raise HTTPException(403, f"Droit manquant : « {capmod.CATALOG[cap]['label']} »")
    d, lvl = require_domain(p, zone, need)
    for ftype, sub, op in checks or []:
        check_record(p, d, ftype, sub, op)
    svc = registry.for_domain(d)
    if records and not svc.supports_records:
        raise HTTPException(400, f"Un domaine « {PROVIDERS[svc.provider]['label']} » n'a pas d'enregistrements à gérer ici")
    return svc, d["name"]


def _ovh_zone(svc) -> None:
    if svc.provider != "ovh":
        raise HTTPException(400, f"Les outils de zone (import, export, historique, SOA, DNSSEC) n'existent que chez OVH, pas pour « {PROVIDERS[svc.provider]['label']} »")


def _check_existing(p: Principal, zone: str, svc, name: str, ids: list[int], op: str) -> None:
    """Les enregistrements visés (modifiés / supprimés) doivent respecter les restrictions de l'accès partagé."""
    if not ids:
        return
    d, _ = require_domain(p, zone, "edit")
    by_id = {r["id"]: r for r in svc.records(name)}
    for rid in ids:
        r = by_id.get(rid)
        if r:
            check_record(p, d, r["fieldType"], r["subDomain"], op)


@router.get("/dns/zones")
def dns_zones(p: Principal = Depends(scoped)):
    out = []
    if not p.can("dns.view"):
        return out
    for v in accessible_domains(p).values():
        d = v["row"]
        svc = registry.for_domain(d)
        try:
            info = svc.zone_info(d["name"])
        except OvhError:
            info = {"name": d["name"]}
        out.append({**info, "name": d["name"], "level": v["level"], "dirty": d["name"] in svc.dirty, "credentialId": d["credential_id"],
                    "provider": svc.provider, "providerLabel": PROVIDERS[svc.provider]["label"], "capabilities": capabilities(svc.provider)})
    return out


@router.get("/dns/zones/{zone}/provider")
def provider_info(zone: str, p: Principal = Depends(scoped)):
    if not p.can("dns.view"):
        raise HTTPException(403, "Droit manquant : « Voir les domaines et enregistrements »")
    d, lvl = require_domain(p, zone, "view")
    svc = registry.for_domain(d)
    out = {"provider": svc.provider, "label": PROVIDERS[svc.provider]["label"], "capabilities": capabilities(svc.provider), "level": lvl}
    if isinstance(svc, DuckDnsService):
        ips = svc.current_ip(d["name"])
        out.update(ip4=ips["v4"], ip6=ips["v6"], txt=svc.txt_of(d["name"]))
    return out


class DuckUpdate(BaseModel):
    ip: str | None = None
    usePublic: bool = False
    txt: str | None = None
    clearTxt: bool = False


@router.put("/dns/zones/{zone}/provider/ip")
def duck_update(zone: str, body: DuckUpdate, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "edit", records=False, cap="dns.provider")
    if not isinstance(svc, DuckDnsService):
        raise HTTPException(400, "Ce domaine n'a pas d'adresse unique à mettre à jour")
    if not (body.ip or body.usePublic or body.txt is not None or body.clearTxt):
        raise HTTPException(422, "Rien à modifier")
    svc.update(name, None if body.usePublic else body.ip, body.txt, body.clearTxt)
    audit(p.username, "dns.duckdns", f"{name} ip={'public' if body.usePublic else body.ip or '-'} txt={'effacé' if body.clearTxt else ('défini' if body.txt is not None else '-')}",
          node=p.node_id or None, session=p.emergency)
    return {"ok": True}


@router.get("/dns/record-types")
def types():
    return RECORD_TYPES


@router.get("/dns/zones/{zone}/records")
def dns_records(zone: str, force: bool = False, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "view", cap="dns.view")
    return svc.records(name, force)


@router.post("/dns/zones/{zone}/records")
def dns_create(zone: str, body: RecordBody, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "edit", cap="dns.records.create", checks=[(body.fieldType, body.subDomain, "create")])
    r = svc.create(name, body.model_dump())
    audit(p.username, "dns.create", f"{name} {body.fieldType} {body.subDomain or '@'} {body.target}", node=p.node_id or None, session=p.emergency)
    return r


@router.put("/dns/zones/{zone}/records/{rid}")
def dns_update(zone: str, rid: int, body: RecordBody, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "edit", cap="dns.records.edit", checks=[(body.fieldType, body.subDomain, "edit")])
    _check_existing(p, zone, svc, name, [rid], "edit")   # l'enregistrement actuel doit lui aussi être dans ce qu'on peut modifier
    svc.update(name, rid, body.model_dump())
    audit(p.username, "dns.update", f"{name} #{rid} {body.fieldType} {body.subDomain or '@'} {body.target}", node=p.node_id or None, session=p.emergency)
    return {"ok": True}


@router.delete("/dns/zones/{zone}/records/{rid}")
def dns_delete(zone: str, rid: int, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "edit", cap="dns.records.delete")
    _check_existing(p, zone, svc, name, [rid], "delete")
    svc.delete(name, rid)
    audit(p.username, "dns.delete", f"{name} #{rid}", node=p.node_id or None, session=p.emergency)
    return {"ok": True}


@router.post("/dns/zones/{zone}/bulk")
def dns_bulk(zone: str, body: BulkBody, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "edit", checks=[(r.fieldType, r.subDomain, "create") for r in body.create])
    _check_existing(p, zone, svc, name, body.delete, "delete")
    for cap, wanted in (("dns.records.create", body.create), ("dns.records.delete", body.delete)):   # chaque moitié a son droit
        if wanted and not p.can(cap):
            raise HTTPException(403, f"Droit manquant : « {capmod.CATALOG[cap]['label']} »")
    done = {"created": 0, "deleted": 0, "errors": []}
    for rec in body.create:
        try:
            svc.create(name, rec.model_dump())
            done["created"] += 1
        except OvhError as e:
            done["errors"].append(f"{rec.fieldType} {rec.subDomain or '@'} : {e}")
    for rid in body.delete:
        try:
            svc.delete(name, rid)
            done["deleted"] += 1
        except OvhError as e:
            done["errors"].append(f"#{rid} : {e}")
    audit(p.username, "dns.bulk", f"{name} +{done['created']} -{done['deleted']}", node=p.node_id or None, session=p.emergency)
    return done


@router.post("/dns/zones/{zone}/refresh")
def dns_refresh(zone: str, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "edit", cap="dns.publish")
    if not getattr(svc, "publishes", True):   # AdGuard & co : rien à « appliquer », les changements sont déjà actifs
        return {"ok": True}
    svc.refresh(name)
    audit(p.username, "dns.refresh", name, node=p.node_id or None, session=p.emergency)
    return {"ok": True}


@router.get("/dns/zones/{zone}/export")
def dns_export(zone: str, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "view", cap="dns.export")
    _ovh_zone(svc)
    return {"zoneFile": svc.call("get", f"/domain/zone/{name}/export")}


@router.post("/dns/zones/{zone}/import")
def dns_import(zone: str, body: ImportBody, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "manage", cap="dns.import")
    _ovh_zone(svc)
    svc.call("post", f"/domain/zone/{name}/import", zoneFile=body.zoneFile)
    svc.invalidate(name, dirty=False)
    audit(p.username, "dns.import", name, node=p.node_id or None, session=p.emergency)
    return {"ok": True}


@router.get("/dns/zones/{zone}/soa")
def dns_soa(zone: str, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "view", cap="dns.view")
    _ovh_zone(svc)
    return svc.call("get", f"/domain/zone/{name}/soa")


@router.get("/dns/zones/{zone}/history")
def dns_history(zone: str, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "view", cap="dns.history.view")
    _ovh_zone(svc)
    return svc.call("get", f"/domain/zone/{name}/history")


@router.post("/dns/zones/{zone}/history/restore")
def dns_restore(zone: str, body: RestoreBody, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "manage", cap="dns.history.restore")
    _ovh_zone(svc)
    svc.call("post", f"/domain/zone/{name}/history/{body.date}/restore")
    svc.invalidate(name, dirty=False)
    audit(p.username, "dns.restore", f"{name} {body.date}", node=p.node_id or None, session=p.emergency)
    return {"ok": True}


@router.get("/dns/zones/{zone}/dnssec")
def dns_dnssec(zone: str, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "view", cap="dns.dnssec.view")
    _ovh_zone(svc)
    try:
        return svc.call("get", f"/domain/zone/{name}/dnssec")
    except OvhError:
        return {"status": "unsupported"}


@router.post("/dns/zones/{zone}/dnssec/{action}")
def dns_dnssec_set(zone: str, action: str, p: Principal = Depends(scoped)):
    svc, name = _svc(p, zone, "manage", cap="dns.dnssec.edit")
    _ovh_zone(svc)
    if action not in ("enable", "disable"):
        raise HTTPException(400, "Action invalide")
    svc.call("post" if action == "enable" else "delete", f"/domain/zone/{name}/dnssec")
    audit(p.username, f"dns.dnssec.{action}", name, node=p.node_id or None, session=p.emergency)
    return {"ok": True}


class HostsBody(BaseModel):
    hosts: list[str]


@router.get("/sync")
def sync_report(ctx: NodeCtx | None = Depends(node_optional), p: Principal = Depends(scoped)):
    return sync.full_report(ctx, p)


@router.post("/sync/check")
def sync_check(body: HostsBody, ctx: NodeCtx | None = Depends(node_optional), p: Principal = Depends(scoped)):
    return sync.analyze(p, body.hosts, ctx.node["dnsTarget"] if ctx else "")
