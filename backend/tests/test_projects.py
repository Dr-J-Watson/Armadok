"""Projets Compose : découverte dans les racines autorisées, import / ignorer / désimporter, rattachement des conteneurs, protection des chemins."""
import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import project_ops, worker, worker_proto as proto
from app.config import settings
from app.main import app
from app.traefik_store import Store
from fakedocker import FakeDocker

client = TestClient(app)

COMPOSE_SITE = """services:
  web:
    image: nginx:alpine
    container_name: web-1
    ports: ["8080:80"]
    labels:
      - traefik.enable=true
  worker:
    image: python:3.12
    build: .
    profiles: [jobs]
"""
COMPOSE_BAD = "services: [this is: not valid"


@pytest.fixture(scope="module")
def pj():
    base = Path(tempfile.mkdtemp(prefix="pj")).resolve()
    (base / "site").mkdir()
    (base / "site" / "docker-compose.yml").write_text(COMPOSE_SITE)
    (base / "site" / ".env").write_text("SECRET=1\n")
    (base / "apps" / "blog").mkdir(parents=True)
    (base / "apps" / "blog" / "compose.yaml").write_text("services:\n  blog:\n    image: ghost:5\n")
    (base / "broken").mkdir()
    (base / "broken" / "docker-compose.yml").write_text(COMPOSE_BAD)
    (base / "node_modules" / "x").mkdir(parents=True)
    (base / "node_modules" / "x" / "docker-compose.yml").write_text("services: {}\n")      # ignoré : dossier de dépendances
    (base / ".hidden").mkdir()
    (base / ".hidden" / "docker-compose.yml").write_text("services: {}\n")                    # ignoré : dossier caché
    outside = Path(tempfile.mkdtemp(prefix="out")).resolve()
    (outside / "docker-compose.yml").write_text("services: {a: {image: x}}\n")
    (base / "evil").symlink_to(outside)                                                       # lien qui sort des racines
    d = tempfile.mkdtemp(prefix="fd")
    fake = FakeDocker(f"{d}/d.sock")
    fake.containers["aaaaaaaaaaaa1111"]["Labels"]["com.docker.compose.project.working_dir"] = str(base / "site")
    old = (settings.docker_socket, settings.docker_manage, settings.project_roots)
    settings.docker_socket, settings.docker_manage, settings.project_roots = fake.path, True, [str(base)]
    tok = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
    nid = next(n["id"] for n in client.get("/api/nodes", headers={"Authorization": f"Bearer {tok}"}).json() if n["kind"] == "local")
    yield {"h": {"Authorization": f"Bearer {tok}", "X-Node": nid}, "base": base, "outside": outside, "fake": fake}
    settings.docker_socket, settings.docker_manage, settings.project_roots = old
    fake.close()


def test_discovery_finds_compose_folders_only_inside_roots(pj):
    r = client.get("/api/projects", headers=pj["h"]).json()
    names = sorted(p["name"] for p in r["discovered"])
    assert names == ["blog", "broken", "site"]                                   # ni node_modules, ni dossier caché, ni le lien qui sort
    assert r["managed"] == [] and r["ignored"] == [] and r["roots"] == [str(pj["base"])]
    site = next(p for p in r["discovered"] if p["name"] == "site")
    assert [s["name"] for s in site["services"]] == ["web", "worker"] and site["hasEnv"] and site["build"] and site["file"] == "docker-compose.yml"
    assert next(s for s in site["services"] if s["name"] == "worker")["profiles"] == ["jobs"]
    blog = next(p for p in r["discovered"] if p["name"] == "blog")
    assert blog["file"] == "compose.yaml" and blog["composeProject"] == "blog"
    assert next(p for p in r["discovered"] if p["name"] == "broken")["error"] == "YAML invalide"


def test_discovered_projects_know_their_containers(pj):
    site = next(p for p in client.get("/api/projects", headers=pj["h"]).json()["discovered"] if p["name"] == "site")
    web = next(s for s in site["services"] if s["name"] == "web")
    assert web["state"] == "running" and [c["name"] for c in web["containers"]] == ["web-1"]
    assert next(s for s in site["services"] if s["name"] == "worker")["state"] == "absent"
    assert site["status"] == "running" and site["running"] == 1 and site["total"] == 1       # le service « jobs » (profil) n'est pas attendu
    assert site["containerIds"] == ["aaaaaaaaaaaa"]


def test_import_ignore_and_unimport_do_not_touch_the_disk(pj):
    h, base = pj["h"], pj["base"]
    before = sorted(str(p) for p in base.rglob("*") if not p.is_symlink())
    r = client.post("/api/projects/import", headers=h, json={"paths": [str(base / "site"), str(base / "apps" / "blog")], "tags": "prod, media,prod"})
    assert r.status_code == 200 and sorted(r.json()["done"]) == ["blog", "site"]
    assert client.post("/api/projects/import", headers=h, json={"paths": [str(base / "site")]}).json()["skipped"] == ["site"]     # déjà géré
    assert client.post("/api/projects/ignore", headers=h, json={"paths": [str(base / "broken")]}).json()["done"] == ["broken"]
    r = client.get("/api/projects", headers=h).json()
    assert sorted(p["name"] for p in r["managed"]) == ["blog", "site"] and [p["name"] for p in r["ignored"]] == ["broken"] and r["discovered"] == []
    site = next(p for p in r["managed"] if p["name"] == "site")
    assert site["tags"] == ["prod", "media"] and site["state"] == "managed" and site["id"]
    assert client.delete(f"/api/projects/{site['id']}", headers=h).json() == {"ok": True}                                           # désimporter
    assert sorted(p["name"] for p in client.get("/api/projects", headers=h).json()["discovered"]) == ["site"]
    assert sorted(str(p) for p in base.rglob("*") if not p.is_symlink()) == before                                                  # le disque n'a pas bougé
    assert client.delete(f"/api/projects/{site['id']}", headers=h).status_code == 404


