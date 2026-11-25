"""Sauvegarde complète du hub : créer, lister, chiffrer (facultatif), téléverser, restaurer.

Une sauvegarde est une archive zip dont le contenu est choisi par composant :
  database : la base (copie cohérente faite pendant que le hub tourne) et le journal d'audit (sa « tête » est dans la base : les deux vont ensemble)
  history  : les versions des fichiers de projets et des fichiers Traefik gardées par l'interface
  home     : la configuration Docker / Git du hub (registres, serveurs SSH connus)
  traefik  : les fichiers de configuration dynamique de Traefik (le dossier que l'interface écrit)
Jamais : la base de géolocalisation (elle se retélécharge), les dossiers temporaires, les sauvegardes elles-mêmes.
La clé de chiffrement des données (ENCRYPTION_KEY) n'est PAS dans l'archive, sauf demande explicite ET archive protégée par un mot de passe : sans cette clé, les secrets
de la base (clés DNS, 2FA, modèles) restent illisibles.

Restauration en deux temps : on prépare les fichiers à côté (`restore-pending`), puis on redémarre ; au démarrage suivant, AVANT d'ouvrir la base, `apply_pending` les met
en place en gardant l'état d'avant dans `avant-restauration` (et une sauvegarde complète « avant-restauration » est faite d'abord)."""
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import struct
import sys
import threading
import time
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .config import env, settings
from .traefik_store import StoreError

FORMAT = 1
COMPONENTS = {
    "database": "Base de données et journal d'audit",
    "history": "Historiques des fichiers (projets et Traefik)",
    "home": "Configuration Docker et Git du hub",
    "traefik": "Fichiers de configuration dynamique de Traefik",
}
MAGIC = b"ARMBK1\n"
ENC_CHUNK = 1024 * 1024
MAX_UPLOAD = 20 * 1024 ** 3
UPLOAD_CHUNK = 2 * 1024 * 1024
NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(zip|armbak)$")
_lock = threading.Lock()
_jobs: dict[str, dict] = {}


# ------------------------------------------------------------------ emplacements
def base() -> Path:
    return settings.data_dir


def store_dir() -> Path:
    d = base() / "sauvegardes"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def tmp_dir() -> Path:
    d = base() / "tmp"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def pending_dir() -> Path:
    return base() / "restore-pending"


def before_dir() -> Path:
    return base() / "avant-restauration"


def key_fingerprint() -> str:
    """Empreinte (non réversible) de la clé de chiffrement : sert à dire « même clé » ou « clé différente » sans jamais la montrer."""
    return hashlib.sha256(("armadok-key-check:" + (env("ENCRYPTION_KEY") or settings.jwt_secret)).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ chiffrement par mot de passe (flux, par morceaux)
def _kdf_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode())


def encrypt_file(src: Path, dst: Path, passphrase: str) -> None:
    salt = secrets.token_bytes(16)
    aes = AESGCM(_kdf_key(passphrase, salt))
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        fo.write(MAGIC + salt + struct.pack(">I", ENC_CHUNK))
        i, cur = 0, fi.read(ENC_CHUNK)
        while True:
            nxt = fi.read(ENC_CHUNK) if len(cur) == ENC_CHUNK else b""
            last = not nxt
            ct = aes.encrypt(i.to_bytes(12, "big"), cur, b"\x01" if last else b"\x00")   # le dernier morceau est marqué : une archive tronquée est refusée
            fo.write(struct.pack(">I", len(ct)) + ct)
            if last:
                return
            i, cur = i + 1, nxt


