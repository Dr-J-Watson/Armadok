"""Comptes DNS : la page ne montre que ceux de l'utilisateur ET rattachés au nœud courant ; ce qui est ailleurs se propose à l'import."""
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


def node(name: str, owner_id: int) -> str:
    nid = str(uuid.uuid4())
    db.run("INSERT INTO nodes(id,name,kind,created_at) VALUES(?,?,?,?)", (nid, name, "remote", db.now()))
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (nid, owner_id, "owner", db.now()))
    return nid


def duck(h: dict, node_id: str, label: str, name: str) -> int:
    r = client.post("/api/credentials/duckdns", headers={**h, "X-Node": node_id}, json={"label": label, "token": "abcdefgh-1234", "domains": [name], "ip": "1.1.1.1"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture(scope="module")
def world():
    root = login("root", "admin-password-1")
    for name in ("cn_alice", "cn_carol"):
        assert client.post("/api/users", headers=root, json={"username": name, "password": PW, "canAddDomains": name == "cn_alice"}).status_code == 200
    ids = {r["username"]: r["id"] for r in db.rows("SELECT id, username FROM users")}
    alice = login("cn_alice")
    paris, lyon = node("CN Paris", ids["cn_alice"]), node("CN Lyon", ids["cn_alice"])
    a = duck(alice, paris, "compte-paris", "cnparis")
    b = duck(alice, lyon, "compte-lyon", "cnlyon")
    return {"root": root, "alice": alice, "carol": login("cn_carol"), "ids": ids, "paris": paris, "lyon": lyon, "a": a, "b": b}


def labels(h, node_id=None, q=""):
    r = client.get("/api/credentials" + q, headers={**h, **({"X-Node": node_id} if node_id else {})})
    assert r.status_code == 200, r.text
    return {c["label"]: c for c in r.json()}


def test_each_node_shows_only_its_own_accounts(world):
    a, paris, lyon = world["alice"], world["paris"], world["lyon"]
    p = labels(a, paris)
    assert set(p) == {"compte-paris"} and p["compte-paris"]["domains"] == ["cnparis.duckdns.org"] and p["compte-paris"]["elsewhere"] == 0
    l = labels(a, lyon)
    assert set(l) == {"compte-lyon"} and l["compte-lyon"]["domains"] == ["cnlyon.duckdns.org"]


def test_all_accounts_remain_reachable_when_asked(world):
    """Pour choisir à quel compte ajouter un domaine : tous les comptes de l'utilisateur, sans le filtre du nœud."""
    assert {"compte-paris", "compte-lyon"} <= set(labels(world["alice"], world["paris"], "?all=1"))


def test_other_people_and_the_platform_admin_do_not_see_them(world):
    for h in (world["root"], world["carol"]):
        assert not ({"compte-paris", "compte-lyon"} & set(labels(h)))
    # un nœud étranger reste introuvable
    assert client.get("/api/credentials", headers={**world["carol"], "X-Node": world["paris"]}).status_code == 404


def test_account_without_domain_or_with_nodeless_domains_stays_visible(world):
    alice, ids, paris = world["alice"], world["ids"], world["paris"]
    empty = db.run("INSERT INTO credentials(label,endpoint,provider,owner_id,source,created_at) VALUES('sans-domaine','local','local',?,'user',?)", (ids["cn_alice"], db.now()))
    orphan = db.run("INSERT INTO credentials(label,endpoint,provider,owner_id,source,created_at) VALUES('orphelin','local','local',?,'user',?)", (ids["cn_alice"], db.now()))
    db.run("INSERT INTO domains(name, credential_id) VALUES('cn-orphelin.lan', ?)", (orphan,))
    got = labels(alice, paris)
    assert "sans-domaine" in got and got["orphelin"]["nodeless"] == ["cn-orphelin.lan"]
    db.run("DELETE FROM domains WHERE credential_id=?", (orphan,))
    db.run("DELETE FROM credentials WHERE id IN (?,?)", (empty, orphan))


def test_import_proposal_lists_what_lives_elsewhere_and_links_it(world):
    alice, paris, lyon = world["alice"], world["paris"], world["lyon"]
    prop = client.get(f"/api/nodes/{lyon}/importable", headers=alice).json()
    assert [(d["name"], d["account"], d["accountId"], d["mine"]) for d in prop] == [("cnparis.duckdns.org", "compte-paris", world["a"], True)]
    assert prop[0]["nodes"] == ["CN Paris"]
    creds_before = db.one("SELECT COUNT(*) c FROM credentials")["c"]
    r = client.post(f"/api/nodes/{lyon}/domains", headers=alice, json={"names": ["cnparis.duckdns.org"]})
    assert r.json() == {"added": ["cnparis.duckdns.org"], "skipped": []}
    assert db.one("SELECT COUNT(*) c FROM credentials")["c"] == creds_before                      # un lien : aucune clé recopiée
    assert set(labels(alice, lyon)) == {"compte-lyon", "compte-paris"}                              # le compte apparaît désormais sur Lyon
    assert set(labels(alice, paris)) == {"compte-paris"}                                            # et Paris ne montre toujours pas celui de Lyon
    assert labels(alice, lyon)["compte-paris"]["elsewhere"] == 0 and client.get(f"/api/nodes/{lyon}/importable", headers=alice).json() == []


def test_without_the_right_nothing_is_listed_nor_imported(world):
    carol, alice, paris = world["carol"], world["alice"], world["paris"]
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (paris, world["ids"]["cn_carol"], "viewer", db.now()))
    assert client.get(f"/api/nodes/{paris}/importable", headers=carol).status_code == 403
    assert client.post(f"/api/nodes/{paris}/domains", headers=carol, json={"names": ["cnlyon.duckdns.org"]}).status_code == 403
    assert labels(carol, paris) == {}
    # un propriétaire de nœud sans le droit global « ajouter des domaines » : rien à proposer, rien à importer
    assert client.post("/api/users", headers=world["root"], json={"username": "cn_dave", "password": PW, "canAddDomains": False}).status_code == 200
    dave_id = db.one("SELECT id FROM users WHERE username='cn_dave'")["id"]
    dave, dnode = login("cn_dave"), node("CN Dave", dave_id)
    assert client.get(f"/api/nodes/{dnode}/importable", headers=dave).json() == []
    assert client.post(f"/api/nodes/{dnode}/domains", headers=dave, json={"names": ["cnparis.duckdns.org"]}).status_code == 403
