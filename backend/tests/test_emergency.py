"""Accès d'urgence du super administrateur, avis aux propriétaires et actions rapides."""
import time
import uuid

import pyotp
import pytest
from fastapi.testclient import TestClient

from app import db
from app.crypto import enc
from app.main import app

PW = "un-mot-de-passe-solide"
client = TestClient(app)


def login(username: str, password: str = PW) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200 and "access_token" in r.json(), r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def new_node(name: str, owner_id: int, members=()) -> str:
    nid = str(uuid.uuid4())
    db.run("INSERT INTO nodes(id,name,kind,created_at) VALUES(?,?,?,?)", (nid, name, "remote", db.now()))
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?, 'owner', ?)", (nid, owner_id, db.now()))
    for uid, role in members:
        db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (nid, uid, role, db.now()))
    return nid


@pytest.fixture(scope="module")
def world():
    root = login("root", "admin-password-1")  # avant d'activer la 2FA : le jeton reste valable
    for name in ("erin", "frank", "gina"):
        assert client.post("/api/users", headers=root, json={"username": name, "password": PW, "canAddDomains": True}).status_code == 200
    ids = {r["username"]: r["id"] for r in db.rows("SELECT id, username FROM users")}
    w = {"root": root, "ids": ids, "erin": login("erin"), "frank": login("frank"), "gina": login("gina"), "secret": pyotp.random_base32()}
    yield w
    db.run("UPDATE users SET totp_enabled=0, totp_secret=NULL, totp_last_step=0 WHERE username='root'")  # ne pas gêner les autres tests
    db.run("DELETE FROM nodes WHERE kind='remote'")


def code(w) -> str:
    """Un code TOTP valide ; on remet le compteur à zéro pour pouvoir en utiliser plusieurs dans la même fenêtre."""
    db.run("UPDATE users SET totp_last_step=0 WHERE username='root'")
    return pyotp.TOTP(w["secret"]).now()


def enable_2fa(w) -> None:
    db.run("UPDATE users SET totp_enabled=1, totp_secret=?, totp_last_step=0 WHERE username='root'", (enc(w["secret"]),))


def test_requires_two_factor_and_a_reason(world):
    nid = new_node("Lyon", world["ids"]["erin"], [(world["ids"]["frank"], "editor")])
    world["nid"] = nid
    r = client.post(f"/api/admin/nodes/{nid}/emergency", headers=world["root"], json={"reason": "panne signalée par le propriétaire", "code": "123456"})
    assert r.status_code == 403 and r.json()["code"] == "needs_2fa" and "double authentification" in r.json()["detail"]
    enable_2fa(world)
    assert client.post(f"/api/admin/nodes/{nid}/emergency", headers=world["root"], json={"reason": "court", "code": code(world)}).status_code == 422
    r = client.post(f"/api/admin/nodes/{nid}/emergency", headers=world["root"], json={"reason": "panne signalée par le propriétaire", "code": "000000"})
    assert r.status_code == 403 and "invalide" in r.json()["detail"]  # jamais 401 : le front y verrait une session expirée
    assert client.get(f"/api/nodes/{nid}", headers=world["root"]).status_code == 404  # toujours aucun accès


def test_only_platform_admins(world):
    nid = world["nid"]
    for who in ("erin", "frank", "gina"):
        assert client.get("/api/admin/nodes", headers=world[who]).status_code == 403
        assert client.post(f"/api/admin/nodes/{nid}/emergency", headers=world[who], json={"reason": "je veux voir", "code": "123456"}).status_code == 403
        assert client.post(f"/api/admin/nodes/{nid}/actions", headers=world[who], json={"action": "suspend", "code": "1"}).status_code == 403


