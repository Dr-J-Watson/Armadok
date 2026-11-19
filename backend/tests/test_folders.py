"""Organisation en dossiers : création, renommage, suppression, rangement automatique."""
import pytest
from fastapi.testclient import TestClient

from app import worker
from app.main import app
from app.traefik_store import MAX_DEPTH, Store, StoreError, suggest_file

client = TestClient(app)
YML = "http:\n  routers:\n    r1:\n      rule: Host(`a.example.com`)\n      service: s1\n  services:\n    s1:\n      loadBalancer:\n        servers:\n          - url: http://x:1\n"


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "dyn", tmp_path / "bak")


def test_make_move_remove_dir(store):
    store.root.mkdir()
    store.make_dir("apps/web")
    assert store.list_dirs() == ["apps", "apps/web"]
    with pytest.raises(StoreError):
        store.make_dir("apps")                      # existe déjà
    store.write("apps/web/a.yml", YML, create=True)
    store.write("apps/web/a.yml", YML.replace("a.example", "b.example"))   # crée une sauvegarde
    with pytest.raises(StoreError) as e:
        store.remove_dir("apps")                    # contient un fichier
    assert e.value.status == 409
    store.move_dir("apps", "services")
    assert store.read("services/web/a.yml").startswith("http:")
    assert len(store.history("services/web/a.yml")) == 1       # l'historique suit le dossier
    assert not (store.root / "apps").exists()
    store.delete("services/web/a.yml")
    store.remove_dir("services")                    # vide : part avec ses sous-dossiers
    assert store.list_dirs() == []


@pytest.mark.parametrize("bad", ["", "/abs", "a/../b", "../x", ".hidden", "a//b", "a b", "x/", "é", "a\x00b", ".."])
def test_dir_names_are_validated(store, bad):
    store.root.mkdir()
    with pytest.raises(StoreError):
        store.make_dir(bad)


def test_depth_and_self_move(store):
    store.root.mkdir()
    store.make_dir("/".join("d" * 1 for _ in range(MAX_DEPTH)))
    with pytest.raises(StoreError):
        store.make_dir("/".join("d" for _ in range(MAX_DEPTH + 1)))
    store.make_dir("a")
    with pytest.raises(StoreError):
        store.move_dir("a", "a/b")                  # dans lui-même
    store.make_dir("z")
    with pytest.raises(StoreError):
        store.move_dir("a", "z")                    # destination existante


def test_move_file_keeps_history(store):
    store.root.mkdir()
    store.write("a.yml", YML, create=True)
    store.write("a.yml", YML.replace("a.example", "b.example"))
    store.move("a.yml", "apps/a.yml")
    assert len(store.history("apps/a.yml")) == 1


def test_placement_follows_existing_organisation():
    item = lambda f, kind="routers", proto="http": {"file": f, "kind": kind, "proto": proto}
    assert suggest_file([], [], "http", "routers", "blog") == "blog.yml"                    # à plat : rien ne change
    assert suggest_file([], ["apps", "infra"], "http", "routers", "blog") == "apps/blog.yml"
    assert suggest_file([item("media/x.yml")], ["media"], "http", "routers", "blog") == "media/blog.yml"
    assert suggest_file([], ["middlewares"], "http", "middlewares", "auth") == "middlewares/auth.yml"
    assert suggest_file([item("core/m.yml", "middlewares")], ["core"], "http", "middlewares", "auth") == "core/m.yml"
    assert suggest_file([], [], "http", "routers", "../etc/passwd") == "etc-passwd.yml"      # nom nettoyé


def test_worker_ops(store):
    store.root.mkdir()
    assert worker.handle(store, "mkdir", {"path": "apps"}) is True
    assert worker.handle(store, "list_dirs", {}) == ["apps"]
    assert worker.handle(store, "move_dir", {"src": "apps", "dst": "infra"}) is True
    assert worker.handle(store, "rmdir", {"path": "infra"}) is True
    with pytest.raises(StoreError):
        worker.handle(store, "mkdir", {"path": "../evil"})


