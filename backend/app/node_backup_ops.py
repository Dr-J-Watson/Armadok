"""Sauvegarde d'un NŒUD : la partie « machine » (les fichiers qui ne sont que là), exécutée par le hub pour son propre nœud ou par un worker (opérations nommées).

Trois éléments, selon les modules du nœud :
  traefik          (module Traefik)  les fichiers de configuration dynamique : les sites, routeurs et middlewares du nœud
  traefik_versions (module Traefik)  les versions précédentes de ces fichiers gardées par l'interface
  project_history  (module Docker)   les versions précédentes des fichiers de projets
Jamais : l'identité du worker (sa clé ne quitte pas sa machine), la configuration Docker / Git (elle peut contenir des commandes exécutées), les fichiers des projets,
les volumes des conteneurs. Tout reste DANS ces dossiers : aucun chemin ne peut en sortir, aucun lien symbolique n'est suivi.

Comme pour les projets, un fichier voyage en morceaux de 2 Mo (messages de 8 Mo au plus entre le hub et un worker)."""
import base64
import binascii
import hashlib
import os
import secrets
import threading
import time
import zipfile
from pathlib import Path

from .config import settings
from .traefik_store import StoreError

CHUNK = 2 * 1024 * 1024
MAX_FILES = 20000
MAX_BYTES = 300 * 1024 * 1024
MAX_READ = 256 * 1024
PACK_TTL = 600
COMPONENTS = {
    "traefik": ("Fichiers de configuration Traefik", "traefik"),
    "traefik_versions": ("Versions précédentes des fichiers Traefik", "traefik"),
    "project_history": ("Versions précédentes des fichiers de projets", "docker"),
}
_packs: dict[str, dict] = {}
_lock = threading.Lock()


def root_of(component: str) -> Path:
    if component == "traefik":
        return settings.dynamic_dir
    if component == "traefik_versions":
        return settings.data_dir / "backups"
    if component == "project_history":
        return settings.data_dir / "project-history"
    raise StoreError("Élément de sauvegarde inconnu", 422)


def available(modules) -> list[str]:
    mods = set(modules or ())
    return [c for c, (_l, m) in COMPONENTS.items() if m in mods]


def _need(component: str, modules) -> Path:
    if component not in available(modules):
        raise StoreError("Cet élément n'existe pas sur ce nœud (module absent)", 409)
    return root_of(component)


def _files(root: Path):
    """(chemin, nom relatif) des fichiers ordinaires sous `root`, jamais un lien symbolique."""
    if not root.is_dir():
        return
    for dp, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(dp) / d).is_symlink()]
        for n in names:
            p = Path(dp) / n
            if p.is_symlink() or n.endswith(".armadok-part"):
                continue
            yield p, str(p.relative_to(root))


def safe_rel(rel: str) -> str:
    r = str(rel or "")
    parts = r.split("/")
    if not r or r.startswith("/") or "\\" in r or "\x00" in r or len(r) > 400 or any(p in ("", ".", "..") for p in parts):
        raise StoreError("Chemin de fichier invalide", 422)
    return r


def _target(root: Path, rel: str) -> Path:
    t = root / safe_rel(rel)
    try:
        real = t.resolve() if t.exists() or t.is_symlink() else t.parent.resolve() / t.name
        base = root.resolve()
    except (OSError, RuntimeError):
        raise StoreError("Chemin de fichier invalide", 422)
    if t.is_symlink() or not (real == base or base in real.parents):
        raise StoreError("Ce chemin sort du dossier sauvegardé", 403)
    return t


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------ opérations
def nb_info(a: dict, modules) -> dict:
    out = []
    for c in available(modules):
        n = size = 0
        for p, _ in _files(root_of(c)):
            try:
                size += p.stat().st_size
                n += 1
            except OSError:
                pass
        out.append({"id": c, "label": COMPONENTS[c][0], "files": n, "bytes": size})
    return {"components": out}


def nb_state(a: dict, modules) -> dict:
    """Les fichiers actuels (empreinte + taille), pour dire ce qui diffère d'une sauvegarde."""
    out = {}
    for c in a.get("components") or available(modules):
        root = _need(c, modules)
        files = {}
        for p, rel in _files(root):
            try:
                files[rel] = [_sha(p), p.stat().st_size]
            except OSError:
                continue
            if len(files) > MAX_FILES:
                raise StoreError("Trop de fichiers sur ce nœud pour être comparés", 413)
        out[c] = files
    return {"files": out}


def nb_read(a: dict, modules) -> dict:
    root = _need(str(a.get("component") or ""), modules)
    t = _target(root, a.get("rel"))
    if not t.is_file():
        raise StoreError("Fichier introuvable sur ce nœud", 404)
    if t.stat().st_size > MAX_READ:
        return {"large": True, "size": t.stat().st_size}
    raw = t.read_bytes()
    if b"\x00" in raw[:8192]:
        return {"binary": True, "size": len(raw)}
    return {"content": raw.decode("utf-8", "replace"), "size": len(raw)}


def _sweep() -> None:
    now = time.time()
    with _lock:
        for k in [k for k, p in _packs.items() if now - p["at"] > PACK_TTL]:
            Path(_packs.pop(k)["path"]).unlink(missing_ok=True)


