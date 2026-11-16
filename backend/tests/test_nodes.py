"""Isolation entre nœuds, rôles, plafond de délégation et import de domaines."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

PW = "un-mot-de-passe-solide"
client = TestClient(app)


def login(username: str, password: str = PW) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture(scope="module")
def world():
    root = login("root", "admin-password-1")
    for name, can_add in (("alice", True), ("bob", False), ("carol", False), ("dave", False)):
        r = client.post("/api/users", headers=root, json={"username": name, "password": PW, "canAddDomains": can_add})
        assert r.status_code == 200, r.text
    ids = {r["username"]: r["id"] for r in db.rows("SELECT id, username FROM users")}
    # un second nœud (distant) appartenant à alice, que personne d'autre ne voit
    nid = str(uuid.uuid4())
    db.run("INSERT INTO nodes(id,name,kind,created_at) VALUES(?,?,?,?)", (nid, "Paris", "remote", db.now()))
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (nid, ids["alice"], "owner", db.now()))
    local = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    return {"root": root, "alice": login("alice"), "bob": login("bob"), "carol": login("carol"), "dave": login("dave"),
            "ids": ids, "paris": nid, "local": local}


def test_seed_local_node(world):
    r = client.get("/api/nodes", headers=world["root"])
    assert [n["name"] for n in r.json()] == ["Principal"]
    assert r.json()[0]["role"] == "owner" and r.json()[0]["entrypoints"] == ["web", "websecure"]
    assert db.one("SELECT COUNT(*) c FROM node_domains WHERE node_id=?", (world["local"],))["c"] > 0


def test_stranger_cannot_see_or_touch_a_node(world):
    for who in ("bob", "carol"):
        h = world[who]
        assert client.get("/api/nodes", headers=h).json() == []
        for method, path in (("get", ""), ("get", "/members"), ("get", "/audit"), ("get", "/importable")):
            assert getattr(client, method)(f"/api/nodes/{world['paris']}{path}", headers=h).status_code == 404
        assert client.put(f"/api/nodes/{world['paris']}", headers=h, json={"name": "pwned"}).status_code == 404
        assert client.post(f"/api/nodes/{world['paris']}/members", headers=h, json={"username": who, "role": "viewer"}).status_code == 404
        assert client.get("/api/traefik/overview", headers={**h, "X-Node": world["paris"]}).status_code == 404
        assert client.get("/api/domains", headers={**h, "X-Node": world["paris"]}).status_code == 404


def test_platform_admin_has_no_implicit_access_to_a_remote_node(world):
    assert client.get(f"/api/nodes/{world['paris']}", headers=world["root"]).status_code == 404


def test_rename_and_settings(world):
    r = client.put(f"/api/nodes/{world['local']}", headers=world["root"],
                   json={"name": "Maison", "entrypoints": ["web", "websecure", "ssh"], "certResolvers": ["le"], "dnsTarget": "203.0.113.7"})
    assert r.status_code == 200 and r.json()["name"] == "Maison"
    m = client.get("/api/meta", headers=world["root"]).json()
    assert "ssh" in m["entrypoints"] and m["defaultTarget"] == "203.0.113.7"
    for bad in ({"dnsTarget": "not an ip!"}, {"entrypoints": ["a b"]}, {"name": "  "}):
        assert client.put(f"/api/nodes/{world['local']}", headers=world["root"], json=bad).status_code == 422


def test_api_url_only_for_platform_admin(world):
    h = world["alice"]
    # un nœud distant n'a pas d'URL d'API côté hub : elle se règle sur le worker
    assert client.put(f"/api/nodes/{world['paris']}", headers=h, json={"apiUrl": "http://169.254.169.254"}).status_code == 400
    assert client.put(f"/api/nodes/{world['paris']}", headers=h, json={"name": "Paris 2"}).status_code == 200
    # sur le nœud local, un administrateur du nœud (non administrateur de la plateforme) ne peut pas choisir l'URL
    assert client.post(f"/api/nodes/{world['local']}/members", headers=world["root"], json={"username": "bob", "role": "admin"}).status_code == 200
    assert client.put(f"/api/nodes/{world['local']}", headers=world["bob"], json={"apiUrl": "http://169.254.169.254"}).status_code == 403
    assert client.delete(f"/api/nodes/{world['local']}/members/{world['ids']['bob']}", headers=world["root"]).status_code == 200
    r = client.put(f"/api/nodes/{world['local']}", headers=world["root"], json={"apiUrl": "file:///etc/passwd"})
    assert r.status_code == 422
    r = client.put(f"/api/nodes/{world['local']}", headers=world["root"], json={"apiUrl": "http://traefik:8080"})
    assert r.status_code == 200


def test_delegation_ceiling(world):
    h, ids = world["alice"], world["ids"]
    nid = world["paris"]
    # le propriétaire nomme un admin et un lecteur
    assert client.post(f"/api/nodes/{nid}/members", headers=h, json={"username": "bob", "role": "admin"}).status_code == 200
    assert client.post(f"/api/nodes/{nid}/members", headers=h, json={"username": "carol", "role": "viewer"}).status_code == 200
    assert client.post(f"/api/nodes/{nid}/members", headers=h, json={"username": "dave", "role": "owner"}).status_code == 422
    bob = world["bob"]
    # l'admin ne peut pas donner admin/owner ni toucher au propriétaire
    assert client.post(f"/api/nodes/{nid}/members", headers=bob, json={"username": "dave", "role": "admin"}).status_code == 403
    assert client.put(f"/api/nodes/{nid}/members/{ids['alice']}", headers=bob, json={"role": "viewer"}).status_code == 403
    assert client.delete(f"/api/nodes/{nid}/members/{ids['alice']}", headers=bob).status_code == 403
    assert client.post(f"/api/nodes/{nid}/members", headers=bob, json={"username": "dave", "role": "editor"}).status_code == 200
    # un lecteur ne gère rien
    carol = world["carol"]
    assert client.get(f"/api/nodes/{nid}/members", headers=carol).status_code == 403
    assert client.put(f"/api/nodes/{nid}", headers=carol, json={"name": "x"}).status_code == 403
    # mais peut partir de lui-même
    assert client.delete(f"/api/nodes/{nid}/members/{ids['carol']}", headers=carol).status_code == 200
    assert client.get(f"/api/nodes/{nid}", headers=carol).status_code == 404
    # le propriétaire ne peut pas quitter sans transférer
    assert client.delete(f"/api/nodes/{nid}/members/{ids['alice']}", headers=h).status_code == 400


def test_dns_role_has_no_traefik(world):
    """Un utilisateur qui n'a qu'un domaine partagé voit le domaine, pas Traefik."""
    root, ids = world["root"], world["ids"]
    zone = db.one("SELECT d.name FROM domains d JOIN credentials c ON c.id=d.credential_id WHERE c.owner_id IS NULL LIMIT 1")["name"]
    r = client.put(f"/api/domains/{zone}/access", headers=root, json={"grants": [{"userId": ids["carol"], "level": "view"}]})
    assert r.status_code == 200, r.text
    carol = world["carol"]
    nodes = client.get("/api/nodes", headers=carol).json()
    assert [n["role"] for n in nodes] == ["dns"]
    assert client.get("/api/traefik/overview", headers=carol).status_code == 403
    assert client.get("/api/traefik/file", params={"path": "a.yml"}, headers=carol).status_code == 403
    assert zone in [d["name"] for d in client.get("/api/domains", headers=carol).json()]


def test_viewer_is_read_only(world):
    root, ids = world["root"], world["ids"]
    client.post(f"/api/nodes/{world['local']}/members", headers=root, json={"username": "dave", "role": "viewer"})
    zone = db.one("SELECT d.name FROM domains d JOIN credentials c ON c.id=d.credential_id WHERE c.owner_id IS NULL LIMIT 1")["name"]
    client.put(f"/api/domains/{zone}/access", headers=root, json={"grants": [{"userId": ids["dave"], "level": "edit"}]})
    dave = world["dave"]
    assert client.get("/api/traefik/overview", headers=dave).status_code == 200
    body = {"name": "x", "router": {"rule": f"Host(`x.{zone}`)", "service": "x"}, "service": {"loadBalancer": {"servers": [{"url": "http://a"}]}}}
    r = client.put("/api/traefik/site", headers=dave, json=body)
    assert r.status_code == 403
    r = client.post(f"/api/dns/zones/{zone}/records", headers=dave, json={"fieldType": "A", "subDomain": "x", "target": "1.2.3.4"})
    assert r.status_code == 403


def test_import_domain_without_duplicating_keys(world):
    """Alice gère un domaine sur ses comptes ; elle le rattache à un second nœud sans recopier les clés OVH."""
    h, ids = world["alice"], world["ids"]
    lyon = str(uuid.uuid4())
    db.run("INSERT INTO nodes(id,name,kind,created_at) VALUES(?,?,?,?)", (lyon, "Lyon", "remote", db.now()))
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (lyon, ids["alice"], "owner", db.now()))
    r = client.post("/api/credentials", headers=h, json={"label": "perso", "endpoint": "ovh-eu", "applicationKey": "ak1",
                                                          "applicationSecret": "as1", "consumerKey": "ck1"})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    zones = [z["name"] for z in r.json()["zones"] if z["available"]]
    assert zones
    # sans en-tête, le domaine est rattaché au premier nœud qu'elle administre (Paris)
    r = client.post(f"/api/credentials/{cid}/import", headers=h, json={"zones": zones[:1]})
    assert r.status_code == 200 and r.json()["added"], r.text
    zone = r.json()["added"][0]
    paris = world["paris"]
    assert db.one("SELECT 1 FROM node_domains nd JOIN domains d ON d.id=nd.domain_id WHERE d.name=? AND nd.node_id=?", (zone, paris))
    creds_before = db.one("SELECT COUNT(*) c FROM credentials")["c"]
    # l'importer aussi sur Lyon : un simple lien, aucune clé dupliquée
    assert [d["name"] for d in client.get(f"/api/nodes/{lyon}/importable", headers=h).json()] == [zone]
    r = client.post(f"/api/nodes/{lyon}/domains", headers=h, json={"names": [zone, "inconnu.example"]})
    assert r.json() == {"added": [zone], "skipped": ["inconnu.example"]}
    assert db.one("SELECT COUNT(*) c FROM credentials")["c"] == creds_before
    assert zone in [d["name"] for d in client.get("/api/domains", headers={**h, "X-Node": lyon}).json()]
    assert client.get(f"/api/dns/zones/{zone}/records", headers={**h, "X-Node": lyon}).status_code == 200
    # un administrateur de Paris qui ne gère pas ce domaine ne le voit pas et ne peut pas l'importer
    bob = world["bob"]
    assert client.get("/api/domains", headers={**bob, "X-Node": paris}).json() == []
    assert client.post(f"/api/nodes/{paris}/domains", headers=bob, json={"names": [zone]}).status_code == 403
    # retirer le domaine d'un nœud ne le supprime pas, et son DNS devient introuvable dans ce contexte
    assert client.delete(f"/api/nodes/{lyon}/domains/{zone}", headers=h).status_code == 200
    assert db.one("SELECT 1 FROM domains WHERE name=?", (zone,))
    assert client.get(f"/api/dns/zones/{zone}/records", headers={**h, "X-Node": lyon}).status_code == 404
    assert client.get(f"/api/dns/zones/{zone}/records", headers={**h, "X-Node": paris}).status_code == 200


def test_cannot_delete_user_owning_nodes(world):
    r = client.delete(f"/api/users/{world['ids']['alice']}", headers=world["root"])
    assert r.status_code == 409


def test_transfer_ownership(world):
    nid, ids = world["paris"], world["ids"]
    assert client.post(f"/api/nodes/{nid}/transfer", headers=world["bob"], json={"userId": ids["bob"]}).status_code == 403
    # le nœud compte dans le quota du nouveau propriétaire : sans droit de créer des nœuds, le transfert est refusé
    assert client.post(f"/api/nodes/{nid}/transfer", headers=world["alice"], json={"userId": ids["bob"]}).status_code == 403
    assert client.put(f"/api/users/{ids['bob']}", headers=world["root"], json={"maxNodes": 1}).status_code == 200
    assert client.post(f"/api/nodes/{nid}/transfer", headers=world["alice"], json={"userId": ids["bob"]}).status_code == 200
    assert client.get(f"/api/nodes/{nid}", headers=world["bob"]).json()["role"] == "owner"
    assert client.get(f"/api/nodes/{nid}", headers=world["alice"]).json()["role"] == "admin"


def test_node_domains_listing(world):
    r = client.get(f"/api/nodes/{world['local']}/domains", headers=world["root"])
    assert r.status_code == 200 and r.json() and all("name" in d for d in r.json())
    assert client.get(f"/api/nodes/{world['local']}/domains", headers=world["dave"]).status_code == 403  # simple lecteur
