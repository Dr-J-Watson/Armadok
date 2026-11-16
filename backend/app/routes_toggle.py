"""Activer / désactiver (mettre en pause) des sites, protections, services, options TLS, fichiers et domaines d'un nœud."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import caps as capmod
from .nodes import NodeCtx, node_traefik
from .perms import accessible_domains, require_domain
from .traefik_lint import extract_hosts
from .traefik_scope import scope_items

router = APIRouter(prefix="/api")


def _run(ctx: NodeCtx, ids: list[str], files: list[str], enable: bool, tag: str | None, dry: bool) -> dict:
    p, store = ctx.p, ctx.store
    scoped = p.traefik == "scoped"
    items0, _ = store.items()
    kinds = {i["id"]: i for i in items0}
    for i in ids:   # chaque famille d'éléments a son droit de mise en pause
        it = kinds.get(i)
        if it:
            ctx.need(capmod.prefix_of(it["proto"], it["kind"]) + ".toggle")
    if files:
        ctx.need("files.toggle")
    if scoped:
        if files:
            raise HTTPException(403, "Droits insuffisants")
        items, _ = store.items()
        vis = {i["id"]: i for i in scope_items(p, [dict(i) for i in items])}
        for i in ids:
            it = vis.get(i)
            if not it or it["proto"] != "http" or it["kind"] != "routers" or not it["editable"]:
                raise HTTPException(403, "Vous ne pouvez agir que sur vos propres sites")
        if enable and store.toggle(ids, [], True, None, reconcile=False, dry=True)["cascade"]:
            raise HTTPException(409, "Le service de ce site est désactivé : demandez à un administrateur du nœud de le réactiver")
    plan = store.toggle(ids, files, enable, tag, reconcile=not scoped, dry=True)
    for b in plan["cascade"]:   # ce qui tombe (ou revient) avec l'élément : même exigence de droit
        ctx.need(capmod.prefix_of("tls" if b["kind"] in ("options", "stores") else "http", b["kind"]) + ".toggle")
    result = plan if dry else store.toggle(ids, files, enable, tag, reconcile=not scoped, dry=False)
    if not dry:
        what = ", ".join([b["id"] for b in result["blocks"]] + [f["path"] for f in result["files"]])[:300]
        ctx.audit("traefik.enable" if enable else "traefik.disable", what)
    return result


class ToggleBody(BaseModel):
    ids: list[str] = Field(default=[], max_length=200)
    files: list[str] = Field(default=[], max_length=50)
    enable: bool
    dry: bool = False


@router.post("/traefik/toggle")
def toggle(body: ToggleBody, ctx: NodeCtx = Depends(node_traefik)):
    if not body.ids and not body.files:
        raise HTTPException(422, "Rien à modifier")
    return _run(ctx, body.ids, body.files, body.enable, None, body.dry)


def _sites(ctx: NodeCtx, domain: str) -> list[dict]:
    items, _ = ctx.store.items()
    out = []
    for it in scope_items(ctx.p, [dict(i) for i in items]):
        if it["kind"] != "routers" or it["proto"] != "http":
            continue
        hosts = extract_hosts((it["spec"] or {}).get("rule", ""))
        if hosts and all(h == domain or h.endswith("." + domain) for h in hosts):
            out.append(it)
    return out


@router.get("/traefik/toggle/domains")
def domains_state(ctx: NodeCtx = Depends(node_traefik)):
    out = []
    for name in sorted(accessible_domains(ctx.p)):
        sites = _sites(ctx, name)
        tag = f"domain:{name}"
        out.append({"domain": name, "sites": len(sites), "paused": sum(1 for s in sites if s.get("pausedBy") == tag),
                    "disabled": sum(1 for s in sites if s["disabled"])})
    return out


class DomainBody(BaseModel):
    domain: str
    paused: bool
    dry: bool = False


@router.post("/traefik/toggle/domain")
def toggle_domain(body: DomainBody, ctx: NodeCtx = Depends(node_traefik)):
    d, _ = require_domain(ctx.p, body.domain.lower(), "edit")
    name, tag = d["name"].lower(), f"domain:{d['name'].lower()}"
    sites = [s for s in _sites(ctx, name) if s["editable"]]
    ids = [s["id"] for s in sites if (not s["disabled"] if body.paused else s.get("pausedBy") == tag)]
    if not ids:
        return {"blocks": [], "cascade": [], "files": [], "refs": [], "skipped": [], "enable": not body.paused, "applied": False}
    return _run(ctx, ids, [], not body.paused, tag if body.paused else None, body.dry)
