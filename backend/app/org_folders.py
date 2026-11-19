"""Dossiers d'organisation (projets, sites) : purement logiques, ils rangent une LISTE de l'interface comme des dossiers et sous-dossiers,
sans rien déplacer sur le disque ni toucher à Docker / Traefik. Une table commune (project_folders), une colonne « kind » pour les distinguer."""
from fastapi import HTTPException

from . import db

MAX_DEPTH = 8
COLORS = {"", "red", "orange", "yellow", "green", "teal", "blue", "purple", "pink", "gray"}


def listing(node_id: str, kind: str) -> list[dict]:
    return [{"id": r["id"], "parentId": r["parent_id"], "name": r["name"], "description": r["description"], "color": r["color"], "createdBy": r["created_by"], "createdAt": r["created_at"]}
            for r in db.rows("SELECT * FROM project_folders WHERE node_id=? AND kind=? ORDER BY name COLLATE NOCASE", (node_id, kind))]


def get(node_id: str, kind: str, fid: int):
    r = db.one("SELECT * FROM project_folders WHERE id=? AND node_id=? AND kind=?", (fid, node_id, kind))
    if not r:
        raise HTTPException(404, "Dossier introuvable")
    return r


def _kids(node_id: str, kind: str) -> dict:
    kids: dict[int | None, list[int]] = {}
    for r in db.rows("SELECT id, parent_id FROM project_folders WHERE node_id=? AND kind=?", (node_id, kind)):
        kids.setdefault(r["parent_id"], []).append(r["id"])
    return kids


def descendants(node_id: str, kind: str, fid: int) -> set[int]:
    kids, out, todo = _kids(node_id, kind), set(), [fid]
    while todo:
        for k in kids.get(todo.pop(), []):
            if k not in out:
                out.add(k)
                todo.append(k)
    return out


def depth(node_id: str, kind: str, fid: int | None) -> int:
    """Nombre de dossiers de la racine jusqu'à fid compris (0 pour la racine)."""
    parents = {r["id"]: r["parent_id"] for r in db.rows("SELECT id, parent_id FROM project_folders WHERE node_id=? AND kind=?", (node_id, kind))}
    n = 0
    while fid is not None and n < 100:
        n += 1
        fid = parents.get(fid)
    return n


def height(node_id: str, kind: str, fid: int) -> int:
    kids = _kids(node_id, kind)
    def h(i, d=0):
        return 1 + max((h(k, d + 1) for k in kids.get(i, []) if d < 100), default=0)
    return h(fid)


def check_place(node_id: str, kind: str, name: str, parent: int | None, color: str, me: int | None = None) -> str:
    """Nom nettoyé si le dossier peut porter ce nom à cet endroit (me = dossier déjà existant qu'on renomme ou déplace) ; sinon HTTPException."""
    name = " ".join((name or "").split())
    if not name:
        raise HTTPException(422, "Donnez un nom au dossier")
    if color not in COLORS:
        raise HTTPException(422, "Couleur inconnue")
    if parent is not None:
        get(node_id, kind, parent)
        if me is not None and (parent == me or parent in descendants(node_id, kind, me)):
            raise HTTPException(422, "Un dossier ne peut pas être rangé dans lui-même ou dans l'un de ses sous-dossiers")
        if depth(node_id, kind, parent) + (height(node_id, kind, me) if me is not None else 1) > MAX_DEPTH:
            raise HTTPException(422, f"Trop de niveaux de dossiers ({MAX_DEPTH} au plus)")
    if db.one("SELECT id FROM project_folders WHERE node_id=? AND kind=? AND parent_id IS ? AND LOWER(name)=LOWER(?) AND id != ?", (node_id, kind, parent, name, -1 if me is None else me)):
        raise HTTPException(409, f"Un dossier « {name} » existe déjà ici")
    return name


def create(node_id: str, kind: str, user: str, name: str, description: str, color: str, parent: int | None) -> tuple[int, str]:
    name = check_place(node_id, kind, name, parent, color)
    fid = db.run("INSERT INTO project_folders(node_id,kind,parent_id,name,description,color,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)", (node_id, kind, parent, name, description.strip(), color, user, db.now()))
    return fid, name


def edit(node_id: str, kind: str, fid: int, name: str, description: str, color: str, parent: int | None) -> str:
    get(node_id, kind, fid)
    name = check_place(node_id, kind, name, parent, color, me=fid)
    db.run("UPDATE project_folders SET name=?, description=?, color=?, parent_id=? WHERE id=?", (name, description.strip(), color, parent, fid))
    return name


def delete(node_id: str, kind: str, fid: int) -> tuple[str, int | None]:
    """Supprime le dossier : ses sous-dossiers remontent d'un niveau (le contenu, lui, est remonté par l'appelant). Renvoie (nom, dossier parent)."""
    row = get(node_id, kind, fid)
    clash = {r["name"].lower() for r in db.rows("SELECT name FROM project_folders WHERE node_id=? AND kind=? AND parent_id IS ? AND id!=?", (node_id, kind, row["parent_id"], fid))}
    for k in db.rows("SELECT id, name FROM project_folders WHERE parent_id=? AND kind=?", (fid, kind)):
        if k["name"].lower() in clash:
            raise HTTPException(409, f"Le sous-dossier « {k['name']} » ferait doublon dans le dossier du dessus : renommez-le d'abord")
    db.run("UPDATE project_folders SET parent_id=? WHERE parent_id=? AND kind=?", (row["parent_id"], fid, kind))
    return row["name"], row["parent_id"]


def move_folders(node_id: str, kind: str, ids: list[int], dest: int | None) -> int:
    n = 0
    for fid in ids:
        row = get(node_id, kind, fid)
        if row["parent_id"] == dest:
            continue
        check_place(node_id, kind, row["name"], dest, row["color"], me=fid)
        db.run("UPDATE project_folders SET parent_id=? WHERE id=?", (dest, fid))
        n += 1
    return n