def is_encrypted_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def decrypt_file(src: Path, dst: Path, passphrase: str) -> None:
    try:
        with open(src, "rb") as fi:
            head = fi.read(len(MAGIC) + 16 + 4)
            if len(head) < len(MAGIC) + 20 or not head.startswith(MAGIC):
                raise ValueError("Ce fichier n'est pas une sauvegarde chiffrée d'Armadok")
            aes = AESGCM(_kdf_key(passphrase, head[len(MAGIC):len(MAGIC) + 16]))
            with open(dst, "wb") as fo:
                i = 0
                while True:
                    raw = fi.read(4)
                    if len(raw) < 4:
                        raise ValueError("Sauvegarde tronquée")
                    (n,) = struct.unpack(">I", raw)
                    ct = fi.read(n)
                    if len(ct) < n:
                        raise ValueError("Sauvegarde tronquée")
                    last = not fi.read(1)
                    if not last:
                        fi.seek(-1, os.SEEK_CUR)
                    fo.write(aes.decrypt(i.to_bytes(12, "big"), ct, b"\x01" if last else b"\x00"))
                    if last:
                        return
                    i += 1
    except InvalidTag:
        dst.unlink(missing_ok=True)
        raise ValueError("Mot de passe incorrect, ou sauvegarde abîmée")
    except (OSError, ValueError):
        dst.unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------ contenu
def _skip_name(n: str) -> bool:
    return n.endswith(".armadok-part") or n.endswith(".armadok-part.tmp")


def _walk(root: Path):
    """(chemin, nom dans l'archive) de tous les fichiers ordinaires sous `root` ; jamais de lien symbolique."""
    if not root.is_dir():
        return
    for dp, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(dp) / d).is_symlink()]
        for n in names:
            p = Path(dp) / n
            if p.is_symlink() or _skip_name(n):
                continue
            yield p, str(p.relative_to(root))


def _members(component: str) -> list[tuple[Path, str]]:
    b = base()
    if component == "database":
        return [(p, "audit/" + p.name) for p in sorted([b / "audit.log", *b.glob("audit-[0-9]*.log")]) if p.is_file()]
    if component == "history":
        return [(p, "history/project-history/" + a) for p, a in _walk(b / "project-history")] + [(p, "history/traefik-versions/" + a) for p, a in _walk(b / "backups")]
    if component == "home":
        return [(p, "home/" + a) for p, a in _walk(b / "home")]
    if component == "traefik":
        return [(p, "traefik/" + a) for p, a in _walk(settings.dynamic_dir)]
    return []


def estimate(components: list[str]) -> int:
    total = 0
    for c in components:
        for p, _ in _members(c):
            try:
                total += p.stat().st_size
            except OSError:
                pass
    if "database" in components:
        try:
            total += (base() / "app.db").stat().st_size
        except OSError:
            pass
    return total


def _snapshot_db(dst: Path) -> None:
    """Copie cohérente de la base ouverte (l'API de sauvegarde de SQLite : pas besoin d'arrêter le hub)."""
    src = sqlite3.connect(f"file:{base() / 'app.db'}?mode=ro", uri=True, timeout=30)
    try:
        out = sqlite3.connect(dst)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()


