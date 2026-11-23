"""Certificats HTTPS d'un nœud : ce que Traefik a obtenu, jusqu'à quand, quels sites sont concernés, et de quoi les gérer depuis l'interface.

 - Supprimer / renouveler : l'interface arrête Traefik, retire l'entrée d'acme.json (sauvegarde faite avant), le relance. Quelques secondes de
   coupure : Traefik garde acme.json en mémoire, une entrée retirée à chaud reviendrait. Les clés ne sont jamais envoyées au navigateur.
 - Redemander le certificat d'un site qui n'en a pas : un changement de configuration sans effet (un middleware inutilisé) fait ré-évaluer à
   Traefik les noms qui n'ont pas de certificat. Aucun redémarrage."""

import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import certpurge
from . import certs as certlib
from .config import settings
from .nodes import NodeCtx, node_ctx
from .perms import accessible_domains, match_domain
from .traefik_scope import scope_items
from .traefik_store import StoreError

router = APIRouter(prefix="/api")


@router.get("/traefik/certs")
def certificates(ctx: NodeCtx = Depends(node_ctx)):
    ctx.need("certs.view")
    data = ctx.store.certs()
    now = time.time()
    items, _ = ctx.store.items()
    visible = scope_items(ctx.p, [dict(i) for i in items])
    certs = data["certs"]
    if ctx.p.traefik != "full":  # un éditeur ne voit que les certificats de ses domaines
        doms = accessible_domains(ctx.p)
        certs = [c for c in certs if any(match_domain(n.lstrip("*.") if n.startswith("*.") else n, doms) for n in (c["names"] or [c["main"]]))]
    sites = certlib.sites(visible, certs, now) if data["configured"] else []
    out = []
    for c in sorted(certs, key=lambda c: c["notAfter"]):
        used = [s for s in sites if any(h["host"] and certlib.covers(c, h["host"]) for h in s["hosts"])]
        sole = [s["name"] for s in used if any(certlib.covers(c, h["host"]) and not any(o is not c and certlib.covers(o, h["host"]) and o["notAfter"] > now for o in certs) for h in s["hosts"])]
        out.append({**c, "daysLeft": certlib.days_left(c, now), "usedBy": [s["name"] for s in used][:20], "soleFor": sole[:20]})
    manage = ctx.can("certs.manage") and ctx.p.traefik == "full"
    return {"configured": data["configured"], "error": data["error"], "updated": data["updated"], "certs": out, "sites": sites,
            "warnings": sum(1 for s in sites if s["worst"] in ("expired", "missing", "expiring")),
            "canManage": manage, "purgeSupported": manage and ctx.node["kind"] == "local" and _why_not() is None,
            "purgeWhy": _why_not() if manage and ctx.node["kind"] == "local" else None}


# ------------------------------------------------------------------ supprimer / renouveler (Traefik arrêté puis relancé, quelques secondes)
_job = {"running": False, "result": None}
_job_lock = threading.Lock()


def _local_manage(ctx: NodeCtx) -> None:
    ctx.need("certs.manage")
    if ctx.p.traefik != "full":
        raise HTTPException(403, "Gérer les certificats demande de voir tout Traefik")
    if ctx.node["kind"] != "local":
        raise HTTPException(400, "Retirer ou renouveler un certificat n'est pas encore pris en charge sur un nœud distant")


def _why_not() -> str | None:
    return certpurge.unavailable(settings.certs_file, settings.docker_socket)


@router.get("/traefik/certs/pending")
def pending(ctx: NodeCtx = Depends(node_ctx)):
    ctx.need("certs.view")
    return {"running": _job["running"], "result": _job["result"] if ctx.node["kind"] == "local" else None}


class PurgeItem(BaseModel):
    resolver: str = Field(default="", max_length=60)
    main: str = Field(min_length=1, max_length=253)


class PurgeBody(BaseModel):
    items: list[PurgeItem] = Field(min_length=1, max_length=50)
    mode: str = "delete"   # « delete » : retirer ; « renew » : retirer pour que Traefik en obtienne un neuf


