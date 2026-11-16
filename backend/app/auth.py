"""Authentification JWT : access token court (en mémoire côté client) + refresh token en cookie httpOnly."""
import hashlib
import hmac
import json
import secrets
import time

import bcrypt
import jwt
import pyotp
import segno
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import db
from .audit import audit
from .config import settings
from .crypto import dec, enc

router = APIRouter(prefix="/api/auth", tags=["auth"])
COOKIE = "refresh_token"
ALG = "HS256"

_dummy = bcrypt.hashpw(b"dummy", bcrypt.gensalt(12))
_attempts: dict[str, tuple[int, float]] = {}       # par adresse
_user_attempts: dict[str, tuple[int, float]] = {}  # par compte : une attaque répartie sur beaucoup d'adresses est freinée quand même
MAX_TRACKED = 20000   # bornes de mémoire : un attaquant ne peut pas gonfler ces tables sans fin
USER_FREE_TRIES = 10   # essais ratés tolérés par compte avant verrou (les adresses : 4)


def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt(12)).decode()


def init_auth() -> None:
    """Crée / met à jour le compte propriétaire défini dans le .env (toujours administrateur)."""
    h = settings.admin_password_hash.strip() if settings.admin_password_hash else hash_pw(settings.admin_password)
    row = db.one("SELECT id FROM users WHERE source='env'")
    if row:
        db.run("UPDATE users SET username=?, password_hash=?, role='admin', can_add_domains=1, disabled=0 WHERE id=?",
               (settings.admin_user, h, row["id"]))
    else:
        db.run("INSERT INTO users(username,password_hash,role,can_add_domains,source,created_at) VALUES(?,?,?,?,?,?)",
               (settings.admin_user, h, "admin", 1, "env", db.now()))


def _client_ip(request: Request) -> str:
    """Adresse du client telle que la voit uvicorn : l'en-tête X-Forwarded-For n'est cru que de Traefik (FORWARDED_ALLOW_IPS), jamais d'un visiteur."""
    return request.client.host if request.client else "?"


def _prune(table: dict) -> None:
    if len(table) <= MAX_TRACKED:
        return
    now = time.time()
    for k in [k for k, (_, until) in table.items() if until < now]:   # d'abord ce qui n'est plus verrouillé
        table.pop(k, None)
    if len(table) > MAX_TRACKED:   # encore trop : on oublie la moitié la plus ancienne
        for k in list(table)[: len(table) // 2]:
            table.pop(k, None)


def _check_lock(ip: str, user: str | None = None) -> None:
    until = max(_attempts.get(ip, (0, 0))[1], _user_attempts.get((user or "").lower(), (0, 0))[1] if user else 0)
    if until > time.time():
        raise HTTPException(429, "Trop de tentatives, réessayez plus tard.",
                            headers={"Retry-After": str(int(until - time.time()) + 1)})


def _fail(ip: str, user: str | None = None) -> None:
    fails, _ = _attempts.get(ip, (0, 0))
    fails += 1
    lock = min(900, 15 * 2 ** (fails - 4)) if fails >= 4 else 0
    _attempts[ip] = (fails, time.time() + lock)
    _prune(_attempts)
    if user:
        key = user.lower()[:80]
        f2, _ = _user_attempts.get(key, (0, 0))
        f2 += 1
        _user_attempts[key] = (f2, time.time() + (min(900, 30 * 2 ** (f2 - USER_FREE_TRIES)) if f2 >= USER_FREE_TRIES else 0))
        _prune(_user_attempts)


def _ok(ip: str, user: str | None = None) -> None:
    _attempts.pop(ip, None)
    if user:
        _user_attempts.pop(user.lower()[:80], None)


# ------------------------------------------------------------------ jetons : version par compte, révocation durable
def _version(uid: int) -> int:
    r = db.one("SELECT token_version FROM users WHERE id=?", (uid,))
    return int(r["token_version"]) if r else 0


def bump_sessions(uid: int) -> None:
    """Invalide toutes les sessions ouvertes d'une personne (changement de mot de passe, 2FA retirée ou réinitialisée, compte désactivé)."""
    db.run("UPDATE users SET token_version=token_version+1 WHERE id=?", (uid,))


def _revoke(jti: str, exp: float) -> None:
    db.run("INSERT OR REPLACE INTO revoked_tokens(jti, exp) VALUES(?,?)", (jti, float(exp)))


def _is_revoked(jti: str) -> bool:
    return bool(db.one("SELECT 1 FROM revoked_tokens WHERE jti=?", (jti,)))


def purge_revoked() -> None:
    db.run("DELETE FROM revoked_tokens WHERE exp<?", (time.time(),))


def _token(uid: int, kind: str, ttl: int) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(uid), "typ": kind, "iat": now, "exp": now + ttl, "jti": secrets.token_hex(8), "tv": _version(uid)},
                      settings.jwt_secret, algorithm=ALG)


