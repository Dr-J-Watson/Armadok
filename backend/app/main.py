import re
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import db
from .auth import init_auth
from .auth import router as auth_router
from .config import env, settings
from .crypto import encrypt_plaintext, migrate_legacy
from .ovh_client import OvhError
from .admin_nodes import purge_expired
from .admin_nodes import router as admin_router
from .nodes import router as nodes_router
from .nodes import seed_local_node
from .routes_access import router as access_router
from .worker_link import router as worker_router
from .events import router as events_router, start_watchers
from .routes_obs import router as obs_router
from .routes_docker import router as docker_router
from .routes_projects import hooks_router, router as projects_router
from .routes_templates import router as templates_router
from .routes_backup import router as backup_router
from .routes_node_backup import router as node_backup_router
from . import backup as backup_mod
from .routes_terminal import router as terminal_router
from . import imagecheck as imagewatch
from . import accesslog, alerts as alerts_engine, automod as automod_engine, bans as bans_engine
from .routes_rbac import router as rbac_router
from . import rbac
from .routes_access import sync_primary
from .routes_dns import router as dns_router
from .routes_traefik import router as traefik_router
from .routes_toggle import router as toggle_router
from .routes_certs import router as certs_router
from .traefik_store import StoreError

try:
    settings.validate()
except RuntimeError as e:
    raise SystemExit(f"\nERREUR DE CONFIGURATION : {e}\n")
for path in settings.harden_data_dir()[:3]:
    print(f"AVERTISSEMENT : permissions de {path} non restreintes (le dossier data/ devrait être en 0700).", file=sys.stderr)
if not env("ENCRYPTION_KEY"):
    print("AVERTISSEMENT : ENCRYPTION_KEY n'est pas définie, les clés chiffrées dépendent de JWT_SECRET. "
          "Générez-en une : openssl rand -hex 32 (les données existantes sont migrées au démarrage).", file=sys.stderr)
_restored = backup_mod.apply_pending()   # une restauration préparée avant le redémarrage se met en place AVANT d'ouvrir la base
db.init()
if (n := migrate_legacy()):
    print(f"{n} valeur(s) chiffrée(s) migrée(s) vers ENCRYPTION_KEY.", file=sys.stderr)
if (n := encrypt_plaintext()):
    print(f"{n} valeur(s) qui étaient en clair (adresse de webhook) ont été chiffrées.", file=sys.stderr)
if settings.admin_password and not settings.admin_password_hash:
    print("AVERTISSEMENT : ADMIN_PASSWORD est en clair dans l'environnement. Utilisez ADMIN_PASSWORD_HASH (python -m app.tools.hash_password).", file=sys.stderr)
init_auth()
seed_local_node()
rbac.seed_roles()
purge_expired()
if _restored:
    from .audit import audit as _audit
    _audit("système", "admin.backup.restore.applied", _restored)
try:
    sync_primary(force=True)
except Exception:  # OVH injoignable au démarrage : repris à la prochaine ouverture
    pass

start_watchers()
accesslog.start()
alerts_engine.start()
bans_engine.start()
imagewatch.start()
from . import project_ops as _po  # noqa: E402
_po.sweep_later()
automod_engine.start()
app = FastAPI(title="Armadok", docs_url=None, redoc_url=None, openapi_url=None)
for r in (auth_router, nodes_router, admin_router, worker_router, events_router, rbac_router, access_router, dns_router, traefik_router, toggle_router, certs_router, obs_router, docker_router, projects_router, templates_router, backup_router, node_backup_router, hooks_router, terminal_router):
    app.include_router(r)


def _ws_sources(request) -> str:
    """Le temps réel (WebSocket sur ce même hôte) : 'self' ne suffit pas dans tous les navigateurs, on cite l'hôte explicitement."""
    host = request.headers.get("host", "")
    return f" ws://{host} wss://{host}" if re.fullmatch(r"[A-Za-z0-9.\-:\[\]]{1,255}", host) else ""


MAX_BODY = 4 * 1024 * 1024   # aucune requête de l'API n'a besoin de plus (les fichiers YAML sont limités à 1 Mo)


class BodyLimit:
    """Refuse (413) une requête dont le corps dépasse MAX_BODY, qu'il soit annoncé (Content-Length) ou envoyé par blocs : pas de mémoire gaspillée sur un corps géant."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for k, v in scope["headers"]:
            if k == b"content-length":
                try:
                    if int(v) > MAX_BODY:
                        return await self._reject(send)
                except ValueError:
                    return await self._reject(send)
        seen = 0

        async def limited():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > MAX_BODY:
                    raise _TooBig()
            return msg

        try:
            await self.app(scope, limited, send)
        except _TooBig:
            await self._reject(send)

    @staticmethod
    async def _reject(send):
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail":"Requ\xc3\xaate trop volumineuse"}'})


class _TooBig(Exception):
    pass


class Headers(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers.update({
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
            "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                       f"font-src 'self' data:; worker-src 'self' blob:; connect-src 'self'{_ws_sources(request)}; "
                                       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        if settings.cookie_secure:   # déploiement en HTTPS : le navigateur ne reviendra plus jamais en clair sur ce site
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp


app.add_middleware(Headers)
app.add_middleware(BodyLimit)


@app.exception_handler(StoreError)
async def _store_err(_, e: StoreError):
    body = {"detail": str(e) or "Opération refusée"}
    for k in ("line", "column", "code"):
        if hasattr(e, k):
            body[k] = getattr(e, k)
    return JSONResponse(body, status_code=e.status)


@app.exception_handler(OvhError)
async def _ovh_err(_, e: OvhError):
    return JSONResponse({"detail": str(e) or "Le fournisseur DNS a refusé la demande sans donner de détail"}, status_code=e.status)


@app.exception_handler(Exception)
async def _unexpected(request, e: Exception):
    """Toute erreur imprévue répond en JSON lisible (sinon le navigateur reçoit du texte brut et n'a rien à afficher)."""
    print(f"ERREUR INTERNE {request.method} {request.url.path} : {type(e).__name__}: {e}", file=sys.stderr)
    return JSONResponse({"detail": "Erreur interne du serveur : l'opération n'a pas abouti. Réessayez ; si cela se répète, consultez les journaux du serveur."}, status_code=500)


@app.exception_handler(RequestValidationError)
async def _val_err(_, e: RequestValidationError):
    return JSONResponse({"detail": "Requête invalide : " + "; ".join(f"{'.'.join(map(str, x['loc'][1:]))} {x['msg']}" for x in e.errors())}, status_code=422)


@app.get("/api/health")
def health():
    return {"ok": True}


STATIC = Path(__file__).resolve().parent.parent / "static"
if STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = (STATIC / path).resolve()
        if path and f.is_file() and f.is_relative_to(STATIC.resolve()):
            return FileResponse(f)
        return FileResponse(STATIC / "index.html")
