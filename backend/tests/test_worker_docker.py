"""Un worker « Docker seul » de bout en bout : un vrai hub (uvicorn), le vrai code du worker, un faux démon Docker.
Les conteneurs, les journaux, les actions, les projets et les commandes compose passent par le canal chiffré ; le terminal et les ordres Traefik sont refusés ;
et la liaison entre nœuds relie un conteneur de ce worker au Traefik du hub (adresses + ports publiés)."""
import json
import os
import tempfile
import time
from pathlib import Path

import pytest
import websockets.sync.client as wsc
from websockets.exceptions import ConnectionClosed

from app import accesslog, db, linkage, project_ops
from app.config import settings
from test_worker import Api, PW, RunningWorker, hub, wait_for  # noqa: F401  (hub : fixture)
from fakedocker import FakeDocker

COMPOSE = "services:\n  web:\n    image: nginx:alpine\n    container_name: web-1\n"


@pytest.fixture(scope="module")
def dw(hub):
    d = tempfile.mkdtemp(prefix="dw")
    fake = FakeDocker(f"{d}/d.sock")
    base = Path(tempfile.mkdtemp(prefix="dwp")).resolve()
    (base / "site").mkdir()
    (base / "site" / "docker-compose.yml").write_text(COMPOSE)
    fake.containers["aaaaaaaaaaaa1111"]["Labels"]["com.docker.compose.project.working_dir"] = str(base / "site")
    old = (settings.docker_socket, settings.project_roots, os.environ.get("WORKER_MODULES"))
    settings.docker_socket, settings.project_roots = fake.path, [str(base)]
    os.environ["WORKER_MODULES"] = "docker"
    root = Api(hub, "root", "admin-password-1")
    node = root.post("/nodes", json={"name": "Machine Docker"}).json()
    w = RunningWorker(hub, node["token"])
    wait_for(lambda: next(n["connection"] for n in root.get("/nodes").json() if n["id"] == node["id"]) == "online")
    local = next(n["id"] for n in root.get("/nodes").json() if n["kind"] == "local")
    yield {"root": root, "nid": node["id"], "local": local, "fake": fake, "base": base, "hub": hub, "w": w}
    w.stop()
    settings.docker_socket, settings.project_roots = old[0], old[1]
    if old[2] is None:
        os.environ.pop("WORKER_MODULES", None)
    else:
        os.environ["WORKER_MODULES"] = old[2]
    fake.close()


def test_worker_announces_the_docker_module(dw):
    me = next(n for n in dw["root"].get("/nodes").json() if n["id"] == dw["nid"])
    assert me["modules"] == ["docker"] and me["connection"] == "online"


def test_containers_logs_and_actions_go_through_the_worker(dw):
    r, nid = dw["root"], dw["nid"]
    lst = r.get("/docker/containers", node=nid).json()["containers"]
    assert {c["name"] for c in lst} >= {"web-1", "blog", "traefik"}
    assert r.get("/docker/info", node=nid).json()["hostname"] == "fakehost"
    lg = r.get("/docker/containers/web-1/logs", node=nid, params={"tail": 20}).json()
    assert [l["m"] for l in lg["lines"]] == ["premier", "deuxième", "erreur"]
    assert r.post("/docker/containers/web-1/action", node=nid, json={"action": "stop"}).json() == {"ok": True, "unchanged": False}
    assert dw["fake"].containers["aaaaaaaaaaaa1111"]["State"] == "exited"                                   # l'ordre est bien arrivé jusqu'au démon du worker
    assert r.post("/docker/containers/web-1/action", node=nid, json={"action": "start"}).status_code == 200
    assert r.get("/docker/stats", node=nid).json()["stats"]["aaaaaaaaaaaa"]["cpu"] == 20.0
    assert r.get("/docker/images", node=nid).status_code == 200 and r.get("/docker/networks", node=nid).status_code == 200


