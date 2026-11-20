"""Correctifs de l'audit de sécurité (AUDIT-SECURITE.md) : un test par faille."""
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
    names = ("sa-owner", "sa-admin", "sa-editor", "sa-viewer", "sa-ops", "sa-svc", "sa-noenv")
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


# ---------- 6 : la carte des routeurs en direct suit la portée de la personne ----------
def test_live_routers_are_limited_to_the_callers_scope(sa, monkeypatch):
    from app import routes_traefik
    fake = {"available": True, "version": "3", "entrypoints": ["web"], "routers": {"http/sa-secret@file": {"status": "enabled", "provider": "file", "error": None},
                                                                                   "http/autre@docker": {"status": "enabled", "provider": "docker", "error": None}}}
    monkeypatch.setattr(routes_traefik, "live_status", lambda node: fake)
    hn = {"X-Node": sa["nid"]}
    full = client.get("/api/traefik/live", headers={**sa["h_sa-owner"], **hn}).json()
    assert set(full["routers"]) == set(fake["routers"])                      # tout Traefik : la carte complète
    scoped = client.get("/api/traefik/live", headers={**sa["h_sa-editor"], **hn})
    assert scoped.status_code == 200 and scoped.json()["routers"] == {}      # restreint à ses domaines : aucun routeur d'autrui
    assert scoped.json()["version"] == "3" and fake["routers"]               # le reste de l'état est conservé, le cache n'est pas modifié


# ---------- 2 : élargir un rôle ne dépasse pas le plafond de chaque nœud où il est attribué ----------
def test_widening_a_role_respects_the_ceiling_of_every_node_it_is_granted_on(sa):
    nid = sa["nid"]
    h = sa["h_sa-admin"]
    mine = client.post("/api/nodes", headers=h, json={"name": "sa-noeud-admin"})        # son propre nœud : il y a tous les droits (réunion de plafonds)
    assert mine.status_code in (200, 201), mine.text
    role = client.post("/api/rights/roles", headers=h, json={"name": "Anodin", "description": "", "caps": ["sites.view"]})
    assert role.status_code == 200, role.text
    rid = role.json()["id"]
    g = client.post("/api/rights/grants", headers=h, json={"subjectType": "user", "subjectId": sa["ids"]["sa-viewer"], "roleId": rid, "nodeId": nid})
    assert g.status_code == 200, g.text
    # docker.exec n'est pas donné aux administrateurs du nœud : il l'a seulement sur son propre nœud, pas ici
    r = client.put(f"/api/rights/roles/{rid}", headers=h, json={"name": "Anodin", "description": "", "caps": ["sites.view", "docker.view", "docker.logs", "docker.exec"]})
    assert r.status_code == 403, r.text
    assert "docker.exec" not in db.one("SELECT caps FROM roles WHERE id=?", (rid,))["caps"]
    # retirer ou ajouter un droit qu'il a sur ce nœud reste permis
    assert client.put(f"/api/rights/roles/{rid}", headers=h, json={"name": "Anodin", "description": "", "caps": ["sites.view", "sites.create"]}).status_code == 200
    assert client.put(f"/api/rights/roles/{rid}", headers=h, json={"name": "Anodin", "description": "", "caps": ["sites.view"]}).status_code == 200


# ---------- 3 et 4 : sans « variables .env », aucune valeur du .env ne sort (image remplacée, message de validation) ----------
def test_env_values_do_not_leak_to_people_without_the_env_right(sa, pj, fakecli):
    nid, h_root = sa["nid"], pj["h"]
    d = pj["base"] / "envsite"
    d.mkdir(exist_ok=True)
    (d / "docker-compose.yml").write_text("services:\n  web:\n    image: \"reg.example/app:${SECRET_TAG}\"\n")
    (d / ".env").write_text("SECRET_TAG=hunter2\n")
    pid = None
    try:
        pid = pid_of(pj, "envsite")
        role = client.post("/api/rights/roles", headers=sa["h_sa-owner"], json={"name": "Voit sans .env", "description": "", "caps": ["docker.view", "projects.view", "projects.files"]})
        assert role.status_code == 200, role.text
        g = client.post("/api/rights/grants", headers=sa["h_sa-owner"], json={"subjectType": "user", "subjectId": sa["ids"]["sa-noenv"], "roleId": role.json()["id"], "nodeId": nid})
        assert g.status_code == 200, g.text
        h = {**login("sa-noenv"), "X-Node": nid}
        for url in (f"/api/projects/{pid}", "/api/projects", f"/api/projects/{pid}/images"):
            r = client.get(url, headers=h)
            assert r.status_code == 200, (url, r.text)
            assert "hunter2" not in r.text, url
        img = next(sv["image"] for sv in client.get(f"/api/projects/{pid}", headers=h).json()["services"])
        assert img == "reg.example/app:${SECRET_TAG}"
        root_img = next(sv["image"] for sv in client.get(f"/api/projects/{pid}", headers={**h_root, "X-Node": nid}).json()["services"])
        assert root_img == "reg.example/app:hunter2"                                   # qui a le droit voit toujours la valeur remplacée
        content = "services:\n  x: {image: alpine}\n"
        low = client.post(f"/api/projects/{pid}/validate", headers=h, json={"content": content}).json()
        assert "--env-file" in low["message"]                                          # le « docker » factice recopie ses arguments : le .env n'est pas chargé
        high = client.post(f"/api/projects/{pid}/validate", headers={**h_root, "X-Node": nid}, json={"content": content}).json()
        assert "--env-file" not in high["message"]
    finally:
        if pid:
            client.delete(f"/api/projects/{pid}", headers=h_root)
        shutil.rmtree(d, ignore_errors=True)


# ---------- pistes écartées par l'audit, corrigées par prudence ----------
def test_credentials_inside_urls_count_as_secrets_in_templates():
    from app import project_templates as pt
    env = "APP_URL=https://exemple.fr\nDATABASE_URL=postgres://app:S3cr3t@db/app\nSENTRY_DSN=https://abc@o1.ingest.sentry.io/2\nREDIS_PASSWORD=x\n"
    flags = {v["key"]: v["secret"] for v in pt.parse_env(env)}
    assert flags == {"APP_URL": False, "DATABASE_URL": True, "SENTRY_DSN": True, "REDIS_PASSWORD": True}
    blank = pt.blank_secrets(env)
    assert "S3cr3t" not in blank and "APP_URL=https://exemple.fr" in blank and "DATABASE_URL=\n" in blank


def test_node_audit_masks_visitor_ips_without_the_reveal_right(sa):
    from app import audit as audit_log
    nid = sa["nid"]
    audit_log.audit("sa-owner", "ip.ban", "203.0.113.77 bloquée", node=nid)
    role = client.post("/api/rights/roles", headers=sa["h_sa-owner"], json={"name": "Auditeur sans IP", "description": "", "caps": ["node.audit"]})
    assert role.status_code == 200, role.text
    g = client.post("/api/rights/grants", headers=sa["h_sa-owner"], json={"subjectType": "user", "subjectId": sa["ids"]["sa-viewer"], "roleId": role.json()["id"], "nodeId": nid})
    assert g.status_code == 200, g.text
    seen = client.get(f"/api/nodes/{nid}/audit", headers=sa["h_sa-viewer"])
    assert seen.status_code == 200 and "203.0.113.77" not in seen.text and "203.0." in seen.text
    assert "203.0.113.77" in client.get(f"/api/nodes/{nid}/audit", headers=sa["h_sa-owner"]).text