def test_emergency_session_and_owner_notice(world):
    root, nid, ids = world["root"], world["nid"], world["ids"]
    r = client.post(f"/api/admin/nodes/{nid}/emergency", headers=root, json={"reason": "le propriétaire ne répond plus", "code": code(world)})
    assert r.status_code == 200 and r.json()["expires"] > time.time() + 25 * 60
    # accès d'administrateur du nœud, pas plus : il ne peut ni le supprimer ni le transférer
    n = client.get(f"/api/nodes/{nid}", headers=root).json()
    assert n["role"] == "admin" and n["emergency"]
    assert client.put(f"/api/nodes/{nid}", headers=root, json={"description": "remis en état"}).status_code == 200
    assert client.get(f"/api/nodes/{nid}/members", headers=root).status_code == 200
    assert client.delete(f"/api/nodes/{nid}", headers=root).status_code == 403
    assert client.post(f"/api/nodes/{nid}/transfer", headers=root, json={"userId": ids["gina"]}).status_code == 403
    assert nid in [x["id"] for x in client.get("/api/nodes", headers=root).json()]
    # l'avis du propriétaire : motif + actions faites pendant la session, non supprimable
    notices = client.get("/api/notices", headers=world["erin"]).json()
    assert len(notices) == 1 and "ne répond plus" in notices[0]["message"]
    assert [a["action"] for a in notices[0]["actions"]] == ["node.update"]
    # les autres membres ne reçoivent pas l'avis d'urgence ; un étranger non plus
    assert client.get("/api/notices", headers=world["frank"]).json() == []
    assert client.get("/api/notices", headers=world["gina"]).json() == []
    assert client.post("/api/notices/ack", headers=world["erin"]).status_code == 200
    assert client.get("/api/notices", headers=world["erin"]).json() == []
    again = client.get("/api/notices", headers=world["erin"], params={"all": True}).json()
    assert len(again) == 1 and again[0]["ackedAt"] and again[0]["actions"]  # consultable, jamais effacé


def test_session_ends_and_expires(world):
    root, nid = world["root"], world["nid"]
    assert client.post(f"/api/admin/nodes/{nid}/emergency/end", headers=root).status_code == 200
    assert client.get(f"/api/nodes/{nid}", headers=root).status_code == 404
    client.post(f"/api/admin/nodes/{nid}/emergency", headers=root, json={"reason": "deuxième intervention nécessaire", "code": code(world)})
    assert client.get(f"/api/nodes/{nid}", headers=root).status_code == 200
    db.run("UPDATE emergency_sessions SET expires_at=? WHERE node_id=?", (time.time() - 1, nid))  # la session expire seule
    assert client.get(f"/api/nodes/{nid}", headers=root).status_code == 404


def test_admin_overview(world):
    rows = client.get("/api/admin/nodes", headers=world["root"]).json()
    mine = next(r for r in rows if r["id"] == world["nid"])
    assert [o["username"] for o in mine["owners"]] == ["erin"] and mine["members"] == 2
    imp = client.get(f"/api/admin/nodes/{world['nid']}/impact", headers=world["root"]).json()
    assert imp["memberNames"] == ["erin", "frank"]


def test_quick_actions_need_second_factor_and_never_touch_the_local_node(world):
    root, nid = world["root"], world["nid"]
    r = client.post(f"/api/admin/nodes/{nid}/actions", headers=root, json={"action": "suspend", "code": "000000"})
    assert r.status_code == 403
    assert db.one("SELECT status FROM nodes WHERE id=?", (nid,))["status"] == "active"
    local = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    assert client.post(f"/api/admin/nodes/{local}/actions", headers=root, json={"action": "suspend", "code": code(world)}).status_code == 400
    assert client.post(f"/api/admin/nodes/{nid}/actions", headers=root, json={"action": "inconnue", "code": code(world)}).status_code == 422


def test_suspend_and_resume(world):
    root, nid = world["root"], world["nid"]
    r = client.post(f"/api/admin/nodes/{nid}/actions", headers=root, json={"action": "suspend", "code": code(world), "note": "enquête en cours"})
    assert r.status_code == 200
    for who in ("erin", "frank"):  # fermé à ses membres, avec l'état visible dans leur liste
        assert client.get("/api/traefik/overview", headers={**world[who], "X-Node": nid}).status_code == 423
        assert next(n for n in client.get("/api/nodes", headers=world[who]).json() if n["id"] == nid)["status"] == "suspended"
    assert client.put(f"/api/nodes/{nid}", headers=world["erin"], json={"name": "x"}).status_code == 423
    n = client.get("/api/notices", headers=world["erin"]).json()
    assert any("suspendu" in x["message"] and "enquête en cours" in x["message"] for x in n)
    assert client.post(f"/api/admin/nodes/{nid}/actions", headers=root, json={"action": "resume", "code": code(world)}).status_code == 200
    assert client.get("/api/nodes", headers=world["erin"]).json()[0]["status"] == "active"
    client.post("/api/notices/ack", headers=world["erin"])
    client.post("/api/notices/ack", headers=world["frank"])