def test_traefik_orders_are_refused_by_a_docker_only_node(dw):
    r, nid = dw["root"], dw["nid"]
    assert r.get("/traefik/overview", node=nid).status_code == 409                                       # le hub ne lui envoie jamais d'ordre Traefik
    f = r.get("/obs/features", node=nid).json()
    assert not any(f[k]["enabled"] for k in ("certs", "metrics", "alerts", "ips", "bans"))
    assert r.get("/docker/containers", node=dw["local"]).status_code == 409                              # et le nœud du hub, sans DOCKER_MANAGE, ne gère pas Docker


def test_projects_files_and_compose_jobs_go_through_the_worker(dw, monkeypatch, tmp_path):
    r, nid, base = dw["root"], dw["nid"], dw["base"]
    d = r.get("/projects", node=nid).json()
    assert d["roots"] == [str(base)] and [p["name"] for p in d["discovered"]] == ["site"]
    assert d["discovered"][0]["status"] == "running"                                                      # rattaché à ses conteneurs via le worker
    assert r.post("/projects/import", node=nid, json={"paths": [str(base / "site")]}).json()["done"] == ["site"]
    pid = r.get("/projects", node=nid).json()["managed"][0]["id"]
    assert r.get(f"/projects/{pid}/file", node=nid, params={"rel": "docker-compose.yml"}).json()["content"] == COMPOSE
    h = r.get(f"/projects/{pid}/file", node=nid, params={"rel": "docker-compose.yml"}).json()["hash"]
    new = COMPOSE.replace("alpine", "1.27")
    assert r.put(f"/projects/{pid}/file", node=nid, json={"rel": "docker-compose.yml", "content": new, "baseHash": h}).status_code == 200
    assert (base / "site" / "docker-compose.yml").read_text() == new                                      # écrit sur la machine du worker
    assert r.get(f"/projects/{pid}/git", node=nid).json()["status"] == {"isRepo": False}
    # commande compose : la tâche tourne sur le worker, le hub relève sa sortie par le canal
    script = tmp_path / "docker"
    script.write_text('#!/bin/sh\necho "ARGS: $@"\nexit 0\n')
    script.chmod(0o755)
    real = project_ops.shutil.which
    monkeypatch.setattr(project_ops.shutil, "which", lambda n, *a, **k: str(script) if n == "docker" else real(n, *a, **k))
    jid = r.post(f"/projects/{pid}/compose", node=nid, json={"action": "up"}).json()["job"]
    j = wait_for(lambda: (lambda x: x if x["done"] else None)(r.get(f"/projects/{pid}/jobs/{jid}", node=nid).json()))
    assert j["code"] == 0 and any("up -d --remove-orphans" in l for l in j["lines"])
    assert r.post(f"/projects/{pid}/compose", node=nid, json={"action": "rm"}).status_code == 422
    assert r.post("/projects/import", node=nid, json={"paths": ["/etc"]}).status_code == 403              # les racines du worker bornent ce qu'il lit
    (base / "site" / "docker-compose.yml").write_text(COMPOSE)


def test_terminal_is_refused_on_a_remote_node(dw):
    root = dw["root"]
    tok = root.h["Authorization"].split()[1]
    ws = wsc.connect(dw["hub"].replace("http://", "ws://") + "/api/docker/exec")
    ws.send(json.dumps({"token": tok, "node": dw["nid"], "container": "web-1"}))
    with pytest.raises(ConnectionClosed) as e:
        ws.recv(timeout=5)
    assert e.value.rcvd.code == 4501


def test_addresses_are_validated(dw):
    r, nid = dw["root"], dw["nid"]
    for bad in (["http://x"], ["a b"], ["x" * 300], ["10.0.0.1:8080"]):
        assert r.put(f"/nodes/{nid}", json={"addresses": bad}).status_code == 422, bad
    ok = r.put(f"/nodes/{nid}", json={"addresses": [" 10.9.9.9 ", "Machine-A.lan", "10.9.9.9", ""]}).json()
    assert ok["addresses"] == ["10.9.9.9", "machine-a.lan"]                                             # minuscules, sans doublon ni vide
    assert r.put(f"/nodes/{nid}", json={"addresses": []}).json()["addresses"] == []


