"""Un worker « les deux » avec ses fonctions facultatives : le journal d'accès lu CHEZ LUI et envoyé au hub (statistiques de visiteurs d'un nœud distant),
et le terminal dans un conteneur de sa machine (autorisé par WORKER_ALLOW_EXEC)."""
import json
import os
import tempfile
import time
from pathlib import Path

import pytest
import websockets.sync.client as wsc
from websockets.exceptions import ConnectionClosed

from app import accesslog, worker, worker_link, worker_log
from app.config import settings
from app.traefik_store import StoreError
from test_worker import Api, RunningWorker, hub, wait_for  # noqa: F401  (hub : fixture)
from fakedocker import FakeDocker


def line(ip, path, status, host="blog.example.org"):
    return json.dumps({"ClientHost": ip, "RequestHost": host, "RequestPath": path, "DownstreamStatus": status, "RequestMethod": "GET",
                       "StartUTC": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "request_User-Agent": "curl/8"})


@pytest.fixture(scope="module")
def wf(hub):
    d = tempfile.mkdtemp(prefix="wf")
    fake = FakeDocker(f"{d}/d.sock")
    log = Path(d) / "access.log"
    log.write_text("\n".join(line("203.0.113.9", f"/p{i}", 404) for i in range(6)) + "\n" + line("203.0.113.10", "/", 200) + "\n")
    old = (settings.docker_socket, settings.project_roots, settings.access_log, worker_log._state_path, os.environ.get("WORKER_MODULES"), os.environ.get("WORKER_ALLOW_EXEC"))
    settings.docker_socket, settings.project_roots, settings.access_log = fake.path, [], str(log)
    worker_log._state_path = lambda: Path(d) / "offset.json"           # type: ignore[assignment]
    os.environ["WORKER_MODULES"], os.environ["WORKER_ALLOW_EXEC"] = "traefik,docker", "true"
    root = Api(hub, "root", "admin-password-1")
    node = root.post("/nodes", json={"name": "Machine complète"}).json()
    w = RunningWorker(hub, node["token"])
    wait_for(lambda: next(n["connection"] for n in root.get("/nodes").json() if n["id"] == node["id"]) == "online")
    yield {"root": root, "nid": node["id"], "fake": fake, "log": log, "hub": hub}
    w.stop()
    settings.docker_socket, settings.project_roots, settings.access_log = old[0], old[1], old[2]
    worker_log._state_path = old[3]                                     # type: ignore[assignment]
    for k, v in (("WORKER_MODULES", old[4]), ("WORKER_ALLOW_EXEC", old[5])):
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    fake.close()


def test_the_worker_announces_its_optional_features(wf):
    assert worker_link.feature(wf["nid"], "exec") and worker_link.feature(wf["nid"], "access")


def test_visitors_of_a_remote_node_come_from_the_log_its_worker_reads(wf):
    r, nid = wf["root"], wf["nid"]
    f = wait_for(lambda: (lambda x: x if x["ips"]["enabled"] else None)(r.get("/obs/features", node=nid).json()))
    assert f["ips"]["enabled"] and "Métriques" in (f["remoteNote"] or "")
    res = wait_for(lambda: (lambda x: x if x.get("rows") else None)(r.get("/obs/ips", node=nid).json()))
    by_ip = {i["ip"]: i for i in res["rows"]}
    assert by_ip["203.0.113.9"]["hits"] == 6 and by_ip["203.0.113.9"]["c404"] == 6 and by_ip["203.0.113.10"]["c2"] == 1
    st = r.get("/obs/stats", node=nid).json()
    assert st["hits"] == 7 and st["hosts"][0]["name"] == "blog.example.org"


def test_remote_bursts_never_mix_with_the_hub_node(wf):
    wait_for(lambda: accesslog.burst(node_id=wf["nid"]))
    assert accesslog.burst(node_id=wf["nid"]).get("203.0.113.9") == 6
    assert "203.0.113.9" not in accesslog.burst()                          # l'état « récent » du nœud du hub est à part


