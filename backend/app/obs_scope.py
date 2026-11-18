"""Surveillance d'un nœud qui publie ses sites par le Traefik d'un autre (vue filtrée) : on ne garde que ce qui concerne SES sites.

Les mesures Prometheus se limitent à ses services ; le journal d'accès, à ses noms de domaine. Ce qui est propre à tout le Traefik (cibles Prometheus,
certificats, rechargements, pic de trafic global) n'est pas montré dans cette vue."""
from . import db, observability as obs
from .traefik_scope import router_hosts


def of(ctx) -> dict | None:
    """{"services": noms sans fournisseur, "hosts": noms de domaine, "sel": sélecteur PromQL} ; None hors vue filtrée."""
    if ctx.p.scope_routers is None:
        return None
    try:
        items = ctx.store.items()[0]
    except Exception:  # noqa: BLE001 - Traefik illisible : une vue vide plutôt qu'un affichage complet par erreur
        items = []
    services, hosts = set(), set()
    for i in items:
        if i.get("proto") == "http" and i.get("kind") == "routers" and i.get("name") in ctx.p.scope_routers:
            spec = i.get("spec") or {}
            services.add(str(spec.get("service") or i["name"]).split("@")[0])
            hosts |= {h.lower() for h in (router_hosts(spec) or ())}
    return {"services": services, "hosts": hosts, "sel": obs.scope_sel(services)}


def host_clause(sc: dict, col: str = "host") -> tuple[str, list]:
    hs = sorted(sc["hosts"])
    return (f"{col} IN ({','.join('?' for _ in hs)})", hs) if hs else ("0", [])


def ips_on_hosts(node_id: str, sc: dict) -> set[str]:
    cl, args = host_clause(sc)
    return {r["ip"] for r in db.rows(f"SELECT DISTINCT ip FROM ip_paths WHERE node_id=? AND {cl}", (node_id, *args))}


def alert_visible(node_id: str, sc: dict, key: str, subject: str) -> bool:
    """Une alerte reste visible si elle vise un de ses services, un de ses domaines, ou une adresse vue sur ses sites. Les alertes de tout le Traefik sont retirées."""
    if key in ("error_rate", "latency", "server_down"):
        return subject.split(" · ")[0].split("@")[0] in sc["services"]
    if key == "cert_expiry":
        cn = subject.lower().lstrip("*.")
        return any(h == cn or h.endswith("." + cn) or cn.endswith("." + h) for h in sc["hosts"])
    if key in ("scan_burst", "exploit_attempt"):
        return subject in ips_on_hosts(node_id, sc)
    return False
