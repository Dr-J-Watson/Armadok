"""Opérations NOMMÉES sur les projets Compose d'une machine : découverte des dossiers, lecture du compose.

Comme docker_ops : exécutées par le hub pour son propre nœud, ou par un worker (liste fermée). Tout ce qui touche au disque reste DANS les racines
autorisées (PROJECT_ROOTS) : un dossier hors des racines, un lien symbolique qui en sort, un nom de fichier piégé sont refusés."""
import hashlib
import json
import os
import re
import shlex
import secrets
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from . import docker_api as dapi
from . import gitssh
from .config import settings
from .traefik_store import StoreError

COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
SKIP_DIRS = {"node_modules", ".git", "vendor", "__pycache__", "venv", ".venv", ".cache", "site-packages", "dist", "build"}
MAX_DEPTH = 4
MAX_PROJECTS = 300
MAX_COMPOSE = 512 * 1024


def roots() -> list[Path]:
    out = []
    for r in settings.project_roots:
        p = Path(r)
        try:
            if p.is_absolute() and p.is_dir():
                out.append(p.resolve())
        except OSError:
            pass
    return out


def inside(p: Path) -> bool:
    """Le chemin (déjà résolu : liens suivis) est-il dans une racine autorisée ?"""
    return any(p == r or r in p.parents for r in roots())


def safe_dir(raw: str) -> Path:
    """Dossier de projet autorisé (existant, dans une racine). Lève StoreError sinon."""
    try:
        p = Path(str(raw or "")).resolve()
    except (OSError, RuntimeError, ValueError):
        raise StoreError("Chemin invalide", 422)
    if not str(raw or "").startswith("/") or not inside(p):
        raise StoreError("Ce dossier n'est pas dans les racines autorisées (PROJECT_ROOTS)", 403)
    if not p.is_dir():
        raise StoreError("Dossier introuvable", 404)
    return p


def compose_file(d: Path) -> Path | None:
    for n in COMPOSE_NAMES:
        f = d / n
        if f.is_file():
            return f
    return None


COMPOSE_RE = re.compile(r"^(docker-)?compose([._-][A-Za-z0-9._-]*)?\.ya?ml$")


def compose_candidates(d: Path) -> list[str]:
    """Tous les fichiers compose du dossier (premier niveau) : le principal d'abord, puis l'« override », puis les autres par ordre alphabétique."""
    try:
        found = sorted(x.name for x in d.iterdir() if COMPOSE_RE.match(x.name) and x.is_file() and not x.is_symlink())
    except OSError:
        return []
    base = compose_file(d)
    ov = override_file(d)
    first = [x for x in (base.name if base else "", ov or "") if x]
    return first + [x for x in found if x not in first]


def override_file(d: Path) -> str:
    """Le fichier « override » que Docker Compose charge de lui-même avec le principal (docker-compose.override.yml, compose.override.yaml…)."""
    base = compose_file(d)
    if not base:
        return ""
    stem = base.name.rsplit(".", 1)[0]
    for ext in (".yml", ".yaml"):
        if (d / (stem + ".override" + ext)).is_file():
            return stem + ".override" + ext
    return ""


def default_files(d: Path) -> list[str]:
    """Ce que fait « docker compose up » sans -f : le fichier principal, plus son override s'il existe."""
    base = compose_file(d)
    return [x for x in (base.name if base else "", override_file(d)) if x]


def pick_files(d: Path, files) -> list[str]:
    """Fichiers demandés (noms simples, présents dans le dossier) ; vide = ceux par défaut."""
    if not files:
        return default_files(d)
    ok = set(compose_candidates(d))
    out = []
    for x in files if isinstance(files, (list, tuple)) else []:
        x = str(x)
        if x not in ok:
            raise StoreError(f"Fichier compose inconnu : {x[:80]}", 422)
        if x not in out:
            out.append(x)
    if not out or len(out) > 10:
        raise StoreError("Choisissez entre 1 et 10 fichiers compose", 422)
    return out


def project_name(d: Path, data: dict | None) -> str:
    """Nom du projet pour Compose : « name: » du fichier, sinon le nom du dossier simplifié (minuscules, lettres, chiffres, - et _)."""
    n = (data or {}).get("name") if isinstance(data, dict) else None
    return re.sub(r"[^a-z0-9_-]", "", str(n or d.name).lower())


def _labels(raw) -> dict:
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    out = {}
    for item in raw if isinstance(raw, list) else []:
        k, _, v = str(item).partition("=")
        out[k] = v
    return out


def parse_compose(text: str) -> dict:
    """Services d'un fichier compose : {"name", "services": [{name, image, build, containerName, ports, profiles, traefik: {labels}}], "error"}."""
    try:
        data = YAML(typ="safe").load(text)
    except YAMLError:
        return {"name": None, "services": [], "error": "YAML invalide"}
    if not isinstance(data, dict):
        return {"name": None, "services": [], "error": "Ce fichier ne ressemble pas à un docker-compose.yml"}
    out = []
    for name, s in (data.get("services") or {}).items() if isinstance(data.get("services"), dict) else []:
        s = s if isinstance(s, dict) else {}
        lab = _labels(s.get("labels"))
        out.append({"name": str(name), "image": str(s.get("image") or ""), "build": bool(s.get("build")), "containerName": str(s.get("container_name") or ""),
                    "ports": [str(p) for p in (s.get("ports") or []) if not isinstance(p, dict)][:12], "profiles": [str(p) for p in (s.get("profiles") or [])],
                    "traefik": {k: v for k, v in lab.items() if k.startswith("traefik.")}})
    return {"name": data.get("name") if isinstance(data.get("name"), str) else None, "services": out, "error": ""}


def _merge(parts: list[dict]) -> dict:
    """Fusionne des fichiers compose lus dans l'ordre (comme -f a -f b) : les services s'additionnent, le dernier fichier l'emporte sur un même réglage."""
    out: dict = {"name": None, "services": [], "error": ""}
    by: dict[str, dict] = {}
    for c in parts:
        out["error"] = out["error"] or c["error"]
        out["name"] = c["name"] or out["name"]
        for sv in c["services"]:
            cur = by.get(sv["name"])
            if cur is None:
                by[sv["name"]] = dict(sv, ports=list(sv["ports"]), profiles=list(sv["profiles"]), traefik=dict(sv["traefik"]))
                continue
            for k in ("image", "containerName"):
                cur[k] = sv[k] or cur[k]
            cur["build"] = cur["build"] or sv["build"]
            cur["ports"] = list(dict.fromkeys(cur["ports"] + sv["ports"]))[:12]
            cur["profiles"] = list(dict.fromkeys(cur["profiles"] + sv["profiles"]))
            cur["traefik"].update(sv["traefik"])
    out["services"] = list(by.values())
    return out


def read_compose(d: Path, files=None) -> tuple[Path | None, dict]:
    base = compose_file(d)
    if not base:
        return None, {"name": None, "services": [], "error": "Aucun fichier compose dans ce dossier"}
    parts = []
    names = pick_files(d, files)
    for n in names:
        pre = f"{n} : " if len(names) > 1 else ""   # le nom du fichier n'est utile que s'il y en a plusieurs
        f = d / n
        try:
            if f.stat().st_size > MAX_COMPOSE:
                parts.append({"name": None, "services": [], "error": f"{pre}fichier trop gros"})
            else:
                c = parse_compose(f.read_text(encoding="utf-8", errors="replace"))
                parts.append(dict(c, error=f"{pre}{c['error']}" if c["error"] else ""))
        except OSError:
            parts.append({"name": None, "services": [], "error": f"{pre}fichier illisible (droits ?)"})
    return base, _merge(parts)


_VAR = re.compile(r"\$(?:(\$)|(\w+)|\{(\w+)(?:(:?[-+?])([^}]*))?\})")


def read_dotenv(d: Path) -> dict[str, str]:
    """Les variables du .env du projet (ce que Docker Compose utilise pour remplacer ${VAR}) ; {} s'il n'y en a pas."""
    try:
        from .project_templates import parse_env
        return {v["key"]: v["value"] for v in parse_env((d / ".env").read_text(errors="replace"))}
    except OSError:
        return {}


def interpolate(text: str, env: dict[str, str]) -> str:
    """${VAR}, $VAR, ${VAR:-défaut}, ${VAR-défaut}, ${VAR:+autre}, $$ (un « $ »). Une variable inconnue sans défaut laisse le texte tel quel."""
    unresolved = False

    def sub(m):
        nonlocal unresolved
        if m.group(1):
            return "$"
        name, op, arg = m.group(2) or m.group(3), m.group(4), m.group(5) or ""
        val = env.get(name)
        if op in (":-", "-"):
            return arg if val is None or (op == ":-" and val == "") else val
        if op in (":+", "+"):
            return arg if val is not None and not (op == ":+" and val == "") else ""
        if val is None or op == "?":
            unresolved = True
            return m.group(0)
        return val
    out = _VAR.sub(sub, text)
    return text if unresolved else out


def entry(d: Path, files=None) -> dict:
    f, c = read_compose(d, files)
    env = read_dotenv(d) if any("$" in s["image"] for s in c["services"]) else {}
    for s in c["services"]:   # image: ghcr.io/x/app:${VERSION} → la valeur du .env (l'écriture d'origine reste dans imageRaw)
        if "$" in s["image"]:
            res = interpolate(s["image"], env)
            if res != s["image"]:
                s["imageRaw"], s["image"] = s["image"], res
    return {"hasMake": make_file(d) is not None, "composeFiles": compose_candidates(d), "defaultFiles": default_files(d), "path": str(d), "name": d.name, "composeProject": project_name(d, {"name": c["name"]}), "file": f.name if f else "", "services": c["services"], "error": c["error"],
            "hasEnv": (d / ".env").is_file(), "hasGit": (d / ".git").exists(), "build": any(s["build"] for s in c["services"]), "mtime": f.stat().st_mtime if f else 0}