def clean_label(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", (label or "").strip())[:40].strip("-")


def _free(path: Path) -> int:
    return shutil.disk_usage(path).free


def validate(components: list[str], passphrase: str = "", include_key: bool = False) -> list[str]:
    """Vérifie une demande de sauvegarde AVANT de la lancer (la tâche, elle, tourne en arrière-plan) et renvoie les composants retenus."""
    comps = [c for c in dict.fromkeys(components) if c in COMPONENTS]
    if not comps:
        raise StoreError("Choisissez au moins un élément à sauvegarder", 422)
    if include_key and not passphrase:
        raise StoreError("Pour inclure la clé de chiffrement, protégez la sauvegarde par un mot de passe", 422)
    if passphrase and len(passphrase) < 8:
        raise StoreError("Le mot de passe de la sauvegarde doit faire 8 caractères au moins", 422)
    return comps


def create(components: list[str], user: str, label: str = "", passphrase: str = "", include_key: bool = False, kind: str = "manuelle") -> dict:
    """Crée une sauvegarde et renvoie sa fiche. Lève StoreError si la demande est invalide ou si la place manque."""
    comps = validate(components, passphrase, include_key)
    store = store_dir()
    need = int(estimate(comps) * 1.2) + 50 * 1024 * 1024
    if _free(store) < need * (2 if passphrase else 1):
        raise StoreError("Pas assez de place sur le disque pour cette sauvegarde", 507)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    lab = clean_label(label)
    stem = f"armadok-{stamp}" + (f"-{lab}" if lab else "")
    work = Path(tmp_dir() / f"backup-{secrets.token_hex(4)}")
    work.mkdir(mode=0o700)
    try:
        zpath = work / (stem + ".zip")
        info: dict = {}
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
            for c in comps:
                files = nbytes = 0
                if c == "database":
                    snap = work / "app.db"
                    _snapshot_db(snap)
                    z.write(snap, "db/app.db")
                    files, nbytes = 1, snap.stat().st_size
                    snap.unlink()
                for p, arc in _members(c):
                    try:
                        z.write(p, arc)
                        files += 1
                        nbytes += p.stat().st_size
                    except OSError:
                        continue
                info[c] = {"files": files, "bytes": nbytes}
            if include_key:
                z.writestr("secrets/encryption_key", env("ENCRYPTION_KEY") or "")
            manifest = {"format": FORMAT, "app": os.environ.get("APP_VERSION", "dev"), "createdAt": time.time(), "createdBy": user, "label": label.strip()[:80], "kind": kind,
                        "components": info, "keyFingerprint": key_fingerprint(), "includesKey": bool(include_key), "hasEnvKey": bool(env("ENCRYPTION_KEY"))}
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        final = store / (stem + (".armbak" if passphrase else ".zip"))
        part = final.with_name(final.name + ".armadok-part")
        if passphrase:
            encrypt_file(zpath, part, passphrase)
        else:
            shutil.move(str(zpath), part)
        os.chmod(part, 0o600)
        os.replace(part, final)
        meta = {**manifest, "name": final.name, "encrypted": bool(passphrase), "size": final.stat().st_size}
        write_meta(final.name, meta)
        return meta
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ liste
def write_meta(name: str, meta: dict, store: Path | None = None) -> None:
    p = (store or store_dir()) / (name + ".json")
    p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    os.chmod(p, 0o600)


def _read_manifest_zip(path: Path) -> dict | None:
    try:
        with zipfile.ZipFile(path) as z:
            return json.loads(z.read("manifest.json"))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None


def path_of(name: str, store: Path | None = None) -> Path:
    if not NAME_OK.match(name or ""):
        raise StoreError("Nom de sauvegarde invalide", 422)
    p = (store or store_dir()) / name
    if not p.is_file():
        raise StoreError("Sauvegarde introuvable", 404)
    return p


def listing(store: Path | None = None) -> list[dict]:
    out = []
    for p in sorted((store or store_dir()).iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not NAME_OK.match(p.name) or not p.is_file():
            continue
        meta: dict = {}
        sidecar = p.with_name(p.name + ".json")
        if sidecar.is_file():
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        if not meta and p.suffix == ".zip":
            meta = _read_manifest_zip(p) or {}
        out.append({"name": p.name, "size": p.stat().st_size, "encrypted": p.suffix == ".armbak", "createdAt": meta.get("createdAt") or p.stat().st_mtime, "createdBy": meta.get("createdBy", ""),
                    "label": meta.get("label", ""), "kind": meta.get("kind", "téléversée" if not meta else ""), "app": meta.get("app", ""), "components": meta.get("components") or {},
                    "includesKey": bool(meta.get("includesKey")), "keyFingerprint": meta.get("keyFingerprint", ""), "scope": meta.get("scope", ""), "node": meta.get("node")})
    return out


def delete(name: str, store: Path | None = None) -> None:
    p = path_of(name, store)
    p.unlink()
    p.with_name(p.name + ".json").unlink(missing_ok=True)


def disk_free() -> int:
    return _free(store_dir())


# ------------------------------------------------------------------ téléversement (par morceaux : l'API refuse les corps de plus de 4 Mo)
def upload_chunk(filename: str, offset: int, data: bytes, last: bool, user: str, store: Path | None = None, scope: str = "") -> dict:
    """`scope` : « » pour une sauvegarde du hub, « node » pour celle d'un nœud (une archive de l'autre sorte est refusée : on ne mélange pas)."""
    name = os.path.basename(filename or "")
    if not NAME_OK.match(name):
        raise StoreError("Choisissez un fichier de sauvegarde (.zip ou .armbak)", 422)
    store = store or store_dir()
    part = store / (re.sub(r"[^A-Za-z0-9._-]", "_", name) + ".armadok-part")
    if len(data) > UPLOAD_CHUNK:
        raise StoreError("Morceau trop gros", 413)
    if offset == 0:
        if _free(store) < 200 * 1024 * 1024:
            raise StoreError("Pas assez de place sur le disque", 507)
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    else:
        if not part.is_file() or part.stat().st_size != offset:
            raise StoreError("Envoi interrompu : recommencez", 409)
        if offset + len(data) > MAX_UPLOAD:
            part.unlink(missing_ok=True)
            raise StoreError("Fichier trop gros", 413)
        with open(part, "ab") as f:
            f.write(data)
    if not last:
        return {"done": False}
    final, n = store / name, 2
    while final.exists():
        final = store / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    os.replace(part, final)
    if final.suffix == ".zip":
        man = _read_manifest_zip(final)
        if not man or man.get("format") is None:
            final.unlink(missing_ok=True)
            raise StoreError("Ce fichier n'est pas une sauvegarde d'Armadok (manifeste absent)", 422)
        if (man.get("scope") or "") != scope:
            final.unlink(missing_ok=True)
            raise StoreError("C'est une sauvegarde de nœud : téléversez-la dans la page du nœud" if man.get("scope") == "node" else "C'est une sauvegarde complète du hub : téléversez-la dans « Tous les nœuds »", 422)
        write_meta(final.name, {**man, "name": final.name, "encrypted": False, "size": final.stat().st_size, "kind": "téléversée"}, store)
    else:
        write_meta(final.name, {"name": final.name, "encrypted": True, "size": final.stat().st_size, "kind": "téléversée", "createdAt": time.time(), "createdBy": user, "scope": scope}, store)
    return {"done": True, "name": final.name}


def abort_upload(filename: str, store: Path | None = None) -> None:
    name = os.path.basename(filename or "")
    ((store or store_dir()) / (re.sub(r"[^A-Za-z0-9._-]", "_", name) + ".armadok-part")).unlink(missing_ok=True)


# ------------------------------------------------------------------ lire une sauvegarde (chiffrée ou non)
class Opened:
    """Une archive lisible : l'originale si elle n'est pas chiffrée, sinon une copie déchiffrée dans un dossier temporaire (à fermer)."""

    def __init__(self, name: str, passphrase: str = "", store: Path | None = None) -> None:
        self.path = path_of(name, store)
        self.tmp: Path | None = None
        if self.path.suffix == ".armbak" or is_encrypted_file(self.path):
            if not passphrase:
                raise StoreError("Cette sauvegarde est protégée : saisissez son mot de passe", 422)
            self.tmp = tmp_dir() / f"open-{secrets.token_hex(4)}.zip"
            try:
                decrypt_file(self.path, self.tmp, passphrase)
            except ValueError as e:
                raise StoreError(str(e), 422)
            self.zip_path = self.tmp
        else:
            self.zip_path = self.path

    def manifest(self) -> dict:
        m = _read_manifest_zip(self.zip_path)
        if not m or "components" not in m:
            raise StoreError("Cette archive n'est pas une sauvegarde d'Armadok (manifeste absent ou illisible)", 422)
        if int(m.get("format") or 0) > FORMAT:
            raise StoreError("Sauvegarde faite par une version plus récente d'Armadok : mettez le hub à jour d'abord", 422)
        return m

    def close(self) -> None:
        if self.tmp:
            self.tmp.unlink(missing_ok=True)


def inspect(name: str, passphrase: str = "") -> dict:
    o = Opened(name, passphrase)
    try:
        m = o.manifest()
    finally:
        o.close()
    same = m.get("keyFingerprint") == key_fingerprint()
    return {"manifest": {k: m.get(k) for k in ("app", "createdAt", "createdBy", "label", "kind", "components", "includesKey")}, "keyStatus": "same" if same else "different" if m.get("keyFingerprint") else "unknown",
            "currentApp": os.environ.get("APP_VERSION", "dev")}


# ------------------------------------------------------------------ restauration
def _safe_member(n: str) -> bool:
    return bool(n) and not n.startswith("/") and "\\" not in n and "\x00" not in n and ".." not in n.split("/")


def stage_restore(name: str, passphrase: str, components: list[str], user: str) -> dict:
    """Prépare la restauration : vérifie l'archive, en extrait les éléments choisis dans `restore-pending` et note le plan. Rien n'est encore remplacé."""
    comps = [c for c in dict.fromkeys(components) if c in COMPONENTS]
    if not comps:
        raise StoreError("Choisissez au moins un élément à restaurer", 422)
    o = Opened(name, passphrase)
    try:
        man = o.manifest()
        have = man.get("components") or {}
        missing = [c for c in comps if c not in have]
        if missing:
            raise StoreError("Cette sauvegarde ne contient pas : " + ", ".join(COMPONENTS[c] for c in missing), 422)
        shutil.rmtree(pending_dir(), ignore_errors=True)
        pend = pending_dir()
        pend.mkdir(mode=0o700)
        prefixes = {"database": ("db/", "audit/"), "history": ("history/",), "home": ("home/",), "traefik": ("traefik/",)}
        wanted = tuple(p for c in comps for p in prefixes[c])
        count = 0
        try:
            with zipfile.ZipFile(o.zip_path) as z:
                for info in z.infolist():
                    n = info.filename
                    if info.is_dir() or not n.startswith(wanted):
                        continue
                    if not _safe_member(n):
                        raise StoreError(f"Chemin refusé dans la sauvegarde : {n[:80]}", 422)
                    dst = pend / n
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as fi, open(dst, "wb") as fo:
                        shutil.copyfileobj(fi, fo)
                    count += 1
        except zipfile.BadZipFile:
            raise StoreError("Archive abîmée", 422)
        if "database" in comps:
            db = pend / "db" / "app.db"
            try:
                c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and c.execute("SELECT 1 FROM sqlite_master WHERE name IN ('users','nodes') LIMIT 1").fetchone()
                c.close()
            except (sqlite3.Error, OSError):
                ok = False
            if not ok:
                raise StoreError("La base de cette sauvegarde est abîmée : restauration refusée, rien n'a été modifié", 422)
        plan = {"components": comps, "from": name, "by": user, "at": time.time(), "files": count, "app": man.get("app", "")}
        (pend / "PLAN.json").write_text(json.dumps(plan), encoding="utf-8")
        return {**plan, "archiveKey": _archive_key(o, man, comps)}
    except Exception:
        shutil.rmtree(pending_dir(), ignore_errors=True)
        raise
    finally:
        o.close()


def _archive_key(o: "Opened", man: dict, comps: list[str]) -> str:
    """La clé de chiffrement de l'archive (si elle y est ET qu'elle diffère de celle de ce hub) : l'administrateur doit la mettre dans son .env."""
    if "database" not in comps or not man.get("includesKey") or man.get("keyFingerprint") == key_fingerprint():
        return ""
    try:
        with zipfile.ZipFile(o.zip_path) as z:
            return z.read("secrets/encryption_key").decode()
    except (KeyError, OSError, zipfile.BadZipFile):
        return ""


def apply_pending() -> str:
    """Au démarrage, AVANT d'ouvrir la base : met en place une restauration préparée. Renvoie un message (vide s'il n'y avait rien à faire)."""
    pend, plan_file = pending_dir(), pending_dir() / "PLAN.json"
    if not plan_file.is_file():
        return ""
    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        shutil.rmtree(pend, ignore_errors=True)
        return "restauration ignorée : plan illisible"
    b = base()
    old = before_dir()
    shutil.rmtree(old, ignore_errors=True)
    old.mkdir(mode=0o700)

    def aside(p: Path, label: str) -> None:
        if p.exists():
            dst = old / label
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dst))

    def put(src: Path, dst: Path) -> None:
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

    try:
        comps = plan.get("components") or []
        if "database" in comps:
            for suffix in ("", "-wal", "-shm"):
                aside(b / f"app.db{suffix}", f"app.db{suffix}")
            put(pend / "db" / "app.db", b / "app.db")
            for p in [b / "audit.log", *b.glob("audit-[0-9]*.log")]:
                aside(p, p.name)
            for p in (pend / "audit").glob("*") if (pend / "audit").is_dir() else []:
                put(p, b / p.name)
        if "history" in comps:
            aside(b / "project-history", "project-history")
            aside(b / "backups", "backups")
            put(pend / "history" / "project-history", b / "project-history")
            put(pend / "history" / "traefik-versions", b / "backups")
        if "home" in comps:
            aside(b / "home", "home")
            put(pend / "home", b / "home")
        if "traefik" in comps and (pend / "traefik").is_dir():
            dyn = settings.dynamic_dir
            for dp, _dirs, names in os.walk(pend / "traefik"):
                for n in names:
                    s = Path(dp) / n
                    d = dyn / s.relative_to(pend / "traefik")
                    d.parent.mkdir(parents=True, exist_ok=True)
                    if d.exists():
                        keep = old / "traefik" / s.relative_to(pend / "traefik")
                        keep.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(d, keep)
                    shutil.copy2(s, d)
        msg = f"restauration de « {plan.get('from', '?')} » appliquée ({', '.join(comps)}) ; l'état précédent est dans {old.name}/"
    except Exception as e:  # noqa: BLE001 - on démarre quand même ; l'état d'avant est conservé
        msg = f"restauration INCOMPLÈTE ({type(e).__name__}: {e}) : l'état précédent est dans {old.name}/"
    shutil.rmtree(pend, ignore_errors=True)
    print("SAUVEGARDE : " + msg, file=sys.stderr)
    return msg


