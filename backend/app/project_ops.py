"""Opérations NOMMÉES sur les projets Compose d'une machine : découverte des dossiers, lecture du compose.

Comme docker_ops : exécutées par le hub pour son propre nœud, ou par un worker (liste fermée). Tout ce qui touche au disque reste DANS les racines
autorisées (PROJECT_ROOTS) : un dossier hors des racines, un lien symbolique qui en sort, un nom de fichier piégé sont refusés."""
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

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


def read_compose(d: Path) -> tuple[Path | None, dict]:
    f = compose_file(d)
    if not f:
        return None, {"name": None, "services": [], "error": "Aucun fichier compose dans ce dossier"}
    try:
        if f.stat().st_size > MAX_COMPOSE:
            return f, {"name": None, "services": [], "error": "Fichier compose trop gros"}
        return f, parse_compose(f.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return f, {"name": None, "services": [], "error": "Fichier compose illisible (droits ?)"}


def entry(d: Path) -> dict:
    f, c = read_compose(d)
    return {"path": str(d), "name": d.name, "composeProject": project_name(d, {"name": c["name"]}), "file": f.name if f else "", "services": c["services"], "error": c["error"],
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
    return entry(safe_dir(a.get("dir")))


def p_info(_a: dict) -> dict:
    cli = shutil.which("docker")
    ok = False
    if cli:
        try:
            ok = subprocess.run([cli, "compose", "version"], capture_output=True, timeout=10, env=_env()).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
    return {"roots": [str(x) for x in roots()], "configuredRoots": list(settings.project_roots), "compose": ok}


# ------------------------------------------------------------------ fichiers du projet
MAX_FILE = 1_000_000
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
    if f.stat().st_size > MAX_FILE:
        raise StoreError("Fichier trop gros pour l'éditeur (1 Mo maximum)", 413)
    raw = f.read_bytes()
    if b"\x00" in raw[:8192]:
        raise StoreError("Fichier binaire : il ne s'affiche pas dans l'éditeur", 415)
    return {"content": raw.decode("utf-8", "replace"), "hash": _hash(raw), "mtime": f.stat().st_mtime, "env": is_env(a.get("rel"))}


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
        raise StoreError("Contenu invalide ou trop gros (1 Mo maximum)", 413)
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


MAX_DIR_DELETE_FILES = 300
MAX_DIR_DELETE_BYTES = 5_000_000


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
    """Supprime un dossier du projet et son contenu. Les fichiers texte sont gardés dans l'historique ; au-delà de quelques centaines de fichiers
    ou de 5 Mo, on refuse (à faire à la main : ce n'est plus un geste d'édition)."""
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
        if len(files) > MAX_DIR_DELETE_FILES or total > MAX_DIR_DELETE_BYTES:
            raise StoreError(f"Ce dossier est trop gros pour être supprimé d'ici (plus de {MAX_DIR_DELETE_FILES} fichiers ou 5 Mo) : supprimez-le sur la machine", 413)
    if any(is_env(p.name) for p, _ in files) and not a.get("canEnv"):
        raise StoreError("Ce dossier contient un fichier .env : il faut le droit de gérer les secrets", 403)
    for p, size in files:
        if p.is_symlink() or size > MAX_FILE:
            continue
        raw = p.read_bytes()
        if b"\x00" not in raw[:8192]:
            _save_version(d, str(p.relative_to(d)), raw)
    try:
        shutil.rmtree(f)
    except PermissionError:
        raise StoreError(f"Droits insuffisants pour supprimer ce dossier : l'interface tourne avec l'utilisateur {os.getuid()}:{os.getgid()}", 403)
    return {"ok": True, "files": len(files)}


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
        r = subprocess.run([_cli(), "compose", "--project-directory", str(d), "-f", "-", "config", "-q"], input=content, capture_output=True, text=True, timeout=30, cwd=d, env=_env())
    except subprocess.TimeoutExpired:
        raise StoreError("La validation a pris trop de temps", 504)
    msg = (r.stderr or r.stdout or "").strip()[:1500]
    return {"ok": r.returncode == 0, "message": msg or "Fichier valide", "services": [s["name"] for s in c["services"]], "full": True}


COMPOSE_ACTIONS = {"up": ["up", "-d", "--remove-orphans"], "recreate": ["up", "-d", "--force-recreate", "--remove-orphans"], "down": ["down"], "restart": ["restart"], "stop": ["stop"],
                   "start": ["start"], "pull": ["pull"], "build": ["build"], "config": ["config"], "ps": ["ps", "-a"]}
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


def p_compose(a: dict) -> dict:
    d = safe_dir(a.get("dir"))
    action = str(a.get("action"))
    if action not in COMPOSE_ACTIONS:
        raise StoreError("Action inconnue", 422)
    f, c = read_compose(d)
    if not f or c["error"]:
        raise StoreError(c["error"] or "Aucun fichier compose", 422)
    names = {s["name"] for s in c["services"]}
    svcs = [str(x) for x in (a.get("services") or [])]
    if any(x not in names for x in svcs) or len(svcs) > 50:
        raise StoreError("Service inconnu dans ce projet", 422)
    args = list(COMPOSE_ACTIONS[action])
    if action in ("up", "recreate") and a.get("build"):
        args.append("--build")
    if action == "up" and a.get("pull"):
        args += ["--pull", "always"]
    if action == "build" and a.get("noCache"):
        args.append("--no-cache")
    if action == "down" and a.get("volumes"):
        args.append("--volumes")
    argv = [_cli(), "compose", "--project-directory", str(d), "-f", str(f), *args, *svcs]
    return _spawn(d, argv, " ".join(["compose", *args, *svcs]), action)


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
    base = Path(str(a.get("root") or "")).resolve() if a.get("root") else rs[0]
    if base not in rs:
        raise StoreError("Ce dossier n'est pas une racine autorisée", 403)
    target = base / name
    if target.exists():
        raise StoreError(f"« {name} » existe déjà dans {base}", 409)
    args = ["clone", "--progress", *( ["--branch", branch] if branch else []), "--", url, str(target)]
    # la commande s'exécute dans la racine ; la clé du job est le dossier qui sera créé (un seul clonage à la fois par nom)
    return _spawn(base, [_gitbin(), *args], "git clone " + _strip_url(url), "git-clone", env=_git_env(None, str(a.get("token") or ""), str(a.get("user") or ""), str(a.get("sshKey") or "")), cwd=base, key=str(target))


OPS = {"p_info": p_info, "p_discover": p_discover, "p_project": p_project, "p_tree": p_tree, "p_read": p_read, "p_write": p_write, "p_delete_file": p_delete_file,
       "p_history": p_history, "p_history_content": p_history_content, "p_validate": p_validate, "p_compose": p_compose, "p_job": p_job, "p_destroy": p_destroy,
       "p_mkdir": p_mkdir, "p_move": p_move, "p_delete_dir": p_delete_dir, "p_git_status": p_git_status, "p_git_log": p_git_log, "p_git_run": p_git_run, "p_git_clone": p_git_clone}


def handle(op: str, a: dict):
    fn = OPS.get(op)
    if not fn or not isinstance(a, dict):
        raise StoreError("Opération non autorisée", 400)
    return fn(a)