def test_paths_outside_the_roots_are_refused(pj):
    h = pj["h"]
    for bad in (str(pj["outside"]), "/etc", "/", str(pj["base"] / "evil"), "relatif/dossier", str(pj["base"] / ".." / "x"), str(pj["base"] / "absent")):
        r = client.post("/api/projects/import", headers=h, json={"paths": [bad]})
        assert r.status_code in (403, 404, 422), (bad, r.status_code)
    assert client.get("/api/projects", headers=h).json()["managed"][0]["name"] == "blog"                                           # rien n'a été ajouté


def test_project_detail_links_services_to_containers(pj):
    h = pj["h"]
    client.post("/api/projects/import", headers=h, json={"paths": [str(pj["base"] / "site")]})
    pid = next(p["id"] for p in client.get("/api/projects", headers=h).json()["managed"] if p["name"] == "site")
    d = client.get(f"/api/projects/{pid}", headers=h).json()
    assert d["name"] == "site" and d["status"] == "running" and d["perms"]["deploy"] is True and d["perms"]["exec"] is True
    web = next(s for s in d["services"] if s["name"] == "web")
    assert web["containers"][0]["ports"] == [{"ip": "0.0.0.0", "private": 80, "public": 8080, "type": "tcp"}] and d["extra"] == []
    assert d["alerts"] == [] and d["access"] is None and d["hosts"] == []                                                          # pas de site Traefik, pas de journal
    assert client.get("/api/projects/99999", headers=h).status_code == 404


def test_missing_managed_project_is_reported(pj):
    h, base = pj["h"], pj["base"]
    (base / "tmpproj").mkdir()
    (base / "tmpproj" / "docker-compose.yml").write_text("services: {a: {image: x}}\n")
    client.post("/api/projects/import", headers=h, json={"paths": [str(base / "tmpproj")]})
    (base / "tmpproj" / "docker-compose.yml").unlink()
    (base / "tmpproj").rmdir()
    gone = next(p for p in client.get("/api/projects", headers=h).json()["managed"] if p["name"] == "tmpproj")
    assert gone["missing"] and gone["alerts"][0]["text"] == "Le dossier de ce projet est introuvable"


def test_module_and_right_guards(pj):
    settings.docker_manage = False
    try:
        assert client.get("/api/projects", headers=pj["h"]).status_code == 409
    finally:
        settings.docker_manage = True


def test_project_ops_are_exposed_to_workers_and_confined(pj):
    from app import docker_ops, host_ops
    assert set(proto.OPS_BY_MODULE["docker"]) == set(docker_ops.OPS) | set(project_ops.OPS) == set(host_ops.names())
    store = Store()
    assert worker.handle(store, "p_discover", {}, "", ("docker",))["projects"]
    with pytest.raises(Exception) as e:
        worker.handle(store, "p_project", {"dir": "/etc"}, "", ("docker",))
    assert e.value.status == 403
    with pytest.raises(Exception) as e:
        worker.handle(store, "p_discover", {}, "", ("traefik",))
    assert e.value.status == 400
    with pytest.raises(Exception) as e:
        host_ops.handle("rm_rf", {})
    assert e.value.status == 400


def test_parse_compose_variants():
    c = project_ops.parse_compose("name: Mon_Projet\nservices:\n  a:\n    image: x\n    labels:\n      traefik.http.routers.a.rule: Host(`a.fr`)\n      autre: 1\n")
    assert c["name"] == "Mon_Projet" and c["services"][0]["traefik"] == {"traefik.http.routers.a.rule": "Host(`a.fr`)"}
    assert project_ops.parse_compose("- 1\n- 2")["error"].startswith("Ce fichier")
    assert project_ops.parse_compose("services: {}")["services"] == []
    assert project_ops.project_name(Path("/x/Mon App!"), {"name": None}) == "monapp"


# =========================================================================== étape 3 : fichiers, déploiement, labels
import time

from app import labels_yaml

COMPOSE_SHOP = """services:
  web:
    image: nginx
    container_name: shop-web
    labels:
      traefik.enable: "true"
      traefik.http.routers.shop.rule: Host(`shop.example.org`)
      traefik.http.routers.shop.entrypoints: websecure
      traefik.http.routers.shop.tls.certresolver: le
      traefik.http.routers.shop.middlewares: auth@file,secure
      traefik.http.services.shop.loadbalancer.server.port: "8080"
      traefik.http.middlewares.secure.headers.stsseconds: "31536000"
      traefik.http.middlewares.secure.ipallowlist.sourcerange: 10.0.0.0/8,192.168.0.0/16
"""