def nb_pack(a: dict, modules) -> dict:
    """Prépare une archive zip des éléments demandés (dans le dossier temporaire du nœud) ; à lire par morceaux avec nb_pack_read."""
    comps = [c for c in dict.fromkeys(a.get("components") or []) if c in COMPONENTS]
    if not comps:
        raise StoreError("Choisissez au moins un élément à sauvegarder", 422)
    for c in comps:
        _need(c, modules)
    _sweep()
    tmp_dir = settings.data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = tmp_dir / f"nb-{secrets.token_hex(6)}.zip"
    total = n = 0
    info: dict = {}
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
            for c in comps:
                files = nbytes = 0
                for p, rel in _files(root_of(c)):
                    try:
                        size = p.stat().st_size
                    except OSError:
                        continue
                    total += size
                    n += 1
                    if total > MAX_BYTES or n > MAX_FILES:
                        raise StoreError(f"Ce nœud est trop gros pour une sauvegarde d'un coup (plus de {MAX_BYTES // 1048576} Mo ou {MAX_FILES} fichiers)", 413)
                    z.write(p, f"{c}/{rel}")
                    files += 1
                    nbytes += size
                info[c] = {"files": files, "bytes": nbytes}
    except Exception:
        path.unlink(missing_ok=True)
        raise
    token = secrets.token_urlsafe(16)
    with _lock:
        _packs[token] = {"path": str(path), "at": time.time()}
    return {"token": token, "size": path.stat().st_size, "components": info}


def nb_pack_read(a: dict, modules) -> dict:
    token = str(a.get("token") or "")
    with _lock:
        p = _packs.get(token)
    if not p:
        raise StoreError("Cette préparation a expiré : recommencez", 410)
    try:
        offset = max(0, int(a.get("offset") or 0))
        with open(p["path"], "rb") as f:
            f.seek(offset)
            raw = f.read(CHUNK)
        size = Path(p["path"]).stat().st_size
    except (OSError, ValueError):
        raise StoreError("Lecture impossible", 500)
    done = offset + len(raw) >= size
    if done:
        with _lock:
            _packs.pop(token, None)
        Path(p["path"]).unlink(missing_ok=True)
    else:
        p["at"] = time.time()
    return {"b64": base64.b64encode(raw).decode(), "done": done}


def _check_yaml(rel: str, data: bytes) -> None:
    if not rel.endswith((".yml", ".yaml", ".yml.off", ".yaml.off")):
        return
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError
    try:
        YAML(typ="safe").load(data.decode("utf-8"))
    except (YAMLError, UnicodeDecodeError, ValueError):
        raise StoreError(f"« {rel} » n'est pas un YAML valide : Traefik ne pourrait pas le lire", 422)


def nb_put(a: dict, modules) -> dict:
    """Écrit un fichier (par morceaux, dans « .armadok-part » puis renommé d'un coup). Le propriétaire et les droits d'un fichier remplacé sont conservés."""
    comp = str(a.get("component") or "")
    root = _need(comp, modules)
    t = _target(root, a.get("rel"))
    part = t.with_name(t.name + ".armadok-part")
    try:
        data = base64.b64decode(str(a.get("b64") or ""), validate=True)
    except (binascii.Error, ValueError):
        raise StoreError("Morceau illisible", 422)
    if len(data) > CHUNK:
        raise StoreError("Morceau trop gros", 413)
    offset = int(a.get("offset") or 0)
    try:
        if offset == 0:
            t.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644 if comp == "traefik" else 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
        else:
            if not part.is_file() or part.stat().st_size != offset:
                raise StoreError("Envoi interrompu : recommencez", 409)
            with open(part, "ab") as f:
                f.write(data)
        if a.get("last"):
            if comp == "traefik":
                _check_yaml(str(a.get("rel")), part.read_bytes())
            if t.exists():
                try:
                    os.chmod(part, t.stat().st_mode & 0o777)
                except OSError:
                    pass
            os.replace(part, t)
    except StoreError:
        part.unlink(missing_ok=True)
        raise
    except PermissionError:
        part.unlink(missing_ok=True)
        raise StoreError(f"Droits insuffisants pour écrire {a.get('rel')} : le service tourne avec l'utilisateur {os.getuid()}:{os.getgid()}", 403)
    return {"ok": True}


def nb_delete(a: dict, modules) -> dict:
    root = _need(str(a.get("component") or ""), modules)
    t = _target(root, a.get("rel"))
    if not t.is_file():
        raise StoreError("Fichier introuvable sur ce nœud", 404)
    try:
        t.unlink()
    except PermissionError:
        raise StoreError(f"Droits insuffisants pour supprimer {a.get('rel')}", 403)
    return {"ok": True}


OPS = {"nb_info": nb_info, "nb_state": nb_state, "nb_read": nb_read, "nb_pack": nb_pack, "nb_pack_read": nb_pack_read, "nb_put": nb_put, "nb_delete": nb_delete}


def handle(op: str, a: dict, modules):
    fn = OPS.get(op)
    if not fn or not isinstance(a, dict):
        raise StoreError("Opération non autorisée", 400)
    return fn(a, modules)
