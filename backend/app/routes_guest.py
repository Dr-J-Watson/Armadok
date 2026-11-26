"""Un invité d'un hub lié définit SON mot de passe et sa 2FA sur ce hub (mode « connexion directe »). Points d'entrée publics : le lien à usage unique est la seule preuve.

Le mot de passe n'est tapé que sur le domaine de ce hub : le hub d'origine ne voit que le lien, jamais le mot de passe ni le secret 2FA."""
import json
import secrets
import time

import pyotp
import segno
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth, db, events
from . import hub_link as hl
from . import link_guests as lg
from .audit import audit
from .crypto import dec, enc

router = APIRouter(prefix="/api/guest", tags=["guest"])


class Start(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class Finish(Start):
    password: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=6, max_length=20)


def _row(token: str, request: Request):
    """Le jeton valide (non utilisé, non expiré) et l'invité qu'il vise, avec toutes les conditions revérifiées ; sinon une réponse volontairement vague."""
    ip = auth._client_ip(request)
    auth._check_lock(ip)
    t = db.one("SELECT * FROM guest_setup_tokens WHERE token_hash=?", (hl.hash_code(token.strip()),))
    u = db.one("SELECT * FROM users WHERE id=? AND source='guest'", (t["user_id"],)) if t and not t["used_at"] and t["expires_at"] > time.time() else None
    r = hl.get(u["guest_link"]) if u else None
    if not (u and r and hl.usable(r) and r["host_enabled"] and r["direct_enabled"] and lg._allowed(r) and not u["disabled"] and not u["guest_blocked"]):
        auth._fail(ip)
        raise HTTPException(401, "Lien invalide, expiré ou déjà utilisé : redemandez-en un depuis votre hub d'origine")
    return ip, t, u


@router.post("/setup/start")
def start(body: Start, request: Request):
    ip, t, u = _row(body.token, request)
    secret = pyotp.random_base32()
    db.run("UPDATE guest_setup_tokens SET totp_secret=? WHERE token_hash=?", (enc(secret), t["token_hash"]))
    uri = pyotp.TOTP(secret).provisioning_uri(name=u["username"], issuer_name="Armadok")
    return {"username": u["username"], "secret": secret, "uri": uri, "qr": segno.make(uri, error="m").svg_inline(scale=5, dark="#000", light="#fff", border=2, omitsize=True)}


@router.post("/setup/finish")
def finish(body: Finish, request: Request):
    ip, t, u = _row(body.token, request)
    if not t["totp_secret"]:
        raise HTTPException(409, "Commencez par enregistrer l'application d'authentification")
    secret = dec(t["totp_secret"])
    if not pyotp.TOTP(secret).verify(body.code.replace(" ", ""), valid_window=1):
        auth._fail(ip)
        raise HTTPException(400, "Code incorrect : vérifiez l'heure de votre téléphone et réessayez")
    auth.check_new_password(body.password, u["username"])
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(10)]
    burned = db.conn().execute("UPDATE guest_setup_tokens SET used_at=? WHERE token_hash=? AND used_at IS NULL", (time.time(), t["token_hash"])).rowcount
    if burned != 1:   # deux validations simultanées avec le même lien : une seule gagne
        raise HTTPException(401, "Lien invalide, expiré ou déjà utilisé : redemandez-en un depuis votre hub d'origine")
    db.run("UPDATE users SET password_hash=?, totp_secret=?, totp_enabled=1, totp_last_step=0, recovery_codes=?, guest_direct=1 WHERE id=?",
           (auth.hash_pw(body.password), enc(secret), json.dumps([auth._hash_code(c) for c in codes]), u["id"]))
    auth.bump_sessions(u["id"])
    audit(u["username"], "admin.link.guest.direct_enabled", f"{ip} ({u['guest_link']})")
    auth._ok(ip)
    events.publish("session", user=u["id"])
    return {"username": u["username"], "recoveryCodes": codes}