@pytest.fixture()
def fakecli(tmp_path, monkeypatch):
    script = tmp_path / "docker"
    script.write_text('#!/bin/sh\necho "ARGS: $@"\nsleep 0.7\nexit ${FAKE_CODE:-0}\n')
    script.chmod(0o755)
    real = project_ops.shutil.which
    monkeypatch.setattr(project_ops.shutil, "which", lambda n, *a, **k: str(script) if n == "docker" else real(n, *a, **k))
    return script


def pid_of(pj, name):
    h = pj["h"]
    d = client.get("/api/projects", headers=h).json()
    p = next((x for x in d["managed"] + d["discovered"] if x["name"] == name), None)
    assert p, name
    if p["id"] is None:
        client.post("/api/projects/import", headers=h, json={"paths": [p["path"]]})
        p = next(x for x in client.get("/api/projects", headers=h).json()["managed"] if x["name"] == name)
    return p["id"]


def test_files_tree_read_write_history(pj):
    h = pj["h"]
    pid = pid_of(pj, "site")
    t = client.get(f"/api/projects/{pid}/tree", headers=h).json()["entries"]
    assert {"path": "docker-compose.yml", "type": "file", "size": len(COMPOSE_SITE), "env": False} in t and any(e["path"] == ".env" and e["env"] for e in t)
    r = client.get(f"/api/projects/{pid}/file", params={"rel": "docker-compose.yml"}, headers=h).json()
    assert r["content"] == COMPOSE_SITE and len(r["hash"]) == 16
    new = COMPOSE_SITE.replace("nginx:alpine", "nginx:1.27")
    w = client.put(f"/api/projects/{pid}/file", headers=h, json={"rel": "docker-compose.yml", "content": new, "baseHash": r["hash"]})
    assert w.status_code == 200 and (pj["base"] / "site" / "docker-compose.yml").read_text() == new
    stale = client.put(f"/api/projects/{pid}/file", headers=h, json={"rel": "docker-compose.yml", "content": "x: 1", "baseHash": r["hash"]})
    assert stale.status_code == 409 and "changé" in stale.json()["detail"]                      # quelqu'un d'autre l'a modifié entre-temps
    hist = client.get(f"/api/projects/{pid}/history", params={"rel": "docker-compose.yml"}, headers=h).json()["versions"]
    assert len(hist) == 1
    old = client.get(f"/api/projects/{pid}/history/{hist[0]['vid']}", params={"rel": "docker-compose.yml"}, headers=h).json()
    assert old["content"] == COMPOSE_SITE                                                       # la version d'avant est gardée
    c = client.put(f"/api/projects/{pid}/file", headers=h, json={"rel": "conf/app.conf", "content": "a=1\n", "create": True})
    assert c.status_code == 200 and (pj["base"] / "site" / "conf" / "app.conf").read_text() == "a=1\n"
    assert client.put(f"/api/projects/{pid}/file", headers=h, json={"rel": "conf/app.conf", "content": "b", "create": True}).status_code == 409
    assert client.put(f"/api/projects/{pid}/file", headers=h, json={"rel": "absent.txt", "content": "b"}).status_code == 404
    env = client.put(f"/api/projects/{pid}/file", headers=h, json={"rel": ".env.new", "content": "K=v\n", "create": True})
    assert env.status_code == 200 and oct((pj["base"] / "site" / ".env.new").stat().st_mode & 0o777) == "0o600"   # un .env neuf n'est lisible que par son propriétaire
    assert client.delete(f"/api/projects/{pid}/file", params={"rel": "docker-compose.yml"}, headers=h).status_code == 409    # le compose ne se supprime pas d'ici
    assert client.delete(f"/api/projects/{pid}/file", params={"rel": "conf/app.conf"}, headers=h).status_code == 200
    assert not (pj["base"] / "site" / "conf" / "app.conf").exists()
    (pj["base"] / "site" / "docker-compose.yml").write_text(COMPOSE_SITE)                       # remise en état pour les tests suivants


def test_file_paths_are_confined_to_the_project(pj):
    h, base = pj["h"], pj["base"]
    pid = pid_of(pj, "site")
    (base / "secret.txt").write_text("hors du projet")
    (base / "site" / "lien").symlink_to(base / "secret.txt")
    (base / "site" / "bin.dat").write_bytes(b"\x00\x01\x02" * 100)
    (base / "site" / "gros.txt").write_text("x" * 1_100_000)
    for rel in ("../secret.txt", "/etc/passwd", ".git/config", "a/../../secret.txt", "a\\b", "", "a//b", "./x"):
        for call in (lambda r: client.get(f"/api/projects/{pid}/file", params={"rel": r}, headers=h),
                     lambda r: client.put(f"/api/projects/{pid}/file", headers=h, json={"rel": r, "content": "x", "create": True}),
                     lambda r: client.delete(f"/api/projects/{pid}/file", params={"rel": r}, headers=h)):
            assert call(rel).status_code in (403, 404, 422), rel
    assert client.get(f"/api/projects/{pid}/file", params={"rel": "lien"}, headers=h).status_code == 403          # lien symbolique qui sort du dossier
    assert client.put(f"/api/projects/{pid}/file", headers=h, json={"rel": "lien", "content": "x"}).status_code == 403
    assert (base / "secret.txt").read_text() == "hors du projet"
    assert client.get(f"/api/projects/{pid}/file", params={"rel": "bin.dat"}, headers=h).status_code == 415
    assert client.get(f"/api/projects/{pid}/file", params={"rel": "gros.txt"}, headers=h).status_code == 413
    assert client.put(f"/api/projects/{pid}/file", headers=h, json={"rel": "x.txt", "content": "y" * 1_100_000, "create": True}).status_code == 413
    for n in ("lien", "bin.dat", "gros.txt"):
        (base / "site" / n).unlink()