def p_discover(a: dict) -> dict:
    """Tous les dossiers qui contiennent un fichier compose, dans les racines autorisées."""
    found: list[dict] = []
    for r in roots():
        base_depth = len(r.parts)
        for dirpath, dirs, _files in os.walk(r, followlinks=False):
            cur = Path(dirpath)
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
            if len(cur.parts) - base_depth >= MAX_DEPTH:
                dirs[:] = []
            if compose_file(cur):
                found.append(entry(cur))
                if len(found) >= MAX_PROJECTS:
                    return {"projects": found, "truncated": True, "roots": [str(x) for x in roots()]}
    return {"projects": sorted(found, key=lambda e: e["path"]), "truncated": False, "roots": [str(x) for x in roots()]}


def p_project(a: dict) -> dict:
    d = safe_dir(a.get("dir"))
    return entry(d, a.get("files") or None)


_compose_seen = 0.0   # dernier instant où « docker compose version » a répondu : la liste des projets le demande à chaque rafraîchissement


def _compose_ok() -> bool:
    global _compose_seen
    if time.monotonic() - _compose_seen < 300:
        return True
    cli = shutil.which("docker")
    ok = False
    if cli:
        try:
            ok = subprocess.run([cli, "compose", "version"], capture_output=True, timeout=10, env=_env()).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
    if ok:
        _compose_seen = time.monotonic()
    return ok


def p_info(_a: dict) -> dict:
    ok = _compose_ok()
    return {"roots": [str(x) for x in roots()], "configuredRoots": list(settings.project_roots), "compose": ok}


# ------------------------------------------------------------------ fichiers du projet
MAX_FILE = 2 * 1024 * 1024   # un fichier s'ouvre et s'enregistre dans l'éditeur jusque-là
MAX_FORCE = 6 * 1024 * 1024  # au-delà de MAX_FILE : lecture seule, sur demande (« charger quand même »)
MAX_TREE = 800
KEEP_VERSIONS = 30
TREE_DEPTH = 5


def is_env(rel: str) -> bool:
    return Path(rel).name.startswith(".env")


def safe_rel(rel: str) -> str:
    """Chemin relatif sûr dans un projet : pas de « .. », pas de chemin absolu, pas de dossier .git, pas de caractère étrange."""
    r = str(rel or "")
    parts = r.split("/")
    if not r or r.startswith("/") or "\\" in r or "\x00" in r or len(r) > 300 or len(parts) > 8 or any(p in ("", ".", "..", ".git") for p in parts):
        raise StoreError("Chemin de fichier invalide", 422)
    return r


def safe_file(d: Path, rel: str, must_exist: bool = True) -> Path:
    r = safe_rel(rel)
    f = d / r
    try:
        real = f.resolve() if f.exists() or f.is_symlink() else f.parent.resolve() / f.name
    except (OSError, RuntimeError):
        raise StoreError("Chemin de fichier invalide", 422)
    if not (real == d or d in real.parents) or not inside(real):
        raise StoreError("Ce fichier sort du dossier du projet", 403)
    if must_exist and not real.is_file():
        raise StoreError("Fichier introuvable", 404)
    return real


def _hash(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def p_tree(a: dict) -> dict:
    d = safe_dir(a.get("dir"))
    out: list[dict] = []
    base = len(d.parts)
    for dirpath, dirs, files in os.walk(d, followlinks=False):
        cur = Path(dirpath)
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and x != ".git")
        if len(cur.parts) - base >= TREE_DEPTH:
            dirs[:] = []
        rel = str(cur.relative_to(d))
        for x in dirs:
            out.append({"path": (rel + "/" if rel != "." else "") + x, "type": "dir"})
        for x in sorted(files):
            fp = cur / x
            try:
                size = fp.stat().st_size
            except OSError:
                continue
            out.append({"path": (rel + "/" if rel != "." else "") + x, "type": "file", "size": size, "env": is_env(x)})
        if len(out) >= MAX_TREE:
            return {"entries": out[:MAX_TREE], "truncated": True}
    return {"entries": out, "truncated": False}


def p_read(a: dict) -> dict:
    d = safe_dir(a.get("dir"))
    f = safe_file(d, a.get("rel"))
    size = f.stat().st_size
    if size > (MAX_FORCE if a.get("force") else MAX_FILE):
        raise StoreError(f"Fichier trop gros pour s'afficher ({MAX_FORCE // 1048576} Mo maximum) : téléchargez-le" if a.get("force") else "Fichier trop volumineux : demandez à le charger quand même", 413)
    raw = f.read_bytes()
    if b"\x00" in raw[:8192]:
        raise StoreError("Fichier binaire : il ne s'affiche pas dans l'éditeur", 415)
    return {"content": raw.decode("utf-8", "replace"), "hash": _hash(raw), "mtime": f.stat().st_mtime, "env": is_env(a.get("rel")), "large": size > MAX_FILE}


def _hist_dir(d: Path, rel: str) -> Path:
    return settings.data_dir / "project-history" / hashlib.sha1(f"{d}/{rel}".encode()).hexdigest()[:24]


def _save_version(d: Path, rel: str, raw: bytes) -> None:
    h = _hist_dir(d, rel)
    h.mkdir(parents=True, exist_ok=True, mode=0o700)
    (h / f"{int(time.time() * 1000)}.txt").write_bytes(raw)
    for old in sorted(h.glob("*.txt"))[:-KEEP_VERSIONS]:
        old.unlink(missing_ok=True)


def p_write(a: dict) -> dict:
    d = safe_dir(a.get("dir"))
    rel = safe_rel(a.get("rel"))
    content = a.get("content")
    if not isinstance(content, str) or len(content.encode()) > MAX_FILE:
        raise StoreError(f"Contenu invalide ou trop gros ({MAX_FILE // 1048576} Mo maximum)", 413)
    f = safe_file(d, rel, must_exist=False)
    if f.exists():
        if not f.is_file():
            raise StoreError("Ce chemin n'est pas un fichier", 409)
        old = f.read_bytes()
        if a.get("baseHash") and a["baseHash"] != _hash(old):
            raise StoreError("Le fichier a changé sur le disque depuis son ouverture : rechargez-le avant d'enregistrer", 409)
        if a.get("create"):
            raise StoreError("Ce fichier existe déjà", 409)
        _save_version(d, rel, old)
    elif not a.get("create"):
        raise StoreError("Fichier introuvable", 404)
    try:
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if is_env(rel) else 0o644)
            with os.fdopen(fd, "wb") as fh:
                fh.write(content.encode())
        else:
            with open(f, "wb") as fh:   # sur place : le propriétaire, les droits et les liens du fichier restent ceux du disque
                fh.write(content.encode())
    except PermissionError:
        raise StoreError(f"Droits insuffisants pour écrire {rel} : l'interface tourne avec l'utilisateur {os.getuid()}:{os.getgid()}", 403)
    return {"hash": _hash(content.encode()), "path": rel}


def p_delete_file(a: dict) -> dict:
    d = safe_dir(a.get("dir"))
    rel = safe_rel(a.get("rel"))
    f = safe_file(d, rel)
    if compose_file(d) == f:
        raise StoreError("Le fichier compose du projet ne se supprime pas d'ici (supprimez le projet)", 409)
    _save_version(d, rel, f.read_bytes())
    f.unlink()
    return {"ok": True}


# Limites des gestes d'édition. Le droit « projects.nolimit » (gestionnaires du nœud) les lève : la route passe alors `unlimited`.
# Quelle que soit la personne, la suppression d'un dossier ne garde dans l'historique qu'un budget raisonnable de fichiers texte.
MAX_DIR_DELETE_FILES = 300
MAX_DIR_DELETE_BYTES = 5_000_000
HISTORY_FILES = MAX_DIR_DELETE_FILES
HISTORY_BYTES = MAX_DIR_DELETE_BYTES
NEED_NOLIMIT = " : il faut le droit « Transferts et suppressions sans limite de taille » (à demander au gestionnaire du nœud)"


def safe_path(d: Path, rel: str, must_exist: bool = True) -> Path:
    """Fichier OU dossier du projet, jamais un lien symbolique, jamais hors du projet ni la racine du projet."""
    r = safe_rel(rel)
    f = d / r
    if f.is_symlink():
        raise StoreError("Un lien symbolique ne se déplace ni ne se supprime d'ici", 409)
    try:
        real = f.resolve() if f.exists() else f.parent.resolve() / f.name
    except (OSError, RuntimeError):
        raise StoreError("Chemin invalide", 422)
    if real == d or d not in real.parents or not inside(real):
        raise StoreError("Ce chemin sort du dossier du projet", 403)
    if must_exist and not real.exists():
        raise StoreError("Introuvable", 404)
    return real


def _has_compose(d: Path, target: Path) -> bool:
    """Le fichier compose du projet est-il ce chemin, ou se trouve-t-il dedans ?"""
    c = compose_file(d)
    return bool(c) and (c == target or target in c.parents)


def p_mkdir(a: dict) -> dict:
    d = safe_dir(a.get("dir"))
    f = safe_path(d, a.get("rel"), must_exist=False)
    if f.exists():
        raise StoreError("Ce dossier existe déjà" if f.is_dir() else "Un fichier porte déjà ce nom", 409)
    try:
        f.mkdir(parents=True)
    except PermissionError:
        raise StoreError(f"Droits insuffisants pour créer ce dossier : l'interface tourne avec l'utilisateur {os.getuid()}:{os.getgid()}", 403)
    return {"path": str(f.relative_to(d))}


