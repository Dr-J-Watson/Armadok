"""Pont Traefik <-> OVH : quels hôtes de vos routeurs ont (ou non) un enregistrement DNS (dans VOS domaines)."""
from collections import Counter

from .dns_providers import LOCAL_SERVERS
from .nodes import NodeCtx
from .ovh_client import OvhError, registry
from .perms import LEVELS, Principal, accessible_domains, match_domain
from .traefik_lint import extract_hosts
from .traefik_scope import scope_items

ADDRESS_TYPES = ("A", "AAAA", "CNAME")


def subdomain(host: str, zone: str) -> str:
    return "" if host == zone else host[: -(len(zone) + 1)]


def suggest(zone: str, recs: list[dict], default_target: str = "") -> dict:
    apex = [r for r in recs if r["subDomain"] == "" and r["fieldType"] == "A"]
    a_all = Counter(r["target"] for r in recs if r["fieldType"] == "A")
    target = apex[0]["target"] if apex else (a_all.most_common(1)[0][0] if a_all else (default_target or None))
    return {"fieldType": "A", "target": target, "ttl": 3600, "cnameTarget": f"{zone}." if apex else None}


def analyze(p: Principal, hosts: list[str], default_target: str = "") -> list[dict]:
    hosts = sorted(set(h.lower() for h in hosts))
    doms = accessible_domains(p)
    out, cache = [], {}
    for h in hosts:
        z = match_domain(h, doms)
        if not z:
            out.append({"host": h, "status": "external"})
            continue
        d = doms[z]
        svc = registry.for_domain(d["row"])
        if not svc.supports_records:  # DNS local / DuckDNS : rien à créer, l'adresse est gérée ailleurs
            out.append({"host": h, "zone": d["row"]["name"], "subDomain": subdomain(h, z), "canEdit": LEVELS[d["level"]] >= LEVELS["edit"],
                        **svc.host_status(h, d["row"]["name"])})
            continue
        try:
            if z not in cache:
                cache[z] = registry.for_domain(d["row"]).records(d["row"]["name"])
        except OvhError as e:
            out.append({"host": h, "status": "error", "error": str(e), "zone": d["row"]["name"]})
            continue
        recs = cache[z]
        zone = d["row"]["name"]
        sub = subdomain(h, z)
        direct = [r for r in recs if r["subDomain"] == sub and r["fieldType"] in ADDRESS_TYPES]
        parts = sub.split(".") if sub else []
        wild = [r for i in range(len(parts)) for r in recs
                if r["subDomain"] == "*" + ("." + ".".join(parts[i + 1:]) if i + 1 < len(parts) else "")
                and r["fieldType"] in ADDRESS_TYPES]
        entry = {"host": h, "zone": zone, "subDomain": sub, "canEdit": LEVELS[d["level"]] >= LEVELS["edit"], "provider": svc.provider}
        short = lambda rs: [{"id": r["id"], "fieldType": r["fieldType"], "target": r["target"]} for r in rs]
        if direct:
            entry.update(status="ok", records=short(direct))
        elif wild:
            entry.update(status="wildcard", records=short(wild))
        else:
            entry.update(status="missing", suggestion=suggest(zone, recs, "" if svc.provider in LOCAL_SERVERS else default_target))
        out.append(entry)
    return out


def traefik_hosts(ctx: NodeCtx) -> dict[str, list[str]]:
    hosts: dict[str, list[str]] = {}
    if ctx.p.traefik == "none":
        return hosts
    items, _ = ctx.store.items()
    for it in scope_items(ctx.p, items):
        if it["kind"] == "routers" and it["proto"] in ("http", "tcp") and not it.get("disabled"):
            for h in extract_hosts((it["spec"] or {}).get("rule", "")):
                hosts.setdefault(h, []).append(it["id"])
    return hosts


def full_report(ctx: NodeCtx | None, p: Principal) -> dict:
    hosts = traefik_hosts(ctx) if ctx else {}
    rows = analyze(p, list(hosts), ctx.node["dnsTarget"] if ctx else "")
    for r in rows:
        r["routers"] = hosts[r["host"]]
    orphans = []
    for z, d in accessible_domains(p).items():
        if not registry.for_domain(d["row"]).supports_records:
            continue
        try:
            for rec in registry.for_domain(d["row"]).records(d["row"]["name"]):
                if rec["fieldType"] in ADDRESS_TYPES and rec["subDomain"] not in ("", "*"):
                    fqdn = f"{rec['subDomain']}.{d['row']['name']}".lower()
                    if fqdn not in hosts:
                        orphans.append({"host": fqdn, "zone": d["row"]["name"], **rec})
        except OvhError:
            pass
    return {"rows": rows, "orphans": orphans}