def test_env_files_need_their_own_right():
    from app.routes_projects import _need_file

    class Ctx:
        def __init__(self, caps): self.caps, self.asked = caps, []
        def need(self, c):
            self.asked.append(c)
            if c not in self.caps:
                raise PermissionError(c)
    c = Ctx({"projects.files", "projects.edit"})
    _need_file(c, "docker-compose.yml", False)
    _need_file(c, "a/b.conf", True)
    for rel, write in ((".env", False), ("sub/.env.prod", True)):
        with pytest.raises(PermissionError):
            _need_file(c, rel, write)
    assert c.asked[-1] == "projects.env"


def test_validate_compose(pj, fakecli):
    h = pj["h"]
    pid = pid_of(pj, "site")
    bad = client.post(f"/api/projects/{pid}/validate", headers=h, json={"content": "services: [oops: {"}).json()
    assert bad["ok"] is False and "YAML" in bad["message"]
    ok = client.post(f"/api/projects/{pid}/validate", headers=h, json={"content": COMPOSE_SITE}).json()
    assert ok["ok"] is True and ok["services"] == ["web", "worker"] and ok["full"] is True


def test_compose_actions_run_as_jobs(pj, fakecli):
    h = pj["h"]
    pid = pid_of(pj, "site")
    assert client.post(f"/api/projects/{pid}/compose", headers=h, json={"action": "rm -rf /"}).status_code == 422
    assert client.post(f"/api/projects/{pid}/compose", headers=h, json={"action": "up", "services": ["nope"]}).status_code == 422
    r = client.post(f"/api/projects/{pid}/compose", headers=h, json={"action": "up", "build": True, "services": ["web"]})
    assert r.status_code == 200
    jid = r.json()["job"]
    assert client.post(f"/api/projects/{pid}/compose", headers=h, json={"action": "pull"}).status_code == 409        # une seule commande à la fois par projet
    for _ in range(40):
        j = client.get(f"/api/projects/{pid}/jobs/{jid}", headers=h).json()
        if j["done"]:
            break
        time.sleep(0.1)
    assert j["done"] and j["code"] == 0
    line = " ".join(j["lines"])
    assert "compose --project-directory" in line and "up -d --remove-orphans --build web" in line and str(pj["base"] / "site" / "docker-compose.yml") in line
    assert client.get(f"/api/projects/{pid}/jobs/zzzz", headers=h).status_code == 404
    ok = client.post(f"/api/projects/{pid}/compose", headers=h, json={"action": "down"}).json()["job"]
    time.sleep(1.2)
    assert client.get(f"/api/projects/{pid}/jobs/{ok}", headers=h).json()["code"] == 0


def test_destroy_project_directory(pj):
    h, base = pj["h"], pj["base"]
    pid = pid_of(pj, "site")
    assert client.post(f"/api/projects/{pid}/destroy", headers=h, json={"confirm": "site"}).status_code == 409        # ses conteneurs tournent
    (base / "todel").mkdir()
    (base / "todel" / "docker-compose.yml").write_text("services: {a: {image: x}}\n")
    (base / "todel" / "data.txt").write_text("1")
    pid2 = pid_of(pj, "todel")
    assert client.post(f"/api/projects/{pid2}/destroy", headers=h, json={"confirm": "autre"}).status_code == 409      # nom à retaper
    assert (base / "todel").exists()
    assert client.post(f"/api/projects/{pid2}/destroy", headers=h, json={"confirm": "todel"}).status_code == 200
    assert not (base / "todel").exists() and client.get(f"/api/projects/{pid2}", headers=h).status_code == 404
    with pytest.raises(Exception) as e:
        project_ops.p_destroy({"dir": str(base), "confirm": base.name})
    assert getattr(e.value, "status", 0) in (409, 403, 404)                                                            # jamais une racine