def schedule_restart(delay: float = 2.0) -> None:
    """Redémarre le hub (le conteneur repart tout seul avec une politique de redémarrage) pour appliquer la restauration."""
    import signal

    def go() -> None:
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Timer(delay, go).start()


def restart_policy() -> str:
    """Politique de redémarrage du conteneur du hub (« unless-stopped »…), « » si aucune, « ? » si on ne sait pas (pas de socket Docker)."""
    try:
        from . import project_ops
        me = project_ops._self_container()
        if me is None:
            return "?"
        return ((me.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") or ""
    except Exception:  # noqa: BLE001
        return "?"


# ------------------------------------------------------------------ tâches en arrière-plan (la création peut durer : la base fait plusieurs dizaines de Mo)
def start_job(fn, *args, **kw) -> str:
    jid = secrets.token_hex(6)
    with _lock:
        for k in [k for k, j in _jobs.items() if j["done"] and time.time() - j["ended"] > 600]:
            del _jobs[k]
        if any(not j["done"] for j in _jobs.values()):
            raise StoreError("Une sauvegarde est déjà en cours", 409)
        _jobs[jid] = {"done": False, "result": None, "error": "", "started": time.time(), "ended": 0.0}

    def run() -> None:
        try:
            _jobs[jid]["result"] = fn(*args, **kw)
        except StoreError as e:
            _jobs[jid]["error"] = str(e)
        except Exception as e:  # noqa: BLE001
            _jobs[jid]["error"] = f"Erreur inattendue ({type(e).__name__})"
        _jobs[jid]["done"], _jobs[jid]["ended"] = True, time.time()
    threading.Thread(target=run, name=f"backup-{jid}", daemon=True).start()
    return jid


def job(jid: str) -> dict:
    j = _jobs.get(jid)
    if not j:
        raise StoreError("Tâche inconnue", 404)
    return {"done": j["done"], "result": j["result"], "error": j["error"]}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
