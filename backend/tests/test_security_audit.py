"""Correctifs de l'audit de sécurité : un test par faille (chaque test échouait avant le correctif)."""
import shutil

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from test_projects import fakecli, pid_of, pj  # noqa: F401  (fixtures : dossier de projets autorisé, faux « docker »)

PW = "un-mot-de-passe-solide"
client = TestClient(app)


def login(username, password=PW):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture(scope="module")
def sa():
    root = login("root", "admin-password-1")
    names = ("sa-owner", "sa-admin", "sa-editor", "sa-viewer", "sa-ops", "sa-svc")
    for n in names:
        client.post("/api/users", headers=root, json={"username": n, "password": PW, "canAddDomains": True, "maxNodes": 3})
    ids = {r["username"]: r["id"] for r in db.rows("SELECT id, username FROM users")}
    nid = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    for n, role in (("sa-owner", "owner"), ("sa-admin", "admin"), ("sa-editor", "editor"), ("sa-viewer", "viewer")):
        db.run("INSERT OR IGNORE INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (nid, ids[n], role, db.now()))
    return {"root": root, "nid": nid, "ids": ids, **{"h_" + n: login(n) for n in names}}


# ---------- 5 : un nom de service ne devient jamais une option de « docker compose » ----------
def test_a_service_named_like_an_option_is_refused(pj, fakecli):
    h = pj["h"]
    d = pj["base"] / "optsite"
    d.mkdir(exist_ok=True)
    (d / "docker-compose.yml").write_text("services:\n  app: {image: busybox}\n  --volumes: {image: busybox}\n")
    pid = None
    try:
        pid = pid_of(pj, "optsite")
        r = client.post(f"/api/projects/{pid}/compose", headers=h, json={"action": "down", "services": ["--volumes"], "volumes": False})
        assert r.status_code == 422 and "Service inconnu" in r.text
    finally:
        if pid:
            client.delete(f"/api/projects/{pid}", headers=h)   # « désimporter » : le projet n'est plus suivi
        shutil.rmtree(d, ignore_errors=True)


# ---------- 7 : retirer un membre via la page Droits respecte les rangs ----------
def test_removing_a_member_from_the_rights_page_respects_ranks(sa):
    nid, ids = sa["nid"], sa["ids"]
    role = client.post("/api/rights/roles", headers=sa["h_sa-owner"], json={"name": "Retire seulement", "description": "", "caps": ["node.members.view", "node.members.remove"]})
    assert role.status_code == 200, role.text
    g = client.post("/api/rights/grants", headers=sa["h_sa-owner"], json={"subjectType": "user", "subjectId": ids["sa-ops"], "roleId": role.json()["id"], "nodeId": nid})
    assert g.status_code == 200, g.text
    # l'administrateur (rang supérieur) et le propriétaire restent intouchables, comme sur la page Membres
    for target in ("sa-admin", "sa-owner"):
        r = client.delete(f"/api/rights/grants/m:{nid}:{ids[target]}", headers=sa["h_sa-ops"])
        assert r.status_code in (400, 403), r.text
        assert db.one("SELECT 1 FROM node_members WHERE node_id=? AND user_id=?", (nid, ids[target]))
    # la route officielle donne la même réponse
    assert client.delete(f"/api/nodes/{nid}/members/{ids['sa-admin']}", headers=sa["h_sa-ops"]).status_code == 403
    # un administrateur ne retire pas un pair non plus, mais retire bien un rang inférieur
    assert client.delete(f"/api/rights/grants/m:{nid}:{ids['sa-owner']}", headers=sa["h_sa-admin"]).status_code in (400, 403)
    assert client.delete(f"/api/rights/grants/m:{nid}:{ids['sa-viewer']}", headers=sa["h_sa-admin"]).status_code == 200


# ---------- 1 : « tout Traefik » + sites.* ne suffit pas à réécrire un service ----------
def test_saving_a_site_cannot_rewrite_a_service_without_service_rights(sa):
    try:
        _site_vs_service(sa)
    finally:   # ne laisse rien derrière : d'autres tests comptent les routeurs de la zone
        for n in ("sa-bank", "sa-autre"):
            client.post("/api/traefik/site/delete", headers={**sa["h_sa-owner"], "X-Node": sa["nid"]}, json={"name": n, "deleteService": True})


def _site_vs_service(sa):
    nid = sa["nid"]
    hn = {"X-Node": nid}
    victim = {"name": "sa-bank", "router": {"rule": "Host(`bank.example.com`)", "service": "sa-bank-svc"}, "serviceName": "sa-bank-svc",
              "service": {"loadBalancer": {"servers": [{"url": "http://bank:80"}]}}}
    assert client.put("/api/traefik/site", headers={**sa["h_sa-owner"], **hn}, json=victim).status_code == 200
    role = client.post("/api/rights/roles", headers=sa["h_sa-owner"], json={"name": "Sites sans services", "description": "", "caps": ["traefik.all", "sites.view", "sites.create", "sites.edit"]})
    assert role.status_code == 200, role.text
    client.post("/api/rights/grants", headers=sa["h_sa-owner"], json={"subjectType": "user", "subjectId": sa["ids"]["sa-svc"], "roleId": role.json()["id"], "nodeId": nid})
    h = {**login("sa-svc"), **hn}
    evil = {"name": "sa-jetable", "router": {"rule": "Host(`x.attaquant.tld`)", "service": "sa-bank-svc"}, "serviceName": "sa-bank-svc",
            "service": {"loadBalancer": {"servers": [{"url": "http://attaquant:8080"}]}}}
    r = client.put("/api/traefik/site", headers=h, json=evil)
    assert r.status_code == 403 and "services" in r.text
    # le service de la victime n'a pas bougé ; sans corps de service, la création d'un routeur reste possible
    ok = client.put("/api/traefik/site", headers=h, json={"name": "sa-autre", "router": {"rule": "Host(`ok.example.com`)", "service": "sa-bank-svc"}})
    assert ok.status_code == 200, ok.text
