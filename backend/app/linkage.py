"""Liaison Docker ⇄ Traefik : quel conteneur sert quel routeur, et sous quelle adresse.

Fonctions PURES (aucun accès à Docker ni au disque) : elles prennent la liste des conteneurs et les éléments Traefik d'un nœud et
renvoient les liens. L'accès à Docker vient plus tard (étape 1 de PLAN-DOCKER.md) ; ici on ne fait que raisonner, donc on teste sur des données fictives.

Deux façons de relier un conteneur à un routeur :
  1. ses LABELS « traefik.http.routers.X.* » (fournisseur Docker de Traefik) : lien sûr, le routeur s'appelle « X@docker » ;
  2. l'URL d'un SERVICE d'un fichier YAML (« http://nom-du-conteneur:80 ») : le routeur qui désigne ce service mène donc au conteneur.
     Lien sûr si l'hôte de l'URL est le nom du conteneur ou l'une de ses adresses ; « probable » si c'est seulement le nom du service Compose
     (le même nom peut exister dans plusieurs projets) ou si le port n'est pas l'un de ceux que le conteneur expose.

Conteneur (entrée) : {"name", "labels": {}, "ips": [], "ports": [int], "service": nom du service Compose ou "", "project": nom du projet ou ""}
Élément Traefik (entrée) : ceux de Store.items() : {"proto", "kind", "name", "spec", …}.
Lien (sortie) : {"router", "provider", "service", "rule", "hosts", "via", "sure", "port", "item"} ;
  « item » est l'identifiant du routeur dans les éléments Traefik (None pour un routeur défini seulement par labels)."""
from urllib.parse import urlparse

from .rules import host_constraint, parse

LABEL_PREFIX = "traefik.http."


def _hosts(rule: str) -> list[str]:
    try:
        return sorted(host_constraint(parse(rule or "")) or [])
    except ValueError:
        return []


def parse_labels(labels: dict | None) -> dict:
    """Routeurs et services déclarés par les labels d'un conteneur : {"routers": {nom: {attribut: valeur}}, "services": {nom: {attribut: valeur}}}.
    Les noms d'attribut sont en minuscules (« rule », « service », « entrypoints », « tls.certresolver », « loadbalancer.server.port »…)."""
    out = {"routers": {}, "services": {}}
    for key, val in (labels or {}).items():
        k = str(key)
        if not k.lower().startswith(LABEL_PREFIX):
            continue
        parts = k[len(LABEL_PREFIX):].split(".")
        if len(parts) < 3 or parts[0] not in ("routers", "services"):
            continue
        out[parts[0]].setdefault(parts[1], {})[".".join(parts[2:]).lower()] = str(val)
    return out


def _disabled(labels: dict | None) -> bool:
    return str((labels or {}).get("traefik.enable", "")).strip().lower() == "false"


def labels_links(c: dict) -> list[dict]:
    """Routeurs que les labels de ce conteneur créent chez Traefik."""
    if _disabled(c.get("labels")):
        return []
    decl = parse_labels(c.get("labels"))
    links = []
    for name, r in sorted(decl["routers"].items()):
        svc = r.get("service") or (name if name in decl["services"] else next(iter(decl["services"]), name))   # sans « service » : celui du conteneur
        port = (decl["services"].get(svc.split("@")[0]) or {}).get("loadbalancer.server.port")
        rule = r.get("rule", "")
        links.append({"router": f"{name}@docker", "provider": "docker", "service": svc, "rule": rule, "hosts": _hosts(rule), "via": "labels",
                      "sure": True, "port": int(port) if str(port or "").isdigit() else None, "item": None})
    return links


def _service_urls(spec: dict) -> list[str]:
    lb = (spec or {}).get("loadBalancer") or {}
    return [s.get("url") for s in (lb.get("servers") or []) if isinstance(s, dict) and isinstance(s.get("url"), str)]


def _match(container: dict, host: str, port: int | None, unique_service: bool) -> tuple[bool, bool] | None:
    """Ce conteneur est-il celui que désigne « host:port » ? Renvoie (correspond, sûr) ou None."""
    host = (host or "").lower()
    exact = host == (container.get("name") or "").lower() or host in [i.lower() for i in container.get("ips") or []]
    alias = bool(container.get("service")) and host == container["service"].lower()
    if not exact and not alias:
        return None
    ports = container.get("ports") or []
    port_ok = port is None or not ports or port in ports
    return True, (exact or unique_service) and port_ok