def test_labels_conversion_both_ways(pj):
    h, base = pj["h"], pj["base"]
    (base / "shop").mkdir(exist_ok=True)
    (base / "shop" / "docker-compose.yml").write_text(COMPOSE_SHOP)
    pid = pid_of(pj, "shop")
    r = client.get(f"/api/projects/{pid}/labels", headers=h).json()
    (svc,) = r["services"]
    kinds = {(i["kind"], i["name"]): i["spec"] for i in svc["items"]}
    assert kinds[("routers", "shop")] == {"rule": "Host(`shop.example.org`)", "entryPoints": ["websecure"], "tls": {"certResolver": "le"}, "middlewares": ["auth@file", "secure"], "service": "shop"}
    assert kinds[("services", "shop")] == {"loadBalancer": {"servers": [{"url": "http://shop-web:8080"}]}}
    assert kinds[("middlewares", "secure")] == {"headers": {"stsSeconds": 31536000}, "ipAllowList": {"sourceRange": ["10.0.0.0/8", "192.168.0.0/16"]}}
    assert "shop.example.org" in svc["yaml"] and "http:" in svc["yaml"]
    assert any("auth@file" in c["text"] and c["level"] == "err" for c in r["checks"])                              # middleware introuvable dans Traefik
    # appliquer : écrit un fichier zz-… dans Traefik, une seule fois
    a = client.post(f"/api/projects/{pid}/labels/apply", headers=h, json={"service": "web"})
    assert a.status_code == 200 and a.json()["file"] == "zz-shop-web.yml"
    text = (Path(settings.dynamic_dir) / "zz-shop-web.yml").read_text()
    assert "shop.example.org" in text and "http://shop-web:8080" in text
    assert client.post(f"/api/projects/{pid}/labels/apply", headers=h, json={"service": "web"}).status_code == 409   # existe déjà
    assert client.post(f"/api/projects/{pid}/labels/apply", headers=h, json={"service": "nope"}).status_code == 404
    # et dans l'autre sens : un site de Traefik → des labels
    back = client.post("/api/projects/labels/from-site", headers=h, json={"router": "shop"}).json()
    assert back["labels"]["traefik.http.routers.shop.rule"] == "Host(`shop.example.org`)" and back["labels"]["traefik.http.routers.shop.entrypoints"] == "websecure"
    assert back["labels"]["traefik.http.routers.shop.tls.certresolver"] == "le" and back["labels"]["traefik.http.services.shop.loadbalancer.server.port"] == "8080"
    assert "traefik.http.routers.shop.rule=" in back["text"] and any("adresse de la cible" in w for w in back["warnings"])
    assert client.post("/api/projects/labels/from-site", headers=h, json={"router": "nope"}).status_code == 404
    (Path(settings.dynamic_dir) / "zz-shop-web.yml").unlink()


def test_labels_yaml_pure_functions():
    c = labels_yaml.labels_to_items({"traefik.http.routers.a.rule": "Host(`a.fr`)", "traefik.http.routers.a.tls": "true", "traefik.http.services.a.loadbalancer.server.port": "80",
                                     "traefik.http.middlewares.auth.basicauth.users": "u:$$2y$$05$$abc", "traefik.tcp.routers.x.rule": "HostSNI(`*`)", "traefik.enable": "true", "traefik.http.bad": "1"}, target="appc")
    by = {(i["kind"], i["name"]): i["spec"] for i in c["items"]}
    assert by[("routers", "a")]["tls"] == {} and by[("routers", "a")]["service"] == "a"                           # tls=true → tls: {} ; service par défaut
    assert by[("middlewares", "auth")] == {"basicAuth": {"users": ["u:$2y$05$abc"]}}                               # « $$ » du compose devient « $ »
    assert any("hors HTTP" in w for w in c["warnings"]) and any("non reconnu" in w for w in c["warnings"])
    back = labels_yaml.items_to_labels(c["items"])
    assert back["labels"]["traefik.http.middlewares.auth.basicauth.users"] == "u:$$2y$$05$$abc"                    # et inversement
    assert back["labels"]["traefik.http.routers.a.tls"] == "true" and back["labels"]["traefik.http.services.a.loadbalancer.server.port"] == "80"
    multi = labels_yaml.items_to_labels([{"proto": "http", "kind": "services", "name": "m", "spec": {"loadBalancer": {"servers": [{"url": "http://a:1"}, {"url": "https://b:2"}]}}}])
    assert any("2 serveurs" in w for w in multi["warnings"])
    assert labels_yaml.items_to_labels([{"proto": "tcp", "kind": "routers", "name": "t", "spec": {}}])["warnings"]
    norule = labels_yaml.check_labels({"s": {"traefik.http.routers.r.entrypoints": "web"}}, [{"proto": "http", "kind": "routers", "name": "r", "spec": {}}])
    assert any("pas de règle" in x["text"] for x in norule) and any("aussi dans un fichier" in x["text"] for x in norule)
    dup = labels_yaml.check_labels({"a": {"traefik.http.routers.r.rule": "Host(`x`)"}, "b": {"traefik.http.routers.r.rule": "Host(`y`)"}}, [])
    assert any("aussi par le service" in x["text"] for x in dup)
    assert labels_yaml.check_labels({"a": {"traefik.enable": "false", "traefik.http.routers.r.entrypoints": "web"}}, []) == []


def test_labels_checks_warn_when_traefik_ignores_docker_labels(pj):
    h, base = pj["h"], pj["base"]
    (base / "lab").mkdir(exist_ok=True)
    (base / "lab" / "docker-compose.yml").write_text("services:\n  a:\n    image: x\n    labels:\n      traefik.http.routers.a.rule: Host(`a.fr`)\n")
    pid = pid_of(pj, "lab")
    cmd = pj["fake"].containers["dddddddddddd4444"]
    old = cmd["Cmd"]
    try:
        r = client.get(f"/api/projects/{pid}/labels", headers=h).json()                                           # fournisseur Docker actif, mais pas d'exposition par défaut
        assert any("traefik.enable=true" in c["text"] and c["service"] == "a" for c in r["checks"])
        cmd["Cmd"] = ["--providers.file.directory=/etc/traefik/dynamic"]
        r = client.get(f"/api/projects/{pid}/labels", headers=h).json()
        assert r["checks"][0]["level"] == "err" and "ne lit pas les labels Docker" in r["checks"][0]["text"]
    finally:
        cmd["Cmd"] = old


# =========================================================================== étape 5 : Git (vrais dépôts locaux)
import subprocess

GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT}, timeout=30)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def wait_job(h, pid, jid, path="jobs", limit=60):
    for _ in range(limit):
        j = client.get(f"/api/projects/{pid}/{path}/{jid}" if path == "jobs" else f"/api/projects/{path}/{jid}", headers=h).json()
        if j["done"]:
            return j
        time.sleep(0.2)
    raise AssertionError("commande trop longue")


@pytest.fixture()
def repo(pj):
    base = pj["base"]
    origin, src = base.parent / (base.name + "-origin.git"), base.parent / (base.name + "-src")
    for p in (origin, src, base / "gitproj"):
        subprocess.run(["rm", "-rf", str(p)])
    git(base.parent, "init", "-q", "--bare", "-b", "main", str(origin))
    git(base.parent, "clone", "-q", str(origin), str(src))
    (src / "docker-compose.yml").write_text("services: {a: {image: nginx:1}}\n")
    git(src, "add", "-A"); git(src, "commit", "-q", "-m", "premier"); git(src, "push", "-q", "origin", "HEAD:main")
    git(base, "clone", "-q", str(origin), str(base / "gitproj"))
    return {"origin": origin, "src": src, "dir": base / "gitproj"}


def test_git_status_fetch_pull_and_rollback(pj, repo):
    h = pj["h"]
    pid = pid_of(pj, "gitproj")
    s = client.get(f"/api/projects/{pid}/git", headers=h).json()
    st = s["status"]
    assert st["isRepo"] and st["branch"] == "main" and st["dirty"] == 0 and st["behind"] == 0 and st["head"]["subject"] == "premier" and st["upstream"] == "origin/main"
    assert st["remote"] == str(repo["origin"]) and [c["subject"] for c in s["commits"]] == ["premier"] and s["hasToken"] is False
    first = s["commits"][0]["sha"]
    (repo["src"] / "docker-compose.yml").write_text("services: {a: {image: nginx:2}}\n")
    git(repo["src"], "commit", "-qam", "deuxième"); git(repo["src"], "push", "-q", "origin", "HEAD:main")
    assert client.get(f"/api/projects/{pid}/git", headers=h).json()["status"]["behind"] == 0                  # sans « fetch », on ne sait pas encore
    assert client.post(f"/api/projects/{pid}/git/fetch", headers=h).json()["behind"] == 1                      # après interrogation du dépôt : en retard d'un commit
    j = client.post(f"/api/projects/{pid}/git/run", headers=h, json={"action": "pull"}).json()["job"]
    done = wait_job(h, pid, j)
    assert done["code"] == 0 and (repo["dir"] / "docker-compose.yml").read_text().endswith("nginx:2}}\n")
    st = client.get(f"/api/projects/{pid}/git", headers=h).json()
    assert st["status"]["behind"] == 0 and [c["subject"] for c in st["commits"]] == ["deuxième", "premier"]
    # retour arrière : on se met sur l'ancien commit (tête détachée), puis on revient sur la branche
    (repo["dir"] / "docker-compose.yml").write_text("services: {a: {image: local-edit}}\n")
    dirty = client.post(f"/api/projects/{pid}/git/run", headers=h, json={"action": "checkout", "ref": first})
    assert dirty.status_code == 409 and "modifications locales" in dirty.json()["detail"]                       # jamais d'écrasement de vos modifications
    git(repo["dir"], "checkout", "--", "docker-compose.yml")
    j = client.post(f"/api/projects/{pid}/git/run", headers=h, json={"action": "checkout", "ref": first}).json()["job"]
    assert wait_job(h, pid, j)["code"] == 0 and "nginx:1" in (repo["dir"] / "docker-compose.yml").read_text()
    assert client.get(f"/api/projects/{pid}/git", headers=h).json()["status"]["detached"] is True
    j = client.post(f"/api/projects/{pid}/git/run", headers=h, json={"action": "checkout", "ref": "main"}).json()["job"]
    assert wait_job(h, pid, j)["code"] == 0 and client.get(f"/api/projects/{pid}/git", headers=h).json()["status"]["branch"] == "main"


def test_git_pull_never_overwrites_local_work(pj, repo):
    h = pj["h"]
    pid = pid_of(pj, "gitproj")
    (repo["dir"] / "docker-compose.yml").write_text("services: {a: {image: ma-modif}}\n")
    (repo["src"] / "docker-compose.yml").write_text("services: {a: {image: autre-modif}}\n")
    git(repo["src"], "commit", "-qam", "conflit"); git(repo["src"], "push", "-q", "origin", "HEAD:main")
    j = client.post(f"/api/projects/{pid}/git/run", headers=h, json={"action": "pull"}).json()["job"]
    done = wait_job(h, pid, j)
    assert done["code"] != 0                                                                                    # git refuse (--ff-only, fichier modifié)
    assert (repo["dir"] / "docker-compose.yml").read_text() == "services: {a: {image: ma-modif}}\n"             # votre travail est intact


