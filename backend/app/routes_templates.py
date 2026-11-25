"""Modèles de projet : dossiers d'organisation, édition des fichiers d'un modèle, duplication, export et import en zip.

Les modèles eux-mêmes (liste, enregistrement depuis un projet, création d'un projet) sont dans routes_projects ; ici, ce qui permet de les GÉRER comme des fichiers.
Les modèles sont communs à tous les nœuds : leurs dossiers d'organisation sont donc rattachés au nœud local (celui du hub), quel que soit le nœud affiché."""
import base64
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import crypto, db, org_folders, project_ops, project_templates, template_zip
from .nodes import NodeCtx, local_node_id
from .routes_projects import FolderBody, _tpl_get, _tpl_owned, _tpl_visible, proj_cap

router = APIRouter(prefix="/api/projects/templates", tags=["templates"])
KIND = "templates"
MAX_IMPORT = 200


def scope() -> str:
    nid = local_node_id()
    if not nid:
        raise HTTPException(409, "Aucun nœud local : les dossiers de modèles ne peuvent pas être créés")
    return nid


def folder_path(folder_id: int | None) -> list[str]:
    """Noms des dossiers, de la racine jusqu'à `folder_id` (pour ranger un export comme la liste)."""
    names: list[str] = []
    fid, n = folder_id, 0
    while fid is not None and n < 20:
        r = db.one("SELECT parent_id, name FROM project_folders WHERE id=? AND kind=?", (fid, KIND))
        if not r:
            break
        names.insert(0, r["name"])
        fid, n = r["parent_id"], n + 1
    return names