def file_links(c: dict, containers: list[dict], items: list[dict]) -> list[dict]:
    """Routeurs d'un fichier YAML qui mènent à ce conteneur (par l'URL de leur service)."""
    same_service = sum(1 for o in containers if o.get("service") and o["service"].lower() == (c.get("service") or "").lower())
    http = [i for i in items if i.get("proto") == "http"]
    routers = [i for i in http if i.get("kind") == "routers"]
    links = []
    for s in (i for i in http if i.get("kind") == "services"):
        hit = None
        for url in _service_urls(s.get("spec")):
            u = urlparse(url)
            m = _match(c, u.hostname or "", u.port or (443 if u.scheme == "https" else 80 if u.scheme == "http" else None), same_service == 1)
            if m:
                hit = (u.port, m[1]) if not hit or m[1] else hit
        if not hit:
            continue
        for r in routers:
            target = str((r.get("spec") or {}).get("service") or "")
            if target.split("@")[0] != s["name"] or target.endswith("@docker") or target.endswith("@internal"):
                continue
            rule = (r.get("spec") or {}).get("rule", "")
            links.append({"router": r["name"], "provider": "file", "service": s["name"], "rule": rule, "hosts": _hosts(rule), "via": "url",
                          "sure": hit[1], "port": hit[0], "item": r.get("id")})
    return links


def address_links(c: dict, items: list[dict], addresses: list[str]) -> list[dict]:
    """Routeurs d'un Traefik d'un AUTRE nœud qui mènent à ce conteneur : l'URL de leur service vise une adresse de la machine du conteneur ET un port qu'il PUBLIE.
    (Entre deux machines, un nom de conteneur ne se résout pas : c'est « adresse:port-publié » que Traefik vise.) c["published"] : ports publiés sur l'hôte."""
    addrs = {a.lower() for a in addresses or []}
    pub = set(c.get("published") or [])
    if not addrs or not pub:
        return []
    http = [i for i in items if i.get("proto") == "http"]
    routers = [i for i in http if i.get("kind") == "routers"]
    links = []
    for s in (i for i in http if i.get("kind") == "services"):
        port = None
        for url in _service_urls(s.get("spec")):
            u = urlparse(url)
            p = u.port or (443 if u.scheme == "https" else 80 if u.scheme == "http" else None)
            if (u.hostname or "").lower() in addrs and p in pub:
                port = p
                break
        if port is None:
            continue
        for r in routers:
            target = str((r.get("spec") or {}).get("service") or "")
            if target.split("@")[0] != s["name"] or target.endswith("@docker") or target.endswith("@internal"):
                continue
            rule = (r.get("spec") or {}).get("rule", "")
            links.append({"router": r["name"], "provider": "file", "service": s["name"], "rule": rule, "hosts": _hosts(rule), "via": "address", "sure": True, "port": port, "item": r.get("id")})
    return links


def links_for(container: dict, containers: list[dict], items: list[dict]) -> list[dict]:
    """Tous les routeurs Traefik qui mènent à ce conteneur, les plus sûrs d'abord."""
    out = labels_links(container) + file_links(container, containers, items)
    return sorted(out, key=lambda x: (not x["sure"], x["router"]))


def link_all(containers: list[dict], items: list[dict]) -> dict[str, list[dict]]:
    """{nom du conteneur: liens} pour tous les conteneurs qui en ont."""
    res = {c["name"]: links_for(c, containers, items) for c in containers}
    return {k: v for k, v in res.items() if v}


def by_router(linked: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Sens inverse : {routeur: [{"container", "sure"}]} pour afficher, sur un site, le conteneur qui le sert."""
    out: dict[str, list[dict]] = {}
    for name, links in linked.items():
        for lk in links:
            out.setdefault(lk["router"], []).append({"container": name, "sure": lk["sure"]})
    return out


def hosts_of(links: list[dict]) -> list[str]:
    """Adresses (sites) par lesquelles on atteint un conteneur : sert à retrouver ses statistiques d'accès et ses IP (par hôte)."""
    return sorted({h for lk in links for h in lk["hosts"]})
