"""Détail Docker (validation des réseaux, droits du contenu d'un volume), création d'un projet Traefik, services « terminés »."""
import pytest

from app import db, docker_ops, project_ops, worker_proto
from app.traefik_store import StoreError
from test_projects import client, pj  # noqa: F401  (fixture)


@pytest.fixture(scope="module", autouse=True)
def _leave_no_project_behind(pj):
    yield
    db.run("DELETE FROM projects")


def test_new_ops_are_registered_and_closed_list():
    for op in ("d_image", "d_network", "d_network_create", "d_network_connect", "d_network_disconnect", "d_volume", "d_exec_volls", "d_exec_volread", "p_create"):
        assert op in worker_proto.OPS and op in {**docker_ops.OPS, **project_ops.OPS}, op
    assert "d_exec_volls" in worker_proto.OPS_BY_MODULE["docker"]   # préfixe d_exec_ : un worker le refuse sans WORKER_ALLOW_EXEC


@pytest.mark.parametrize("bad", ["", "-x", "a/b", "a b", "bridge", "host", "x" * 70])
def test_network_name_validation(bad):
    with pytest.raises(StoreError):
        docker_ops.handle("d_network_create", {"name": bad})


def test_network_subnet_and_alias_validation():
    with pytest.raises(StoreError):
        docker_ops.handle("d_network_create", {"name": "ok", "subnet": "pas-un-reseau"})
    with pytest.raises(StoreError):
        docker_ops.handle("d_network_connect", {"network": "n", "container": "c", "aliases": ["a b"]})
    with pytest.raises(StoreError):
        docker_ops.handle("d_network_connect", {"network": "host", "container": "c"})


def test_create_project_writes_files_and_imports_it(pj):
    h = pj["h"]
    body = {"name": "traefik-neuf", "files": {"docker-compose.yml": "services:\n  traefik:\n    image: traefik:v3\n", ".env": "A=1\n", "traefik-dynamic/middlewares/x.yml": "http: {}\n"}}
    r = client.post("/api/projects/create", headers=h, json=body)
    assert r.status_code == 200, r.text
    d = pj["base"] / "traefik-neuf"
    assert (d / "docker-compose.yml").is_file() and (d / "traefik-dynamic/middlewares/x.yml").is_file()
    assert oct((d / ".env").stat().st_mode & 0o777) == "0o600"          # le .env n'est lisible que par son propriétaire
    assert r.json()["id"]                                               # ajouté à la liste des projets gérés
    assert client.post("/api/projects/create", headers=h, json=body).status_code == 409   # jamais d'écrasement


@pytest.mark.parametrize("name,files", [("../x", {"a.yml": "x"}), ("ok2", {"../evil": "x"}), ("ok3", {"/abs": "x"}), ("ok4", {".git/config": "x"}), ("a b", {"a.yml": "x"})])
def test_create_project_refuses_escapes(pj, name, files):
    assert client.post("/api/projects/create", headers=pj["h"], json={"name": name, "files": files}).status_code in (409, 422)
    assert not (pj["base"] / "ok2").exists() and not (pj["base"].parent / "evil").exists()


def test_exited_zero_service_is_not_an_alert():
    from app.routes_projects import summarize
    e = {"services": [{"name": "app", "profiles": []}, {"name": "init", "profiles": []}], "error": "", "path": "/p/x", "name": "x", "composeProject": "x", "file": "c.yml",
         "hasEnv": False, "hasGit": False, "build": False, "mtime": 0}
    mk = lambda svc, state, status: {"id": svc, "fullId": svc, "name": svc, "service": svc, "state": state, "health": "", "image": "i", "status": status, "ports": []}
    out = summarize(None, e, [mk("app", "running", "Up 2 hours"), mk("init", "exited", "Exited (0) 2 hours ago")], {}, access=False)
    assert out["alerts"] == [] and out["status"] == "running" and out["total"] == 1
    assert [s["done"] for s in out["services"]] == [False, True]
    bad = summarize(None, e, [mk("app", "running", "Up 2 hours"), mk("init", "exited", "Exited (1) 2 hours ago")], {}, access=False)
    assert any("init" in a["text"] for a in bad["alerts"])               # un arrêt en erreur reste signalé