def test_git_refs_urls_and_names_are_validated(pj, repo):
    h = pj["h"]
    pid = pid_of(pj, "gitproj")
    for ref in ("--upload-pack=x", "a..b", "", "-x", "a b", "$(id)"):
        assert client.post(f"/api/projects/{pid}/git/run", headers=h, json={"action": "checkout", "ref": ref}).status_code == 422, ref
    assert client.post(f"/api/projects/{pid}/git/run", headers=h, json={"action": "reset"}).status_code == 422
    for url in ("--upload-pack=touch /tmp/pwn", "ext::sh -c id", "/etc/passwd", "file:///etc", "ftp://x/y", "https://a b/c", "x"):
        assert client.post("/api/projects/git/clone", headers=h, json={"url": url, "name": "nouveau"}).status_code == 422, url
    for name in ("../x", ".cache", "a b", "", "a/b"):
        assert client.post("/api/projects/git/clone", headers=h, json={"url": "https://github.com/a/b.git", "name": name}).status_code == 422, name
    assert client.post("/api/projects/git/clone", headers=h, json={"url": "https://github.com/a/b.git", "name": "gitproj"}).status_code == 409   # existe déjà


def test_git_clone_into_the_root(pj, repo, monkeypatch):
    h, base = pj["h"], pj["base"]
    subprocess.run(["rm", "-rf", str(base / "clone1")])
    monkeypatch.setattr(settings, "git_allow_local", True)
    r = client.post("/api/projects/git/clone", headers=h, json={"url": str(repo["origin"]), "name": "clone1", "branch": "main"})
    assert r.status_code == 200, r.text
    for _ in range(60):
        j = client.get(f"/api/projects/clone-jobs/{r.json()['job']}", headers=h).json()
        if j["done"]:
            break
        time.sleep(0.2)
    assert j["code"] == 0 and (base / "clone1" / "docker-compose.yml").is_file()
    assert "clone1" in [p["name"] for p in client.get("/api/projects", headers=h).json()["discovered"]]       # apparaît dans « Découverts »
    monkeypatch.setattr(settings, "git_allow_local", False)
    assert client.post("/api/projects/git/clone", headers=h, json={"url": str(repo["origin"]), "name": "clone2"}).status_code == 422    # local interdit hors essais


def test_git_credentials_are_encrypted_and_never_leak(pj, repo):
    h = pj["h"]
    pid = pid_of(pj, "gitproj")
    assert client.put(f"/api/projects/{pid}/git/credentials", headers=h, json={"username": "bob", "token": "ghp_SECRET123"}).status_code == 200
    r = client.get(f"/api/projects/{pid}/git", headers=h)
    assert r.json()["hasToken"] is True and r.json()["username"] == "bob" and "ghp_SECRET123" not in r.text
    from app import db
    raw = db.one("SELECT token FROM project_git WHERE project_id=?", (pid,))["token"]
    assert "ghp_SECRET123" not in raw                                                                           # chiffré en base
    env = project_ops._git_env(repo["dir"], "ghp_SECRET123", "bob")
    import base64
    i = next(n for n in range(int(env["GIT_CONFIG_COUNT"])) if env[f"GIT_CONFIG_KEY_{n}"] == "http.extraHeader")
    assert base64.b64decode(env[f"GIT_CONFIG_VALUE_{i}"].split()[-1]).decode() == "bob:ghp_SECRET123"
    assert "protocol.ext.allow" in [env[f"GIT_CONFIG_KEY_{n}"] for n in range(int(env["GIT_CONFIG_COUNT"]))]                      # transport « ext » interdit
    assert "ghp_SECRET123" not in " ".join(f"{k}={v}" for k, v in env.items() if not k.startswith("GIT_CONFIG_VALUE"))
    assert project_ops._strip_url("https://bob:tok@github.com/a/b.git") == "https://github.com/a/b.git"
    assert client.delete(f"/api/projects/{pid}/git/credentials", headers=h).status_code == 200
    assert client.get(f"/api/projects/{pid}/git", headers=h).json()["hasToken"] is False


def test_non_git_project_is_reported(pj):
    pid = pid_of(pj, "blog")
    s = client.get(f"/api/projects/{pid}/git", headers=pj["h"]).json()
    assert s["status"] == {"isRepo": False} and s["commits"] == []


# =========================================================================== étape 5 : suivi des images et notifications
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

from app import imagecheck, registry

OLD, NEW = "sha256:" + "a" * 64, "sha256:" + "b" * 64


