"""Un nœud « Docker seul » dont les sites passent par le Traefik d'un autre nœud : réglage, adresses annoncées, liaison."""
import pytest

from app import db, docker_link, worker
from test_projects import client, pj  # noqa: F401  (fixture)


def test_machine_addresses_merge_manual_and_announced():
    n = {"addresses": ["192.168.1.20", "nas.lan"], "addressesSeen": ["nas.lan", "10.0.0.5"]}
    assert docker_link.machine_addresses(n) == ["192.168.1.20", "nas.lan", "10.0.0.5"]
    assert docker_link.machine_addresses({}) == []


def test_worker_announces_clean_addresses(monkeypatch):
    monkeypatch.setenv("WORKER_ADDRESSES", " 192.168.1.20 ; NAS.lan,evil addr, ,x/y ")
    assert worker.announced_addresses() == ["192.168.1.20", "nas.lan"]
    monkeypatch.delenv("WORKER_ADDRESSES")
    assert worker.announced_addresses() == []


def test_traefik_via_setting_is_validated(pj):
    h = pj["h"]
    nodes = client.get("/api/nodes", headers=h).json()
    main = next(n for n in nodes if n["kind"] == "local")
    r = client.post("/api/nodes", headers=h, json={"name": "Docker seul"})
    nid = r.json()["id"]
    ok = client.put(f"/api/nodes/{nid}", headers=h, json={"traefikVia": main["id"]})
    assert ok.status_code == 200 and ok.json()["traefikVia"] == main["id"]
    assert client.put(f"/api/nodes/{nid}", headers=h, json={"traefikVia": nid}).status_code == 422            # pas lui-même
    assert client.put(f"/api/nodes/{nid}", headers=h, json={"traefikVia": "inconnu"}).status_code == 422
    assert client.put(f"/api/nodes/{nid}", headers=h, json={"traefikVia": ""}).json()["traefikVia"] == ""     # on peut l'enlever
    db.run("DELETE FROM nodes WHERE id=?", (nid,))