def p_move(a: dict) -> dict:
    """Renomme ou déplace un fichier ou un dossier, DANS le projet. Le fichier compose du projet ne bouge pas d'ici."""
    d = safe_dir(a.get("dir"))
    src = safe_path(d, a.get("src"))
    dst = safe_path(d, a.get("dst"), must_exist=False)
    if dst.exists():
        raise StoreError("Cette destination existe déjà", 409)
    if src == dst or src in dst.parents:
        raise StoreError("Impossible de déplacer un dossier dans lui-même", 422)
    if _has_compose(d, src):
        raise StoreError("Le fichier compose du projet ne se déplace pas d'ici (il définit le projet)", 409)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)
    except PermissionError:
        raise StoreError(f"Droits insuffisants pour déplacer ce chemin : l'interface tourne avec l'utilisateur {os.getuid()}:{os.getgid()}", 403)
    return {"path": str(dst.relative_to(d))}


def p_delete_dir(a: dict) -> dict:
    """Supprime un dossier du projet et son contenu. Les fichiers texte sont gardés dans l'historique ; au-delà de 300 fichiers ou 5 Mo, on refuse
    — sauf avec le droit « sans limite » (`unlimited`) : le dossier part alors en entier et seul un budget de fichiers texte va à l'historique (node_modules, caches…)."""
    d = safe_dir(a.get("dir"))
    f = safe_path(d, a.get("rel"))
    if not f.is_dir():
        raise StoreError("Ce n'est pas un dossier", 409)
    if _has_compose(d, f):
        raise StoreError("Ce dossier contient le fichier compose du projet : supprimez le projet pour cela", 409)
    files, total = [], 0
    for dp, dirs, names in os.walk(f, followlinks=False):
        for n in names:
            p = Path(dp) / n
            try:
                size = p.lstat().st_size
            except OSError:
                continue
            files.append((p, size))
            total += size
        if not a.get("unlimited") and (len(files) > MAX_DIR_DELETE_FILES or total > MAX_DIR_DELETE_BYTES):
            raise StoreError(f"Ce dossier est trop gros pour être supprimé d'ici (plus de {MAX_DIR_DELETE_FILES} fichiers ou {MAX_DIR_DELETE_BYTES // 1_000_000} Mo)" + NEED_NOLIMIT, 413)
    if any(is_env(p.name) for p, _ in files) and not a.get("canEnv"):
        raise StoreError("Ce dossier contient un fichier .env : il faut le droit de gérer les secrets", 403)
    kept, budget = 0, HISTORY_BYTES
    for p, size in files:
        if kept >= HISTORY_FILES or size > budget:
            continue
        if p.is_symlink() or size > MAX_FILE:
            continue
        raw = p.read_bytes()
        if b"\x00" not in raw[:8192]:
            _save_version(d, str(p.relative_to(d)), raw)
            kept += 1
            budget -= size
    try:
        shutil.rmtree(f)
    except PermissionError:
        raise StoreError(f"Droits insuffisants pour supprimer ce dossier : l'interface tourne avec l'utilisateur {os.getuid()}:{os.getgid()}", 403)
    return {"ok": True, "files": len(files), "history": kept}


# ------------------------------------------------------------------ télécharger / importer des fichiers (par morceaux)
# Le hub et les workers échangent des messages JSON de 8 Mo au plus, et l'API refuse les corps de plus de 4 Mo : un fichier voyage donc en morceaux
# de 2 Mo encodés en base64, dans les deux sens. Rien n'est jamais lu ou écrit hors du projet (mêmes contrôles que le reste de l'explorateur).
CHUNK = 2 * 1024 * 1024
MAX_UPLOAD = 100 * 1024 * 1024    # un fichier importé
MAX_PACK = 100 * 1024 * 1024      # ce qu'on accepte de télécharger d'un coup (avant compression)
MAX_PACK_FILES = 5000
PACK_TTL = 600
_packs: dict[str, dict] = {}
_packs_lock = threading.Lock()


def _drop_pack(p: dict) -> None:
    if p.get("temp"):
        Path(p["path"]).unlink(missing_ok=True)


def _sweep_packs() -> None:
    now = time.time()
    with _packs_lock:
        for k in [k for k, p in _packs.items() if now - p["at"] > PACK_TTL]:
            _drop_pack(_packs.pop(k))


def p_pack(a: dict) -> dict:
    """Prépare le téléchargement d'un fichier (tel quel) ou de plusieurs éléments (archive zip, arborescence conservée). Renvoie un jeton à lire par morceaux."""
    import tempfile
    import zipfile
    d = safe_dir(a.get("dir"))
    rels = a.get("rels")
    if not isinstance(rels, list) or not 1 <= len(rels) <= 500 or not all(isinstance(r, str) for r in rels):
        raise StoreError("Sélection invalide", 422)
    can_env = bool(a.get("canEnv"))
    members: list[tuple[Path, str]] = []
    total = 0
    for rel in rels:
        f = safe_path(d, rel)
        if f.is_file():
            todo = [(f, str(f.relative_to(d)))]
        else:
            todo = []
            for dp, dirs, names in os.walk(f, followlinks=False):
                dirs[:] = [x for x in dirs if x != ".git" and not (Path(dp) / x).is_symlink()]
                todo += [(Path(dp) / n, str((Path(dp) / n).relative_to(d))) for n in names if not (Path(dp) / n).is_symlink()]
                if len(todo) > MAX_PACK_FILES and not a.get("unlimited"):
                    raise StoreError(f"Trop de fichiers dans la sélection (plus de {MAX_PACK_FILES})" + NEED_NOLIMIT, 413)
        for p, arc in todo:
            if is_env(p.name) and not can_env:
                if f.is_file():
                    raise StoreError("Ce fichier contient des secrets : il faut le droit de gérer les secrets", 403)
                continue
            members.append((p, arc))
            total += p.stat().st_size
            if not a.get("unlimited") and (total > MAX_PACK or len(members) > MAX_PACK_FILES):
                raise StoreError(f"Sélection trop grosse pour un téléchargement (plus de {MAX_PACK // 1048576} Mo ou {MAX_PACK_FILES} fichiers)" + NEED_NOLIMIT, 413)
    if not members:
        raise StoreError("Rien à télécharger dans cette sélection", 404)
    _sweep_packs()
    first = safe_path(d, rels[0])
    if len(rels) == 1 and first.is_file():
        path, name, temp = first, first.name, False
    else:
        name = (first.name if len(rels) == 1 else d.name + "-selection") + ".zip"
        tmp_dir = settings.data_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(suffix=".zip", dir=tmp_dir)
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for p, arc in members:
                    z.write(p, arc)
        except OSError as e:
            Path(tmp).unlink(missing_ok=True)
            raise StoreError(f"Impossible de préparer l'archive : {e}", 500)
        path, temp = Path(tmp), True
    token = secrets.token_urlsafe(16)
    with _packs_lock:
        _packs[token] = {"path": str(path), "temp": temp, "at": time.time()}
    return {"token": token, "name": name, "size": path.stat().st_size, "files": len(members)}


def p_pack_read(a: dict) -> dict:
    import base64
    token = str(a.get("token") or "")
    with _packs_lock:
        p = _packs.get(token)
    if not p:
        raise StoreError("Ce téléchargement a expiré : relancez-le", 410)
    try:
        offset = max(0, int(a.get("offset") or 0))
        with open(p["path"], "rb") as fh:
            fh.seek(offset)
            raw = fh.read(CHUNK)
    except (OSError, ValueError):
        raise StoreError("Lecture impossible", 500)
    done = len(raw) < CHUNK or offset + len(raw) >= Path(p["path"]).stat().st_size
    if done:
        with _packs_lock:
            _packs.pop(token, None)
        _drop_pack(p)
    else:
        p["at"] = time.time()
    return {"b64": base64.b64encode(raw).decode(), "done": done}


def p_upload(a: dict) -> dict:
    """Reçoit un fichier par morceaux : écrit dans « <nom>.armadok-part » puis renommé d'un coup à la fin (jamais de fichier à moitié écrit sous son vrai nom)."""
    import base64
    d = safe_dir(a.get("dir"))
    rel = safe_rel(a.get("rel"))
    f = safe_file(d, rel, must_exist=False)
    part = f.with_name(f.name + ".armadok-part")
    if a.get("abort"):
        part.unlink(missing_ok=True)
        return {"ok": True}
    try:
        raw = base64.b64decode(str(a.get("b64") or ""), validate=True)
    except ValueError:
        raise StoreError("Morceau de fichier illisible", 422)
    if len(raw) > CHUNK:
        raise StoreError("Morceau trop gros", 413)
    offset = int(a.get("offset") or 0)
    try:
        if offset == 0:
            if f.exists() and (f.is_dir() or not a.get("overwrite")):
                raise StoreError("Un dossier porte ce nom" if f.is_dir() else "Ce fichier existe déjà", 409)
            f.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if is_env(rel) else 0o644)
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
        else:
            if not part.is_file() or part.stat().st_size != offset:
                raise StoreError("Envoi interrompu : recommencez", 409)
            if offset + len(raw) > MAX_UPLOAD and not a.get("unlimited"):
                part.unlink(missing_ok=True)
                raise StoreError(f"Fichier trop gros (plus de {MAX_UPLOAD // 1048576} Mo)" + NEED_NOLIMIT, 413)
            with open(part, "ab") as fh:
                fh.write(raw)
        if a.get("last"):
            if f.is_file():
                try:
                    if f.stat().st_size <= MAX_FILE:
                        old = f.read_bytes()
                        if b"\x00" not in old[:8192]:
                            _save_version(d, rel, old)
                except OSError:
                    pass
            os.replace(part, f)
    except PermissionError:
        raise StoreError(f"Droits insuffisants pour écrire {rel} : l'interface tourne avec l'utilisateur {os.getuid()}:{os.getgid()}", 403)
    return {"path": rel, "done": bool(a.get("last"))}