def test_api_folders_and_auto_placement():
    r = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"})
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert client.post("/api/traefik/dir", headers=h, json={"path": "apps"}).status_code == 200
    assert "apps" in client.get("/api/traefik/overview", headers=h).json()["dirs"]
    spec = {"rule": "Host(`dir.example.com`)", "service": "noop@internal"}
    assert client.put("/api/traefik/item", headers=h, json={"proto": "http", "kind": "routers", "name": "dirtest", "spec": spec}).json()["file"] == "apps/dirtest.yml"
    assert client.get("/api/traefik/placement", headers=h, params={"proto": "http", "kind": "routers", "name": "zz"}).json() == {"file": "apps/zz.yml"}
    assert client.delete("/api/traefik/dir", headers=h, params={"path": "apps"}).status_code == 409
    assert client.post("/api/traefik/dir/move", headers=h, json={"src": "apps", "dst": "projets"}).status_code == 200
    assert client.get("/api/traefik/file", headers=h, params={"path": "projets/dirtest.yml"}).status_code == 200
    assert client.post("/api/traefik/dir", headers=h, json={"path": "../x"}).status_code == 400
    client.delete("/api/traefik/item", headers=h, params={"proto": "http", "kind": "routers", "name": "dirtest"})
    client.delete("/api/traefik/file", headers=h, params={"path": "projets/dirtest.yml"})
    assert client.delete("/api/traefik/dir", headers=h, params={"path": "projets"}).status_code == 200


def test_site_folders_organise_the_sites_list(tmp_path):
    """Dossiers de sites : mêmes règles que les dossiers de projets, rangement par nom de routeur, indépendants des dossiers de projets."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.config import settings
    c = TestClient(app)
    from app import db
    from app.nodes import node_out, store_for
    old = None
    try:
        tok = c.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
        nid = next(n["id"] for n in c.get("/api/nodes", headers={"Authorization": f"Bearer {tok}"}).json() if n["kind"] == "local")
        h = {"Authorization": f"Bearer {tok}", "X-Node": nid}
        st = store_for(node_out(db.one("SELECT * FROM nodes WHERE id=?", (nid,))))
        st.upsert("http", "services", "s", {"loadBalancer": {"servers": [{"url": "http://x:1"}]}}, file="sf.yml")
        for n in ("alpha", "beta"):
            st.upsert("http", "routers", n, {"rule": f"Host(`{n}.example.com`)", "service": "s"}, file="sf.yml")
        a = c.post("/api/traefik/site-folders", headers=h, json={"name": "Public", "description": "En ligne", "color": "blue"}).json()["id"]
        b = c.post("/api/traefik/site-folders", headers=h, json={"name": "Interne", "parentId": a}).json()["id"]
        assert c.post("/api/traefik/site-folders", headers=h, json={"name": "public"}).status_code == 409
        r = c.get("/api/traefik/site-folders", headers=h).json()
        assert {f["name"] for f in r["folders"]} == {"Public", "Interne"} and r["assign"] == {} and r["canManage"]
        # un dossier de projets de même nom ne gêne pas, et n'apparaît pas ici
        from app import org_folders; org_folders.create(nid, "projects", "t", "Public", "", "", None)
        assert {f["name"] for f in c.get("/api/traefik/site-folders", headers=h).json()["folders"]} == {"Public", "Interne"}
        rr = c.post("/api/traefik/site-folders/regroup", headers=h, json={"routers": ["alpha", "beta"], "folderId": b}); assert rr.status_code == 200, rr.text
        assert c.get("/api/traefik/site-folders", headers=h).json()["assign"] == {"alpha": b, "beta": b}
        assert c.post("/api/traefik/site-folders/regroup", headers=h, json={"routers": ["nope"], "folderId": b}).status_code == 404
        assert c.post("/api/traefik/site-folders/regroup", headers=h, json={"folders": [a], "folderId": b}).status_code == 422   # pas de boucle
        assert c.delete(f"/api/traefik/site-folders/{b}", headers=h).status_code == 200      # le contenu remonte
        assert c.get("/api/traefik/site-folders", headers=h).json()["assign"] == {"alpha": a, "beta": a}
        assert c.post("/api/traefik/site-folders/regroup", headers=h, json={"routers": ["alpha"], "folderId": None}).status_code == 200
        assert c.get("/api/traefik/site-folders", headers=h).json()["assign"] == {"beta": a}
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            org_folders.get(nid, "projects", a)   # un dossier de sites n'est pas un dossier de projets
        org_folders.delete(nid, "projects", org_folders.listing(nid, "projects")[0]["id"])
        c.delete(f"/api/traefik/site-folders/{a}", headers=h)
    finally:
        for n in ("alpha", "beta"):
            st.delete_item("http", "routers", n)
        st.delete_item("http", "services", "s")