def test_a_container_here_is_linked_to_the_traefik_of_another_node(dw):
    r, nid, local = dw["root"], dw["nid"], dw["local"]
    dyn = Path(settings.dynamic_dir) / "cross.yml"
    log = Path(tempfile.mkdtemp()) / "access.log"
    log.write_text("")
    old_log = settings.access_log
    dyn.write_text("http:\n  routers:\n    cross-site:\n      rule: Host(`cross.example.org`)\n      service: cross-svc\n  services:\n    cross-svc:\n      loadBalancer:\n        servers:\n          - url: http://10.9.9.9:8080\n"
                   "    other-svc:\n      loadBalancer:\n        servers:\n          - url: http://10.9.9.9:9999\n")
    for ip, hits, errs in (("45.1.1.1", 30, 3), ("45.2.2.2", 12, 0)):
        db.run("INSERT OR REPLACE INTO ip_paths(node_id,ip,path,host,hits,errors,last_seen) VALUES(?,?,?,?,?,?,?)", (local, ip, "/", "cross.example.org", hits, errs, time.time()))
    settings.access_log = str(log)
    try:
        # sans adresses connues pour la machine du worker : rien ne peut être relié
        assert r.get("/docker/containers", node=nid).json()["containers"][0]["links"] == [] or all(c["links"] == [] for c in r.get("/docker/containers", node=nid).json()["containers"])
        assert r.put(f"/nodes/{nid}", json={"addresses": ["10.9.9.9"]}).status_code == 200
        web = next(c for c in r.get("/docker/containers", node=nid).json()["containers"] if c["name"] == "web-1")
        local_name = next(n["name"] for n in r.get("/nodes").json() if n["id"] == local)
        assert [(l["router"], l["via"], l["sure"], l["node"]["name"], l["hosts"], l["port"]) for l in web["links"]] == [("cross-site", "address", True, local_name, ["cross.example.org"], 8080)]
        assert next(c for c in r.get("/docker/containers", node=nid).json()["containers"] if c["name"] == "blog")["links"] == []   # blog ne publie aucun port : rien à viser
        d = r.get("/docker/containers/web-1", node=nid).json()
        assert d["links"][0]["node"]["id"] == local
        assert d["access"]["hits"] == 42 and d["access"]["visitors"] == 2 and d["access"]["topIps"][0]["hits"] == 30      # statistiques lues dans le journal du nœud qui sert le site
        pid = r.post("/projects/import", node=nid, json={"paths": [str(dw["base"] / "site")]}).json() and r.get("/projects", node=nid).json()["managed"][0]
        assert pid["hosts"] == ["cross.example.org"] and pid["access"]["hits"] == 42                                       # et la carte du projet les montre
    finally:
        dyn.unlink(missing_ok=True)
        settings.access_log = old_log
        db.run("DELETE FROM ip_paths WHERE node_id=? AND host='cross.example.org'", (local,))
        r.put(f"/nodes/{nid}", json={"addresses": []})


def test_address_links_pure_function():
    items = [{"id": "http/routers/r", "proto": "http", "kind": "routers", "name": "r", "spec": {"rule": "Host(`a.fr`)", "service": "s"}},
             {"id": "http/services/s", "proto": "http", "kind": "services", "name": "s", "spec": {"loadBalancer": {"servers": [{"url": "https://Srv-A.lan:8443"}]}}}]
    c = {"name": "x", "published": [8443], "labels": {}}
    assert [l["port"] for l in linkage.address_links(c, items, ["srv-a.lan"])] == [8443]                    # hôte insensible à la casse
    assert linkage.address_links(c, items, ["autre.lan"]) == [] and linkage.address_links({**c, "published": [80]}, items, ["srv-a.lan"]) == []
    assert linkage.address_links({**c, "published": []}, items, ["srv-a.lan"]) == [] and linkage.address_links(c, items, []) == []