def _decode(token: str, kind: str) -> dict:
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=[ALG])
    except jwt.PyJWTError:
        raise HTTPException(401, "Session invalide ou expirée")
    jti = data.get("jti")
    if data.get("typ") != kind or not jti or _is_revoked(jti):
        raise HTTPException(401, "Session invalide ou expirée")
    try:
        if int(data.get("tv", -1)) != _version(int(data["sub"])):   # mot de passe ou 2FA changés depuis : cette session n'est plus valable
            raise HTTPException(401, "Session invalide ou expirée")
    except (TypeError, ValueError):
        raise HTTPException(401, "Session invalide ou expirée")
    return data


def _load(uid: int):
    from .perms import principal_from_row
    r = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not r or r["disabled"]:
        raise HTTPException(401, "Compte inconnu ou désactivé")
    return principal_from_row(r)


def current_user(request: Request):
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Authentification requise")
    return _load(int(_decode(header[7:], "access")["sub"]))


def _set_refresh(response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=settings.refresh_ttl, httponly=True, samesite="strict",
                        secure=settings.cookie_secure, path="/api/auth")


def _session(p, response: Response, rotate: bool = True) -> dict:
    if rotate:
        _set_refresh(response, _token(p.id, "refresh", settings.refresh_ttl))
    return {"access_token": _token(p.id, "access", settings.access_ttl), "expires_in": settings.access_ttl, "user": p.public()}


class Login(BaseModel):
    username: str
    password: str


_LOGIN_AUDIT: dict[str, float] = {}   # une ligne d'audit par adresse et par minute au plus : un visiteur anonyme ne remplit pas le journal
_LOGIN_AUDIT_EVERY = 60


def _audit_failed(ip: str, username: str) -> None:
    now = time.time()
    if now - _LOGIN_AUDIT.get(ip, 0) < _LOGIN_AUDIT_EVERY:
        return
    _LOGIN_AUDIT[ip] = now
    _prune_audit()
    audit(username[:40], "login.failed", ip)


def _prune_audit() -> None:
    if len(_LOGIN_AUDIT) > MAX_TRACKED:
        cut = time.time() - _LOGIN_AUDIT_EVERY
        for k in [k for k, t in _LOGIN_AUDIT.items() if t < cut]:
            _LOGIN_AUDIT.pop(k, None)


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    ip = _client_ip(request)
    name = body.username.strip()
    _check_lock(ip, name)
    row = db.one("SELECT * FROM users WHERE username=?", (name,))
    ok = False
    try:
        ok = bcrypt.checkpw(body.password.encode()[:72], (row["password_hash"] if row else "").encode() or _dummy)
    except ValueError:
        ok = False
    if not (row and ok and not row["disabled"]):
        _fail(ip, name)
        _audit_failed(ip, body.username)
        raise HTTPException(401, "Identifiants incorrects")
    if row["totp_enabled"]:
        return {"mfa_required": True, "mfa_token": _token(row["id"], "mfa", 300)}
    _ok(ip, name)
    p = _load(row["id"])
    audit(p.username, "login", ip)
    return _session(p, response)