def test_new_log_lines_are_sent_as_they_arrive_and_only_once(wf):
    r, nid = wf["root"], wf["nid"]
    with open(wf["log"], "a") as f:
        f.write(line("203.0.113.11", "/x", 500) + "\n")
    wait_for(lambda: any(i["ip"] == "203.0.113.11" for i in r.get("/obs/ips", node=nid).json()["rows"]))
    time.sleep(4)                                                            # un tour de plus du worker : rien ne doit être compté deux fois
    rows = {i["ip"]: i for i in r.get("/obs/ips", node=nid).json()["rows"]}
    assert rows["203.0.113.11"]["c5"] == 1 and rows["203.0.113.9"]["hits"] == 6


def test_the_reader_resumes_after_a_restart_and_survives_a_rotation(tmp_path, monkeypatch):
    log = tmp_path / "a.log"
    monkeypatch.setattr(settings, "access_log", str(log))
    monkeypatch.setattr(worker_log, "_state_path", lambda: tmp_path / "o.json")
    log.write_text("one\ntwo\n")
    rd = worker_log.Reader()
    assert rd.read() == ["one", "two"]
    rd.commit()
    with open(log, "a") as f:
        f.write("three\npartial")
    rd2 = worker_log.Reader()                                                # « redémarrage » : la position est relue sur disque
    assert rd2.read() == ["three"]                                          # la ligne incomplète est reprise plus tard
    rd2.commit()
    log.write_text("new1\nnew2\n")                                           # fichier remplacé (rotation / troncature)
    rd3 = worker_log.Reader()
    assert rd3.read() == ["new1", "new2"]
    rd3.commit()
    assert worker_log.Reader().read() == []                                  # tout est parti, rien n'est renvoyé


def test_an_unconfirmed_batch_is_resent(tmp_path, monkeypatch):
    log = tmp_path / "b.log"
    monkeypatch.setattr(settings, "access_log", str(log))
    monkeypatch.setattr(worker_log, "_state_path", lambda: tmp_path / "o.json")
    log.write_text("a\nb\n")
    assert worker_log.Reader().read() == ["a", "b"]                         # lu mais jamais validé (envoi échoué) : on le relira
    assert worker_log.Reader().read() == ["a", "b"]


def test_the_hub_bounds_what_a_worker_sends(wf):
    assert accesslog.ingest_remote(wf["nid"], "pas une liste") == 0
    assert accesslog.ingest_remote(wf["nid"], [123, None, "x" * 9000]) == 0
    assert accesslog.ingest_remote(wf["nid"], [line("203.0.113.50", "/", 200)] * 3000) == 2000   # au plus 2000 lignes par message


def test_terminal_in_a_container_of_the_worker(wf):
    tok = wf["root"].h["Authorization"].split()[1]
    ws = wsc.connect(wf["hub"].replace("http://", "ws://") + "/api/docker/exec")
    ws.send(json.dumps({"token": tok, "node": wf["nid"], "container": "web-1", "cols": 100, "rows": 30}))
    assert json.loads(ws.recv(timeout=10)) == {"t": "ready"}
    ws.send(json.dumps({"i": "ls -la\n"}))
    got = b""
    end = time.time() + 10
    while b"echo:ls -la\n" not in got and time.time() < end:
        got += ws.recv(timeout=5)
    assert got == b"echo:ls -la\n"                                           # la saisie part chez le worker, la sortie revient par le hub
    ws.send(json.dumps({"r": [120, 40]}))
    wait_for(lambda: ("POST", "/exec/exec1/resize?h=40&w=120") in wf["fake"].calls)
    ws.close()
    wait_for(lambda: not worker_link.call(wf["nid"], "d_exec_close", {"sid": "x"}) is None)   # la fermeture est tolérée même si la session est déjà partie


def test_exec_is_refused_by_a_worker_that_did_not_allow_it(wf, monkeypatch):
    monkeypatch.delenv("WORKER_ALLOW_EXEC")
    with pytest.raises(StoreError) as e:
        worker.handle(None, "d_exec_open", {"id": "web-1"}, "", ("traefik", "docker"))
    assert e.value.status == 403