def _do_purge(asked: list[dict]) -> None:
    try:
        _job["result"] = certpurge.run(Path(settings.certs_file), asked, settings.docker_socket, settings.traefik_container)
    except Exception as e:   # noqa: BLE001
        _job["result"] = {"version": 1, "at": time.time(), "removed": [], "missing": [], "backup": None, "restarted": False, "error": f"Erreur inattendue ({type(e).__name__})"}
    finally:
        _job["running"] = False


@router.post("/traefik/certs/purge", status_code=202)
def request_purge(body: PurgeBody, ctx: NodeCtx = Depends(node_ctx)):
    _local_manage(ctx)
    if body.mode not in ("delete", "renew"):
        raise HTTPException(422, "Action inconnue")
    why = _why_not()
    if why:
        raise HTTPException(422, f"Impossible depuis l'interface : {why}. L'aide (Fonctionnalités facultatives → Certificats) explique comment l'activer.")
    known = {(c["resolver"], c["main"]) for c in ctx.store.certs()["certs"]}   # on ne prend que des certificats que le relevé connaît : pas de texte libre
    asked = []
    for it in body.items:
        key = (it.resolver.strip(), it.main.strip().lower())
        if key not in known:
            raise HTTPException(422, f"Certificat inconnu : {key[1]} (le relevé a peut-être changé : rechargez la page)")
        asked.append({"resolver": key[0], "main": key[1], "mode": body.mode})
    with _job_lock:
        if _job["running"]:
            raise HTTPException(409, "Une opération sur les certificats est déjà en cours")
        _job["running"], _job["result"] = True, None
    ctx.audit("cert.purge", f"{body.mode}: " + ", ".join(a["main"] for a in asked))
    threading.Thread(target=_do_purge, args=(asked,), daemon=True).start()
    return {"running": True}


# ------------------------------------------------------------------ redemander le certificat d'un site
NUDGE_FILE, NUDGE_NAME = "zz-webui-nudge.yml", "webui-nudge"
_last_nudge: dict[str, float] = {}
_nudge_lock = threading.Lock()


class RequestBody(BaseModel):
    host: str = Field(min_length=3, max_length=253)


@router.post("/traefik/certs/request")
def request_certificate(body: RequestBody, ctx: NodeCtx = Depends(node_ctx)):
    ctx.need("certs.manage")
    if ctx.p.traefik != "full":
        raise HTTPException(403, "Gérer les certificats demande de voir tout Traefik")
    host = body.host.strip().lower()
    data = ctx.store.certs()
    items, _ = ctx.store.items()
    now = time.time()
    site = next((i for i in items if i["kind"] == "routers" and i["proto"] == "http" and not i.get("disabled") and host in [h.lower() for h in certlib.extract_hosts((i["spec"] or {}).get("rule", ""))]), None)
    if not site:
        raise HTTPException(404, f"Aucun site actif n'utilise {host}")
    resolver = ((site["spec"] or {}).get("tls") or {}).get("certResolver")
    if not resolver:
        raise HTTPException(422, f"Le site « {site['name']} » n'a pas de résolveur de certificats : ouvrez-le et choisissez-en un dans « Certificat automatique », sinon Traefik n'en demandera jamais")
    covering = [c for c in data["certs"] if certlib.covers(c, host)] if data["configured"] else []
    if any(c["notAfter"] > now for c in covering):
        raise HTTPException(409, f"{host} a déjà un certificat valide")
    if covering:   # un certificat expiré est DÉJÀ dans acme.json : Traefik ne redemande que les noms qui en sont absents
        raise HTTPException(409, f"{host} a un certificat expiré encore dans acme.json : Traefik ne le redemande pas tout seul. Utilisez « Renouveler » (retrait de l'entrée, appliqué Traefik arrêté)")
    with _nudge_lock:
        if now - _last_nudge.get(ctx.id, 0) < 30:
            raise HTTPException(429, "Une demande vient d'être faite : attendez 30 secondes (Let's Encrypt limite les essais ratés à 5 par heure et par nom)")
        _last_nudge[ctx.id] = now
    try:
        ctx.store.upsert("http", "middlewares", NUDGE_NAME, {"stripPrefix": {"prefixes": [f"/-nudge-{int(now)}"]}}, file=NUDGE_FILE)
    except StoreError as e:
        raise HTTPException(e.status, str(e))
    ctx.audit("cert.ask", f"{host} ({resolver})")
    return {"ok": True, "resolver": resolver, "site": site["name"]}