# ------------------------------------------------------------------ double authentification (TOTP)
_mfa_fails: dict[str, int] = {}


def _prune_mfa() -> None:
    if len(_mfa_fails) > MAX_TRACKED:
        for k in list(_mfa_fails)[: len(_mfa_fails) // 2]:
            _mfa_fails.pop(k, None)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace("-", "").encode()).hexdigest()


def _check_second_factor(uid: int, code: str) -> bool:
    """Code TOTP à 6 chiffres (usage unique) ou code de secours (usage unique)."""
    row = db.one("SELECT totp_secret, totp_last_step, recovery_codes FROM users WHERE id=?", (uid,))
    code = code.strip().replace(" ", "")
    if not row or not row["totp_secret"]:
        return False
    if code.isdigit() and len(code) == 6:
        totp = pyotp.TOTP(dec(row["totp_secret"]))
        now = int(time.time() // 30)
        for step in (now - 1, now, now + 1):
            if hmac.compare_digest(totp.at(step * 30), code) and step > row["totp_last_step"]:
                db.run("UPDATE users SET totp_last_step=? WHERE id=?", (step, uid))
                return True
        return False
    codes = json.loads(row["recovery_codes"] or "[]")
    h = _hash_code(code)
    if h in codes:
        codes.remove(h)
        db.run("UPDATE users SET recovery_codes=? WHERE id=?", (json.dumps(codes), uid))
        return True
    return False


class Mfa(BaseModel):
    mfaToken: str
    code: str


@router.post("/login/2fa")
def login_2fa(body: Mfa, request: Request, response: Response):
    ip = _client_ip(request)
    data = _decode(body.mfaToken, "mfa")
    uid = int(data["sub"])
    uname = (db.one("SELECT username FROM users WHERE id=?", (uid,)) or {"username": ""})["username"]
    _check_lock(ip, uname)
    if not _check_second_factor(uid, body.code):
        _fail(ip, uname)
        _mfa_fails[data["jti"]] = _mfa_fails.get(data["jti"], 0) + 1
        _prune_mfa()
        if _mfa_fails[data["jti"]] >= 5:  # 5 essais max par connexion
            _revoke(data["jti"], data["exp"])
        raise HTTPException(401, "Code incorrect")
    _revoke(data["jti"], data["exp"])
    _ok(ip, uname)
    p = _load(uid)
    audit(p.username, "login.2fa", ip)
    return _session(p, response)


class SetupOut(BaseModel):
    secret: str
    uri: str
    qr: str


@router.post("/2fa/setup")
def setup_2fa(p=Depends(current_user)):
    if p.two_factor:
        raise HTTPException(400, "La double authentification est déjà activée")
    secret = pyotp.random_base32()
    db.run("UPDATE users SET totp_secret=?, totp_enabled=0, totp_last_step=0 WHERE id=?", (enc(secret), p.id))
    uri = pyotp.TOTP(secret).provisioning_uri(name=p.username, issuer_name="Armadok")
    return {"secret": secret, "uri": uri, "qr": segno.make(uri, error="m").svg_inline(scale=5, dark="#000", light="#fff", border=2, omitsize=True)}


class Code(BaseModel):
    code: str


@router.post("/2fa/enable")
def enable_2fa(body: Code, p=Depends(current_user)):
    if p.two_factor:
        raise HTTPException(400, "Déjà activée")
    if not _check_second_factor(p.id, body.code):
        raise HTTPException(400, "Code incorrect : vérifiez l'heure de votre téléphone et réessayez")
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(10)]
    db.run("UPDATE users SET totp_enabled=1, recovery_codes=? WHERE id=?", (json.dumps([_hash_code(c) for c in codes]), p.id))
    audit(p.username, "2fa.enable")
    return {"recoveryCodes": codes}


