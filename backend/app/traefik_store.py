"""Lecture / écriture du dossier dynamique Traefik en conservant commentaires et mise en forme (ruamel round-trip)."""
import io
import os
import posixpath
import re
import shutil
import threading
import time
from collections import Counter
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from .config import settings
from .yamltext import is_marked, pause_tag, read_refs, uncomment

try:
    from ruamel.yaml.scalarbool import ScalarBoolean
except ImportError:  # pragma: no cover
    ScalarBoolean = ()

# (proto, kind) -> chemin dans le document
KINDS: dict[tuple[str, str], tuple[str, str]] = {
    ("http", "routers"): ("http", "routers"),
    ("http", "services"): ("http", "services"),
    ("http", "middlewares"): ("http", "middlewares"),
    ("http", "serversTransports"): ("http", "serversTransports"),
    ("tcp", "routers"): ("tcp", "routers"),
    ("tcp", "services"): ("tcp", "services"),
    ("tcp", "middlewares"): ("tcp", "middlewares"),
    ("tcp", "serversTransports"): ("tcp", "serversTransports"),
    ("udp", "routers"): ("udp", "routers"),
    ("udp", "services"): ("udp", "services"),
    ("tls", "options"): ("tls", "options"),
    ("tls", "stores"): ("tls", "stores"),
}
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./-]+$")
EXT = (".yml", ".yaml")
OFF = tuple(e + ".off" for e in EXT)   # fichier désactivé : Traefik ne lit que .yml / .yaml
MAX_BACKUPS = 40
MAX_YAML_BYTES = 1_000_000   # un fichier de configuration Traefik n'approche jamais cette taille
DIR_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
MAX_DEPTH = 4     # niveaux de sous-dossiers
MAX_DIRS = 200

_lock = threading.RLock()


class StoreError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def new_yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    return y


def to_plain(x):
    if isinstance(x, dict):
        return {str(k): to_plain(v) for k, v in x.items()}
    if isinstance(x, list):
        return [to_plain(i) for i in x]
    if ScalarBoolean and isinstance(x, ScalarBoolean):
        return bool(x)
    if isinstance(x, bool):
        return bool(x)
    if isinstance(x, int):
        return int(x)
    if isinstance(x, float):
        return float(x)
    if isinstance(x, str):
        return str(x)
    return x


def build(value):
    """JSON -> structure ruamel (nouvelles valeurs : chaînes « spéciales » entre guillemets, comme dans vos fichiers)."""
    if isinstance(value, dict):
        m = CommentedMap()
        for k, v in value.items():
            m[k] = build(v)
        return m
    if isinstance(value, list):
        s = CommentedSeq()
        for v in value:
            s.append(build(v))
        return s
    if isinstance(value, str) and not SAFE_VALUE.match(value):
        return DoubleQuotedScalarString(value)
    return value


def _merge(cur, new):
    if isinstance(cur, CommentedMap) and isinstance(new, dict):
        for k in [k for k in cur.keys() if k not in new]:
            del cur[k]
        for k, v in new.items():
            cur[k] = _merge(cur[k], v) if k in cur else build(v)
        return cur
    if isinstance(cur, CommentedSeq) and isinstance(new, list):
        for i in range(min(len(cur), len(new))):
            cur[i] = _merge(cur[i], new[i])
        while len(cur) > len(new):
            cur.pop()
        for v in new[len(cur):]:
            cur.append(build(v))
        return cur
    if to_plain(cur) == new and type(to_plain(cur)) is type(new):
        return cur
    return build(new)


