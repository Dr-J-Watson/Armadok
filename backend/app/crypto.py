"""Chiffrement des clés API OVH et des secrets 2FA stockés en base (Fernet).
Clé : ENCRYPTION_KEY (recommandé, distincte de JWT_SECRET) ; à défaut, dérivée de JWT_SECRET (ancien comportement).
Quand ENCRYPTION_KEY est définie, les données chiffrées avec l'ancienne clé restent lisibles et sont rechiffrées au démarrage."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import env, settings

_cache: dict[str, Fernet] = {}


def _fernet(seed: str) -> Fernet:
    if seed not in _cache:
        _cache[seed] = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("ovh-webui:" + seed).encode()).digest()))
    return _cache[seed]


def _primary() -> Fernet:
    return _fernet(env("ENCRYPTION_KEY") or settings.jwt_secret)


def _legacy() -> Fernet | None:
    """Ancienne clé (dérivée de JWT_SECRET), utile seulement si ENCRYPTION_KEY a été ajoutée après coup."""
    return _fernet(settings.jwt_secret) if env("ENCRYPTION_KEY") else None


def enc(value: str) -> str:
    return _primary().encrypt(value.encode()).decode()


def dec(token: str) -> str:
    for f in (_primary(), _legacy()):
        if f is None:
            continue
        try:
            return f.decrypt(token.encode()).decode()
        except InvalidToken:
            continue
    raise RuntimeError("Impossible de déchiffrer une clé API (ENCRYPTION_KEY / JWT_SECRET modifié ?)")


def is_encrypted(value: str | None) -> bool:
    """Un jeton Fernet commence toujours par « gAAAA » : sert à reconnaître une valeur déjà chiffrée (et à migrer les anciennes en clair)."""
    return bool(value) and str(value).startswith("gAAAA")


def reveal(value: str | None) -> str:
    """Valeur en clair, qu'elle soit chiffrée ou (ancienne base) encore en clair."""
    if not value:
        return ""
    return dec(value) if is_encrypted(value) else value


# (table, colonnes chiffrées)
_ENCRYPTED = (("credentials", ("app_key", "app_secret", "consumer_key", "secret")), ("users", ("totp_secret",)), ("nodes", ("webhook_url",)))


def encrypt_plaintext() -> int:
    """Chiffre ce qui était stocké en clair avant (les adresses de webhook contiennent souvent un jeton secret). Retourne le nombre de valeurs chiffrées."""
    from . import db
    n = 0
    for r in db.rows("SELECT id, webhook_url FROM nodes WHERE webhook_url IS NOT NULL AND webhook_url != ''"):
        if not is_encrypted(r["webhook_url"]):
            db.run("UPDATE nodes SET webhook_url=? WHERE id=?", (enc(r["webhook_url"]), r["id"]))
            n += 1
    return n


def migrate_legacy() -> int:
    """Rechiffre avec ENCRYPTION_KEY ce qui l'est encore avec l'ancienne clé. Retourne le nombre de valeurs migrées."""
    legacy = _legacy()
    if legacy is None:
        return 0
    from . import db
    n = 0
    for table, cols in _ENCRYPTED:
        for col in cols:
            for r in db.rows(f"SELECT id, {col} AS v FROM {table} WHERE {col} IS NOT NULL AND {col} != ''"):
                try:
                    _primary().decrypt(r["v"].encode())
                    continue  # déjà avec la bonne clé
                except InvalidToken:
                    pass
                try:
                    plain = legacy.decrypt(r["v"].encode()).decode()
                except InvalidToken:
                    continue  # illisible avec les deux clés : on n'y touche pas
                db.run(f"UPDATE {table} SET {col}=? WHERE id=?", (enc(plain), r["id"]))
                n += 1
    return n
