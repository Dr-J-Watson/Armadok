"""Ce que Docker et Traefik se disent : pour un conteneur, ses règles Traefik, son site, ses visiteurs.

S'appuie sur linkage.py (les liens, fonctions pures) et sur le journal d'accès (table ip_paths, par site) quand le nœud le lit."""
import time

from . import accesslog, db, geo, linkage
from .traefik_scope import scope_items


def _cut(days: int) -> float:
    return time.time() - max(1, days) * 86400


class Links(dict):
    """{nom du conteneur: liens}, avec `ctxs` : les nœuds (autres que celui-ci) dont un Traefik sert ces conteneurs, pour retrouver leurs statistiques d'accès."""
    ctxs: dict

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.ctxs = {}


def _simple(containers: list[dict]) -> list[dict]:
    return [{"name": c["name"], "labels": c["labels"], "ips": [n["ip"] for n in c["networks"].values() if n["ip"]],
             "ports": sorted({p["private"] for p in c["ports"] if isinstance(p["private"], int)}), "service": c["service"], "project": c["project"],
             "published": sorted({p["public"] for p in c["ports"] if p["public"]})} for c in containers]


def _local_links(ctx, simple: list[dict]) -> dict:
    if "traefik" not in (ctx.node.get("modules") or []) or not ctx.can("sites.view"):
        return {}
    try:
        items, _ = ctx.store.items()
    except Exception:  # noqa: BLE001 - worker injoignable, fichier illisible : la liste Docker reste utilisable
        return {}
    out = linkage.link_all(simple, scope_items(ctx.p, items))
    if not ctx.can("traefik.all"):   # sans la vue complète, les routeurs « labels » (hors fichiers) ne sont pas filtrables par domaine : on ne les montre pas
        out = {k: [l for l in v if l["provider"] == "file"] for k, v in out.items()}
        out = {k: v for k, v in out.items() if v}
    return out


def _cross_links(ctx, simple: list[dict]) -> tuple[dict, dict]:
    """Conteneurs de CE nœud servis par le Traefik d'un AUTRE nœud (visible de la personne) : on compare les adresses de la machine et les ports publiés."""
    addrs = list(ctx.node.get("addresses") or [])
    if not addrs or not any(c["published"] for c in simple):
        return {}, {}
    from .nodes import accessible_nodes
    out: dict[str, list[dict]] = {}
    ctxs: dict = {}
    for other in accessible_nodes(ctx.p):
        if other.id == ctx.id or "traefik" not in (other.node.get("modules") or []) or not other.can("sites.view") or other.node["status"] != "active":
            continue
        try:
            items = scope_items(other.p, other.store.items()[0])
        except Exception:  # noqa: BLE001 - nœud hors ligne : on ignore
            continue
        for c in simple:
            for lk in linkage.address_links(c, items, addrs):
                out.setdefault(c["name"], []).append({**lk, "node": {"id": other.id, "name": other.node["name"]}})
                ctxs[other.id] = other
    return out, ctxs


def link_containers(ctx, containers: list[dict]) -> Links:
    """{nom du conteneur: liens Traefik}, limité à ce que la personne a le droit de voir : ceux du Traefik de ce nœud (labels, URL de service) et ceux d'un autre nœud."""
    simple = _simple(containers)
    res = Links(_local_links(ctx, simple))
    cross, ctxs = _cross_links(ctx, simple)
    for name, lks in cross.items():
        res[name] = sorted([*res.get(name, []), *lks], key=lambda x: (not x["sure"], x["router"]))
    res.ctxs = ctxs
    return res


def access_for(ctx, links: list[dict], ctxs: dict, days: int = 7, totals: bool = False) -> dict | None:
    """Statistiques d'accès des sites d'un ensemble de liens : chaque site est lu dans le journal du nœud dont le Traefik le sert (celui-ci, ou un autre)."""
    groups: dict[str, tuple] = {}
    for lk in links:
        node = ctxs.get((lk.get("node") or {}).get("id")) if lk.get("node") else ctx
        if node is None:
            continue
        groups.setdefault(node.id, (node, set()))[1].update(lk["hosts"])
    parts = [(access_totals if totals else access_stats)(n, sorted(h), days) for n, h in groups.values() if h]
    parts = [p for p in parts if p]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    out = {"days": days, "hits": sum(p["hits"] for p in parts), "errors": sum(p["errors"] for p in parts), "visitors": sum(p["visitors"] for p in parts)}
    if not totals:
        ips: dict[str, dict] = {}
        for p in parts:
            for i in p["topIps"]:
                if i["ref"] in ips:
                    ips[i["ref"]]["hits"] += i["hits"]
                    ips[i["ref"]]["errors"] += i["errors"]
                else:
                    ips[i["ref"]] = dict(i)
        countries: dict[str, dict] = {}
        for p in parts:
            for c in p["countries"]:
                countries.setdefault(c["code"], {**c, "ips": 0})["ips"] += c["ips"]
        out.update(hosts=sorted({h for p in parts for h in p["hosts"]}), topIps=sorted(ips.values(), key=lambda i: -i["hits"])[:10],
                   topPaths=sorted((x for p in parts for x in p["topPaths"]), key=lambda x: -x["hits"])[:8], countries=sorted(countries.values(), key=lambda c: -c["ips"])[:5])
    return out