def p_history(a: dict) -> dict:
    d = safe_dir(a.get("dir"))
    rel = safe_rel(a.get("rel"))
    h = _hist_dir(d, rel)
    out = [{"vid": p.stem, "time": int(p.stem) / 1000, "size": p.stat().st_size} for p in sorted(h.glob("*.txt"), reverse=True)] if h.is_dir() else []
    return {"versions": out}


def p_history_content(a: dict) -> dict:
    d = safe_dir(a.get("dir"))
    rel = safe_rel(a.get("rel"))
    vid = str(a.get("vid") or "")
    if not re.fullmatch(r"\d{10,16}", vid):
        raise StoreError("Version invalide", 422)
    p = _hist_dir(d, rel) / f"{vid}.txt"
    if not p.is_file():
        raise StoreError("Version introuvable", 404)
    return {"content": p.read_text(encoding="utf-8", errors="replace")}


# ------------------------------------------------------------------ docker compose
def _env() -> dict:
    e = dict(os.environ)
    e.update(COMPOSE_PROGRESS="plain", COMPOSE_ANSI="never", NO_COLOR="1")
    home = settings.data_dir / "home"   # le conteneur est en lecture seule : docker, buildx et git ont besoin d'un dossier personnel inscriptible
    try:
        home.mkdir(parents=True, exist_ok=True)
        e.update(HOME=str(home), DOCKER_CONFIG=str(home / ".docker"), BUILDX_CONFIG=str(home / ".buildx"), XDG_CONFIG_HOME=str(home / ".config"))
    except OSError:
        pass
    if settings.docker_socket:
        e["DOCKER_HOST"] = f"unix://{settings.docker_socket}"
    return e


def _cli() -> str:
    cli = shutil.which("docker")
    if not cli:
        raise StoreError("La commande « docker » (avec le plugin compose) n'est pas installée dans ce conteneur", 501)
    return cli


def p_validate(a: dict) -> dict:
    """Contrôle d'un fichier compose AVANT de l'enregistrer : YAML, puis « docker compose config » (variables du .env incluses) si la commande existe."""
    d = safe_dir(a.get("dir"))
    content = a.get("content")
    if not isinstance(content, str) or len(content) > MAX_FILE:
        raise StoreError("Contenu invalide", 422)
    c = parse_compose(content)
    if c["error"]:
        return {"ok": False, "message": c["error"], "services": []}
    if not shutil.which("docker"):
        return {"ok": True, "message": "Syntaxe correcte (la validation complète demande la commande docker compose)", "services": [s["name"] for s in c["services"]], "full": False}
    try:
        envf = ["--env-file", os.devnull] if a.get("noEnv") else []   # appelant sans « variables .env » : on ne charge pas le .env (une erreur citerait la valeur remplacée)
        r = subprocess.run([_cli(), "compose", "--project-directory", str(d), *envf, "-f", "-", "config", "-q"], input=content, capture_output=True, text=True, timeout=30, cwd=d, env=_env())
    except subprocess.TimeoutExpired:
        raise StoreError("La validation a pris trop de temps", 504)
    msg = (r.stderr or r.stdout or "").strip()[:1500]
    return {"ok": r.returncode == 0, "message": msg or "Fichier valide", "services": [s["name"] for s in c["services"]], "full": True}


COMPOSE_ACTIONS = {"up": ["up", "-d", "--remove-orphans"], "recreate": ["up", "-d", "--force-recreate", "--remove-orphans"], "down": ["down"], "restart": ["restart"], "stop": ["stop"],
                   "start": ["start"], "pull": ["pull"], "build": ["build"], "config": ["config"], "ps": ["ps", "-a"],
                   "rm": ["rm", "-f", "-s"]}   # « rm » : arrête puis supprime les conteneurs d'UN service (volumes gardés) ; jamais sans service, ni dans une action personnalisée
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
MAX_JOB_LINES = 8000
JOB_TIMEOUT = 30 * 60


def _run_job(job: dict, argv: list[str], d: Path) -> None:
    def add(line: str) -> None:
        if len(job["lines"]) < MAX_JOB_LINES:
            job["lines"].append(line[:2000])
        elif len(job["lines"]) == MAX_JOB_LINES:
            job["lines"].append("… sortie tronquée")
    try:
        p = subprocess.Popen(argv, cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=job.get("env") or _env())
        timer = threading.Timer(JOB_TIMEOUT, p.kill)
        timer.start()
        try:
            for line in p.stdout:
                add(line.rstrip("\n"))
            job["code"] = p.wait()
        finally:
            timer.cancel()
    except OSError as e:
        add(f"Impossible de lancer la commande : {e}")
        job["code"] = 127
    _drop_key(job.get("env"))
    job["ended"], job["done"] = time.time(), True


def _spawn(d: Path, argv: list[str], label: str, action: str, env: dict | None = None, cwd: Path | None = None, key: str = "") -> dict:
    """Lance une commande en tâche de fond (une seule à la fois par dossier) ; sa sortie se lit avec p_job."""
    with _jobs_lock:
        if any(j["dir"] == (key or str(d)) and not j["done"] for j in _jobs.values()):
            _drop_key(env)
            raise StoreError("Une commande est déjà en cours sur ce projet : attendez qu'elle se termine", 409)
        for k in sorted(_jobs, key=lambda k: _jobs[k]["started"])[:-19]:   # les 20 dernières seulement
            if _jobs[k]["done"]:
                del _jobs[k]
        jid = secrets.token_hex(6)
        job = {"id": jid, "dir": key or str(d), "label": label, "action": action, "lines": [], "done": False, "code": None, "started": time.time(), "ended": None, "env": env}
        _jobs[jid] = job
    threading.Thread(target=_run_job, args=(job, argv, cwd or d), name=f"job-{jid}", daemon=True).start()
    return {"job": jid, "label": label}


# ------------------------------------------------------------------ un projet qui contient Armadok lui-même
# « docker compose up/down/restart » lancé PAR le conteneur d'Armadok (hub ou worker) sur SON PROPRE projet s'arrête au milieu : Docker stoppe ce conteneur,
# la commande meurt avec lui, et le nouveau conteneur reste « Created », jamais démarré. La commande est donc confiée à un conteneur auxiliaire éphémère,
# qui survit au redémarrage et termine le travail.
SELF_ACTIONS = ("up", "recreate", "down", "stop", "start", "restart", "rm")


def _self_container() -> dict | None:
    """Le conteneur qui exécute ce processus (son nom d'hôte est son identifiant), ou None hors Docker."""
    h = socket.gethostname()
    if not re.fullmatch(r"[0-9a-f]{12,64}", h):
        return None
    try:
        return dapi.request("GET", f"/containers/{h}/json")
    except Exception:  # noqa: BLE001 - pas de socket Docker, conteneur introuvable : on se comporte comme avant
        return None


def _touches_self(d: Path) -> dict | None:
    me = _self_container()
    wd = (((me or {}).get("Config") or {}).get("Labels") or {}).get("com.docker.compose.project.working_dir")
    return me if me and wd and os.path.normpath(wd) == os.path.normpath(str(d)) else None


def _sweep_helpers() -> None:
    """Retire les conteneurs auxiliaires terminés qui traîneraient encore (anciennes versions, Docker qui n'a pas pu les supprimer)."""
    try:
        old = dapi.request("GET", "/containers/json", {"all": 1, "filters": json.dumps({"label": ["armadok.helper=compose"], "status": ["exited", "created", "dead"]})}) or []
        for c in old:
            dapi.request("DELETE", f"/containers/{c['Id']}", {"force": 1})
    except Exception:  # noqa: BLE001
        pass


def sweep_later(delay: float = 30) -> None:
    """Au démarrage : nettoie les auxiliaires restés derrière un redémarrage (celui qui les lançait est mort avant de pouvoir le faire)."""
    def go():
        time.sleep(delay)
        _sweep_helpers()
    threading.Thread(target=go, name="helper-sweep", daemon=True).start()


def _run_helper_job(job: dict, cid: str) -> None:
    """Suit un conteneur auxiliaire. Il est créé avec AutoRemove : Docker le supprime tout seul dès qu'il s'arrête, même si CE processus a été
    remplacé entre-temps (c'est le but : relancer Armadok lui-même). Le code de sortie vient d'un « wait » posé avant le démarrage."""
    def add(line: str) -> None:
        if len(job["lines"]) < MAX_JOB_LINES:
            job["lines"].append(line[:2000])
    seen = 0
    started = time.time()
    res: dict = {}

    def waiter() -> None:
        try:
            res["r"] = dapi.request("POST", f"/containers/{cid}/wait", {"condition": "removed"}, timeout=JOB_TIMEOUT + 10)
        except dapi.DockerError:
            pass
    w = threading.Thread(target=waiter, daemon=True)
    w.start()
    time.sleep(0.3)
    try:
        dapi.request("POST", f"/containers/{cid}/start")
        while "r" not in res:
            time.sleep(0.3)
            try:
                text = "".join(t for _, t in dapi.demux(dapi.request("GET", f"/containers/{cid}/logs", {"stdout": 1, "stderr": 1}, raw=True) or b""))
            except dapi.DockerError:
                break   # déjà supprimé
            lines = text.splitlines()
            for line in lines[seen:]:
                add(line)
            seen = len(lines)
            if time.time() - started > JOB_TIMEOUT:
                job["code"] = 124
                add("Délai dépassé")
                try:
                    dapi.request("DELETE", f"/containers/{cid}", {"force": 1})
                except dapi.DockerError:
                    pass
                break
    except dapi.DockerError as e:
        add(f"Impossible de lancer la commande : {e}")
        job["code"] = 127
        try:
            dapi.request("DELETE", f"/containers/{cid}", {"force": 1})
        except dapi.DockerError:
            pass
    w.join(3)
    if job["code"] is None:
        job["code"] = int((res.get("r") or {}).get("StatusCode", 0))
    job["ended"], job["done"] = time.time(), True


