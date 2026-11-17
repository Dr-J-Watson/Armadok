"""Conteneur lancé à la main → projet compose : conversion (variables dans un .env, volumes externes, options reprises) et création du projet."""
import yaml
import pytest

from app import compose_convert as cc
from app import db, routes_projects
from test_projects import client, pid_of, pj  # noqa: F401  (fixture)


@pytest.fixture(scope="module", autouse=True)
def _leave_no_project_behind(pj):
    yield
    db.run("DELETE FROM projects")


SPEC = {
    "name": "blog", "image": "ghost:5", "cmd": ["node", "index.js", "--prod"], "imageCmd": ["node", "current/index.js"], "entrypoint": [], "imageEntrypoint": [],
    "env": [["url", "https://blog.example.org"], ["database__client", "sqlite3"], ["SECRET", "p@ss $word 'x'"], ["EMPTY", ""], ["MAIL", "a b"]],
    "labels": {"traefik.enable": "true", "traefik.http.routers.blog.rule": "Host(`blog.example.org`)", "pwd": "a$b"}, "user": "1000:1000", "workdir": "/var/lib/ghost", "hostname": "",
    "tty": False, "stdinOpen": False, "restart": {"name": "unless-stopped", "max": 0}, "ports": [{"ip": "", "host": "8080", "container": "2368", "proto": "tcp"}, {"ip": "127.0.0.1", "host": "2222", "container": "22", "proto": "tcp"}],
    "networkMode": "", "mounts": [{"type": "bind", "name": "", "source": "/srv/blog/content", "destination": "/var/lib/ghost/content", "rw": True},
                                  {"type": "volume", "name": "blog_db", "source": "", "destination": "/db", "rw": False},
                                  {"type": "volume", "name": "a" * 64, "source": "", "destination": "/tmp/x", "rw": True}],
    "tmpfs": [], "networks": {"bridge": {"aliases": [], "ipv4": ""}, "proxy": {"aliases": ["blog-alias"], "ipv4": ""}}, "privileged": False, "capAdd": ["NET_ADMIN"], "capDrop": [],
    "devices": [], "extraHosts": [], "dns": [], "dnsSearch": [], "securityOpt": [], "groupAdd": [], "sysctls": {}, "readOnly": False, "init": False, "pid": "", "ipc": "", "shmSize": 0,
    "memory": 536870912, "memoryReservation": 0, "nanoCpus": 500000000, "pidsLimit": 0, "healthcheck": {"Test": ["CMD", "curl", "-f", "http://localhost"], "Interval": 30000000000, "Retries": 3},
    "logConfig": {"Type": "json-file", "Config": {}}, "ulimits": [], "links": ["x:y"], "volumesFrom": [], "stopSignal": "", "stopTimeout": 0, "domainname": "",
}


def test_convert_builds_service_and_env():
    r = cc.convert(SPEC, project="blog")
    doc = yaml.safe_load(r["compose"])
    s = doc["services"]["blog"]
    assert s["image"] == "ghost:5" and s["container_name"] == "blog" and s["restart"] == "unless-stopped" and s["user"] == "1000:1000"
    assert s["command"] == ["node", "index.js", "--prod"] and "entrypoint" not in s                    # la commande qui diffère de l'image est gardée
    assert s["ports"] == ["8080:2368", "127.0.0.1:2222:22"]
    assert s["volumes"] == ["/srv/blog/content:/var/lib/ghost/content", "blog_db:/db:ro", "a" * 64 + ":/tmp/x"]
    assert doc["volumes"]["blog_db"] == {"external": True} and doc["networks"]["proxy"] == {"external": True}
    assert s["networks"]["proxy"] == {"aliases": ["blog-alias"]}
    assert s["environment"]["url"] == "${url}" and "p@ss" not in r["compose"]                           # aucune valeur dans le compose
    assert s["mem_limit"] == "512m" and s["cpus"] == 0.5 and s["cap_add"] == ["NET_ADMIN"]
    assert s["healthcheck"]["test"] == ["CMD", "curl", "-f", "http://localhost"] and s["healthcheck"]["interval"] == "30s" and s["healthcheck"]["retries"] == 3
    assert "traefik.http.routers.blog.rule=Host(`blog.example.org`)" in s["labels"] and "pwd=a$$b" in s["labels"]   # « $ » doublé dans un compose
    env = r["env"]
    assert "SECRET='p@ss $word '" in env.replace('"', "'") or "SECRET=" in env
    assert "url=https://blog.example.org" in env and "MAIL='a b'" in env and "EMPTY=\n" in env
    assert r["envKeys"][:2] == ["url", "database__client"]
    assert any("anonyme" in w for w in r["warnings"]) and any("--link" in w for w in r["warnings"]) and any("complexe" in w for w in r["warnings"])


