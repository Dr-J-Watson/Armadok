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


def test_domains_of_the_via_node_are_inherited_read_only(pj):
    h = pj["h"]
    main = next(n for n in client.get("/api/nodes", headers=h).json() if n["kind"] == "local")
    nid = client.post("/api/nodes", headers=h, json={"name": "PC2"}).json()["id"]
    mine = [d["name"] for d in client.get(f"/api/nodes/{main['id']}/domains", headers=h).json()]
    assert client.get(f"/api/nodes/{nid}/domains", headers=h).json() == []
    client.put(f"/api/nodes/{nid}", headers=h, json={"traefikVia": main["id"]})
    inh = client.get(f"/api/nodes/{nid}/domains", headers=h).json()
    assert sorted(d["name"] for d in inh) == sorted(mine) and all(d["inherited"] == main["name"] for d in inh)      # lecture seule, venant du Traefik choisi
    client.put(f"/api/nodes/{nid}", headers=h, json={"traefikVia": ""})
    assert client.get(f"/api/nodes/{nid}/domains", headers=h).json() == []
    db.run("DELETE FROM nodes WHERE id=?", (nid,))


def test_scope_keeps_only_routers_of_the_node_and_what_they_use():
    from app import traefik_scope as ts
    from app.perms import Principal
    items = [
        {"id": "http/routers/a", "proto": "http", "kind": "routers", "name": "a", "spec": {"rule": "Host(`a.x.fr`)", "service": "sa", "middlewares": ["m1"]}, "file": "f.yml"},
        {"id": "http/routers/b", "proto": "http", "kind": "routers", "name": "b", "spec": {"rule": "Host(`b.x.fr`)", "service": "sb", "middlewares": ["m2"]}, "file": "f.yml"},
        {"id": "http/services/sa", "proto": "http", "kind": "services", "name": "sa", "spec": {}, "file": "f.yml"},
        {"id": "http/services/sb", "proto": "http", "kind": "services", "name": "sb", "spec": {}, "file": "f.yml"},
        {"id": "http/middlewares/m1", "proto": "http", "kind": "middlewares", "name": "m1", "type": "chain", "spec": {"chain": {"middlewares": ["m3"]}}, "file": "f.yml"},
        {"id": "http/middlewares/m2", "proto": "http", "kind": "middlewares", "name": "m2", "type": "headers", "spec": {}, "file": "f.yml"},
        {"id": "http/middlewares/m3", "proto": "http", "kind": "middlewares", "name": "m3", "type": "headers", "spec": {}, "file": "f.yml"},
    ]
    p = Principal(id=1, username="u", role="admin", can_add_domains=True, traefik_access="full", node_id="n", node_role="owner", caps=frozenset({"traefik.all"}), scope_routers=frozenset({"a"}))
    out = ts.scope_items(p, [dict(i) for i in items])
    assert sorted(i["id"] for i in out) == ["http/middlewares/m1", "http/middlewares/m3", "http/routers/a", "http/services/sa"]      # b, sb, m2 : pas à ce nœud ; m3 : utilisé par m1
    full = ts.scope_items(Principal(**{**p.__dict__, "scope_routers": None}), [dict(i) for i in items])
    assert len(full) == len(items)