def _spawn_detached(me: dict, d: Path, argv: list[str], label: str, action: str) -> dict:
    with _jobs_lock:
        if any(j["dir"] == str(d) and not j["done"] for j in _jobs.values()):
            raise StoreError("Une commande est déjà en cours sur ce projet : attendez qu'elle se termine", 409)
        jid = secrets.token_hex(6)
        job = {"id": jid, "dir": str(d), "label": label, "action": action, "lines": [], "done": False, "code": None, "started": time.time(), "ended": None, "env": None}
        _jobs[jid] = job
    _sweep_helpers()
    sock = next((m.get("Source") for m in me.get("Mounts") or [] if m.get("Destination") == settings.docker_socket), None) or settings.docker_socket
    groups = sorted({str(g) for g in os.getgroups()} | {str(g) for g in (me.get("HostConfig") or {}).get("GroupAdd") or []})
    body = {
        "Image": (me.get("Config") or {}).get("Image") or me.get("Image"), "Entrypoint": [argv[0]], "Cmd": argv[1:], "WorkingDir": str(d), "User": f"{os.getuid()}:{os.getgid()}",
        "Env": ["HOME=/tmp", "DOCKER_CONFIG=/tmp/.docker", "DOCKER_HOST=unix:///var/run/docker.sock", "COMPOSE_PROGRESS=plain", "COMPOSE_ANSI=never", "NO_COLOR=1"],
        "Labels": {"armadok.helper": "compose"}, "NetworkDisabled": True,
        "HostConfig": {"AutoRemove": True, "Binds": [f"{sock}:/var/run/docker.sock", f"{d}:{d}:ro"], "GroupAdd": groups, "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"], "NetworkMode": "none",
                       "ReadonlyRootfs": True, "Tmpfs": {"/tmp": "rw,size=64m"}, "Memory": 256 * 1024 * 1024, "PidsLimit": 128}}
    try:
        cid = (dapi.request("POST", "/containers/create", {"name": f"armadok-helper-{jid}"}, body=body) or {}).get("Id")
    except dapi.DockerError as e:
        job["done"], job["code"], job["ended"] = True, 127, time.time()
        job["lines"].append(f"Impossible de lancer la commande : {e}")
        return {"job": jid, "label": label}
    job["lines"].append("Ce projet contient Armadok lui-même : la commande est lancée par un conteneur auxiliaire, pour qu'elle survive au redémarrage.")
    job["lines"].append("L'interface (ou le worker) va redémarrer quelques secondes : la page se reconnecte toute seule.")
    threading.Thread(target=_run_helper_job, args=(job, cid), name=f"helper-{jid}", daemon=True).start()
    return {"job": jid, "label": label}


def _compose_cmd(a: dict) -> tuple[Path, list[str], str]:
    """Valide une action compose et en construit la commande : (dossier, argv, libellé). Rien n'est lancé."""
    d = safe_dir(a.get("dir"))
    action = str(a.get("action"))
    if action not in COMPOSE_ACTIONS:
        raise StoreError("Action inconnue", 422)
    files = pick_files(d, a.get("files"))
    f, c = read_compose(d, files)
    if not f or c["error"]:
        raise StoreError(c["error"] or "Aucun fichier compose", 422)
    names = {s["name"] for s in c["services"]}
    svcs = [str(x) for x in (a.get("services") or [])]
    if any(x not in names or x.startswith("-") for x in svcs) or len(svcs) > 50:
        raise StoreError("Service inconnu dans ce projet", 422)   # un nom en « - » serait lu comme une option (--volumes)
    if action == "rm" and not svcs:
        raise StoreError("Indiquez le service à supprimer (pour tout supprimer, utilisez « Down »)", 422)
    args = list(COMPOSE_ACTIONS[action])
    if action in ("up", "recreate") and a.get("build"):
        args.append("--build")
    if action == "up" and a.get("pull"):
        args += ["--pull", "always"]
    if action == "build" and a.get("noCache"):
        args.append("--no-cache")
    if action == "down" and a.get("volumes"):
        args.append("--volumes")
    fargs = [x for n in files for x in ("-f", str(d / n))]
    argv = [_cli(), "compose", "--project-directory", str(d), *fargs, *args, *(["--", *svcs] if svcs else [])]
    label = " ".join(["compose", *(["-f " + " -f ".join(files)] if len(files) > 1 else []), *args, *svcs])
    return d, argv, label


def p_compose(a: dict) -> dict:
    d, argv, label = _compose_cmd(a)
    action = str(a.get("action"))
    if action in SELF_ACTIONS:
        me = _touches_self(d)
        if me:
            return _spawn_detached(me, d, argv, label, action)
    return _spawn(d, argv, label, action)


# ------------------------------------------------------------------ une action personnalisée : plusieurs étapes d'affilée
MAX_STEPS = 10
STEP_ACTIONS = tuple(k for k in COMPOSE_ACTIONS if k not in ("config", "ps", "rm"))   # « config » affiche les valeurs du .env : jamais dans une suite ; « rm » vise un service précis


def _cmd_argv(line: str) -> list[str]:
    """Une commande libre en liste d'arguments : aucun shell n'est utilisé (pas de « ; », de « && », de redirection ni de $(…)). Pour un script, une cible make."""
    if not isinstance(line, str) or not line.strip() or len(line) > 2000 or any(c in line for c in "\n\r\0"):
        raise StoreError("Commande invalide (une seule ligne, 2000 caractères au plus)", 422)
    try:
        argv = shlex.split(line)
    except ValueError:
        raise StoreError("Commande invalide : guillemets non fermés", 422)
    if not argv:
        raise StoreError("Commande vide", 422)
    return argv


def _step_cmd(d: Path, st: dict, files, allow_exec: bool) -> tuple[list[str], str]:
    kind = st.get("kind") if isinstance(st, dict) else None
    if kind == "compose":
        _, argv, label = _compose_cmd({"dir": str(d), "action": st.get("action"), "files": files})
        return argv, label
    if not allow_exec:
        raise StoreError("Cette étape exécute des commandes : elle demande le droit du terminal", 403)
    if kind == "make":
        return _make_cmd({"dir": str(d), "target": st.get("target"), "vars": st.get("vars") or {}})
    if kind == "cmd":
        argv = _cmd_argv(st.get("line"))
        return argv, argv[0] + (" …" if len(argv) > 1 else "")   # le libellé n'a pas les arguments : ils peuvent contenir un secret
    raise StoreError("Type d'étape inconnu", 422)


def _run_chain(job: dict, steps: list[tuple[list[str], str]], d: Path) -> None:
    def add(line: str) -> None:
        if len(job["lines"]) < MAX_JOB_LINES:
            job["lines"].append(line[:2000])
        elif len(job["lines"]) == MAX_JOB_LINES:
            job["lines"].append("… sortie tronquée")
    job["code"] = 0
    for i, (argv, label) in enumerate(steps, 1):
        add(f"▶ Étape {i}/{len(steps)} : {label}")
        try:
            p = subprocess.Popen(argv, cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=_env())
            timer = threading.Timer(JOB_TIMEOUT, p.kill)
            timer.start()
            try:
                for line in p.stdout:
                    add(line.rstrip("\n"))
                code = p.wait()
            finally:
                timer.cancel()
        except OSError as e:
            add(f"Impossible de lancer la commande : {e}")
            code = 127
        if code != 0:
            add(f"✖ Étape {i} en échec (code {code}) : les étapes suivantes ne sont pas lancées.")
            job["code"] = code
            break
    job["ended"], job["done"] = time.time(), True


def _chain(a: dict, allow_exec: bool) -> dict:
    d = safe_dir(a.get("dir"))
    raw = a.get("steps")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_STEPS:
        raise StoreError(f"Une à {MAX_STEPS} étapes", 422)
    files = a.get("files") or []
    built = [_step_cmd(d, st, files, allow_exec) for st in raw]   # tout est validé AVANT de lancer la première étape
    action = str(a.get("action") or "steps")
    if any(st.get("kind") == "compose" and st.get("action") in SELF_ACTIONS for st in raw) and _touches_self(d):
        raise StoreError("Ce projet contient Armadok lui-même : une suite d'étapes ne peut pas le redémarrer (il s'arrêterait en plein milieu). Utilisez l'action simple.", 409)
    label = f"{action} · {len(built)} étape{'s' if len(built) > 1 else ''}"
    with _jobs_lock:
        if any(j["dir"] == str(d) and not j["done"] for j in _jobs.values()):
            raise StoreError("Une commande est déjà en cours sur ce projet : attendez qu'elle se termine", 409)
        for k in sorted(_jobs, key=lambda k: _jobs[k]["started"])[:-19]:
            if _jobs[k]["done"]:
                del _jobs[k]
        jid = secrets.token_hex(6)
        job = {"id": jid, "dir": str(d), "label": label, "action": action, "lines": [], "done": False, "code": None, "started": time.time(), "ended": None, "env": None}
        _jobs[jid] = job
    threading.Thread(target=_run_chain, args=(job, built, d), name=f"chain-{jid}", daemon=True).start()
    return {"job": jid, "label": label}


def p_chain(a: dict) -> dict:
    """Une suite d'étapes docker compose seulement (pas de commande libre)."""
    return _chain(a, False)


