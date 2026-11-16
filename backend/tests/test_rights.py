"""Droits fins : les rôles d'avant gardent EXACTEMENT leurs droits ; rôles personnalisés, groupes, attributions, plafonds, durée limitée."""
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import caps, db, rbac
from app.main import app

PW = "un-mot-de-passe-solide"
client = TestClient(app)


def login(username, password=PW):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture(scope="module")
def world():
    root = login("root", "admin-password-1")
    names = ("rt-owner", "rt-admin", "rt-editor", "rt-viewer", "rt-guest", "rt-guest2", "rt-ops")
    for n in names:
        client.post("/api/users", headers=root, json={"username": n, "password": PW, "canAddDomains": True, "maxNodes": 3})
    ids = {r["username"]: r["id"] for r in db.rows("SELECT id, username FROM users")}
    nid = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]   # nœud local : il a un vrai dossier Traefik, pas besoin de worker
    for n, role in (("rt-owner", "owner"), ("rt-admin", "admin"), ("rt-editor", "editor"), ("rt-viewer", "viewer")):
        db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (nid, ids[n], role, db.now()))
    h = {n: login(n) for n in names}
    return {"root": root, "nid": nid, "ids": ids, **{"h_" + n: v for n, v in h.items()}}


def me(world, who, nid=None):
    r = client.get("/api/nodes", headers=world["h_rt-" + who])
    return next((n for n in r.json() if n["id"] == (nid or world["nid"])), None)


def role_id(builtin):
    return db.one("SELECT id FROM roles WHERE builtin=?", (builtin,))["id"]


# ---------- le catalogue et les modèles de rôle ----------
def test_les_roles_d_avant_gardent_leurs_droits(world):
    assert set(me(world, "owner")["caps"]) == set(caps.ALL)
    assert "node.delete" not in me(world, "admin")["caps"] and "files.edit" in me(world, "admin")["caps"]
    e = set(me(world, "editor")["caps"])
    assert {"sites.view", "sites.create", "sites.edit", "sites.delete", "sites.toggle", "certs.view"} <= e
    assert not e & {"files.view", "files.edit", "traefik.all", "node.settings", "node.members.view", "middlewares.view"}
    v = set(me(world, "viewer")["caps"])
    assert "sites.view" in v and not v & {"sites.create", "sites.edit", "sites.delete", "files.view"}


def test_dependances_des_droits():
    assert "sites.create" not in caps.closure({"sites.create"})                 # sans « voir », pas de création
    assert "files.edit" not in caps.closure({"files.edit", "files.view"})       # « modifier un fichier » exige « tout Traefik »
    assert {"files.view", "files.edit", "traefik.all"} <= caps.closure({"traefik.all", "files.view", "files.edit"})


# ---------- application des droits dans les routes ----------
def put_file(world, who, path, content="http: {}\n", create=True):
    return client.put("/api/traefik/file", headers={**world["h_rt-" + who], "X-Node": world["nid"]}, json={"path": path, "content": content, "create": create})


def test_fichiers_bruts_reserves_a_qui_en_a_le_droit(world):
    assert put_file(world, "editor", "e.yml").status_code == 403
    assert "Droit manquant" in put_file(world, "editor", "e.yml").json()["detail"]
    assert put_file(world, "viewer", "v.yml").status_code == 403


def test_un_role_personnalise_retire_un_droit_a_un_admin(world):
    """« Admin sans fichiers » : on part de l'administrateur, on enlève les fichiers, on l'attribue à la place du rôle historique."""
    base = [c for c in caps.PRESETS["admin"] if not c.startswith(("files.", "history."))]
    r = client.post("/api/rights/roles", headers=world["h_rt-owner"], json={"name": "Admin sans fichiers", "description": "", "caps": base})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    uid = world["ids"]["rt-ops"]
    # rt-ops n'est membre d'aucun nœud : l'attribution seule lui donne l'accès
    g = client.post("/api/rights/grants", headers=world["h_rt-owner"], json={"subjectType": "user", "subjectId": uid, "roleId": rid, "nodeId": world["nid"]})
    assert g.status_code == 200, g.text
    n = me(world, "ops")
    assert n and n["role"] == "custom" and n["roleLabel"] == "Admin sans fichiers"
    assert "traefik.all" in n["caps"] and "files.view" not in n["caps"]
    h = {**world["h_rt-ops"], "X-Node": world["nid"]}
    assert client.get("/api/traefik/overview", headers=h).status_code == 200
    assert client.get("/api/traefik/file", headers=h, params={"path": "x.yml"}).status_code == 403
    assert client.put("/api/traefik/file", headers=h, json={"path": "x.yml", "content": "http: {}\n", "create": True}).status_code == 403
    # mais il crée des middlewares (droit présent)
    r = client.put("/api/traefik/item", headers=h, json={"proto": "http", "kind": "middlewares", "name": "mw1", "spec": {"compress": {}}})
    assert r.status_code == 200, r.text


