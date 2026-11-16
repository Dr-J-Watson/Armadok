"""Docker d'un nœud : conteneurs, journaux, statistiques, images, réseaux, volumes, protections — contre un faux démon Docker (socket Unix)."""
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import caps, docker_api, docker_ops, nodes, worker, worker_proto as proto
from app.config import settings
from app.main import app
from app.traefik_store import Store
from fakedocker import FakeDocker

client = TestClient(app)


@pytest.fixture(scope="module")
def dk():
    d = tempfile.mkdtemp(prefix="fd")
    fake = FakeDocker(f"{d}/d.sock")
    old = (settings.docker_socket, settings.docker_manage)
    settings.docker_socket, settings.docker_manage = fake.path, True
    tok = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
    nid = next(n["id"] for n in client.get("/api/nodes", headers={"Authorization": f"Bearer {tok}"}).json() if n["kind"] == "local")
    yield {"h": {"Authorization": f"Bearer {tok}", "X-Node": nid}, "fake": fake, "nid": nid}
    settings.docker_socket, settings.docker_manage = old
    fake.close()


def test_docker_is_off_unless_requested(dk):
    settings.docker_manage = False
    try:
        assert client.get("/api/docker/containers", headers=dk["h"]).status_code == 409      # socket présent, mais non demandé
        assert "docker" not in next(n for n in client.get("/api/nodes", headers=dk["h"]).json() if n["id"] == dk["nid"])["modules"]
    finally:
        settings.docker_manage = True
    assert "docker" in next(n for n in client.get("/api/nodes", headers=dk["h"]).json() if n["id"] == dk["nid"])["modules"]


def test_container_list_is_clean_and_linked_to_traefik(dk):
    r = client.get("/api/docker/containers", headers=dk["h"]).json()["containers"]
    by = {c["name"]: c for c in r}
    assert set(by) == {"web-1", "blog", "db", "traefik"}
    web = by["web-1"]
    assert web["ports"] == [{"ip": "0.0.0.0", "private": 80, "public": 8080, "type": "tcp"}]          # IPv4 et IPv6 ne font qu'une ligne
    assert web["project"] == "site" and web["service"] == "web" and web["health"] == "healthy" and web["state"] == "running"
    assert "secret.label" not in web["labels"] and web["networks"]["proxy"]["ip"] == "172.20.0.5"       # seuls les labels utiles sortent
    assert by["db"]["state"] == "exited" and by["traefik"]["protected"] == "traefik" and by["blog"]["protected"] == ""
    assert [(l["router"], l["hosts"], l["sure"]) for l in by["blog"]["links"]] == [("blog@docker", ["blog.example.org"], True)]
    assert by["db"]["links"] == []


def test_detail_never_leaks_env_values_and_gives_access_none_without_log(dk):
    d = client.get("/api/docker/containers/web-1", headers=dk["h"])
    assert d.status_code == 200
    body = d.json()
    assert body["envNames"] == ["PATH", "SECRET"] and "hunter2" not in d.text                         # noms seulement
    assert body["restart"] == "unless-stopped" and body["health"] == "healthy" and body["ports"][0]["public"] == 8080
    assert body["access"] is None and body["canExec"] is True                                         # pas de journal d'accès sur ce nœud de test
    assert client.get("/api/docker/containers/nope", headers=dk["h"]).status_code == 404
    assert client.get("/api/docker/containers/..%2Fx", headers=dk["h"]).status_code in (404, 422)


def test_actions_confirmations_and_audit(dk):
    h = dk["h"]
    assert client.post("/api/docker/containers/web-1/action", headers=h, json={"action": "stop"}).json() == {"ok": True, "unchanged": False}
    assert next(c for c in client.get("/api/docker/containers", headers=h).json()["containers"] if c["name"] == "web-1")["state"] == "exited"
    assert client.post("/api/docker/containers/web-1/action", headers=h, json={"action": "stop"}).json()["unchanged"] is True      # déjà arrêté
    assert client.post("/api/docker/containers/web-1/action", headers=h, json={"action": "start"}).status_code == 200
    assert client.post("/api/docker/containers/web-1/action", headers=h, json={"action": "rm -rf"}).status_code == 422
    # le conteneur de Traefik demande de retaper son nom
    r = client.post("/api/docker/containers/traefik/action", headers=h, json={"action": "restart"})
    assert r.status_code == 409 and "Retapez son nom" in r.json()["detail"]
    assert client.post("/api/docker/containers/traefik/action", headers=h, json={"action": "restart", "confirm": "traefik"}).status_code == 200
    log = client.get(f"/api/nodes/{dk['nid']}/audit", headers=h)
    if log.status_code == 200:
        assert "docker.stop" in log.text and "docker.restart" in log.text