def p_exec_steps(a: dict) -> dict:
    """Une suite d'étapes qui peut contenir une cible make ou une commande : même niveau de droit que le terminal (un worker la refuse sans WORKER_ALLOW_EXEC)."""
    return _chain(a, True)


def p_job(a: dict) -> dict:
    j = _jobs.get(str(a.get("id") or ""))
    if not j:
        raise StoreError("Commande inconnue (trop ancienne ?)", 404)
    since = max(0, int(a.get("since") or 0))
    return {"lines": j["lines"][since:since + 2000], "next": min(len(j["lines"]), since + 2000), "done": j["done"], "code": j["code"], "label": j["label"], "action": j["action"], "started": j["started"], "ended": j["ended"]}


def p_destroy(a: dict) -> dict:
    """Supprime le DOSSIER du projet (irréversible). Garde-fous : jamais une racine, nom retapé, aucun conteneur du projet ne doit tourner (vérifié par l'appelant)."""
    d = safe_dir(a.get("dir"))
    if str(a.get("confirm")) != d.name:
        raise StoreError("Retapez le nom du dossier pour confirmer", 409)
    if d in roots() or any(d == r.parent or d in r.parents for r in roots()):
        raise StoreError("Ce dossier est une racine de projets (ou en contient une) : il ne se supprime pas", 409)
    shutil.rmtree(d)
    return {"ok": True}


# ------------------------------------------------------------------ Makefile du projet
MAKE_NAMES = ("Makefile", "makefile", "GNUmakefile")
_TARGET = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_./-]*)\s*:(?![=:])([^#\n]*?)(?:\s*##\s*(.*))?$")


def make_file(d: Path) -> Path | None:
    for n in MAKE_NAMES:
        f = d / n
        if f.is_file() and not f.is_symlink():
            return f
    return None


_MAKE_REF = re.compile(r"(?<!\$)\$[({]([A-Za-z_][A-Za-z0-9_.]*)(?=[)}:])")          # $(VAR)  ${VAR}  $(VAR:a=b)  (pas $$VAR : variable du shell)
_SHELL_REF = re.compile(r"\$\$\{([A-Za-z_][A-Za-z0-9_]*):([?-])([^}]*)\}")           # $${VAR:?message}  $${VAR:-défaut} : obligatoire / avec défaut, pour le shell de la recette
_MAKE_DEF = re.compile(r"^(?:export\s+|override\s+)*([A-Za-z_][A-Za-z0-9_.]*)\s*(\?=|::=|:=|=|\+=)\s*(.*?)\s*$")
_MAKE_IFNDEF = re.compile(r"^\s*ifn?def\s+([A-Za-z_][A-Za-z0-9_.]*)")
_MAKE_IFEQ_EMPTY = re.compile(r"^\s*ifn?eq\s*[(\s]\s*\$[({]([A-Za-z_][A-Za-z0-9_.]*)[)}]\s*,\s*[)'\"]")
MAKE_SPECIAL = frozenset({"MAKE", "MAKEFLAGS", "MAKEFILES", "MAKEFILE_LIST", "MAKECMDGOALS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS", "MFLAGS", "CURDIR", "SHELL", "SHELLFLAGS", ".SHELLFLAGS",
                          "MAKEVARIABLES", "VPATH", "SUFFIXES", "LD_PRELOAD", "LD_LIBRARY_PATH", "PATH", "IFS"})
MAX_MAKE_VARS, MAX_MAKE_VALUE = 20, 1000


def _make_env_names() -> set[str]:
    return set(os.environ) | {"HOME", "DOCKER_HOST", "DOCKER_CONFIG", "BUILDX_CONFIG", "XDG_CONFIG_HOME", "COMPOSE_PROGRESS", "COMPOSE_ANSI", "NO_COLOR"}


def parse_make(text: str) -> list[dict]:
    """Cibles d'un Makefile : {name, doc, deps, vars}. La description vient du « ## texte » en fin de ligne (convention « make help ») ou du commentaire « # texte » juste au-dessus.
    Les règles à motif (%), les cibles spéciales (.PHONY…), les affectations (:=) et les cibles sans nom simple sont ignorées.
    `vars` : les variables que la cible (et ses dépendances) attend et que rien ne définit : {name, required, default}. « required » quand le Makefile les exige
    (ifndef, $(error …), ${VAR:?}) ; un `VAR ?= défaut` est facultatif avec ce défaut. Les variables d'environnement et celles que le Makefile affecte lui-même sont écartées."""
    out, seen, prev, phony = [], set(), "", set()
    defs: dict[str, tuple[str, str]] = {}
    guards: set[str] = set()
    recipes: dict[str, list[str]] = {}
    cur = ""
    for line in text.splitlines()[:5000]:
        if line.startswith(".PHONY"):
            phony.update(line.split(":", 1)[-1].split())
        m = _TARGET.match(line)
        if m and not line.startswith((".", "\t")):
            name, deps, doc = m.group(1), m.group(2).strip(), (m.group(3) or "").strip()
            deps, _sep, inline = deps.partition(";")
            cur = name
            if inline.strip():
                recipes.setdefault(name, []).append(inline)
            if name not in seen and "%" not in name:
                seen.add(name)
                out.append({"name": name, "doc": doc or prev, "deps": deps.split()[:8]})
        elif line.startswith("\t"):
            if cur:
                recipes.setdefault(cur, []).append(line)
        elif line.strip() and not line.lstrip().startswith("#"):
            cur = ""
            d = _MAKE_DEF.match(line)
            if d:
                defs.setdefault(d.group(1), (d.group(2), d.group(3)))
        prev = line[1:].strip()[:200] if line.startswith("#") and not line.startswith("##") else (line[2:].strip()[:200] if line.startswith("##") else "")
        if line.strip() == "":
            prev = ""
        for rx in (_MAKE_IFNDEF, _MAKE_IFEQ_EMPTY):
            g = rx.match(line)
            if g:
                guards.add(g.group(1))
        if "$(error" in line or "${error" in line:
            guards.update(_MAKE_REF.findall(line))
            guards.update(re.findall(r"\b[A-Z][A-Z0-9_]{1,40}\b", line))
    for t in out:
        t["phony"] = t["name"] in phony
    env = _make_env_names()
    by_name = {t["name"]: t for t in out}

    def wanted(name: str, depth: int = 0, seen_t: frozenset = frozenset()) -> dict[str, dict]:
        res: dict[str, dict] = {}
        body = "\n".join(recipes.get(name, []))
        for v in _MAKE_REF.findall(body):
            res.setdefault(v, {"name": v, "required": v in guards, "default": None})
        for v, kind, rest in _SHELL_REF.findall(body):
            res.setdefault(v, {"name": v, "required": kind == "?", "default": rest if kind == "-" else None})
        if depth < 4:
            for dep in by_name.get(name, {}).get("deps", []):
                if dep in by_name and dep not in seen_t:
                    for k, v in wanted(dep, depth + 1, seen_t | {name}).items():
                        res.setdefault(k, v)
        return res
    for t in out:
        vs = []
        for v in wanted(t["name"]).values():
            n = v["name"]
            if n in MAKE_SPECIAL or n in env:
                continue
            if n in defs:
                op, val = defs[n]
                if op != "?=":
                    continue                                    # une constante du Makefile : ce n'est pas un argument
                v = {**v, "required": False, "default": val[:200]}
            vs.append(v)
        t["vars"] = sorted(vs, key=lambda v: (not v["required"], v["name"]))[:12]
    return out[:150]


def p_make_targets(a: dict) -> dict:
    d = safe_dir(a.get("dir"))
    f = make_file(d)
    if not f:
        return {"file": "", "targets": []}
    try:
        if f.stat().st_size > MAX_COMPOSE:
            return {"file": f.name, "targets": [], "error": "Makefile trop gros"}
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"file": f.name, "targets": [], "error": "Makefile illisible (droits ?)"}
    return {"file": f.name, "targets": parse_make(text), "make": bool(shutil.which("make"))}


def _make_cmd(a: dict) -> tuple[list[str], str]:
    """Valide une cible make et ses arguments, et en construit la commande : (argv, libellé). Rien n'est lancé."""
    d = safe_dir(a.get("dir"))
    f = make_file(d)
    if not f:
        raise StoreError("Aucun Makefile dans ce projet", 404)
    if not shutil.which("make"):
        raise StoreError("La commande make n'est pas installée dans cette image", 501)
    target = str(a.get("target") or "")
    targets = {t["name"]: t for t in parse_make(f.read_text(encoding="utf-8", errors="replace"))}
    if target not in targets:
        raise StoreError("Cible inconnue dans ce Makefile", 422)
    given = a.get("vars") or {}
    if not isinstance(given, dict) or len(given) > MAX_MAKE_VARS:
        raise StoreError("Arguments invalides", 422)
    allowed = {v["name"] for t in targets.values() for v in t["vars"]}
    assigns = []
    for k, v in given.items():
        if not isinstance(k, str) or k not in allowed or k in MAKE_SPECIAL:
            raise StoreError(f"Argument inconnu pour ce Makefile : {str(k)[:40]}", 422)
        if not isinstance(v, str) or len(v) > MAX_MAKE_VALUE or any(c in v for c in "\n\r\0"):
            raise StoreError(f"Valeur invalide pour {k}", 422)
        if v != "":
            assigns.append(f"{k}={v}")           # une donnée, pas du texte de commande : make reçoit des arguments, aucun shell n'est utilisé ici
    missing = [x["name"] for x in targets[target]["vars"] if x["required"] and not given.get(x["name"])]
    if missing:
        raise StoreError("Argument obligatoire manquant : " + ", ".join(missing), 422)
    shown = sorted(k for k, v in given.items() if v != "")
    return ["make", "-f", f.name, "--no-print-directory", *assigns, "--", target], f"make {target}" + (f" ({', '.join(shown)})" if shown else "")


def p_exec_make_run(a: dict) -> dict:
    """Lance UNE cible du Makefile du projet (nommée dans le fichier : jamais de commande libre). Exécute des commandes : même niveau de droit que le terminal."""
    argv, label = _make_cmd(a)
    return _spawn(safe_dir(a.get("dir")), argv, label, "make")


# ------------------------------------------------------------------ cible make en terminal interactif
# Une cible qui pose des questions (confirmation, saisie d'un mot de passe…) ne peut pas tourner en « tâche de fond » : personne ne peut lui répondre.
# Ici elle tourne dans un vrai pseudo-terminal (pty) tenu par la machine ; le hub l'interroge par des opérations nommées (ouvrir, lire, écrire,
# redimensionner, fermer), comme pour le terminal d'un conteneur. Le nom « p_exec_ » les range avec les opérations qu'un worker refuse sans WORKER_ALLOW_EXEC.
# Rien de libre : la commande est TOUJOURS « make <cible> » du Makefile du projet (mêmes contrôles que p_exec_make_run).
MAX_TERMS = 5
TERM_READ = 64 * 1024
TERM_IDLE = 15 * 60
_terms: dict[str, dict] = {}
_terms_lock = threading.Lock()


def _term_kill(t: dict) -> None:
    import signal
    try:
        os.killpg(t["proc"].pid, signal.SIGHUP)
    except (OSError, ProcessLookupError):
        pass

    def later() -> None:
        try:
            t["proc"].wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(t["proc"].pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    threading.Thread(target=later, daemon=True).start()


def _term_drop(sid: str) -> dict | None:
    with _terms_lock:
        t = _terms.pop(sid, None)
    if t:
        try:
            os.close(t["fd"])
        except OSError:
            pass
        if t["proc"].poll() is None:
            _term_kill(t)
    return t


def _term_size(fd: int, cols: int, rows: int) -> None:
    import fcntl
    import struct
    import termios
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", max(1, min(rows, 500)), max(1, min(cols, 500)), 0, 0))
    except OSError:
        pass


def _term_get(a: dict) -> dict:
    t = _terms.get(str(a.get("sid") or ""))
    if not t:
        raise StoreError("Terminal fermé ou inconnu", 404)
    t["at"] = time.time()
    return t


def p_exec_term_open(a: dict) -> dict:
    import fcntl
    import pty
    import termios
    d = safe_dir(a.get("dir"))
    argv, label = _make_cmd(a)
    now = time.time()
    with _terms_lock:
        for k in [k for k, t in _terms.items() if now - t["at"] > TERM_IDLE]:
            t = _terms.pop(k)
            try:
                os.close(t["fd"])
            except OSError:
                pass
            if t["proc"].poll() is None:
                _term_kill(t)
        if len(_terms) >= MAX_TERMS:
            raise StoreError("Trop de terminaux ouverts sur ce nœud", 429)
        if any(t["dir"] == str(d) and t["proc"].poll() is None for t in _terms.values()):
            raise StoreError("Une commande est déjà en cours sur ce projet : attendez qu'elle se termine", 409)
        with _jobs_lock:
            if any(j["dir"] == str(d) and not j["done"] for j in _jobs.values()):
                raise StoreError("Une commande est déjà en cours sur ce projet : attendez qu'elle se termine", 409)
        env = _env()
        for k in ("COMPOSE_PROGRESS", "COMPOSE_ANSI", "NO_COLOR"):   # un vrai terminal : couleurs et barres de progression
            env.pop(k, None)
        env["TERM"] = "xterm-256color"
        master, slave = pty.openpty()
        try:
            _term_size(master, int(a.get("cols") or 80), int(a.get("rows") or 24))
            proc = subprocess.Popen(argv, cwd=d, stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True, start_new_session=True,
                                    preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))   # le terminal devient celui de la commande : Ctrl-C l'interrompt
        except OSError as e:
            os.close(master)
            raise StoreError(f"Impossible de lancer la commande : {e}", 500)
        finally:
            os.close(slave)
        sid = secrets.token_urlsafe(12)
        _terms[sid] = {"proc": proc, "fd": master, "dir": str(d), "label": label, "at": now, "pending": bytearray(), "eof": False, "lock": threading.Lock()}
    return {"sid": sid, "label": label}


def p_exec_term_read(a: dict) -> dict:
    import base64
    import select
    t = _term_get(a)
    wait = max(0.0, min(float(a.get("wait") or 0), 2.0))
    with t["lock"]:
        if not t["pending"] and not t["eof"]:
            try:
                if select.select([t["fd"]], [], [], wait)[0]:
                    chunk = os.read(t["fd"], TERM_READ)
                    if chunk:
                        t["pending"] += chunk
                    else:
                        t["eof"] = True
            except (OSError, ValueError):   # EIO : la commande a fermé son terminal (elle est finie)
                t["eof"] = True
        data, t["pending"] = bytes(t["pending"][:TERM_READ]), t["pending"][TERM_READ:]
    out = {"data": base64.b64encode(data).decode(), "eof": t["eof"] and not t["pending"]}
    if out["eof"]:
        try:
            out["code"] = t["proc"].wait(timeout=3)
        except subprocess.TimeoutExpired:
            out["code"] = None
        _term_drop(str(a.get("sid")))
    return out


def p_exec_term_write(a: dict) -> dict:
    import base64
    import binascii
    t = _term_get(a)
    try:
        data = base64.b64decode(str(a.get("data") or ""), validate=True)
    except (binascii.Error, ValueError):
        raise StoreError("Saisie invalide", 422)
    if len(data) > 64 * 1024:
        raise StoreError("Saisie trop longue", 413)
    try:
        while data:
            data = data[os.write(t["fd"], data):]
    except OSError:
        raise StoreError("Terminal fermé", 410)
    return {"ok": True}


def p_exec_term_resize(a: dict) -> dict:
    t = _term_get(a)
    _term_size(t["fd"], int(a.get("cols") or 80), int(a.get("rows") or 24))
    return {"ok": True}


def p_exec_term_close(a: dict) -> dict:
    _term_drop(str(a.get("sid") or ""))
    return {"ok": True}


# ------------------------------------------------------------------ Git
GIT_URL = re.compile(r"^(https?://[^\s/@]+(:\d+)?/[^\s]+|ssh://[^\s@]+@[^\s/]+(:\d+)?/[^\s]+|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[^\s]+)$")
GIT_REF = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._/-]{0,100}$")
NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")