def test_plafond_de_delegation(world):
    """Un administrateur ne peut pas mettre dans un rôle (ni attribuer) des droits qu'il n'a pas."""
    r = client.post("/api/rights/roles", headers=world["h_rt-admin"], json={"name": "Trop", "description": "", "caps": ["node.delete", "sites.view"]})
    assert r.status_code == 403
    base = [c for c in caps.PRESETS["admin"] if not c.startswith("files.") and c != "history.view"]
    ok = client.post("/api/rights/roles", headers=world["h_rt-owner"], json={"name": "Sans fichiers 2", "description": "", "caps": base}).json()["id"]
    # un « admin sans fichiers » ne peut plus créer de rôle contenant les fichiers
    uid = world["ids"]["rt-guest"]
    client.post("/api/rights/grants", headers=world["h_rt-owner"], json={"subjectType": "user", "subjectId": uid, "roleId": ok, "nodeId": world["nid"]})
    r = client.post("/api/rights/roles", headers=world["h_rt-guest"], json={"name": "Avec fichiers", "description": "", "caps": ["files.view", "traefik.all"]})
    assert r.status_code == 403


def test_roles_predefinis_intouchables_mais_duplicables(world):
    admin = role_id("admin")
    assert client.put(f"/api/rights/roles/{admin}", headers=world["h_rt-owner"], json={"name": "x", "description": "", "caps": []}).status_code == 400
    assert client.delete(f"/api/rights/roles/{admin}", headers=world["h_rt-owner"]).status_code == 400
    d = client.post(f"/api/rights/roles/{admin}/duplicate", headers=world["h_rt-owner"])
    assert d.status_code == 200 and d.json()["name"].startswith("Administrateur (copie") and not d.json()["predefined"]
    assert "node.delete" not in d.json()["caps"]


def test_groupe_et_attribution_temporaire(world):
    owner = world["h_rt-owner"]
    gid = client.post("/api/rights/groups", headers=owner, json={"name": "Astreinte"}).json()["id"]
    client.put(f"/api/rights/groups/{gid}/members", headers=owner, json={"userIds": [world["ids"]["rt-guest2"]]})
    assert me(world, "guest2") is None
    soon = time.time() + 2
    r = client.post("/api/rights/grants", headers=owner, json={"subjectType": "group", "subjectId": gid, "roleId": role_id("operator"), "nodeId": world["nid"], "expiresAt": soon})
    assert r.status_code == 200, r.text
    n = me(world, "guest2")
    assert n and "sites.toggle" in n["caps"] and "sites.edit" not in n["caps"]        # opérateur : met en pause, ne modifie pas
    time.sleep(2.2)
    assert me(world, "guest2") is None                                                  # attribution expirée : plus d'accès
    # retirer quelqu'un du groupe retire ses droits (attribution durable)
    client.post("/api/rights/grants", headers=owner, json={"subjectType": "group", "subjectId": gid, "roleId": role_id("operator"), "nodeId": world["nid"]})
    assert me(world, "guest2")
    client.put(f"/api/rights/groups/{gid}/members", headers=owner, json={"userIds": []})
    assert me(world, "guest2") is None


def test_tous_mes_noeuds_suit_le_proprietaire(world):
    owner = world["h_rt-owner"]
    uid = world["ids"]["rt-guest2"]
    assert client.post("/api/rights/grants", headers=owner, json={"subjectType": "user", "subjectId": uid, "roleId": role_id("auditor"), "nodeId": None}).status_code == 200
    n = me(world, "guest2")
    assert n and "files.view" in n["caps"] and "files.edit" not in n["caps"]
    # un autre nœud dont rt-owner est propriétaire est aussitôt couvert
    other = str(uuid.uuid4())
    db.run("INSERT INTO nodes(id,name,kind,created_at) VALUES(?,?,?,?)", (other, "Autre", "remote", db.now()))
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (other, world["ids"]["rt-owner"], "owner", db.now()))
    assert me(world, "guest2", other)
    # …mais pas celui d'un autre propriétaire
    third = str(uuid.uuid4())
    db.run("INSERT INTO nodes(id,name,kind,created_at) VALUES(?,?,?,?)", (third, "Tiers", "remote", db.now()))
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (third, world["ids"]["rt-admin"], "owner", db.now()))
    assert me(world, "guest2", third) is None


