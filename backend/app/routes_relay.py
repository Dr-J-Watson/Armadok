"""Côté hub d'origine : l'interface travaille sur les nœuds d'un hub lié à travers /api/linked/{liaison}/… (voir link_relay)."""
import asyncio
import base64
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from . import hub_link as hl
from . import link_proto as proto
from . import link_relay as relay
from .audit import audit
from .auth import current_user
from .perms import Principal

router = APIRouter(prefix="/api", tags=["linked"])


def _wrap(res: dict) -> Response:
    try:
        body = base64.b64decode(res.get("body") or "")
    except Exception:
        raise HTTPException(502, "Réponse invalide du hub lié")
    status = res.get("status") if isinstance(res.get("status"), int) and 100 <= res["status"] < 600 else 502
    headers = {}
    disp = res.get("disposition")
    if isinstance(disp, str) and disp.startswith(("attachment", "inline")) and "\n" not in disp and len(disp) < 300:
        headers["Content-Disposition"] = disp
    ctype = res.get("ctype") if isinstance(res.get("ctype"), str) and res["ctype"].isprintable() and len(res["ctype"]) < 200 else None
    return Response(content=body, status_code=status, media_type=ctype or "application/json", headers=headers)


@router.api_route("/linked/{lid}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def through(lid: str, path: str, request: Request, p: Principal = Depends(current_user)):
    body = await request.body()
    sub = "/" + path
    if request.method != "GET":
        audit(p.username, "link.relay", f"{lid[:8]} {request.method} {sub}")
    res = await relay.call(lid, p, request.method, sub, request.url.query, request.headers.get("x-node", ""), body, request.headers.get("content-type", ""))
    return _wrap(res)


@router.post("/linked-setup/{lid}")
async def setup_link(lid: str, p: Principal = Depends(current_user)):
    """Demande à l'autre hub un lien à usage unique pour y définir MON mot de passe (mode direct). Le mot de passe se tape sur le domaine de l'autre hub, jamais ici."""
    res = await relay.call(lid, p, "POST", "/", "", "", b"", "", op="setup")
    body = json.loads(base64.b64decode(res.get("body") or "") or b"{}")
    if res.get("status") != 200 or not isinstance(body, dict) or not body.get("token"):
        raise HTTPException(res.get("status") if isinstance(res.get("status"), int) and 400 <= res["status"] < 600 else 502, body.get("detail", "Le hub lié a refusé") if isinstance(body, dict) else "Le hub lié a refusé")
    r = hl.get(lid)
    ui = r["peer_ui_url"] if r else ""
    audit(p.username, "link.relay.setup", lid[:8])
    return {"token": body["token"], "url": f"{ui}/guest-setup#{body['token']}" if ui else "", "expiresIn": 600}


@router.get("/linked-nodes")
async def linked_nodes(p: Principal = Depends(current_user)):
    """Les nœuds des hubs liés auxquels cette personne a accès (rien si aucune de ses équipes n'est partagée), un bloc par liaison."""
    out = []
    for r in hl.all_links():
        if not hl.usable(r) or not relay.can_use(p.id, r["id"]):
            continue
        item = {"linkId": r["id"], "hub": r["peer_name"], "url": r["peer_ui_url"], "direct": bool(r["peer_direct"]), "state": hl.public(r)["state"], "nodes": [], "error": ""}
        if not proto.RELAY_FEATURE in (r["features"] or "").split(","):
            item["error"] = "Versions incompatibles pour le relais : mettez à jour le plus ancien des deux hubs." if r["protocol"] or r["last_error"] else "En attente de connexion."
        elif not hl.online(r["id"]):
            item["error"] = "Hub hors ligne."
        else:
            try:
                res = await relay.call(r["id"], p, "GET", "/nodes", "", "", b"", "")
                if res.get("status") == 200:
                    data = json.loads(base64.b64decode(res.get("body") or "") or b"[]")
                    item["nodes"] = [{**n, "id": f"L:{r['id']}:{n['id']}", "remoteId": n["id"], "linkId": r["id"], "hub": r["peer_name"], "kind": "linked"}
                                     for n in data if isinstance(n, dict) and isinstance(n.get("id"), str)][:200]
                else:
                    item["error"] = "Aucun accès sur ce hub pour l'instant." if res.get("status") == 403 else "Réponse inattendue du hub."
            except HTTPException as e:
                item["error"] = e.detail
            except (ValueError, asyncio.TimeoutError):
                item["error"] = "Réponse invalide du hub."
        out.append(item)
    return out