def _gitbin() -> str:
    g = shutil.which("git")
    if not g:
        raise StoreError("La commande « git » n'est pas installée dans ce conteneur", 501)
    return g


def _strip_url(u: str) -> str:
    return re.sub(r"//[^/@\s]*@", "//", u or "")


def _ssh_command(e: dict, ssh_key: str) -> str:
    """Commande ssh de git : jamais d'invite, serveur mémorisé au premier contact (known_hosts dans le dossier personnel), clé de déploiement seulement si fournie.
    La clé est écrite dans un fichier 0600 (chemin dans l'environnement : voir _drop_key) le temps de la commande."""
    import shlex
    ssh_dir = Path(e.get("HOME") or settings.data_dir) / ".ssh"
    parts = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={ssh_dir / 'known_hosts'}", "-o", "ConnectTimeout=20"]
    if ssh_key:
        if not gitssh.looks_valid(ssh_key):
            raise StoreError("Clé SSH invalide", 422)
        ssh_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(ssh_dir, 0o700)
        path = ssh_dir / ("k-" + secrets.token_hex(8))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # créé en 0600 d'emblée
        with os.fdopen(fd, "w") as f:
            f.write(ssh_key if ssh_key.endswith("\n") else ssh_key + "\n")
        e["OVH_WEBUI_KEYFILE"] = str(path)
        parts += ["-i", str(path), "-o", "IdentitiesOnly=yes"]
    return " ".join(shlex.quote(x) for x in parts)


def _drop_key(env: dict | None) -> None:
    """Efface le fichier de clé temporaire d'une commande git (terminée ou refusée)."""
    p = (env or {}).get("OVH_WEBUI_KEYFILE")
    if p:
        try:
            os.unlink(p)
        except OSError:
            pass


