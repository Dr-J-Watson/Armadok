"""Modèles de projet <-> archives zip. Fonctions pures (pas de base de données) : fabriquer une archive, lire (sans jamais rien exécuter) celle d'un tiers.

Format : un modèle = un dossier contenant ses fichiers et un `armadok-template.json` {"format": 1, "name", "description"}. Plusieurs modèles = un sous-dossier chacun,
rangés comme dans les dossiers d'organisation (« Production/Web/Mon modèle/… »). Une archive sans manifeste est lue quand même : son dossier unique (ou sa racine) devient
UN modèle, nommé d'après lui. Rien n'est lu comme du binaire : les fichiers doivent être du texte UTF-8."""
import io
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath

MANIFEST = "armadok-template.json"
FORMAT = 1
MAX_ENTRIES = 2000
MAX_UNPACKED = 12 * 1024 * 1024       # taille totale décompressée : protège d'une « bombe » zip
MAX_FILE_READ = 1024 * 1024           # un fichier plus gros n'est de toute façon pas un modèle (voir project_templates.MAX_FILE)
_IGNORED = {".DS_Store", "Thumbs.db", "desktop.ini"}


def safe_name(name: str, fallback: str = "modele") -> str:
    """Nom utilisable comme nom de dossier dans l'archive : sans « / », sans point de tête, sans caractère de contrôle."""
    s = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", "-", str(name or "")).strip(" .-")
    return s[:80] or fallback


def build(templates: list[dict], single: bool | None = None) -> bytes:
    """`templates` : [{name, description, files, folders (liste de noms de dossiers, du haut vers le bas)}]. Un seul modèle sans dossier : ses fichiers à la racine."""
    single = (len(templates) == 1 and not templates[0].get("folders")) if single is None else single
    out = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for t in templates:
            if single:
                base = ""
            else:
                parts = [safe_name(f, "dossier") for f in t.get("folders") or []] + [safe_name(t["name"])]
                base = "/".join(parts)
                n = 2
                while base.lower() in used:     # deux modèles de même nom dans le même dossier ne peuvent pas arriver, mais une désinfection peut les rapprocher
                    base = "/".join(parts[:-1] + [f"{parts[-1]} ({n})"])
                    n += 1
                used.add(base.lower())
                base += "/"
            z.writestr(base + MANIFEST, json.dumps({"format": FORMAT, "name": t["name"], "description": t.get("description") or ""}, ensure_ascii=False, indent=2) + "\n")
            for rel, content in sorted(t["files"].items()):
                z.writestr(base + rel, content)
    return out.getvalue()


def _clean_parts(raw: str) -> tuple[str, ...] | None:
    """Chemin d'une entrée, en morceaux sûrs ; None pour une entrée à ignorer (dossier, déchet de système) ; ValueError pour un chemin dangereux."""
    if "\x00" in raw or "\\" in raw:
        raise ValueError(f"Chemin refusé dans l'archive : {raw[:80]!r}")
    p = PurePosixPath(raw)
    if p.is_absolute() or any(x == ".." for x in p.parts):
        raise ValueError(f"Chemin refusé dans l'archive : {raw[:80]!r}")
    parts = tuple(x for x in p.parts if x not in ("", "."))
    if not parts or parts[0] == "__MACOSX" or parts[-1] in _IGNORED:
        return None
    return parts


def parse(data: bytes, filename: str = "") -> list[dict]:
    """Lit une archive : [{folders, name, description, files}]. Lève ValueError (message lisible) si l'archive est illisible ou dangereuse. Les modèles eux-mêmes ne sont
    pas validés ici (voir project_templates.validate_files) : un modèle invalide n'empêche pas de lire les autres."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Ce fichier n'est pas une archive zip valide")
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ValueError(f"Archive trop grosse ({MAX_ENTRIES} entrées au plus)")
    if sum(i.file_size for i in infos) > MAX_UNPACKED:
        raise ValueError(f"Archive trop grosse une fois décompressée ({MAX_UNPACKED // 1048576} Mo au plus)")
    entries: dict[tuple[str, ...], str] = {}
    for i in infos:
        if i.is_dir():
            continue
        if stat.S_ISLNK(i.external_attr >> 16):
            raise ValueError(f"Lien symbolique refusé dans l'archive : {i.filename[:80]}")
        if i.flag_bits & 0x1:
            raise ValueError("Archive chiffrée : retirez le mot de passe d'abord")
        parts = _clean_parts(i.filename)
        if parts is None:
            continue
        if i.file_size > MAX_FILE_READ:
            raise ValueError(f"« {'/'.join(parts)[:80]} » est trop gros pour un modèle")
        try:
            entries[parts] = z.read(i).decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"« {'/'.join(parts)[:80]} » n'est pas un fichier texte : un modèle ne contient que du texte")
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
            raise ValueError("Archive abîmée ou d'un format non pris en charge")
    if not entries:
        raise ValueError("Cette archive ne contient aucun fichier")
    stem = re.sub(r"\.zip$", "", (filename or "").replace("\\", "/").split("/")[-1], flags=re.I).strip()
    roots = sorted({p[:-1] for p in entries if p[-1] == MANIFEST}, key=len)
    if not roots:                                  # pas de manifeste : le dossier unique, sinon la racine, est UN modèle
        tops = {p[0] for p in entries if len(p) > 1}
        solo = len(tops) == 1 and all(len(p) > 1 for p in entries)
        roots = [(next(iter(tops)),)] if solo else [()]
    out = []
    for root in roots:
        n = len(root)
        deeper = [r for r in roots if len(r) > n and r[:n] == root]
        files = {"/".join(p[n:]): c for p, c in entries.items() if p[:n] == root and len(p) > n and p[-1] != MANIFEST
                 and not any(p[:len(r)] == r for r in deeper)}
        meta = {}
        if (*root, MANIFEST) in entries:
            try:
                meta = json.loads(entries[(*root, MANIFEST)])
            except ValueError:
                raise ValueError(f"« {MANIFEST} » illisible" + (f" dans {'/'.join(root)}" if root else ""))
            if not isinstance(meta, dict):
                meta = {}
            if int(meta.get("format") or 1) > FORMAT:
                raise ValueError("Archive faite par une version plus récente d'Armadok")
        name = str(meta.get("name") or (root[-1] if root else stem) or "Modèle importé").strip()[:80]
        folders = list(root[:-1]) if root else []
        out.append({"folders": [f[:60] for f in folders], "name": name, "description": str(meta.get("description") or "")[:300], "files": files})
    return out
