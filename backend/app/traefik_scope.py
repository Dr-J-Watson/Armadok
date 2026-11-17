"""Visibilité et droits d'édition des éléments Traefik selon l'utilisateur."""
from .perms import LEVELS, Principal, accessible_domains, hosts_min_level
from .rules import host_constraint, parse
from .traefik_lint import used_by


def router_hosts(spec: dict) -> set[str] | None:
    try:
        return host_constraint(parse((spec or {}).get("rule", "")))
    except ValueError:
        return None


def router_level(p: Principal, spec: dict) -> str | None:
    hosts = router_hosts(spec)
    return hosts_min_level(p, hosts) if hosts else None


def scope_items(p: Principal, items: list[dict]) -> list[dict]:
    """Ajoute `editable` à chaque élément et ne garde que ce que `p` a le droit de voir. Si la vue est filtrée sur un autre nœud (`scope_routers`), ne garde
    que les routeurs de ce nœud et ce qu'ils utilisent (services, middlewares, options)."""
    out = _scope_items(p, items)
    return _restrict(p, out) if p.scope_routers is not None else out


def _restrict(p: Principal, items: list[dict]) -> list[dict]:
    keep = {i["id"] for i in items if i["proto"] == "http" and i["kind"] == "routers" and i["name"] in p.scope_routers}
    usage = used_by(items)
    changed = True
    while changed:   # un middleware utilisé par un middleware gardé est gardé aussi
        changed = False
        for it in items:
            if it["id"] in keep or it["proto"] != "http" or it["kind"] == "routers":
                continue
            if any(u in keep for u in usage.get(it["id"], [])):
                keep.add(it["id"])
                changed = True
    out = [it for it in items if it["id"] in keep]
    for it in out:
        it["usedBy"] = [u for u in usage.get(it["id"], []) if u in keep]
    return out


def _scope_items(p: Principal, items: list[dict]) -> list[dict]:
    """Ajoute `editable` à chaque élément et ne garde que ce que `p` a le droit de voir."""
    usage = used_by(items)
    if p.traefik == "full":
        for it in items:
            it["editable"] = True
            it["usedBy"] = usage.get(it["id"], [])
        return items
    if p.traefik != "scoped":
        return []
    http_routers = [i for i in items if i["proto"] == "http" and i["kind"] == "routers"]
    level = {r["name"]: router_level(p, r["spec"]) for r in http_routers}
    visible_routers = {n for n, l in level.items() if l}
    editable_routers = {n for n, l in level.items() if l and LEVELS[l] >= LEVELS["edit"]}
    out = []
    for it in items:
        if it["proto"] != "http":
            continue
        if it["kind"] == "routers" and it["name"] in visible_routers:
            it["editable"] = it["name"] in editable_routers
            out.append(it)
        elif it["kind"] == "services":
            users = [u.split("/")[-1] for u in usage.get(it["id"], [])]
            mine = [u for u in users if u in visible_routers]
            if mine:
                it["editable"] = all(u in editable_routers for u in users)
                out.append(it)
        elif it["kind"] == "middlewares":  # noms et types seulement : les réglages peuvent contenir des secrets
            out.append({**it, "spec": {it["type"]: {}} if it.get("type") else {}, "editable": False})
    ids = {i["id"] for i in out}
    for it in out:
        it["usedBy"] = [u for u in usage.get(it["id"], []) if u in ids]
    return out
