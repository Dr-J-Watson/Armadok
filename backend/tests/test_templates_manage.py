"""Modèles de projet gérés comme des fichiers : dossiers, édition, duplication, export et import en zip."""
import base64
import io
import json
import stat
import zipfile

import pytest

from app import db, template_zip
from test_projects import client, pj  # noqa: F401  (fixture)

COMPOSE = "services:\n  app:\n    image: ${APP_IMAGE}\n"
BASE = "/api/projects/templates"


@pytest.fixture(autouse=True)
def _clean_templates(pj):
    yield
    db.run("DELETE FROM project_templates")
    db.run("DELETE FROM project_folders WHERE kind='templates'")


def zipped(entries: dict, symlink: str | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in entries.items():
            z.writestr(name, content)
        if symlink:
            i = zipfile.ZipInfo(symlink)
            i.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(i, "/etc/passwd")
    return out.getvalue()


def imp(pj, data: bytes, **kw):
    body = {"b64": base64.b64encode(data).decode(), "filename": "x.zip", **kw}
    return client.post(BASE + "/import", headers=pj["h"], json=body)


def tpl_list(pj):
    return client.get(BASE + "/list", headers=pj["h"]).json()


def test_import_preview_writes_nothing_then_apply_creates(pj):
    z = zipped({"armadok-template.json": json.dumps({"format": 1, "name": "Web", "description": "un site"}), "docker-compose.yml": COMPOSE, ".env": "APP_IMAGE=nginx\nDB_PASSWORD=secret\n"})
    r = imp(pj, z).json()
    assert r["applied"] is False and r["templates"][0]["name"] == "Web" and r["templates"][0]["vars"] == 2 and not r["templates"][0]["error"]
    assert not [t for t in tpl_list(pj)["templates"] if t["name"] == "Web"]                       # aperçu : rien n'est écrit
    r = imp(pj, z, apply=True).json()
    assert r["created"] == 1
    t = next(t for t in tpl_list(pj)["templates"] if t["name"] == "Web")
    assert t["description"] == "un site" and t["shared"] is False and sorted(t["files"]) == [".env", "docker-compose.yml"]
    got = client.get(f"{BASE}/{t['id']}/file", headers=pj["h"], params={"rel": ".env"}).json()
    assert "DB_PASSWORD=secret" in got["content"] and got["readOnly"] is False                    # importé en privé : le .env est gardé tel quel


def test_import_shared_blanks_secrets_and_conflicts(pj):
    z = zipped({"armadok-template.json": json.dumps({"name": "Web"}), "docker-compose.yml": COMPOSE, ".env": "DB_PASSWORD=secret\n"})
    assert imp(pj, z, apply=True, shared=True).json()["created"] == 1
    t = next(t for t in tpl_list(pj)["templates"] if t["name"] == "Web")
    assert "secret" not in client.get(f"{BASE}/{t['id']}/file", headers=pj["h"], params={"rel": ".env"}).json()["content"]
    assert imp(pj, z, apply=True, onConflict="skip").json()["skipped"] == 1                       # même nom : ignoré
    assert imp(pj, z, apply=True, onConflict="rename").json()["renamed"] == 1                     # ou renommé
    assert imp(pj, z, apply=True, onConflict="replace").json()["replaced"] == 1                   # ou remplacé (c'est le mien)
    assert sorted(x["name"] for x in tpl_list(pj)["templates"] if x["name"].startswith("Web")) == ["Web", "Web (importé)"]
    z2 = zipped({"armadok-template.json": json.dumps({"name": "Modèle de base"}), "docker-compose.yml": COMPOSE})
    r = imp(pj, z2, apply=True, onConflict="replace").json()                                      # un modèle intégré ne se remplace jamais : copie renommée
    assert r["renamed"] == 1 and r["replaced"] == 0


def test_import_refuses_dangerous_or_wrong_archives(pj):
    for bad, msg in ((zipped({"../evil/docker-compose.yml": COMPOSE}), "Chemin refusé"),
                     (zipped({"/etc/docker-compose.yml": COMPOSE}), "Chemin refusé"),
                     (zipped({"docker-compose.yml": COMPOSE}, symlink="lien"), "Lien symbolique")):
        r = imp(pj, bad, apply=True)
        assert r.status_code == 422 and msg in r.json()["detail"], r.text
    assert imp(pj, b"pas un zip").status_code == 422
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:                                                          # binaire : refusé (un modèle = du texte)
        z.writestr("docker-compose.yml", COMPOSE)
        z.writestr("logo.bin", b"\xff\xfe\x00\x01")
    assert imp(pj, out.getvalue()).status_code == 422
    no_compose = imp(pj, zipped({"armadok-template.json": json.dumps({"name": "Vide"}), "notes.txt": "x"}), apply=True).json()
    assert no_compose["errors"] == 1 and no_compose["created"] == 0 and "compose" in no_compose["templates"][0]["error"]   # un modèle invalide n'empêche pas les autres
    assert not [t for t in tpl_list(pj)["templates"] if t["name"] == "Vide"]


def test_zip_bomb_and_size_limits():
    big = zipped({"docker-compose.yml": COMPOSE, "gros.txt": "a" * (template_zip.MAX_UNPACKED + 1)})
    with pytest.raises(ValueError, match="décompressée"):
        template_zip.parse(big)
    with pytest.raises(ValueError, match="trop gros"):
        template_zip.parse(zipped({"docker-compose.yml": COMPOSE, "a.txt": "x" * (template_zip.MAX_FILE_READ + 1)}))


def test_archive_without_manifest_is_one_template_named_after_its_folder_or_file(pj):
    one = template_zip.parse(zipped({"monprojet/docker-compose.yml": COMPOSE, "monprojet/conf/a.conf": "x"}), "peu-importe.zip")
    assert [(t["name"], t["folders"], sorted(t["files"])) for t in one] == [("monprojet", [], ["conf/a.conf", "docker-compose.yml"])]
    flat = template_zip.parse(zipped({"docker-compose.yml": COMPOSE}), "Mon Modele.zip")
    assert flat[0]["name"] == "Mon Modele"


def test_folders_regroup_delete_and_roundtrip_export_import(pj):
    h = pj["h"]
    a = client.post(BASE + "/folders", headers=h, json={"name": "Prod"}).json()["id"]
    b = client.post(BASE + "/folders", headers=h, json={"name": "Web", "parentId": a}).json()["id"]
    assert client.post(BASE + "/folders", headers=h, json={"name": "prod"}).status_code == 409    # doublon (casse ignorée)
    z = zipped({"armadok-template.json": json.dumps({"name": "Site"}), "docker-compose.yml": COMPOSE})
    imp(pj, z, apply=True)
    tid = next(t["id"] for t in tpl_list(pj)["templates"] if t["name"] == "Site")
    assert client.post(BASE + "/regroup", headers=h, json={"templates": [tid], "folders": [], "folderId": b}).json()["moved"] == 1
    listing = tpl_list(pj)
    assert next(t for t in listing["templates"] if t["id"] == tid)["folderId"] == b and {f["name"] for f in listing["folders"]} == {"Prod", "Web"}
    assert client.post(BASE + "/regroup", headers=h, json={"templates": ["builtin:base"], "folderId": b}).status_code == 403   # un intégré ne se range pas
    # export du dossier « Prod » : le modèle part avec son chemin de dossiers
    ex = client.post(BASE + "/export", headers=h, json={"folders": [a]}).json()
    names = zipfile.ZipFile(io.BytesIO(base64.b64decode(ex["b64"]))).namelist()
    assert "Prod/Web/Site/docker-compose.yml" in names and "Prod/Web/Site/armadok-template.json" in names
    # …et un import dans une base vierge recrée les dossiers
    db.run("DELETE FROM project_templates")
    db.run("DELETE FROM project_folders WHERE kind='templates'")
    assert imp(pj, base64.b64decode(ex["b64"]), apply=True).json()["created"] == 1
    listing = tpl_list(pj)
    site = next(t for t in listing["templates"] if t["name"] == "Site")
    path = []
    fid = site["folderId"]
    by_id = {f["id"]: f for f in listing["folders"]}
    while fid:
        path.insert(0, by_id[fid]["name"])
        fid = by_id[fid]["parentId"]
    assert path == ["Prod", "Web"]
    # supprimer un dossier fait remonter son contenu, sans rien supprimer
    web = next(f["id"] for f in listing["folders"] if f["name"] == "Web")
    assert client.delete(f"{BASE}/folders/{web}", headers=h).status_code == 200
    after = tpl_list(pj)
    assert next(t for t in after["templates"] if t["name"] == "Site")["folderId"] == next(f["id"] for f in after["folders"] if f["name"] == "Prod")


def test_export_blanks_secrets_unless_asked(pj):
    h = pj["h"]
    imp(pj, zipped({"armadok-template.json": json.dumps({"name": "Sec"}), "docker-compose.yml": COMPOSE, ".env": "API_TOKEN=abc123\nPORT=80\n"}), apply=True)
    tid = next(t["id"] for t in tpl_list(pj)["templates"] if t["name"] == "Sec")

    def env_of(body):
        ex = client.post(BASE + "/export", headers=h, json={"ids": [tid], **body}).json()
        z = zipfile.ZipFile(io.BytesIO(base64.b64decode(ex["b64"])))
        assert ex["name"] == "Sec.zip"                                                            # un seul modèle : ses fichiers à la racine, nommé d'après lui
        return z.read(".env").decode()
    assert "abc123" not in env_of({}) and "PORT=80" in env_of({})
    assert "abc123" in env_of({"includeSecrets": True})
    ex = client.post(BASE + "/export", headers=h, json={"ids": ["builtin:base"]}).json()          # les intégrés s'exportent aussi
    assert ex["count"] == 1 and client.post(BASE + "/export", headers=h, json={"ids": []}).status_code == 404


def test_edit_files_of_a_template(pj):
    h = pj["h"]
    imp(pj, zipped({"armadok-template.json": json.dumps({"name": "Ed"}), "docker-compose.yml": COMPOSE}), apply=True)
    tid = next(t["id"] for t in tpl_list(pj)["templates"] if t["name"] == "Ed")
    f = f"{BASE}/{tid}/file"
    assert client.put(f, headers=h, json={"rel": "conf/a.conf", "content": "x", "create": True}).json()["files"] == ["conf/a.conf", "docker-compose.yml"]
    assert client.put(f, headers=h, json={"rel": "conf/a.conf", "content": "x", "create": True}).status_code == 409           # existe déjà
    assert client.put(f, headers=h, json={"rel": "conf/a.conf", "content": "y"}).status_code == 200
    assert client.get(f, headers=h, params={"rel": "conf/a.conf"}).json()["content"] == "y"
    assert client.put(f, headers=h, json={"rel": "../x", "content": "y", "create": True}).status_code == 422                  # chemin sûr
    assert client.post(f"{BASE}/{tid}/move", headers=h, json={"src": "conf", "dst": "config"}).json()["files"] == ["config/a.conf", "docker-compose.yml"]
    assert client.delete(f, headers=h, params={"rel": "docker-compose.yml"}).status_code == 422                               # un modèle garde son compose
    assert client.delete(f, headers=h, params={"rel": "config"}).json()["files"] == ["docker-compose.yml"]
    # les modèles intégrés ne se modifient pas ; on en fait une copie privée
    assert client.put(f"{BASE}/builtin:base/file", headers=h, json={"rel": "docker-compose.yml", "content": "x"}).status_code == 403
    d = client.post(f"{BASE}/builtin:base/duplicate", headers=h, json={}).json()
    assert d["name"] == "Modèle de base (copie)"
    assert client.post(f"{BASE}/builtin:base/duplicate", headers=h, json={}).json()["name"] == "Modèle de base (copie 2)"
    assert client.put(f"{BASE}/{d['id']}/file", headers=h, json={"rel": "docker-compose.yml", "content": COMPOSE}).status_code == 200   # la copie, elle, est modifiable


def test_editing_the_env_of_a_shared_template_never_keeps_secrets(pj):
    h = pj["h"]
    imp(pj, zipped({"armadok-template.json": json.dumps({"name": "Part"}), "docker-compose.yml": COMPOSE}), apply=True, shared=True)
    tid = next(t["id"] for t in tpl_list(pj)["templates"] if t["name"] == "Part")
    client.put(f"{BASE}/{tid}/file", headers=h, json={"rel": ".env", "content": "DB_PASSWORD=oups\n", "create": True})
    assert "oups" not in client.get(f"{BASE}/{tid}/file", headers=h, params={"rel": ".env"}).json()["content"]


def test_zip_build_then_parse_is_lossless():
    ts = [{"name": "A/B", "description": "d", "files": {"docker-compose.yml": COMPOSE, "é.txt": "ü"}, "folders": ["Un", "Deux"]}, {"name": "C", "description": "", "files": {"docker-compose.yml": COMPOSE}, "folders": []}]
    back = template_zip.parse(template_zip.build(ts))
    by = {t["name"]: t for t in back}
    assert by["A/B"]["folders"] == ["Un", "Deux"] and by["A/B"]["files"]["é.txt"] == "ü" and by["C"]["folders"] == []