def _git_env(d: Path | None, token: str = "", user: str = "", ssh_key: str = "") -> dict:
    """Environnement d'une commande git : jamais d'invite, jamais de transport « ext », identifiants passés par l'environnement (jamais dans .git/config ni dans la ligne de commande)."""
    import base64
    e = _env()
    e["GIT_SSH_COMMAND"] = _ssh_command(e, ssh_key)
    e.update(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true", GIT_CONFIG_NOSYSTEM="1")
    cfg = [("protocol.ext.allow", "never"), ("core.askPass", "true")]
    if d is not None:
        cfg.append(("safe.directory", str(d)))
    if token:
        cfg.append(("http.extraHeader", "Authorization: Basic " + base64.b64encode(f"{user or 'x-access-token'}:{token}".encode()).decode()))
    e["GIT_CONFIG_COUNT"] = str(len(cfg))
    for i, (k, v) in enumerate(cfg):
        e[f"GIT_CONFIG_KEY_{i}"], e[f"GIT_CONFIG_VALUE_{i}"] = k, v
    return e


def _git(d: Path, args: list[str], token: str = "", user: str = "", timeout: int = 60, ssh_key: str = "") -> tuple[int, str, str]:
    env = _git_env(d, token, user, ssh_key)
    try:
        r = subprocess.run([_gitbin(), *args], cwd=d, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise StoreError("Git n'a pas répondu à temps", 504)
    finally:
        _drop_key(env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _gitmsg(err: str) -> str:
    return re.sub(r"https?://[^\s/@]*@", "https://", err)[:600]


def p_git_status(a: dict) -> dict:
    """État Git d'un projet. Avec fetch : interroge d'abord le dépôt distant (sans rien modifier) pour savoir si on est en retard."""
    d = safe_dir(a.get("dir"))
    if not (d / ".git").exists():
        return {"isRepo": False}
    out: dict = {"isRepo": True, "fetchError": ""}
    if a.get("fetch"):
        code, _, err = _git(d, ["fetch", "--quiet", "--prune"], str(a.get("token") or ""), str(a.get("user") or ""), timeout=90, ssh_key=str(a.get("sshKey") or ""))
        if code:
            out["fetchError"] = _gitmsg(err) or "Impossible de joindre le dépôt"
    _, branch, _ = _git(d, ["rev-parse", "--abbrev-ref", "HEAD"])
    _, remote, _ = _git(d, ["config", "--get", "remote.origin.url"])
    code, head, _ = _git(d, ["log", "-1", "--format=%H%x1f%s%x1f%an%x1f%ct"])
    h = head.split("\x1f") if code == 0 and head else []
    _, dirty, _ = _git(d, ["status", "--porcelain"])
    ahead = behind = 0
    code, lr, _ = _git(d, ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"])
    if code == 0 and lr:
        ahead, behind = (int(x) for x in lr.split()[:2])
    _, up, _ = _git(d, ["rev-parse", "--abbrev-ref", "@{upstream}"])
    out.update(branch=branch, detached=branch == "HEAD", remote=_strip_url(remote), upstream=up if "/" in up else "", dirty=len([l for l in dirty.splitlines() if l.strip()]), ahead=ahead, behind=behind,
               head={"sha": h[0][:12], "subject": h[1], "author": h[2], "time": int(h[3])} if len(h) == 4 else None)
    return out


def p_git_log(a: dict) -> dict:
    d = safe_dir(a.get("dir"))
    if not (d / ".git").exists():
        raise StoreError("Ce dossier n'est pas un dépôt Git", 409)
    code, out, err = _git(d, ["log", f"-{max(1, min(int(a.get('n') or 20), 100))}", "--format=%H%x1f%s%x1f%an%x1f%ct"])
    if code:
        raise StoreError(_gitmsg(err) or "Journal Git illisible", 502)
    commits = []
    for line in out.splitlines():
        h = line.split("\x1f")
        if len(h) == 4:
            commits.append({"sha": h[0][:12], "subject": h[1], "author": h[2], "time": int(h[3])})
    return {"commits": commits}


def p_git_run(a: dict) -> dict:
    """pull (sans fusion : « --ff-only », ne réécrit jamais vos modifications locales), fetch, ou checkout d'un commit / d'une branche (retour arrière)."""
    d = safe_dir(a.get("dir"))
    if not (d / ".git").exists():
        raise StoreError("Ce dossier n'est pas un dépôt Git", 409)
    action = str(a.get("action"))
    if action == "pull":
        args = ["pull", "--ff-only"]
    elif action == "fetch":
        args = ["fetch", "--prune"]
    elif action == "checkout":
        ref = str(a.get("ref") or "")
        if not GIT_REF.match(ref) or ".." in ref:
            raise StoreError("Référence invalide", 422)
        code, dirty, _ = _git(d, ["status", "--porcelain", "--untracked-files=no"])
        if dirty:
            raise StoreError("Des fichiers suivis par Git ont des modifications locales : enregistrez-les ou annulez-les avant de changer de version", 409)
        args = ["checkout", "--detach", ref] if re.fullmatch(r"[0-9a-f]{7,40}", ref) else ["checkout", ref]
    else:
        raise StoreError("Action Git inconnue", 422)
    argv = [_gitbin(), *args]
    return _spawn(d, argv, "git " + " ".join(args), "git-" + action, env=_git_env(d, str(a.get("token") or ""), str(a.get("user") or ""), str(a.get("sshKey") or "")))


def p_git_clone(a: dict) -> dict:
    url = str(a.get("url") or "").strip()
    if not (GIT_URL.match(url) and not url.startswith("-")) and not (settings.git_allow_local and (url.startswith("/") or url.startswith("file://"))):
        raise StoreError("Adresse de dépôt invalide (https://…, ssh://… ou git@hôte:chemin)", 422)
    name = str(a.get("name") or "")
    branch = str(a.get("branch") or "")
    if not NAME_OK.match(name):
        raise StoreError("Nom de dossier invalide (lettres, chiffres, . _ -)", 422)
    if branch and not GIT_REF.match(branch):
        raise StoreError("Nom de branche invalide", 422)
    rs = roots()
    if not rs:
        raise StoreError("Aucun dossier de projets configuré (PROJECT_ROOTS)", 409)
    base = pick_root(a.get("root"), rs)
    target = base / name
    if target.exists():
        raise StoreError(f"« {name} » existe déjà dans {base}", 409)
    args = ["clone", "--progress", *( ["--branch", branch] if branch else []), "--", url, str(target)]
    # la commande s'exécute dans la racine ; la clé du job est le dossier qui sera créé (un seul clonage à la fois par nom)
    return _spawn(base, [_gitbin(), *args], "git clone " + _strip_url(url), "git-clone", env=_git_env(None, str(a.get("token") or ""), str(a.get("user") or ""), str(a.get("sshKey") or "")), cwd=base, key=str(target))


def pick_root(raw, rs: list[Path]) -> Path:
    """La racine choisie pour un nouveau projet : celle demandée si elle fait partie des racines autorisées, sinon la première (une seule racine = pas de choix)."""
    if not raw:
        return rs[0]
    try:
        base = Path(str(raw)).resolve()
    except (OSError, RuntimeError, ValueError):
        raise StoreError("Chemin invalide", 422)
    if base not in rs:
        raise StoreError("Ce dossier n'est pas une racine autorisée", 403)
    return base


def p_create(a: dict) -> dict:
    """Crée un NOUVEAU dossier de projet (dans la racine choisie, par défaut la première) avec ses fichiers de départ. Refuse un dossier qui existe déjà."""
    name = str(a.get("name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,60}", name):
        raise StoreError("Nom de dossier invalide (lettres, chiffres, « - », « _ » et « . »)", 422)
    rs = roots()
    if not rs:
        raise StoreError("Aucun dossier de projets configuré sur ce nœud (PROJECT_ROOTS)", 409)
    files = a.get("files")
    if not isinstance(files, dict) or not files or len(files) > 30:
        raise StoreError("Fichiers de départ invalides", 422)
    base = pick_root(a.get("root"), rs)
    d = base / name
    if d.exists() or d.is_symlink():
        raise StoreError(f"Un dossier « {name} » existe déjà dans {base}", 409)
    d.mkdir(mode=0o755)
    try:
        for rel, content in files.items():
            rel = safe_rel(rel)
            if not isinstance(content, str) or len(content.encode()) > MAX_FILE:
                raise StoreError("Contenu invalide ou trop gros", 413)
            f = d / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if is_env(rel) else 0o644)
            with os.fdopen(fd, "wb") as fh:
                fh.write(content.encode())
    except Exception:
        shutil.rmtree(d, ignore_errors=True)   # rien de demi-créé
        raise
    return {"path": str(d), "name": name}


def p_rename(a: dict) -> dict:
    """Renomme le DOSSIER d'un projet, au même endroit (même dossier parent). Refusé pour une racine, vers un nom pris, ou pendant une commande en cours dans ce dossier.
    L'appelant vérifie avant qu'aucun conteneur du projet n'existe (le nom Compose peut en dépendre)."""
    d = safe_dir(a.get("dir"))
    name = str(a.get("name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,60}", name):
        raise StoreError("Nom de dossier invalide (lettres, chiffres, « - », « _ » et « . »)", 422)
    if d in roots() or any(d == r.parent or d in r.parents for r in roots()):
        raise StoreError("Ce dossier est une racine de projets (ou en contient une) : il ne se renomme pas", 409)
    if name == d.name:
        return {"path": str(d), "name": d.name}
    target = d.parent / name
    if target.exists() or target.is_symlink():
        raise StoreError(f"Un dossier « {name} » existe déjà à cet endroit", 409)
    with _jobs_lock:
        if any(j["dir"] == str(d) and not j["done"] for j in _jobs.values()):
            raise StoreError("Une commande tourne dans ce projet : attendez qu'elle se termine", 409)
        os.rename(d, target)
    return {"path": str(target), "name": name}


OPS = {"p_make_targets": p_make_targets, "p_exec_make_run": p_exec_make_run, "p_chain": p_chain, "p_exec_steps": p_exec_steps, "p_create": p_create, "p_info": p_info, "p_discover": p_discover, "p_project": p_project, "p_tree": p_tree, "p_read": p_read, "p_write": p_write, "p_delete_file": p_delete_file,
       "p_history": p_history, "p_history_content": p_history_content, "p_validate": p_validate, "p_compose": p_compose, "p_job": p_job, "p_destroy": p_destroy, "p_rename": p_rename,
       "p_mkdir": p_mkdir, "p_move": p_move, "p_delete_dir": p_delete_dir, "p_pack": p_pack, "p_pack_read": p_pack_read, "p_upload": p_upload,
       "p_exec_term_open": p_exec_term_open, "p_exec_term_read": p_exec_term_read, "p_exec_term_write": p_exec_term_write, "p_exec_term_resize": p_exec_term_resize, "p_exec_term_close": p_exec_term_close, "p_git_status": p_git_status, "p_git_log": p_git_log, "p_git_run": p_git_run, "p_git_clone": p_git_clone}


def handle(op: str, a: dict):
    fn = OPS.get(op)
    if not fn or not isinstance(a, dict):
        raise StoreError("Opération non autorisée", 400)
    return fn(a)