def test_delete_and_ban_then_restore(world):
    root, ids = world["root"], world["ids"]
    nid = new_node("Marseille", ids["gina"], [(ids["frank"], "viewer")])
    other = new_node("Marseille 2", ids["gina"])
    r = client.post(f"/api/admin/nodes/{nid}/actions", headers=root, json={"action": "delete_and_ban", "code": code(world)})
    assert r.status_code == 200
    # bannissement immédiat : le jeton qu'elle possède déjà ne sert plus, et elle ne peut plus se reconnecter
    assert client.get("/api/nodes", headers=world["gina"]).status_code == 401
    assert client.post("/api/auth/login", json={"username": "gina", "password": PW}).status_code == 401
    # ses autres nœuds sont suspendus, le nœud supprimé disparaît pour les membres mais reste visible de l'admin
    assert db.one("SELECT status FROM nodes WHERE id=?", (other,))["status"] == "suspended"
    assert client.get(f"/api/nodes/{nid}", headers=world["frank"]).status_code == 404
    row = next(x for x in client.get("/api/admin/nodes", headers=root).json() if x["id"] == nid)
    assert row["deletedAt"] and row["purgeAt"] > time.time() + 29 * 86400
    # l'avis reste pour le propriétaire (consultable s'il est réactivé) et les membres sont prévenus
    assert any("supprimé" in x["message"] for x in client.get("/api/notices", headers=world["frank"]).json())
    assert db.one("SELECT COUNT(*) c FROM node_notices WHERE user_id=? AND kind='banned'", (ids["gina"],))["c"] == 1
    # restauration
    assert client.post(f"/api/admin/nodes/{nid}/actions", headers=root, json={"action": "restore", "code": code(world)}).status_code == 200
    assert client.get(f"/api/nodes/{nid}", headers=world["frank"]).status_code == 200
    # purge : seulement sur un nœud déjà supprimé
    assert client.post(f"/api/admin/nodes/{nid}/actions", headers=root, json={"action": "purge", "code": code(world)}).status_code == 400
    client.post(f"/api/admin/nodes/{nid}/actions", headers=root, json={"action": "delete", "code": code(world)})
    assert client.post(f"/api/admin/nodes/{nid}/actions", headers=root, json={"action": "purge", "code": code(world)}).status_code == 200
    assert db.one("SELECT 1 FROM nodes WHERE id=?", (nid,)) is None


def test_cannot_ban_a_platform_admin(world):
    root, ids = world["root"], world["ids"]
    nid = new_node("Admin node", ids["erin"])
    db.run("UPDATE users SET role='admin' WHERE id=?", (ids["erin"],))
    r = client.post(f"/api/admin/nodes/{nid}/actions", headers=root, json={"action": "ban_owner", "code": code(world)})
    assert r.status_code == 400 and "administrateur" in r.json()["detail"]
    db.run("UPDATE users SET role='user' WHERE id=?", (ids["erin"],))
    assert db.one("SELECT disabled FROM users WHERE id=?", (ids["erin"],))["disabled"] == 0


def test_take_ownership_and_remove_quota(world):
    root, ids, nid = world["root"], world["ids"], world["nid"]
    assert client.post(f"/api/admin/nodes/{nid}/actions", headers=root, json={"action": "remove_quota", "code": code(world)}).status_code == 200
    assert db.one("SELECT max_nodes FROM users WHERE id=?", (ids["erin"],))["max_nodes"] == 0
    assert client.post(f"/api/admin/nodes/{nid}/actions", headers=root, json={"action": "take_ownership", "code": code(world)}).status_code == 200
    assert client.get(f"/api/nodes/{nid}", headers=root).json()["role"] == "owner"  # propriété permanente, sans session d'urgence
    assert client.get(f"/api/nodes/{nid}", headers=world["erin"]).json()["role"] == "viewer"
    assert any("repris la propriété" in x["message"] for x in client.get("/api/notices", headers=world["erin"]).json())


def test_audit_trail_marks_emergency_actions(world):
    from app import audit as a
    rows = a.tail(200, node=world["nid"])
    assert any(e["action"] == "admin.emergency.start" and e.get("session") for e in rows)
    assert any(e["action"].startswith("admin.node.") for e in rows)