def test_the_interface_container_cannot_stop_or_delete_itself(dk, monkeypatch):
    monkeypatch.setattr(docker_ops, "_self_ids", lambda: ("bbbbbbbbbbbb",))
    h = dk["h"]
    assert next(c for c in client.get("/api/docker/containers", headers=h).json()["containers"] if c["name"] == "blog")["protected"] == "self"
    assert client.post("/api/docker/containers/blog/action", headers=h, json={"action": "stop", "confirm": "blog"}).status_code == 409   # même confirmé : impossible
    assert client.delete("/api/docker/containers/blog?confirm=blog", headers=h).status_code == 409
    assert client.post("/api/docker/containers/blog/action", headers=h, json={"action": "restart", "confirm": "blog"}).status_code == 200  # relancer reste permis


def test_remove_container_and_volumes_flag(dk):
    h = dk["h"]
    assert client.delete("/api/docker/containers/traefik", headers=h).status_code == 409             # protégé : nom à retaper
    assert client.delete("/api/docker/containers/db?volumes=true", headers=h).status_code == 200
    assert "db" in dk["fake"].removed
    assert any(m == "DELETE" and p.startswith("/containers/") and "v=1" in p for m, p in dk["fake"].calls)
    assert client.delete("/api/docker/containers/db", headers=h).status_code == 404


def test_logs_with_cursor(dk):
    h = dk["h"]
    r = client.get("/api/docker/containers/web-1/logs?tail=50", headers=h).json()
    assert [(l["s"], l["m"]) for l in r["lines"]] == [("out", "premier"), ("out", "deuxième"), ("err", "erreur")]
    assert r["since"] > 1.7e9
    again = client.get(f"/api/docker/containers/web-1/logs?since={r['since']}", headers=h).json()
    assert again["lines"] == [] and again["since"] == r["since"]                                       # rien de nouveau : même curseur


def test_stats_are_computed(dk):
    st = client.get("/api/docker/stats", headers=dk["h"]).json()["stats"]
    assert st["aaaaaaaaaaaa"]["cpu"] == 20.0 and st["aaaaaaaaaaaa"]["mem"] == 4000 and st["aaaaaaaaaaaa"]["memLimit"] == 10000
    assert st["aaaaaaaaaaaa"]["netRx"] == 10 and set(st) == {"aaaaaaaaaaaa", "bbbbbbbbbbbb", "dddddddddddd"}   # les arrêtés n'ont pas de statistiques


def test_images_networks_volumes_and_prune(dk):
    h = dk["h"]
    imgs = {i["id"]: i for i in client.get("/api/docker/images", headers=h).json()["images"]}
    assert imgs["img1aaaaaaaa"]["usedBy"] and imgs["img9zzzzzzzz"]["dangling"] and imgs["img9zzzzzzzz"]["tags"] == []
    nets = {n["name"]: n for n in client.get("/api/docker/networks", headers=h).json()["networks"]}
    assert nets["bridge"]["builtin"] and "web-1" in nets["proxy"]["usedBy"] and nets["proxy"]["subnets"] == ["172.20.0.0/16"]
    assert client.delete("/api/docker/networks/bridge", headers=h).status_code == 409
    vols = {v["name"]: v for v in client.get("/api/docker/volumes", headers=h).json()["volumes"]}
    assert vols["site_data"]["usedBy"] == ["web-1"] and vols["orphan"]["usedBy"] == [] and vols["orphan"]["labels"] == {}
    assert client.delete("/api/docker/volumes/orphan", headers=h).status_code == 200
    assert client.delete("/api/docker/images/img9zzzzzzzz", headers=h).status_code == 200
    assert client.post("/api/docker/prune", headers=h, json={"what": "images"}).json() == {"deleted": 2, "spaceReclaimed": 123}
    assert client.post("/api/docker/prune", headers=h, json={"what": "tout"}).status_code == 422