def test_env_value_quoting():
    assert cc.env_value("abc-1.2") == "abc-1.2" and cc.env_value("a b") == "'a b'" and cc.env_value("a$b") == "'a$b'"
    assert cc.env_value("it's") == '"it\'s"' and cc.env_value("l1\nl2") == '"l1\\nl2"'
    assert cc.service_name("/My App_1") == "my-app_1" and cc.service_name("") == "app"


def test_minimal_container_has_few_lines():
    r = cc.convert({"name": "web", "image": "nginx", "restart": {"name": "no"}, "networks": {"bridge": {}}, "env": [], "mounts": [], "ports": []})
    assert yaml.safe_load(r["compose"])["services"]["web"] == {"image": "nginx", "container_name": "web"}
    assert r["env"] == "" and any("bridge" in w for w in r["warnings"])


def _fake_run(monkeypatch, spec, containers, calls):
    real = routes_projects.run

    def fake(ctx, op, args=None):
        if op == "d_containers":
            return {"containers": containers}
        if op == "d_spec":
            return spec
        if op in ("d_action", "d_rename"):
            calls.append((op, args))
            return {"ok": True}
        if op == "p_compose":
            calls.append((op, args))
            return {"job": "j1", "label": "compose up"}
        return real(ctx, op, args)
    monkeypatch.setattr(routes_projects, "run", fake)


def test_from_container_creates_project_and_replaces_old(pj, monkeypatch):
    h = pj["h"]
    cs = [{"name": "blog", "id": "abc123def456", "fullId": "abc123def456" + "0" * 52, "image": "ghost:5", "state": "running", "status": "Up", "project": "", "ports": [], "labels": {}}]
    calls = []
    _fake_run(monkeypatch, {**SPEC, "composeProject": "", "self": False, "running": True}, cs, calls)
    lst = client.get("/api/projects/from-container/list", headers=h).json()
    assert [c["name"] for c in lst["containers"]] == ["blog"] and lst["canConvert"]
    pv = client.post("/api/projects/from-container/preview", headers=h, json={"container": "blog"}).json()
    assert pv["name"] == "blog" and "services:" in pv["compose"] and "url" in pv["envKeys"] and "p@ss" not in pv["compose"]
    r = client.post("/api/projects/from-container", headers=h, json={"container": "blog", "name": "blog-projet", "replace": True})
    assert r.status_code == 200, r.text
    d = pj["base"] / "blog-projet"
    assert (d / "docker-compose.yml").is_file() and oct((d / ".env").stat().st_mode & 0o777) == "0o600" and "url=https://blog.example.org" in (d / ".env").read_text()
    assert r.json()["id"] and r.json()["oldContainer"] == "blog-avant-armadok" and r.json()["job"]["job"] == "j1"
    assert [c[0] for c in calls] == ["d_action", "d_rename", "p_compose"] and calls[1][1]["name"] == "blog-avant-armadok"      # l'ancien est arrêté puis renommé, jamais supprimé
    assert client.post("/api/projects/from-container", headers=h, json={"container": "blog", "name": "blog-projet"}).status_code == 409   # le dossier existe déjà


def test_already_in_a_project_or_self_is_refused(pj, monkeypatch):
    h = pj["h"]
    cs = [{"name": "x", "id": "1" * 12, "fullId": "1" * 64, "image": "i", "state": "running", "status": "Up", "project": "p", "ports": [], "labels": {}}]
    _fake_run(monkeypatch, {**SPEC, "composeProject": "p", "self": False, "running": True}, cs, [])
    assert client.post("/api/projects/from-container/preview", headers=h, json={"container": "x"}).status_code == 409
    _fake_run(monkeypatch, {**SPEC, "composeProject": "", "self": True, "running": True}, cs, [])
    assert client.post("/api/projects/from-container/preview", headers=h, json={"container": "x"}).status_code == 409