class Disable(BaseModel):
    password: str
    code: str


@router.post("/2fa/disable")
def disable_2fa(body: Disable, request: Request, response: Response, p=Depends(current_user)):
    key = f"2fa-off:{p.id}"
    _check_lock(key, p.username)
    row = db.one("SELECT password_hash FROM users WHERE id=?", (p.id,))
    if not bcrypt.checkpw(body.password.encode()[:72], row["password_hash"].encode()):
        _fail(key, p.username)
        raise HTTPException(403, "Mot de passe incorrect")
    if not _check_second_factor(p.id, body.code):
        _fail(key, p.username)
        raise HTTPException(403, "Code incorrect")
    _ok(key, p.username)
    db.run("UPDATE users SET totp_enabled=0, totp_secret=NULL, totp_last_step=0, recovery_codes='' WHERE id=?", (p.id,))
    bump_sessions(p.id)   # les autres appareils ouverts sont déconnectés ; celui-ci reçoit une session neuve
    audit(p.username, "2fa.disable")
    return _session(_load(p.id), response)


@router.post("/refresh")
def refresh(request: Request, response: Response):
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(401, "Pas de session")
    data = _decode(raw, "refresh")
    p = _load(int(data["sub"]))
    _revoke(data["jti"], data["exp"])  # rotation : un refresh token ne sert qu'une fois
    purge_revoked()
    return _session(p, response)


@router.post("/logout")
def logout(request: Request, response: Response):
    raw = request.cookies.get(COOKIE)
    if raw:
        try:
            data = jwt.decode(raw, settings.jwt_secret, algorithms=[ALG])
            _revoke(data["jti"], data["exp"])
        except jwt.PyJWTError:
            pass
    response.delete_cookie(COOKIE, path="/api/auth")
    return {"ok": True}


@router.get("/me")
def me(p=Depends(current_user)):
    return p.public()


@router.post("/logout-all")
def logout_all(response: Response, p=Depends(current_user)):
    """Déconnecte tous les appareils (celui-ci reçoit une session neuve)."""
    bump_sessions(p.id)
    audit(p.username, "logout.all")
    return _session(_load(p.id), response)


def check_new_password(pw: str, username: str = "") -> None:
    """Règles pour un NOUVEAU mot de passe : 12 caractères au moins, pas le nom d'utilisateur, au plus 72 octets (bcrypt ignore le reste en silence)."""
    if len(pw) < 12:
        raise HTTPException(422, "Le mot de passe doit faire au moins 12 caractères")
    if len(pw.encode()) > 72:
        raise HTTPException(422, "Le mot de passe est trop long (72 octets au plus : bcrypt ignorerait la suite)")
    if username and pw.lower() == username.lower():
        raise HTTPException(422, "Le mot de passe ne peut pas être le nom d'utilisateur")
    if len(set(pw)) < 5:
        raise HTTPException(422, "Mot de passe trop répétitif")


class PasswordBody(BaseModel):
    current: str
    new: str = Field(min_length=10, max_length=200)


@router.post("/password")
def change_password(body: PasswordBody, response: Response, p=Depends(current_user)):
    if p.source == "env":
        raise HTTPException(400, "Le mot de passe du compte propriétaire se change dans le fichier .env")
    key = f"pw:{p.id}"
    _check_lock(key, p.username)
    row = db.one("SELECT password_hash FROM users WHERE id=?", (p.id,))
    if not bcrypt.checkpw(body.current.encode()[:72], row["password_hash"].encode()):
        _fail(key, p.username)
        raise HTTPException(403, "Mot de passe actuel incorrect")
    _ok(key, p.username)
    check_new_password(body.new, p.username)
    db.run("UPDATE users SET password_hash=? WHERE id=?", (hash_pw(body.new), p.id))
    bump_sessions(p.id)   # les autres appareils ouverts sont déconnectés
    audit(p.username, "user.password.change")
    return _session(_load(p.id), response)