def test_rights_and_default_roles():
    assert "docker.exec" in caps.PRESETS["owner"] and "docker.exec" not in caps.PRESETS["admin"] and "projects.delete" not in caps.PRESETS["admin"]
    assert {"docker.view", "docker.control", "docker.remove", "projects.deploy"} <= set(caps.PRESETS["admin"])
    assert not set(caps.PRESETS["editor"]) & {k for k in caps.CATALOG if k.startswith(("docker.", "projects."))}     # rien ne change pour les rôles d'avant
    assert not set(caps.PRESETS["viewer"]) & {k for k in caps.CATALOG if k.startswith(("docker.", "projects."))}
    assert "docker.logs" not in caps.closure({"docker.logs"})                                                       # dépend de docker.view
    assert {"docker.control", "docker.remove", "docker.exec"} <= caps.MFA_SENSITIVE


def test_worker_side_exposes_exactly_the_docker_ops(dk):
    from app import host_ops
    assert set(proto.OPS_BY_MODULE["docker"]) == set(host_ops.names())      # la liste fermée du protocole = ce que host_ops sait exécuter (Docker + projets)
    store = Store()
    assert worker.handle(store, "d_info", {}, "", ("docker",))["hostname"] == "fakehost"                            # un worker « docker » les exécute
    for modules in (("traefik",), ()):
        with pytest.raises(Exception) as e:
            worker.handle(store, "d_info", {}, "", modules)
        assert e.value.status == 400                                                                                # un worker « traefik » seul, jamais
    with pytest.raises(Exception) as e:
        worker.handle(store, "d_nope", {}, "", ("docker",))
    assert e.value.status == 400


def test_api_helpers():
    for bad in ("../x", "a/b", "a b", "", "-v", "a?b"):
        with pytest.raises(docker_api.DockerError):
            docker_api.ident(bad)
    assert docker_api.ident("web-1") == "web-1"
    assert docker_api.demux(b"plain tty output\n") == [("out", "plain tty output\n")]
    assert docker_api.demux(b"") == [("out", "")]


def test_publish_hint_tells_how_to_reach_a_container(dk):
    h = dk["h"]
    r = client.get("/api/docker/containers/web-1/publish-hint", headers=h).json()
    assert r["name"] == "web" and r["port"] == 80 and r["url"] == "http://web-1:80" and r["ports"] == [80] and r["linked"] is False
    t = r["traefik"]
    assert t["found"] and t["docker"] is True and t["exposedByDefault"] is False and t["sharedNetworks"] == ["proxy"] and t["reachable"] is True
    dk["fake"].containers["eeeeeeeeeeee5555"] = {"Id": "eeeeeeeeeeee5555", "Names": ["/isole"], "Image": "app", "ImageID": "sha256:img5", "State": "running", "Status": "Up", "Created": 1, "Ports": [], "Labels": {},
                                                 "NetworkSettings": {"Networks": {"backend": {"IPAddress": "172.30.0.2", "Aliases": []}}}, "Mounts": []}
    try:
        iso = client.get("/api/docker/containers/isole/publish-hint", headers=h).json()
        assert iso["port"] is None and iso["traefik"]["sharedNetworks"] == [] and iso["traefik"]["reachable"] is False     # pas sur un réseau de Traefik : il ne pourrait pas l'atteindre
    finally:
        del dk["fake"].containers["eeeeeeeeeeee5555"]
    blog = client.get("/api/docker/containers/blog/publish-hint", headers=h).json()
    assert blog["port"] == 2368 and blog["linked"] is True                                                                  # le port des labels l'emporte ; déjà publié
    assert client.get("/api/docker/containers/nope/publish-hint", headers=h).status_code == 404