def traefik_provider(ctx, containers: list[dict]) -> dict:
    """Ce que le Traefik de CE nœud lit : le fournisseur Docker (les labels) est-il activé, et les conteneurs sont-ils exposés d'office ?
    Déduit de la commande du conteneur de Traefik. {found, docker, exposedByDefault, networks} ; docker/exposedByDefault valent None si on ne peut pas savoir
    (Traefik configuré par un fichier, conteneur introuvable ou sous un autre nom)."""
    from . import routes_docker
    from .config import settings
    t = next((c for c in containers if c["name"] == settings.traefik_container), None)
    if not t:
        return {"found": False, "docker": None, "exposedByDefault": None, "networks": []}
    try:
        cmd = routes_docker.run(ctx, "d_container", {"id": t["fullId"]}).get("command", "")
    except Exception:  # noqa: BLE001
        cmd = ""
    on = "--providers.docker" in cmd and "--providers.docker=false" not in cmd
    return {"found": True, "docker": True if on else (False if "--providers." in cmd or "--providers" in cmd else None),
            "exposedByDefault": (False if "exposedbydefault=false" in cmd.lower() else True) if on else None, "networks": sorted(t["networks"])}


def access_totals(ctx, hosts: list[str], days: int = 7) -> dict | None:
    """Les trois chiffres d'un site (requêtes, erreurs, visiteurs) : version légère pour les listes. None si le nœud ne lit pas de journal d'accès."""
    if not hosts or not accesslog.available(ctx.id) or not ctx.can("security.view"):
        return None
    ph = ",".join("?" for _ in hosts)
    t = db.one(f"SELECT COALESCE(SUM(hits),0) hits, COALESCE(SUM(errors),0) errors, COUNT(DISTINCT ip) ips FROM ip_paths WHERE node_id=? AND last_seen>=? AND host IN ({ph})",
               (ctx.id, _cut(days), *[h.lower() for h in hosts]))
    return {"hits": int(t["hits"]), "errors": int(t["errors"]), "visitors": int(t["ips"])}


def access_stats(ctx, hosts: list[str], days: int = 7) -> dict | None:
    """Visites des sites d'un conteneur : requêtes, erreurs, visiteurs, adresses les plus actives. None si le nœud ne lit pas de journal d'accès
    ou si la personne n'a pas le droit de voir l'activité des adresses."""
    if not hosts or not accesslog.available(ctx.id) or not ctx.can("security.view"):
        return None
    cut, ph = _cut(days), ",".join("?" for _ in hosts)
    base = (ctx.id, cut, *[h.lower() for h in hosts])
    t = db.one(f"SELECT COALESCE(SUM(hits),0) hits, COALESCE(SUM(errors),0) errors, COUNT(DISTINCT ip) ips FROM ip_paths WHERE node_id=? AND last_seen>=? AND host IN ({ph})", base)
    reveal = ctx.can("security.ips.reveal")
    banned = {r["ip"] for r in db.rows("SELECT ip FROM ip_bans WHERE node_id=?", (ctx.id,))}
    top = []
    for r in db.rows(f"SELECT ip, SUM(hits) hits, SUM(errors) errors, MAX(last_seen) last FROM ip_paths WHERE node_id=? AND last_seen>=? AND host IN ({ph}) "
                     "GROUP BY ip ORDER BY hits DESC LIMIT 10", base):
        g = geo.lookup(r["ip"], precise=False)
        top.append({"ip": r["ip"] if reveal else accesslog.mask_ip(r["ip"]), "ref": accesslog.ref_of(r["ip"]), "hits": r["hits"], "errors": r["errors"],
                    "last": r["last"], "country": (g or {}).get("country"), "countryName": (g or {}).get("countryName"), "banned": r["ip"] in banned})
    paths = [dict(p) for p in db.rows(f"SELECT host, path, SUM(hits) hits, SUM(errors) errors FROM ip_paths WHERE node_id=? AND last_seen>=? AND host IN ({ph}) "
                                      "GROUP BY host, path ORDER BY hits DESC LIMIT 8", base)]
    countries: dict[str, dict] = {}
    for r in db.rows(f"SELECT DISTINCT ip FROM ip_paths WHERE node_id=? AND last_seen>=? AND host IN ({ph})", base):
        g = geo.lookup(r["ip"], precise=False)
        if g:
            c = countries.setdefault(g["country"], {"code": g["country"], "name": g["countryName"], "ips": 0})
            c["ips"] += 1
    return {"days": days, "hosts": sorted(hosts), "hits": int(t["hits"]), "errors": int(t["errors"]), "visitors": int(t["ips"]), "topIps": top, "topPaths": paths,
            "countries": sorted(countries.values(), key=lambda c: -c["ips"])[:5]}
