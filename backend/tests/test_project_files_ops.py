"""Explorateur de fichiers d'un projet : créer un dossier, renommer / déplacer, supprimer un dossier — toujours DANS le projet."""
import os

import pytest

from app import db, project_ops
from app.traefik_store import StoreError
from test_projects import client, pid_of, pj  # noqa: F401  (fixture)


@pytest.fixture(scope="module", autouse=True)
def _leave_no_project_behind(pj):
    yield
    db.run("DELETE FROM projects")


def url(pid, what):
    return f"/api/projects/{pid}/{what}"


def test_mkdir_and_paths_that_escape(pj):
    h, pid = pj["h"], pid_of(pj, "site")
    assert client.post(url(pid, "mkdir"), headers=h, json={"rel": "conf/nginx"}).json() == {"path": "conf/nginx"}
    assert (pj["base"] / "site" / "conf" / "nginx").is_dir()
    assert client.post(url(pid, "mkdir"), headers=h, json={"rel": "conf"}).status_code == 409                       # existe déjà
    for bad in ("../dehors", "/etc/x", ".git/x", "a/../../b", "a\\b", ""):
        assert client.post(url(pid, "mkdir"), headers=h, json={"rel": bad}).status_code == 422, bad


def test_move_and_rename_files_and_folders(pj):
    h, pid, root = pj["h"], pid_of(pj, "site"), pj["base"] / "site"
    client.put(url(pid, "file"), headers=h, json={"rel": "a.txt", "content": "bonjour", "create": True})
    assert client.post(url(pid, "move"), headers=h, json={"src": "a.txt", "dst": "conf/b.txt"}).json() == {"path": "conf/b.txt"}   # crée le dossier au besoin
    assert not (root / "a.txt").exists() and (root / "conf" / "b.txt").read_text() == "bonjour"
    assert client.post(url(pid, "move"), headers=h, json={"src": "conf", "dst": "configuration"}).status_code == 200          # un dossier entier
    assert (root / "configuration" / "b.txt").exists() and not (root / "conf").exists()
    client.put(url(pid, "file"), headers=h, json={"rel": "c.txt", "content": "x", "create": True})
    assert client.post(url(pid, "move"), headers=h, json={"src": "c.txt", "dst": "configuration/b.txt"}).status_code == 409   # destination prise
    assert client.post(url(pid, "move"), headers=h, json={"src": "configuration", "dst": "configuration/sous"}).status_code == 422   # dans lui-même
    assert client.post(url(pid, "move"), headers=h, json={"src": "absent", "dst": "z"}).status_code == 404
    for dst in ("../dehors", "/tmp/x", ".git/x"):
        assert client.post(url(pid, "move"), headers=h, json={"src": "c.txt", "dst": dst}).status_code == 422, dst
    assert (root / "c.txt").exists()


def test_the_compose_file_of_the_project_never_moves_or_vanishes(pj):
    h, pid = pj["h"], pid_of(pj, "site")
    assert client.post(url(pid, "move"), headers=h, json={"src": "docker-compose.yml", "dst": "autre.yml"}).status_code == 409
    client.post(url(pid, "mkdir"), headers=h, json={"rel": "sub"})
    assert client.post(url(pid, "move"), headers=h, json={"src": "docker-compose.yml", "dst": "sub/docker-compose.yml"}).status_code == 409
    assert (pj["base"] / "site" / "docker-compose.yml").exists()


def test_delete_a_folder_keeps_text_files_in_the_history(pj):
    h, pid, root = pj["h"], pid_of(pj, "site"), pj["base"] / "site"
    client.post(url(pid, "mkdir"), headers=h, json={"rel": "tmp/deep"})
    client.put(url(pid, "file"), headers=h, json={"rel": "tmp/deep/n.txt", "content": "à garder", "create": True})
    (root / "tmp" / "bin.dat").write_bytes(b"\x00\x01\x02")
    r = client.delete(url(pid, "dir") + "?rel=tmp", headers=h)
    assert r.status_code == 200 and r.json()["files"] == 2 and not (root / "tmp").exists()
    hist = client.get(url(pid, "history") + "?rel=tmp/deep/n.txt", headers=h).json()["versions"]
    assert len(hist) == 1                                                                                           # le texte est récupérable, pas le binaire
    assert client.delete(url(pid, "dir") + "?rel=tmp", headers=h).status_code == 404
    assert client.delete(url(pid, "dir") + "?rel=docker-compose.yml", headers=h).status_code in (404, 409)         # ce n'est pas un dossier / protégé


def test_folder_deletion_guards(pj):
    h, pid, root = pj["h"], pid_of(pj, "site"), pj["base"] / "site"
    (root / "gros").mkdir()
    for i in range(project_ops.MAX_DIR_DELETE_FILES + 1):
        (root / "gros" / f"f{i}").write_text("x")
    assert client.delete(url(pid, "dir") + "?rel=gros", headers=h).status_code == 413                                # trop de fichiers : à faire à la main
    assert (root / "gros" / "f0").exists()
    for bad in ("..", "../x", "/etc", ".git"):
        assert client.delete(url(pid, "dir") + f"?rel={bad}", headers=h).status_code in (404, 422), bad
    (root / "secrets").mkdir()
    (root / "secrets" / ".env").write_text("A=1")
    with pytest.raises(StoreError) as e:                                                                            # sans le droit « secrets », le .env d'un dossier ne part pas à l'historique
        project_ops.p_delete_dir({"dir": str(root), "rel": "secrets", "canEnv": False})
    assert e.value.status == 403 and (root / "secrets" / ".env").exists()


def test_symlinks_are_never_followed(pj, tmp_path):
    h, pid, root = pj["h"], pid_of(pj, "site"), pj["base"] / "site"
    target = tmp_path / "ailleurs"
    target.mkdir()
    (target / "precieux.txt").write_text("a ne pas toucher")
    os.symlink(target, root / "lien")
    assert client.post(url(pid, "move"), headers=h, json={"src": "lien", "dst": "lien2"}).status_code == 409
    assert client.delete(url(pid, "dir") + "?rel=lien", headers=h).status_code == 409
    assert client.post(url(pid, "mkdir"), headers=h, json={"rel": "lien/nouveau"}).status_code == 403              # sortirait du projet par le lien
    assert (target / "precieux.txt").read_text() == "a ne pas toucher" and not (target / "nouveau").exists()
    os.unlink(root / "lien")


def test_the_new_operations_are_exposed_to_workers():
    from app import worker_proto as proto
    for op in ("p_mkdir", "p_move", "p_delete_dir"):
        assert op in proto.OPS_BY_MODULE["docker"]