def test_traefik_provider_detection(dk):
    from app import docker_link
    lst = client.get("/api/docker/containers", headers=dk["h"]).json()["containers"]
    class Ctx:
        node = {"kind": "local", "id": dk["nid"]}
        id = dk["nid"]
    cmd = dk["fake"].containers["dddddddddddd4444"]
    old = cmd["Cmd"]
    try:
        for args, docker, exposed in ((["--providers.docker=true"], True, True), (["--providers.docker=true", "--providers.docker.exposedbydefault=false"], True, False),
                                      (["--providers.file.directory=/x"], False, None), (["--api"], None, None), (["--providers.docker=false", "--providers.file=true"], False, None)):
            cmd["Cmd"] = args
            p = docker_link.traefik_provider(Ctx(), lst)
            assert (p["docker"], p["exposedByDefault"]) == (docker, exposed), args
    finally:
        cmd["Cmd"] = old
    assert docker_link.traefik_provider(Ctx(), [c for c in lst if c["name"] != "traefik"])["found"] is False


# ------------------------------------------------------------------ terminal (WebSocket)
import json as _json
import time as _time

from starlette.websockets import WebSocketDisconnect


def _token(dk):
    return dk["h"]["Authorization"].split()[1]


def _expect_close(dk, first, code):
    with client.websocket_connect("/api/docker/exec") as ws:
        ws.send_text(_json.dumps(first))
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == code, (first, e.value.code)


def test_terminal_streams_both_ways_and_resizes(dk):
    with client.websocket_connect("/api/docker/exec") as ws:
        ws.send_text(_json.dumps({"token": _token(dk), "node": dk["nid"], "container": "web-1", "cols": 100, "rows": 30}))
        assert _json.loads(ws.receive_text()) == {"t": "ready"}
        ws.send_text(_json.dumps({"i": "ls -la\n"}))
        assert ws.receive_bytes() == b"echo:ls -la\n"                                      # ce qu'on tape arrive au terminal, sa sortie revient
        ws.send_text(_json.dumps({"r": [120, 40]}))
        for _ in range(30):
            if ("POST", "/exec/exec1/resize?h=40&w=120") in dk["fake"].calls:
                break
            _time.sleep(0.1)
    assert ("POST", "/exec/exec1/resize?h=40&w=120") in dk["fake"].calls                      # redimensionné
    assert any(c[1] == "/containers/aaaaaaaaaaaa1111/exec" for c in dk["fake"].calls)


def test_terminal_refusals(dk):
    t, n = _token(dk), dk["nid"]
    _expect_close(dk, {"token": "nimporte", "node": n, "container": "web-1"}, 4401)                          # jeton invalide
    _expect_close(dk, {"token": t, "node": n, "container": "nope"}, 4403)                                    # conteneur inconnu
    _expect_close(dk, {"token": t, "node": "pas-un-noeud", "container": "web-1"}, 4403)
    dk["fake"].containers["ffffffffffff6666"] = {"Id": "ffffffffffff6666", "Names": ["/arrete"], "Image": "x", "ImageID": "sha256:x", "State": "exited", "Status": "Exited", "Created": 1, "Ports": [], "Labels": {},
                                                 "NetworkSettings": {"Networks": {}}, "Mounts": []}
    try:
        _expect_close(dk, {"token": t, "node": n, "container": "arrete"}, 4501)                              # pas démarré
    finally:
        del dk["fake"].containers["ffffffffffff6666"]
    settings.docker_manage = False
    try:
        _expect_close(dk, {"token": t, "node": n, "container": "web-1"}, 4501)                               # nœud sans Docker
    finally:
        settings.docker_manage = True


def test_terminal_needs_its_own_right_even_for_admins(dk):
    from app import db
    r = client.post("/api/users", headers=dk["h"], json={"username": "adm-term", "password": "un-mot-de-passe-solide", "role": "user"})
    assert r.status_code == 200, r.text
    uid = db.one("SELECT id FROM users WHERE username='adm-term'")["id"]
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (dk["nid"], uid, "admin", db.now()))
    tok = client.post("/api/auth/login", json={"username": "adm-term", "password": "un-mot-de-passe-solide"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}", "X-Node": dk["nid"]}
    assert client.get("/api/docker/containers", headers=h).status_code == 200                                   # un administrateur voit Docker…
    _expect_close(dk, {"token": tok, "node": dk["nid"], "container": "web-1"}, 4403)                             # …mais n'a pas le terminal
    db.run("DELETE FROM node_members WHERE user_id=?", (uid,))