class Store:
    def __init__(self, root: Path | None = None, backup_root: Path | None = None) -> None:
        self.root = root or settings.dynamic_dir
        self.backup_root = backup_root or settings.data_dir / "backups"

    # ---------- chemins ----------
    def safe(self, rel: str, must_exist: bool = False) -> Path:
        if not rel or rel.startswith("/") or "\x00" in rel:
            raise StoreError("Chemin invalide")
        p = (self.root / rel)
        parts = Path(rel).parts
        if any(part in ("..", "") or part.startswith(".") for part in parts):
            raise StoreError("Chemin invalide")
        if not rel.lower().endswith(EXT + OFF):
            raise StoreError("Seuls les fichiers .yml / .yaml sont gérés")
        resolved = p.resolve()
        if not str(resolved).startswith(str(self.root.resolve()) + os.sep):
            raise StoreError("Chemin hors du dossier Traefik")
        if must_exist and not resolved.is_file():
            raise StoreError("Fichier introuvable", 404)
        return resolved

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root.resolve()).as_posix()

    def safe_dir(self, rel: str, must_exist: bool = False) -> Path:
        parts = (rel or "").split("/")
        if not rel or "\x00" in rel:
            raise StoreError("Nom de dossier invalide")
        if len(parts) > MAX_DEPTH:
            raise StoreError(f"Pas plus de {MAX_DEPTH} niveaux de dossiers")
        if not all(DIR_SEGMENT.match(x) and not x.endswith(".") for x in parts):
            raise StoreError("Nom de dossier invalide (lettres, chiffres, « . », « - » et « _ » seulement)")
        resolved = (self.root / rel).resolve()
        if not str(resolved).startswith(str(self.root.resolve()) + os.sep):
            raise StoreError("Chemin hors du dossier Traefik")
        if must_exist and not resolved.is_dir():
            raise StoreError("Dossier introuvable", 404)
        return resolved

    # ---------- fichiers ----------
    def list_files(self) -> list[dict]:
        out = []
        root = self.root.resolve()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for fn in sorted(filenames):
                if fn.startswith(".") or not fn.lower().endswith(EXT + OFF):
                    continue
                full = Path(dirpath) / fn
                st = full.stat()
                out.append({"path": full.relative_to(root).as_posix(), "size": st.st_size, "mtime": st.st_mtime,
                            "disabled": fn.lower().endswith(OFF)})
        return out

    def list_dirs(self) -> list[str]:
        root = self.root.resolve()
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                      if p.is_dir() and not any(x.startswith(".") for x in p.relative_to(root).parts))

    def make_dir(self, rel: str) -> None:
        with _lock:
            p = self.safe_dir(rel)
            if p.exists():
                raise StoreError("Ce dossier existe déjà", 409)
            if len(self.list_dirs()) >= MAX_DIRS:
                raise StoreError(f"Trop de dossiers ({MAX_DIRS} maximum)", 409)
            p.mkdir(parents=True)

    def remove_dir(self, rel: str) -> None:
        """Ne supprime qu'un dossier sans aucun fichier (les sous-dossiers vides partent avec)."""
        with _lock:
            p = self.safe_dir(rel, True)
            if any(f for _, _, fs in os.walk(p) for f in fs):
                raise StoreError("Ce dossier contient des fichiers : déplacez-les ou supprimez-les d'abord", 409)
            for dirpath, _, _ in os.walk(p, topdown=False):
                os.rmdir(dirpath)

    def move_dir(self, src: str, dst: str) -> None:
        """Renomme / déplace un dossier avec tout son contenu (l'historique des fichiers suit)."""
        with _lock:
            a, b = self.safe_dir(src, True), self.safe_dir(dst)
            if b.exists():
                raise StoreError("La destination existe déjà", 409)
            if str(b).startswith(str(a) + os.sep):
                raise StoreError("Impossible de déplacer un dossier dans lui-même")
            deepest = max((len(Path(f["path"]).parts) for f in self.list_files() if f["path"].startswith(src + "/")), default=0)
            if deepest and deepest - len(Path(src).parts) + len(Path(dst).parts) > MAX_DEPTH + 1:
                raise StoreError(f"Pas plus de {MAX_DEPTH} niveaux de dossiers")
            b.parent.mkdir(parents=True, exist_ok=True)
            os.replace(a, b)
            self._move_backups(src, dst)
            self._poke(src + "/", dst + "/")

    def _move_backups(self, src: str, dst: str) -> None:
        a, b = self.backup_root / src, self.backup_root / dst
        if a.is_dir() and not b.exists():
            try:
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(a), str(b))
            except OSError:
                pass

    def read(self, rel: str) -> str:
        return self.safe(rel, True).read_text(encoding="utf-8")

    def _poke(self, *changed: str) -> None:
        """Traefik ne détecte pas les modifications dans les sous-dossiers. Si des fichiers y vivent (ou viennent d'y être
        touchés), on réécrit un fichier `reload` à la racine pour forcer le rechargement. Dossier à plat : rien à faire."""
        if not (any("/" in c for c in changed) or any("/" in f["path"] for f in self.list_files())):
            return
        try:
            (self.root / "reload").write_text(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
        except OSError:
            pass

    def _backup(self, rel: str, content: str, tag: str = "") -> None:
        d = self.backup_root / rel
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
        (d / f"{stamp}{tag}.bak").write_text(content, encoding="utf-8")
        for old in sorted(d.glob("*.bak"))[:-MAX_BACKUPS]:
            old.unlink(missing_ok=True)

    def write(self, rel: str, content: str, create: bool = False) -> None:
        if len(content.encode("utf-8", "ignore")) > MAX_YAML_BYTES:
            raise StoreError(f"Fichier trop gros ({MAX_YAML_BYTES // 1000} Ko au plus)", 413)
        with _lock:
            p = self.safe(rel)
            exists = p.is_file()
            if exists and create:
                raise StoreError("Ce fichier existe déjà", 409)
            if not exists and not create:
                raise StoreError("Fichier introuvable", 404)
            self.parse_text(content)
            if exists and p.read_text(encoding="utf-8") == content:
                return
            if exists:
                self._backup(rel, p.read_text(encoding="utf-8"))
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(f".{p.name}.tmp")
            tmp.write_text(content, encoding="utf-8")
            if exists:
                shutil.copymode(p, tmp)
            os.replace(tmp, p)
            self._poke(rel)

    def delete(self, rel: str) -> None:
        with _lock:
            p = self.safe(rel, True)
            self._backup(rel, p.read_text(encoding="utf-8"), "-deleted")
            p.unlink()
            self._poke(rel)

    def move(self, src: str, dst: str) -> None:
        with _lock:
            a, b = self.safe(src, True), self.safe(dst)
            if b.exists():
                raise StoreError("La destination existe déjà", 409)
            b.parent.mkdir(parents=True, exist_ok=True)
            os.replace(a, b)
            self._move_backups(src, dst)
            self._poke(src, dst)

    def certs(self) -> dict:
        from .certs import load
        from .config import settings as cfg
        return load(cfg.certs_file)

    def toggle(self, ids: list[str], files: list[str], enable: bool, tag: str | None = None, reconcile: bool = True, dry: bool = False) -> dict:
        from .toggle import toggle
        return toggle(self, ids, files, enable, tag, reconcile, dry)

    def move_blocks(self, src: str, ids: list[str], dst: str) -> None:
        """Déplace des blocs (routeur, service, middleware…) d'un fichier à un autre en gardant commentaires et mise en forme."""
        with _lock:
            docs, errors = self.load_docs()
            self.safe(src, True), self.safe(dst)
            if src == dst:
                return
            for f in (src, dst):
                if f in errors:
                    raise StoreError(f"Le fichier {f} contient une erreur YAML : {errors[f]}")
            sdoc, ddoc = docs[src], docs.get(dst) or CommentedMap()
            for bid in ids:
                proto, kind, name = (bid.split("/", 2) + ["", ""])[:3]
                if (proto, kind) not in KINDS:
                    raise StoreError("Type d'élément inconnu")
                top, sub = KINDS[(proto, kind)]
                block = (sdoc.get(top) or {}).get(sub) if isinstance(sdoc.get(top), dict) else None
                if not isinstance(block, dict) or name not in block:
                    raise StoreError(f"« {name} » est introuvable dans {src}", 404)
                if self._find({dst: ddoc}, proto, kind, name)[0]:
                    raise StoreError(f"« {name} » existe déjà dans {dst}", 409)
                value = block[name]
                del block[name]
                self._ensure_block(ddoc, proto, kind)[name] = value
                self._prune(sdoc, proto, kind)
            self.write(dst, self.dump(ddoc), create=not self.safe(dst).is_file())
            if len(sdoc):
                self.write(src, self.dump(sdoc))
            else:
                self.delete(src)  # plus rien dedans : le fichier part (il reste dans l'historique)

    def _snapshot(self) -> None:
        stamp = time.strftime("%Y%m%dT%H%M%S")
        dest = self.backup_root / "_reorg" / stamp
        for f in self.list_files():
            (dest / f["path"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.root / f["path"], dest / f["path"])
        old = sorted(p for p in (self.backup_root / "_reorg").iterdir() if p.is_dir())[:-5]
        for p in old:
            shutil.rmtree(p, ignore_errors=True)

    def reorganize(self, steps: list[dict], clean: bool = True) -> dict:
        """Applique un plan de rangement : blocs extraits, fichiers déplacés, fichiers vides supprimés. Copie de sécurité avant.
        clean : les fichiers écrits sont réécrits sans commentaires (sinon les titres collés à un bloc le suivent n'importe où)."""
        with _lock:
            files = [s for s in steps if s["kind"] == "file"]
            blocks = [s for s in steps if s["kind"] == "blocks"]
            deletes = [s for s in steps if s["kind"] == "delete"]
            rmdirs = [s for s in steps if s["kind"] == "rmdir"]
            seen: set[str] = set()
            for s in files:
                self.safe(s["from"], True)
                dst = self.safe(s["to"])
                if dst.exists() or s["to"] in seen:
                    raise StoreError(f"« {s['to']} » existe déjà", 409)
                seen.add(s["to"])
            for s in blocks:
                self.safe(s["from"], True), self.safe(s["to"])
            for s in deletes:
                self.safe(s["from"], True)
            for s in rmdirs:
                self.safe_dir(s["from"], True)
            self._snapshot()
            # Les blocs en pause (« #~ ») des fichiers concernés sont réactivés le temps du travail puis remis en pause (avec leur étiquette) :
            # ils suivent ainsi leur groupe et la réécriture sans commentaires ne les efface pas.
            texts, off = self.read_all()
            items, _ = parse_items(texts, off)
            paused = {i["id"]: i.get("pausedBy") for i in items if i["marked"] and i["file"] not in off}
            involved = {s["from"] for s in steps} | {s["to"] for s in blocks}
            wake = sorted(i for i in paused if next(x["file"] for x in items if x["id"] == i) in involved)
            if wake:
                try:
                    self.toggle(wake, [], True, None, True)
                except StoreError as e:
                    raise StoreError(f"Des blocs désactivés ({', '.join(w.split('/')[-1] for w in wake[:4])}…) empêchent le rangement : réactivez-les d'abord. {e}", 409)
            try:
                touched: set[str] = set()
                for s in blocks:
                    self.move_blocks(s["from"], s["ids"], s["to"])
                    touched |= {s["from"], s["to"]}
                for s in files:
                    self.move(s["from"], s["to"])
                    touched.add(s["to"])
                for s in deletes:
                    p = self.safe(s["from"])
                    if p.is_file() and not len(self.parse_text(p.read_text(encoding="utf-8"))):
                        self.delete(s["from"])
                    touched.discard(s["from"])
                if clean:
                    for f in sorted(touched):
                        if self.safe(f).is_file():
                            self._strip_comments(f)
                # dossiers devenus vides à cause du rangement, puis dossiers vides demandés
                gone = 0
                for d in sorted({posixpath.dirname(x) for s in steps if s["kind"] != "rmdir" for x in (s["from"],) if posixpath.dirname(x)}, key=lambda d: -d.count("/")):
                    gone += self._rm_empty_up(d)
                for s in rmdirs:
                    if self.safe_dir(s["from"]).is_dir():
                        self.remove_dir(s["from"])
                        gone += 1
            finally:
                if wake:
                    texts, off = self.read_all()
                    now, _ = parse_items(texts, off)
                    active = {i["id"] for i in now if not i["marked"]}
                    by_tag: dict[str | None, list[str]] = {}
                    for i in wake:
                        if i in active:
                            by_tag.setdefault(paused[i], []).append(i)
                    for tag, ids in by_tag.items():
                        self.toggle(sorted(ids), [], False, tag, True)
            return {"blocks": sum(len(s["ids"]) for s in blocks), "files": len(files), "deleted": len(deletes), "dirs": gone}

    def _rm_empty_up(self, rel: str) -> int:
        """Supprime ce dossier s'il est vide (aucun fichier dessous) puis ses parents devenus vides. Retourne le nombre supprimé."""
        n = 0
        while rel:
            p = self.safe_dir(rel)
            if not p.is_dir() or any(f for _, _, fs in os.walk(p) for f in fs):
                break
            self.remove_dir(rel)
            n += 1
            rel = posixpath.dirname(rel)
        return n

    def _strip_comments(self, rel: str) -> None:
        """Réécrit un fichier sans aucun commentaire (le contenu et l'ordre des blocs restent identiques)."""
        text = self.safe(rel).read_text(encoding="utf-8")
        doc = self.parse_text(text)
        out = self.dump(self._spaced(build(to_plain(doc)))) if len(doc) else "{}\n"
        if out != text:
            self.write(rel, out)

    @staticmethod
    def _spaced(doc):
        """Une ligne vide entre les sections (routers, services…) et entre les blocs : le fichier reste lisible sans commentaires."""
        for sec in doc.values():
            if not isinstance(sec, CommentedMap):
                continue
            for n, (key, blk) in enumerate(list(sec.items())):
                if n:
                    sec.yaml_set_comment_before_after_key(key, before="\n", indent=2)
                if isinstance(blk, CommentedMap):
                    for m, bkey in enumerate(list(blk.keys())):
                        if m:
                            blk.yaml_set_comment_before_after_key(bkey, before="\n", indent=4)
        return doc

    def history(self, rel: str) -> list[dict]:
        self.safe(rel)
        d = self.backup_root / rel
        if not d.is_dir():
            return []
        return [{"id": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime, "deleted": "-deleted" in f.name}
                for f in sorted(d.glob("*.bak"), reverse=True)]

    def history_content(self, rel: str, bid: str) -> str:
        self.safe(rel)
        if "/" in bid or ".." in bid:
            raise StoreError("Identifiant invalide")
        f = self.backup_root / rel / bid
        if not f.is_file():
            raise StoreError("Sauvegarde introuvable", 404)
        return f.read_text(encoding="utf-8")

    # ---------- YAML ----------
    @staticmethod
    def parse_text(content: str):
        try:
            doc = new_yaml().load(content)
        except YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            line = (mark.line + 1) if mark else None
            msg = getattr(e, "problem", None) or str(e)
            err = StoreError(f"YAML invalide{f' (ligne {line})' if line else ''} : {msg}")
            err.line = line  # type: ignore[attr-defined]
            err.column = (mark.column + 1) if mark else 1  # type: ignore[attr-defined]
            raise err
        if doc is None:
            return CommentedMap()
        if not isinstance(doc, CommentedMap):
            raise StoreError("Le fichier doit contenir un objet YAML (http:, tcp:, udp:, tls:)")
        return doc

    @staticmethod
    def dump(doc) -> str:
        buf = io.StringIO()
        new_yaml().dump(doc, buf)
        return buf.getvalue()

    def load_docs(self) -> tuple[dict[str, CommentedMap], dict[str, str]]:
        """Les documents ACTIFS (les blocs mis en commentaire et les fichiers .off n'y figurent pas) : c'est ce qu'on modifie."""
        docs, errors = {}, {}
        for f in self.list_files():
            if f["disabled"]:
                continue
            try:
                docs[f["path"]] = self.parse_text(self.safe(f["path"]).read_text(encoding="utf-8"))
            except StoreError as e:
                errors[f["path"]] = str(e)
            except OSError as e:
                errors[f["path"]] = str(e)
        return docs, errors

    def read_all(self) -> tuple[dict[str, str], set[str]]:
        texts, off = {}, set()
        for f in self.list_files():
            try:
                texts[f["path"]] = self.safe(f["path"]).read_text(encoding="utf-8")
            except OSError:
                continue
            if f["disabled"]:
                off.add(f["path"])
        return texts, off

    # ---------- éléments ----------
    def items(self) -> tuple[list[dict], dict[str, str]]:
        texts, off = self.read_all()
        items, errors = parse_items(texts, off)
        for it in items:  # détails internes du moteur d'activation : inutiles pour les appelants
            for k in ("line", "flow", "marked", "refs"):
                it.pop(k, None)
        return items, errors

    def _find(self, docs, proto, kind, name):
        top, sub = KINDS[(proto, kind)]
        for path, doc in docs.items():
            block = (doc.get(top) or {}).get(sub) if isinstance(doc.get(top), dict) else None
            if isinstance(block, dict) and name in block:
                return path, block
        return None, None

    def _ensure_block(self, doc, proto, kind):
        top, sub = KINDS[(proto, kind)]
        if not isinstance(doc.get(top), dict):
            doc[top] = CommentedMap()
        if not isinstance(doc[top].get(sub), dict):
            doc[top][sub] = CommentedMap()
        return doc[top][sub]

    def _prune(self, doc, proto, kind):
        top, sub = KINDS[(proto, kind)]
        if isinstance(doc.get(top), dict):
            if isinstance(doc[top].get(sub), dict) and not doc[top][sub]:
                del doc[top][sub]
            if not doc[top]:
                del doc[top]

    def upsert(self, proto, kind, name, spec, file=None, old_name=None) -> str:
        if (proto, kind) not in KINDS:
            raise StoreError("Type d'élément inconnu")
        if not re.match(r"^[^\s@/:]+$", name or ""):
            raise StoreError("Nom invalide (pas d'espace, '@', '/' ni ':')")
        with _lock:
            docs, errors = self.load_docs()
            key = old_name or name
            cur_file, block = self._find(docs, proto, kind, key)
            if name != key and self._find(docs, proto, kind, name)[0]:
                raise StoreError(f"« {name} » existe déjà", 409)
            if cur_file is None and self._find(docs, proto, kind, name)[0]:
                raise StoreError(f"« {name} » existe déjà", 409)
            if (cur_file is None or name != key) and any(i["disabled"] and i["id"] == f"{proto}/{kind}/{name}" for i in self.items()[0]):
                raise StoreError(f"« {name} » existe déjà mais est désactivé : réactivez-le (ou supprimez-le) d'abord", 409)
            target = file or cur_file
            if not target:
                raise StoreError("Choisissez un fichier de destination")
            if target in errors:
                raise StoreError(f"Le fichier {target} contient une erreur YAML : {errors[target]}")
            self.safe(target)
            changed: dict[str, CommentedMap] = {}
            if cur_file == target and block is not None:
                if name != key:
                    keys = list(block.keys())
                    pos = keys.index(key)
                    value = _merge(block[key], spec)
                    del block[key]
                    block.insert(pos, name, value)
                else:
                    block[name] = _merge(block[name], spec)
                changed[target] = docs[target]
            else:
                if cur_file is not None:
                    del block[key]
                    self._prune(docs[cur_file], proto, kind)
                    changed[cur_file] = docs[cur_file]
                doc = docs.get(target)
                if doc is None:
                    doc = CommentedMap()
                    docs[target] = doc
                self._ensure_block(doc, proto, kind)[name] = build(spec)
                changed[target] = doc
            if name != key:
                from .rename import rename_in_doc
                for path, doc in docs.items():                      # les références actives suivent le nouveau nom
                    if rename_in_doc(doc, proto, kind, key, name):
                        changed[path] = doc
            for path, doc in changed.items():
                text = self.dump(doc) if len(doc) else "{}\n"
                self.write(path, text, create=not self.safe(path).is_file())
            if name != key:
                self._rename_elsewhere(proto, kind, key, name)
            return target

    def _rename_elsewhere(self, proto: str, kind: str, old: str, new: str) -> None:
        """Références hors des blocs actifs : fichiers désactivés (.off), blocs en pause (« #~ ») et notes « #% ref »."""
        from .rename import rename_in_doc, rename_text
        for f in self.list_files():
            p = self.safe(f["path"])
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            out = text
            if f["disabled"]:
                try:
                    doc = self.parse_text(text)
                except StoreError:
                    continue
                if rename_in_doc(doc, proto, kind, old, new):
                    out = self.dump(doc)
            out = rename_text(out, proto, kind, old, new)
            if out != text:
                self.write(f["path"], out)

    def delete_item(self, proto, kind, name) -> str:
        if (proto, kind) not in KINDS:
            raise StoreError("Type d'élément inconnu")
        with _lock:
            docs, _ = self.load_docs()
            path, block = self._find(docs, proto, kind, name)
            if not path:
                from .toggle import purge  # un élément désactivé se supprime aussi
                return purge(self, f"{proto}/{kind}/{name}")
            del block[name]
            self._prune(docs[path], proto, kind)
            self.write(path, self.dump(docs[path]) if len(docs[path]) else "{}\n")
            return path


def parse_items(texts: dict[str, str], off: set[str]) -> tuple[list[dict], dict[str, str]]:
    """Tous les éléments, y compris les désactivés (bloc en commentaire « #~ » ou fichier .off). `line`/`flow` servent au moteur d'activation."""
    items, errors = [], {}
    for path, text in texts.items():
        plain = uncomment(text)
        try:
            doc = Store.parse_text(plain)
        except StoreError as e:
            errors[path] = str(e)
            continue
        lines, plain_lines = text.split("\n"), plain.split("\n")
        for (proto, kind), (top, sub) in KINDS.items():
            section = doc.get(top)
            block = section.get(sub) if isinstance(section, dict) else None
            if not isinstance(block, dict):
                continue
            flow = bool(getattr(block.fa, "flow_style", lambda: False)())
            for name, spec in block.items():
                line = block.lc.key(name)[0]
                spec_plain = to_plain(spec) if spec is not None else {}
                item = {"id": f"{proto}/{kind}/{name}", "proto": proto, "kind": kind, "name": str(name), "file": path,
                        "spec": spec_plain, "type": item_type(kind, spec_plain), "line": line, "flow": flow,
                        "marked": is_marked(lines[line]), "refs": read_refs(plain_lines, line) if kind in ("routers", "middlewares") else {}, "disabled": path in off or is_marked(lines[line])}
                if item["disabled"] and path not in off:
                    item["pausedBy"] = pause_tag(plain_lines, line)
                items.append(item)
    return items, errors


def suggest_file(items: list[dict], dirs: list[str], proto: str, kind: str, name: str) -> str:
    """Où ranger un nouvel élément, d'après l'organisation déjà en place sur le nœud (rien n'est imposé : à plat, tout va à la racine)."""
    base = re.sub(r"[^A-Za-z0-9._-]", "-", name or "").strip(".-") or "element"
    top = {d for d in dirs if "/" not in d}
    if kind == "middlewares":
        if "middlewares" in top:
            return f"middlewares/{base}.yml"
        same = next((i["file"] for i in items if i["kind"] == "middlewares"), None)
        return same or "middlewares.yml"
    if proto == "tls" or kind == "serversTransports":
        same = next((i["file"] for i in items if i["proto"] == proto and i["kind"] == kind), None)
        return same or "tls.yml"
    dominant = Counter(i["file"].rsplit("/", 1)[0] for i in items if i["kind"] == "routers" and "/" in i["file"]).most_common(1)
    folder = "apps" if "apps" in top else (dominant[0][0] if dominant else "")
    return f"{folder}/{base}.yml" if folder else f"{base}.yml"


def item_type(kind: str, spec: dict) -> str | None:
    if kind in ("middlewares", "services") and isinstance(spec, dict) and spec:
        return next(iter(spec.keys()))
    return None


store = Store()
