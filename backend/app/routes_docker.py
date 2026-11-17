"""Docker d'un nœud : conteneurs, journaux, statistiques, images, réseaux, volumes — et leur lien avec Traefik.

Les opérations sont des opérations nommées (docker_ops) : exécutées ici pour le nœud du hub, envoyées au worker pour un nœud distant.
Chaque route exige le module « docker » sur le nœud ET un droit fin ; tout ce qui agit est audité."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import docker_link, docker_ops, host_ops, linkage, worker_link
from .config import settings
from .nodes import NodeCtx, node_ctx
from .traefik_store import StoreError

router = APIRouter(prefix="/api/docker", tags=["docker"])


def docker_cap(cap: str):
    """Dépendance : le nœud gère Docker ET la personne a ce droit fin."""
    def dep(ctx: NodeCtx = Depends(node_ctx)) -> NodeCtx:
        if "docker" not in (ctx.node.get("modules") or []):
            raise HTTPException(409, "Ce nœud ne gère pas Docker")
        ctx.need(cap)
        return ctx
    return dep


def run(ctx: NodeCtx, op: str, args: dict | None = None):
    """Exécute une opération Docker sur le nœud (ici, ou via son worker) ; les erreurs deviennent des réponses HTTP."""
    try:
        if ctx.node["kind"] == "remote":
            return worker_link.call(ctx.id, op, args or {})
        return host_ops.handle(op, args or {})
    except StoreError as e:
        raise HTTPException(e.status, str(e))


def _protected(c: dict) -> str:
    return "self" if c.get("self") else "traefik" if c["name"] == settings.traefik_container else ""


def _find(containers: list[dict], key: str) -> dict:
    for c in containers:
        if key in (c["name"], c["id"]) or (len(key) >= 4 and c["fullId"].startswith(key)):
            return c
    raise HTTPException(404, "Conteneur introuvable")


@router.get("/info")
def info(ctx: NodeCtx = Depends(docker_cap("docker.view"))):
    return run(ctx, "d_info")


@router.get("/containers")
def containers(ctx: NodeCtx = Depends(docker_cap("docker.view"))):
    lst = run(ctx, "d_containers", {"all": True})["containers"]
    links = docker_link.link_containers(ctx, lst)
    for c in lst:
        c["links"] = links.get(c["name"], [])
        c["protected"] = _protected(c)
    return {"containers": lst}


@router.get("/containers/{cid}")
def container(cid: str, days: int = 7, ctx: NodeCtx = Depends(docker_cap("docker.view"))):
    lst = run(ctx, "d_containers", {"all": True})["containers"]
    c = _find(lst, cid)
    d = run(ctx, "d_container", {"id": c["fullId"]})
    lmap = docker_link.link_containers(ctx, lst)
    links = lmap.get(c["name"], [])
    d["links"], d["protected"] = links, _protected(c)
    d["project"], d["service"], d["workingDir"] = c["project"], c["service"], c["workingDir"]
    d["access"] = docker_link.access_for(ctx, links, lmap.ctxs, max(1, min(days, 7)))
    d["canLogs"], d["canControl"], d["canRemove"], d["canExec"] = (ctx.can("docker.logs"), ctx.can("docker.control"), ctx.can("docker.remove"), ctx.can("docker.exec"))
    d["execAvailable"] = ctx.node["kind"] != "remote" or worker_link.feature(ctx.id, "exec")   # un worker n'ouvre le terminal que s'il l'a autorisé (WORKER_ALLOW_EXEC)
    return d


@router.get("/containers/{cid}/publish-hint")
def publish_hint(cid: str, ctx: NodeCtx = Depends(docker_cap("docker.view"))):
    """De quoi publier ce conteneur sur Traefik : l'adresse cible à viser, le port, et si Traefik peut l'atteindre (réseau partagé)."""
    lst = run(ctx, "d_containers", {"all": True})["containers"]
    c = _find(lst, cid)
    prov = docker_link.traefik_provider(ctx, lst)
    lab = linkage.parse_labels(c["labels"])
    declared = next((str((s or {}).get("loadbalancer.server.port")) for s in lab["services"].values() if (s or {}).get("loadbalancer.server.port")), "")
    exposed = [p["private"] for p in c["ports"] if p["type"] == "tcp" and isinstance(p["private"], int)]
    ports = sorted(set(exposed), key=lambda p: (p not in (80, 8080, 3000, 8000), p))
    port = int(declared) if declared.isdigit() else (ports[0] if ports else None)
    shared = [n for n in c["networks"] if n in prov["networks"]]
    via = None
    if ctx.node.get("traefikVia"):   # les sites de ce nœud passent par le Traefik d'un AUTRE nœud : il joint le conteneur par l'adresse de la machine et le port PUBLIÉ
        from .nodes import accessible_nodes
        other = next((x for x in accessible_nodes(ctx.p) if x.id == ctx.node["traefikVia"]), None)
        if other:
            addrs = docker_link.machine_addresses(ctx.node)
            pub = next((p["public"] for p in c["ports"] if p["public"]), None)
            via = {"id": other.id, "name": other.node["name"], "canCreate": other.can("sites.create"), "address": addrs[0] if addrs else "", "publicPort": pub}
    url = f"http://{c['name']}:{port}" if port else f"http://{c['name']}:"
    if via and via["address"] and via["publicPort"]:
        url = f"http://{via['address']}:{via['publicPort']}"
    return {"name": (c["service"] or c["name"]).lower(), "container": c["name"], "port": port, "ports": ports, "url": url, "via": via,
            "networks": sorted(c["networks"]), "traefik": {**prov, "sharedNetworks": shared, "reachable": bool(shared) if prov["found"] and not via else None},
            "linked": bool(docker_link.link_containers(ctx, lst).get(c["name"])), "canCreate": via["canCreate"] if via else ctx.can("sites.create")}


class ActionBody(BaseModel):
    action: str = Field(max_length=20)
    confirm: str = Field(default="", max_length=200)   # le nom du conteneur, exigé pour agir sur celui de l'interface ou de Traefik


def _guard_critical(ctx: NodeCtx, cid: str, confirm: str, what: str) -> dict:
    c = _find(run(ctx, "d_containers", {"all": True})["containers"], cid)
    p = _protected(c)
    if p and confirm != c["name"]:
        who = "de l'interface" if p == "self" else "de Traefik"
        raise HTTPException(409, f"« {c['name']} » est le conteneur {who} : {what} peut couper l'accès. Retapez son nom pour confirmer")
    return c


@router.post("/containers/{cid}/action")
def action(cid: str, body: ActionBody, ctx: NodeCtx = Depends(docker_cap("docker.control"))):
    if body.action not in docker_ops.ACTIONS:
        raise HTTPException(422, "Action inconnue")
    c = _guard_critical(ctx, cid, body.confirm, "l'arrêter ou le relancer") if body.action in ("stop", "restart") else _find(run(ctx, "d_containers", {"all": True})["containers"], cid)
    r = run(ctx, "d_action", {"id": c["fullId"], "action": body.action})
    ctx.audit(f"docker.{body.action}", c["name"])
    return r


@router.delete("/containers/{cid}")
def remove_container(cid: str, force: bool = False, volumes: bool = False, confirm: str = "", ctx: NodeCtx = Depends(docker_cap("docker.remove"))):
    c = _guard_critical(ctx, cid, confirm, "le supprimer")
    r = run(ctx, "d_remove_container", {"id": c["fullId"], "force": force, "volumes": volumes})
    ctx.audit("docker.remove", f"conteneur {c['name']}{' + volumes' if volumes else ''}")
    return r


@router.get("/containers/{cid}/logs")
def logs(cid: str, tail: int = 300, since: float = 0, ctx: NodeCtx = Depends(docker_cap("docker.logs"))):
    c = _find(run(ctx, "d_containers", {"all": True})["containers"], cid)
    return run(ctx, "d_logs", {"id": c["fullId"], "tail": tail, "since": since})


@router.get("/stats")
def stats(ctx: NodeCtx = Depends(docker_cap("docker.view"))):
    return run(ctx, "d_stats", {})


@router.get("/images")
def images(ctx: NodeCtx = Depends(docker_cap("docker.view"))):
    return run(ctx, "d_images")


@router.get("/networks")
def networks(ctx: NodeCtx = Depends(docker_cap("docker.view"))):
    return run(ctx, "d_networks")


@router.get("/volumes")
def volumes(ctx: NodeCtx = Depends(docker_cap("docker.view"))):
    return run(ctx, "d_volumes")


@router.delete("/images/{iid}")
def remove_image(iid: str, force: bool = False, ctx: NodeCtx = Depends(docker_cap("docker.remove"))):
    r = run(ctx, "d_remove_image", {"id": iid, "force": force})
    ctx.audit("docker.remove", f"image {iid}")
    return r


@router.delete("/networks/{nid}")
def remove_network(nid: str, ctx: NodeCtx = Depends(docker_cap("docker.remove"))):
    r = run(ctx, "d_remove_network", {"id": nid})
    ctx.audit("docker.remove", f"réseau {nid}")
    return r


@router.delete("/volumes/{name}")
def remove_volume(name: str, force: bool = False, ctx: NodeCtx = Depends(docker_cap("docker.remove"))):
    r = run(ctx, "d_remove_volume", {"id": name, "force": force})
    ctx.audit("docker.remove", f"volume {name}")
    return r


class PruneBody(BaseModel):
    what: str = Field(max_length=20)
    all: bool = False


@router.post("/prune")
def prune(body: PruneBody, ctx: NodeCtx = Depends(docker_cap("docker.remove"))):
    r = run(ctx, "d_prune", {"what": body.what, "all": body.all})
    ctx.audit("docker.prune", f"{body.what}{' (toutes)' if body.all else ''} : {r['deleted']} supprimé(s)")
    return r


# ── détail des images, réseaux et volumes ─────────────────────────────────────────────────────────────────────────────

@router.get("/images/{iid}/detail")
def image_detail(iid: str, ctx: NodeCtx = Depends(docker_cap("docker.view"))):
    return run(ctx, "d_image", {"id": iid})


@router.get("/networks/{nid}/detail")
def network_detail(nid: str, ctx: NodeCtx = Depends(docker_cap("docker.view"))):
    d = run(ctx, "d_network", {"id": nid})
    d["canControl"] = ctx.can("docker.control")
    return d


class NetworkNew(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    subnet: str = Field(default="", max_length=64)
    internal: bool = False


@router.post("/networks")
def network_create(body: NetworkNew, ctx: NodeCtx = Depends(docker_cap("docker.control"))):
    r = run(ctx, "d_network_create", body.model_dump())
    ctx.audit("docker.network.create", body.name)
    return r


class NetworkLink(BaseModel):
    container: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    force: bool = False


@router.post("/networks/{nid}/connect")
def network_connect(nid: str, body: NetworkLink, ctx: NodeCtx = Depends(docker_cap("docker.control"))):
    r = run(ctx, "d_network_connect", {"network": nid, "container": body.container, "aliases": body.aliases})
    ctx.audit("docker.network.connect", f"{body.container} → {nid}")
    return r


@router.post("/networks/{nid}/disconnect")
def network_disconnect(nid: str, body: NetworkLink, ctx: NodeCtx = Depends(docker_cap("docker.control"))):
    r = run(ctx, "d_network_disconnect", {"network": nid, "container": body.container, "force": body.force})
    ctx.audit("docker.network.disconnect", f"{body.container} ✕ {nid}")
    return r


@router.get("/volumes/{name}/detail")
def volume_detail(name: str, ctx: NodeCtx = Depends(docker_cap("docker.view"))):
    d = run(ctx, "d_volume", {"id": name})
    d["canBrowse"] = ctx.can("docker.exec") and (ctx.node["kind"] != "remote" or bool(worker_link.feature(ctx.id, "exec")))
    return d


def _browse_ok(ctx: NodeCtx) -> None:
    if ctx.node["kind"] == "remote" and not worker_link.feature(ctx.id, "exec"):
        raise HTTPException(409, "Ce worker n'autorise pas l'accès aux conteneurs (WORKER_ALLOW_EXEC=true)")


@router.get("/volumes/{name}/files")
def volume_files(name: str, path: str = "", ctx: NodeCtx = Depends(docker_cap("docker.exec"))):
    _browse_ok(ctx)
    r = run(ctx, "d_exec_volls", {"id": name, "path": path})
    ctx.audit("docker.volume.browse", f"{name}:{path or '/'}")
    return r


@router.get("/volumes/{name}/file")
def volume_file(name: str, path: str, ctx: NodeCtx = Depends(docker_cap("docker.exec"))):
    _browse_ok(ctx)
    r = run(ctx, "d_exec_volread", {"id": name, "path": path})
    ctx.audit("docker.volume.read", f"{name}:{path}")
    return r


@router.get("/traefik-status")
def traefik_status(ctx: NodeCtx = Depends(docker_cap("docker.view"))):
    """Traefik tourne-t-il sur cette machine ? (sert à proposer d'en installer un quand il manque)"""
    lst = run(ctx, "d_containers", {"all": True})["containers"]
    prov = docker_link.traefik_provider(ctx, lst)
    return {"found": bool(prov.get("found")), "local": ctx.node["kind"] != "remote"}


@router.get("/via-sites")
def via_sites(ctx: NodeCtx = Depends(docker_cap("docker.view"))):
    """Pour un nœud dont les sites passent par le Traefik d'un autre nœud : les sites de ce Traefik qui mènent aux conteneurs de CETTE machine."""
    via = ctx.node.get("traefikVia") or ""
    lst = run(ctx, "d_containers", {"all": True})["containers"]
    links = docker_link.link_containers(ctx, lst)
    agg: dict[tuple, dict] = {}
    for c in lst:
        for lk in links.get(c["name"], []):
            if not lk.get("node"):
                continue
            key = (lk["node"]["id"], lk["router"])
            a = agg.setdefault(key, {"router": lk["router"], "rule": lk.get("rule") or "", "hosts": lk.get("hosts") or [], "sure": bool(lk.get("sure")), "provider": lk.get("provider") or "",
                                     "node": lk["node"], "containers": []})
            a["sure"] = a["sure"] or bool(lk.get("sure"))
            if c["name"] not in a["containers"]:
                a["containers"].append(c["name"])
    other = None
    if via:
        from .nodes import accessible_nodes
        o = next((x for x in accessible_nodes(ctx.p) if x.id == via), None)
        other = {"id": via, "name": o.node["name"], "canCreate": o.can("sites.create")} if o else None
    return {"via": other, "addresses": docker_link.machine_addresses(ctx.node), "sites": sorted(agg.values(), key=lambda a: (not a["sure"], a["router"]))}