# ------------------------------------------------------------------ dossiers
@router.post("/folders")
def create_folder(body: FolderBody, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    fid, name = org_folders.create(scope(), KIND, ctx.p.username, body.name, body.description, body.color, body.parentId)
    ctx.audit("project.template.folder.create", name)
    return {"id": fid}


@router.put("/folders/{fid}")
def edit_folder(fid: int, body: FolderBody, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    ctx.audit("project.template.folder.edit", org_folders.edit(scope(), KIND, fid, body.name, body.description, body.color, body.parentId))
    return {"ok": True}


@router.delete("/folders/{fid}")
def delete_folder(fid: int, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    """Supprime le dossier : ses modèles et sous-dossiers remontent d'un niveau, aucun modèle n'est supprimé."""
    name, parent = org_folders.delete(scope(), KIND, fid)
    db.run("UPDATE project_templates SET folder_id=? WHERE folder_id=?", (parent, fid))
    db.run("DELETE FROM project_folders WHERE id=?", (fid,))
    ctx.audit("project.template.folder.delete", name)
    return {"ok": True}


class RegroupBody(BaseModel):
    templates: list[str] = Field(default_factory=list, max_length=200)
    folders: list[int] = Field(default_factory=list, max_length=200)
    folderId: int | None = None   # destination (null = la racine)


@router.post("/regroup")
def regroup(body: RegroupBody, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    """Range des modèles et/ou des dossiers dans un dossier (ou à la racine). Seul l'auteur d'un modèle (ou un administrateur) le range ; les modèles intégrés restent à la racine."""
    sc = scope()
    dest = org_folders.get(sc, KIND, body.folderId)["name"] if body.folderId is not None else "la racine"
    for tid in body.templates:
        _tpl_owned(tid, ctx)
    moved = org_folders.move_folders(sc, KIND, body.folders, body.folderId)
    for tid in body.templates:
        db.run("UPDATE project_templates SET folder_id=? WHERE id=?", (body.folderId, int(tid)))
        moved += 1
    if moved:
        ctx.audit("project.template.folder.move", f"{moved} élément(s) → {dest}")
    return {"moved": moved}


# ------------------------------------------------------------------ fichiers d'un modèle
def _need_env(ctx: NodeCtx, rel: str) -> None:
    if project_ops.is_env(rel):
        ctx.need("projects.env")   # le .env d'un modèle privé peut garder des secrets


def _save(tid: str, t: dict, files: dict) -> dict:
    if t["shared"] and ".env" in files:   # un modèle partagé ne garde jamais de secret
        files[".env"] = project_templates.blank_secrets(files[".env"])
    try:
        clean = project_templates.validate_files(files)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.run("UPDATE project_templates SET data=?, updated_at=? WHERE id=?", (crypto.enc(json.dumps(clean)), db.now(), int(tid)))
    return clean


@router.get("/{tid}/file")
def read_file(tid: str, rel: str, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    t = _tpl_get(tid, ctx)
    _need_env(ctx, rel)
    if rel not in t["files"]:
        raise HTTPException(404, "Fichier introuvable dans ce modèle")
    mine = (not t["builtin"]) and (t["createdBy"] == ctx.p.username or ctx.p.is_admin)
    return {"content": t["files"][rel], "readOnly": not mine}


class FileBody(BaseModel):
    rel: str = Field(min_length=1, max_length=300)
    content: str = Field(default="", max_length=project_templates.MAX_FILE)
    create: bool = False


@router.put("/{tid}/file")
def write_file(tid: str, body: FileBody, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    """Modifie ou crée un fichier d'un modèle (le vôtre). Le modèle reste valide : un compose au moins, des chemins sûrs, des tailles raisonnables."""
    ctx.need("projects.edit")
    t = _tpl_owned(tid, ctx)
    _need_env(ctx, body.rel)
    files = dict(t["files"])
    if body.create and body.rel in files:
        raise HTTPException(409, "Ce fichier existe déjà dans le modèle")
    if not body.create and body.rel not in files:
        raise HTTPException(404, "Fichier introuvable dans ce modèle")
    files[body.rel] = body.content
    clean = _save(tid, t, files)
    ctx.audit("project.template.file.create" if body.create else "project.template.file.edit", f"{t['name']} : {body.rel}")   # jamais le contenu
    return {"path": body.rel, "files": sorted(clean)}


@router.delete("/{tid}/file")
def delete_file(tid: str, rel: str, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    ctx.need("projects.edit")
    t = _tpl_owned(tid, ctx)
    _need_env(ctx, rel)
    files = {k: v for k, v in t["files"].items() if k != rel and not k.startswith(rel + "/")}
    if len(files) == len(t["files"]):
        raise HTTPException(404, "Fichier introuvable dans ce modèle")
    clean = _save(tid, t, files)
    ctx.audit("project.template.file.delete", f"{t['name']} : {rel}")
    return {"files": sorted(clean)}


class MoveBody(BaseModel):
    src: str = Field(min_length=1, max_length=300)
    dst: str = Field(min_length=1, max_length=300)


@router.post("/{tid}/move")
def move_file(tid: str, body: MoveBody, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    """Renomme / déplace un fichier du modèle (ou tout un dossier de fichiers)."""
    ctx.need("projects.edit")
    t = _tpl_owned(tid, ctx)
    _need_env(ctx, body.src)
    _need_env(ctx, body.dst)
    files = t["files"]
    moving = {k: v for k, v in files.items() if k == body.src or k.startswith(body.src + "/")}
    if not moving:
        raise HTTPException(404, "Introuvable dans ce modèle")
    if body.dst == body.src or body.dst.startswith(body.src + "/"):
        raise HTTPException(422, "Destination invalide")
    renamed = {body.dst + k[len(body.src):]: v for k, v in moving.items()}
    rest = {k: v for k, v in files.items() if k not in moving}
    if any(k in rest or any(r.startswith(k + "/") or k.startswith(r + "/") for r in rest) for k in renamed):
        raise HTTPException(409, "Cette destination existe déjà")
    clean = _save(tid, t, {**rest, **renamed})
    ctx.audit("project.template.file.move", f"{t['name']} : {body.src} → {body.dst}")
    return {"files": sorted(clean)}


# ------------------------------------------------------------------ créer un modèle neuf
class NewBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    folderId: int | None = None


@router.post("/new")
def new_template(body: NewBody, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    """Un modèle de départ (un compose et un .env minimaux), PRIVÉ, à compléter dans l'éditeur."""
    ctx.need("projects.edit")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Le nom ne peut pas être vide")
    if db.one("SELECT 1 FROM project_templates WHERE lower(name)=lower(?)", (name,)) or any(b["name"].lower() == name.lower() for b in project_templates.BUILTIN):
        raise HTTPException(409, "Un modèle porte déjà ce nom")
    if body.folderId is not None:
        org_folders.get(scope(), KIND, body.folderId)
    files = {"docker-compose.yml": "services:\n  app:\n    image: ${APP_IMAGE}\n    restart: unless-stopped\n", ".env": "# Image à lancer\nAPP_IMAGE=nginx:alpine\n"}
    now = db.now()
    new = db.run("INSERT INTO project_templates(name,description,data,created_by,created_at,updated_at,shared,folder_id) VALUES(?,?,?,?,?,?,0,?)",
                 (name, body.description.strip(), crypto.enc(json.dumps(files)), ctx.p.username, now, now, body.folderId))
    ctx.audit("project.template.create", name)
    return {"id": str(new), "name": name}


# ------------------------------------------------------------------ dupliquer
def _unique_name(base: str, suffix: str = " (copie)") -> str:
    name, n = (base + suffix)[:80], 2
    builtin = {b["name"].lower() for b in project_templates.BUILTIN}
    while name.lower() in builtin or db.one("SELECT 1 FROM project_templates WHERE lower(name)=lower(?)", (name,)):
        name = f"{base[:70]}{suffix[:-1]} {n})" if suffix.endswith(")") else f"{base[:70]} {n}"
        n += 1
    return name


class DuplicateBody(BaseModel):
    name: str | None = Field(default=None, max_length=80)


@router.post("/{tid}/duplicate")
def duplicate(tid: str, body: DuplicateBody, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    """Une copie PRIVÉE à votre nom (même d'un modèle intégré ou partagé) : le point de départ pour modifier un modèle qui n'est pas le vôtre."""
    ctx.need("projects.edit")
    t = _tpl_get(tid, ctx)
    name = (body.name or "").strip()
    if name:
        if db.one("SELECT 1 FROM project_templates WHERE lower(name)=lower(?)", (name,)) or any(b["name"].lower() == name.lower() for b in project_templates.BUILTIN):
            raise HTTPException(409, "Un modèle porte déjà ce nom")
    else:
        name = _unique_name(t["name"])
    folder = None if t["builtin"] else db.one("SELECT folder_id FROM project_templates WHERE id=?", (int(tid),))["folder_id"]
    now = db.now()
    new = db.run("INSERT INTO project_templates(name,description,data,created_by,created_at,updated_at,shared,folder_id) VALUES(?,?,?,?,?,?,0,?)",
                 (name, t["description"], crypto.enc(json.dumps(t["files"])), ctx.p.username, now, now, folder))
    ctx.audit("project.template.duplicate", f"{t['name']} → {name}")
    return {"id": str(new), "name": name}


# ------------------------------------------------------------------ export / import (zip)
class ExportBody(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=500)
    folders: list[int] = Field(default_factory=list, max_length=200)   # un dossier = tous ses modèles, sous-dossiers compris, rangés comme dans la liste
    includeSecrets: bool = False                                        # garde les valeurs du .env des modèles PRIVÉS (droit « .env » requis) ; sinon les secrets sont vidés


@router.post("/export")
def export(body: ExportBody, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    """Archive zip des modèles choisis. Rien de secret par défaut : les valeurs des secrets du .env sont vidées, comme pour un modèle partagé."""
    if body.includeSecrets:
        ctx.need("projects.env")
    picked: dict[str, dict] = {}
    for tid in dict.fromkeys(body.ids):
        picked[tid] = _tpl_get(tid, ctx)
    if body.folders:
        sc = scope()
        ids = set(body.folders)
        for f in body.folders:
            org_folders.get(sc, KIND, f)
            ids |= org_folders.descendants(sc, KIND, f)
        for r in db.rows(f"SELECT * FROM project_templates WHERE folder_id IN ({','.join('?' * len(ids))})", tuple(ids)):
            if _tpl_visible(r, ctx) and str(r["id"]) not in picked:
                picked[str(r["id"])] = _tpl_get(str(r["id"]), ctx)
    if not picked:
        raise HTTPException(404, "Rien à exporter")
    out = []
    for tid, t in picked.items():
        files = dict(t["files"])
        keep = body.includeSecrets and not t["shared"] and not t["builtin"]
        if ".env" in files and not keep:
            files[".env"] = project_templates.blank_secrets(files[".env"])
        row = None if t["builtin"] else db.one("SELECT folder_id FROM project_templates WHERE id=?", (int(tid),))
        out.append({"name": t["name"], "description": t["description"], "files": files, "folders": folder_path(row["folder_id"]) if row and body.folders else []})
    data = template_zip.build(out)
    name = (template_zip.safe_name(out[0]["name"]) if len(out) == 1 else "modeles-armadok") + ".zip"
    ctx.audit("project.template.export", f"{len(out)} modèle(s){' avec secrets' if body.includeSecrets else ''}")
    return {"name": name, "b64": base64.b64encode(data).decode(), "count": len(out)}


class ImportBody(BaseModel):
    b64: str = Field(max_length=4_000_000)    # l'API refuse les corps de plus de 4 Mo
    filename: str = Field(default="", max_length=200)
    apply: bool = False                       # faux : seulement lire et dire ce qu'on trouverait (aperçu)
    onConflict: str = Field(default="rename", pattern="^(skip|replace|rename)$")
    shared: bool = False                      # importés en privé par défaut (secrets : ceux de l'archive) ; partagés = secrets vidés
    folderId: int | None = None               # dossier où ranger ce qui n'a pas de dossier propre dans l'archive


def _ensure_folders(sc: str, user: str, parent: int | None, names: list[str]) -> int | None:
    for n in names:
        r = db.one("SELECT id FROM project_folders WHERE node_id=? AND kind=? AND parent_id IS ? AND LOWER(name)=LOWER(?)", (sc, KIND, parent, n))
        parent = r["id"] if r else org_folders.create(sc, KIND, user, n, "", "", parent)[0]
    return parent


@router.post("/import")
def import_zip(body: ImportBody, ctx: NodeCtx = Depends(proj_cap("projects.import"))):
    """Lit une archive zip de modèles (jamais exécutée) et, avec `apply`, les ajoute. Sans `apply` : aperçu, rien n'est écrit. Les erreurs d'un modèle n'empêchent pas les autres."""
    ctx.need("projects.edit")
    try:
        raw = base64.b64decode(body.b64, validate=True)
        found = template_zip.parse(raw, body.filename)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if len(found) > MAX_IMPORT:
        raise HTTPException(422, f"Trop de modèles dans l'archive ({MAX_IMPORT} au plus)")
    builtin = {b["name"].lower() for b in project_templates.BUILTIN}
    sc = scope() if body.apply or body.folderId is not None else None
    if body.folderId is not None:
        org_folders.get(sc, KIND, body.folderId)
    preview, made = [], {"created": 0, "replaced": 0, "skipped": 0, "renamed": 0, "errors": 0}
    for i, t in enumerate(found):
        err, clean = "", {}
        try:
            clean = project_templates.validate_files(t["files"])
            if ".env" in clean and not ctx.can("projects.env"):
                err = "Ce modèle contient un .env : il faut le droit sur le .env pour l'importer"
        except ValueError as e:
            err = str(e)
        exist = db.one("SELECT * FROM project_templates WHERE lower(name)=lower(?)", (t["name"],))
        conflict = {"builtin": True} if t["name"].lower() in builtin else ({"id": str(exist["id"]), "mine": exist["created_by"] == ctx.p.username or ctx.p.is_admin} if exist else None)
        item = {"index": i, "folders": t["folders"], "name": t["name"], "description": t["description"], "files": sorted(t["files"]), "vars": len(project_templates.variables(clean)) if clean else 0,
                "conflict": conflict, "error": err}
        preview.append(item)
        if not body.apply:
            continue
        if err:
            made["errors"] += 1
            continue
        name, replace = t["name"], None
        if conflict:
            if body.onConflict == "skip":
                made["skipped"] += 1
                continue
            if body.onConflict == "replace" and exist and conflict["mine"]:
                replace = exist
            else:
                name = _unique_name(t["name"], " (importé)")
                made["renamed"] += 1
        files = dict(clean)
        if body.shared and ".env" in files:
            files[".env"] = project_templates.blank_secrets(files[".env"])
        folder = _ensure_folders(sc, ctx.p.username, body.folderId, t["folders"]) if (t["folders"] or body.folderId is not None) else None
        now = db.now()
        if replace:
            db.run("UPDATE project_templates SET description=?, data=?, updated_at=? WHERE id=?", (t["description"], crypto.enc(json.dumps(files)), now, replace["id"]))
            made["replaced"] += 1
        else:
            db.run("INSERT INTO project_templates(name,description,data,created_by,created_at,updated_at,shared,folder_id) VALUES(?,?,?,?,?,?,?,?)",
                   (name, t["description"], crypto.enc(json.dumps(files)), ctx.p.username, now, now, 1 if body.shared else 0, folder))
            made["created"] += 1
    if body.apply:
        ctx.audit("project.template.import", f"{body.filename or 'archive'} : " + ", ".join(f"{v} {k}" for k, v in made.items() if v))
        return {"applied": True, **made, "templates": preview}
    return {"applied": False, "templates": preview}