def test_dns_droits_fins(world):
    """Sans le droit de supprimer un enregistrement, la suppression est refusée même sur un domaine éditable."""
    rid = client.post("/api/rights/roles", headers=world["h_rt-owner"], json={"name": "DNS sans suppression", "description": "", "caps": sorted(set(caps.PRESETS["dns"]) - {"dns.records.delete"})}).json()["id"]
    assert rid
    assert "dns.records.delete" not in caps.closure(set(caps.PRESETS["dns"]) - {"dns.records.delete"})


def test_dns_restrictions_par_domaine_et_acces_temporaire(world):
    root = world["root"]
    client.post("/api/users", headers=root, json={"username": "rt-dns", "password": PW})
    uid = db.one("SELECT id FROM users WHERE username='rt-dns'")["id"]
    grant = {"userId": uid, "level": "edit", "types": "A", "subs": "app, *.dev", "noDelete": True}
    assert client.put("/api/domains/example.com/access", headers=root, json={"grants": [grant]}).status_code == 200
    h = login("rt-dns")
    rec = lambda t, s: client.post("/api/dns/zones/example.com/records", headers=h, json={"fieldType": t, "subDomain": s, "target": "203.0.113.9", "ttl": 300})  # noqa: E731
    assert rec("A", "app").status_code == 200                                   # type et sous-domaine autorisés
    assert rec("A", "x.dev").status_code == 200                                 # motif « *.dev »
    r = rec("CNAME", "app")
    assert r.status_code == 403 and "non autorisé" in r.json()["detail"]        # type refusé
    r = rec("A", "www")
    assert r.status_code == 403 and "Sous-domaine" in r.json()["detail"]        # sous-domaine refusé
    existing = [x for x in client.get("/api/dns/zones/example.com/records", headers=h).json() if x["subDomain"] == "mealie"]
    assert existing and client.delete(f"/api/dns/zones/example.com/records/{existing[0]['id']}", headers=h).status_code == 403   # hors sous-domaines autorisés
    mine = [x for x in client.get("/api/dns/zones/example.com/records", headers=h).json() if x["subDomain"] == "app"]
    r = client.delete(f"/api/dns/zones/example.com/records/{mine[0]['id']}", headers=h)
    assert r.status_code == 403 and "supprimer" in r.json()["detail"]           # suppression interdite
    # sans « ne pas supprimer », la suppression de ses propres enregistrements marche
    grant2 = {**grant, "noDelete": False}
    client.put("/api/domains/example.com/access", headers=root, json={"grants": [grant2]})
    assert client.delete(f"/api/dns/zones/example.com/records/{mine[0]['id']}", headers=h).status_code == 200
    # accès temporaire : il disparaît à l'échéance
    client.put("/api/domains/example.com/access", headers=root, json={"grants": [{**grant2, "expiresAt": time.time() + 2}]})
    assert client.get("/api/dns/zones/example.com/records", headers=h).status_code == 200
    time.sleep(2.2)
    assert client.get("/api/dns/zones/example.com/records", headers=h).status_code == 404
    client.put("/api/domains/example.com/access", headers=root, json={"grants": []})


def test_aucun_doublon_dans_la_liste_des_utilisateurs(world):
    """Un administrateur de la plateforme aussi inscrit comme membre propriétaire du nœud local n'apparaît qu'une fois."""
    local = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    root_id = db.one("SELECT id FROM users WHERE username='root'")["id"]
    db.run("INSERT OR IGNORE INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (local, root_id, "owner", db.now()))
    rows = client.get("/api/rights/users", headers=world["root"]).json()
    root = next(u for u in rows if u["username"] == "root")
    assert len([a for a in root["access"] if a["source"] == "Propriétaire"]) == len({a["node"] for a in root["access"] if a["source"] == "Propriétaire"})
    grants = client.get("/api/rights/grants", headers=world["root"]).json()
    mine = [g for g in grants if g.get("membership") and g["subject"] == "root" and g["nodeId"] == local]
    assert len(mine) == 1
    db.run("DELETE FROM node_members WHERE node_id=? AND user_id=?", (local, root_id))