@pytest.fixture()
def fakereg(monkeypatch):
    state = {"digest": NEW, "calls": []}

    def http(method, url, headers=None):
        state["calls"].append((method, url, dict(headers or {})))
        if url.startswith("https://auth.docker.io/token"):
            return 200, {}, b'{"token": "tok"}'
        if "registry-1.docker.io/v2/library/nginx/manifests/alpine" in url:
            if (headers or {}).get("Authorization") != "Bearer tok":
                return 401, {"www-authenticate": 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="repository:library/nginx:pull"'}, b""
            return 200, {"docker-content-digest": state["digest"]}, b""
        if "ghcr.io/v2/acme/secret" in url:
            return 403, {}, b""
        if "/manifests/nope" in url:
            return 404, {}, b""
        return 500, {}, b""

    monkeypatch.setattr(registry, "_http", http)
    monkeypatch.setattr(registry, "public_host", lambda h: not h.startswith(("internal", "127.", "10.", "localhost")))
    return state


def test_parse_ref_and_ssrf_guard():
    assert registry.parse_ref("nginx")["key"] == "docker.io/library/nginx:latest"
    assert registry.parse_ref("user/app:1.2") == {"registry": "docker.io", "repo": "user/app", "tag": "1.2", "digest": "", "key": "docker.io/user/app:1.2"}
    assert registry.parse_ref("ghcr.io/org/app:tag")["registry"] == "ghcr.io" and registry.parse_ref("registry.example.com:5000/a/b")["registry"] == "registry.example.com:5000"
    p = registry.parse_ref("nginx@" + OLD)
    assert p["digest"] == OLD and p["tag"] == ""
    for bad in ("", "${IMAGE}", "Nginx:Bad Tag", "a b", "http://x/y", "-x"):
        assert registry.parse_ref(bad) is None, bad
    assert registry.public_host("127.0.0.1") is False and registry.public_host("10.1.2.3") is False and registry.public_host("[::1]".strip("[]")) is False
    assert registry.status_of(registry.parse_ref("nginx"), {"id": "x", "digests": {OLD}}, NEW, "") == "update"
    assert registry.status_of(registry.parse_ref("nginx"), {"id": "x", "digests": {OLD}}, OLD, "") == "latest"
    assert registry.status_of(registry.parse_ref("nginx"), {"id": "x", "digests": set()}, NEW, "") == "unknown"        # image construite en local : on ne peut pas comparer
    assert registry.status_of(registry.parse_ref("nginx"), None, NEW, "") == "absent"


def test_remote_digest_follows_the_token_dance_and_refuses_private_hosts(fakereg):
    assert registry.remote_digest(registry.parse_ref("nginx:alpine")) == (NEW, "")
    assert any(c[0] == "GET" and "auth.docker.io" in c[1] for c in fakereg["calls"])                                    # jeton anonyme demandé
    assert registry.remote_digest(registry.parse_ref("ghcr.io/acme/secret:1"))[1].startswith("Image privée")
    assert "introuvable" in registry.remote_digest(registry.parse_ref("nginx:nope"))[1]
    before = len(fakereg["calls"])
    assert "privé" in registry.remote_digest(registry.parse_ref("internal.corp:5000/app:1"))[1] and len(fakereg["calls"]) == before    # jamais d'appel vers l'interne


def test_image_status_for_a_project(pj, fakereg):
    h, base = pj["h"], pj["base"]
    (base / "imgs").mkdir(exist_ok=True)
    (base / "imgs" / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx:alpine\n  job:\n    image: python:3.12\n  pin:\n    image: nginx@" + OLD + "\n  var:\n    image: ${IMG}\n  mine:\n    build: .\n")
    pid = pid_of(pj, "imgs")
    r = client.get(f"/api/projects/{pid}/images", headers=h).json()
    by = {i["service"]: i for i in r["images"]}
    assert set(by) == {"web", "job", "pin", "var"} and by["web"]["status"] == "unknown" and by["web"]["checkedAt"] is None     # pas encore interrogé ; « build » sans image : ignoré
    assert by["pin"]["status"] == "pinned" and by["var"]["status"] == "unresolved" and by["job"]["status"] == "absent"
    c = client.post(f"/api/projects/{pid}/images/check", headers=h).json()
    by = {i["service"]: i for i in c["images"]}
    assert by["web"]["status"] == "update" and by["web"]["remote"] == "bbbbbbbbbbbb" and by["web"]["checkedAt"]                # nouvelle version au registre
    fakereg["digest"] = OLD
    assert {i["service"]: i for i in client.post(f"/api/projects/{pid}/images/check", headers=h).json()["images"]}["web"]["status"] == "latest"
    assert client.get(f"/api/projects/{pid}/images", headers=h).json()["images"][0]["status"] in ("latest", "absent", "pinned")


def test_image_tracking_notifies_once_per_new_version(pj, fakereg):
    from app import crypto, db, host_ops
    h, base = pj["h"], pj["base"]
    got = []

    class Hook(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])))); self.send_response(200); self.end_headers()
    srv = HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        (base / "watch").mkdir(exist_ok=True)
        (base / "watch" / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx:alpine\n")
        pid = pid_of(pj, "watch")
        nid = next(n["id"] for n in client.get("/api/nodes", headers=h).json() if n["kind"] == "local")
        db.run("UPDATE nodes SET webhook_url=? WHERE id=?", (crypto.enc(f"http://127.0.0.1:{srv.server_address[1]}/hook"), nid))
        fakereg["digest"] = NEW
        node = dict(db.one("SELECT * FROM nodes WHERE id=?", (nid,)))
        assert imagecheck.check_node(node, host_ops.handle) == 0                                               # rien n'est suivi : rien ne part
        assert client.put(f"/api/projects/{pid}/images/track", headers=h, json={"enabled": True}).json() == {"track": True}
        assert client.get(f"/api/projects/{pid}/images", headers=h).json()["track"] is True
        assert imagecheck.check_node(node, host_ops.handle) == 1                                               # nouvelle version : une notification
        time.sleep(0.5)
        assert len(got) == 1 and "Mise à jour disponible" in got[0]["text"] and got[0]["project"] == "watch" and got[0]["image"] == "nginx:alpine"
        assert imagecheck.check_node(node, host_ops.handle) == 0                                               # la même version ne prévient pas deux fois
        fakereg["digest"] = "sha256:" + "c" * 64
        assert imagecheck.check_node(node, host_ops.handle) == 1                                               # une autre version, si
        assert client.put(f"/api/projects/{pid}/images/track", headers=h, json={"enabled": False}).json() == {"track": False}
    finally:
        db.run("UPDATE nodes SET webhook_url='' WHERE id=?", (nid,))
        srv.shutdown()
