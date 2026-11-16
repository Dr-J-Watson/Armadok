"""Double authentification exigée pour certains droits, par nœud : sans elle les droits n'existent pas ; garde-fous pour ne pas se verrouiller."""
import pytest
from fastapi.testclient import TestClient

from app import caps, db
from app.main import app

client = TestClient(app)
PW = "Un-Mot-De-Passe-Solide-9"


@pytest.fixture()
def world():
    root = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
    nid = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    h = {"Authorization": "Bearer " + root, "X-Node": nid}
    if not db.one("SELECT 1 FROM users WHERE username='mfa-adm'"):
        client.post("/api/users", headers=h, json={"username": "mfa-adm", "password": PW})
        uid = db.one("SELECT id FROM users WHERE username='mfa-adm'")["id"]
        db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (nid, uid, "admin", db.now()))
    uid = db.one("SELECT id FROM users WHERE username='mfa-adm'")["id"]
    db.run("UPDATE users SET totp_enabled=0 WHERE id=?", (uid,))
    db.run("UPDATE users SET totp_enabled=0 WHERE username='root'")
    db.run("UPDATE nodes SET mfa_caps='' WHERE id=?", (nid,))
    login = lambda: {"Authorization": "Bearer " + client.post("/api/auth/login", json={"username": "mfa-adm", "password": PW}).json()["access_token"], "X-Node": nid}
    yield {"h": h, "adm": login, "nid": nid, "uid": uid}
    db.run("UPDATE nodes SET mfa_caps='' WHERE id=?", (nid,))
    db.run("UPDATE users SET totp_enabled=0 WHERE username='root'")


def test_catalog_marks_the_sensitive_rights():
    assert {"certs.manage", "security.automod", "files.edit", "node.settings"} <= caps.MFA_SENSITIVE and caps.MFA_SENSITIVE <= set(caps.CATALOG)
    assert caps.parse_mfa('["certs.manage","nimporte.quoi"]') == frozenset({"certs.manage"}) and caps.parse_mfa("n'importe quoi") == frozenset()


def test_owner_needs_2fa_to_require_it_but_can_always_clear_the_requirement(world):
    h, nid = world["h"], world["nid"]
    r = client.put(f"/api/nodes/{nid}/mfa-policy", headers=h, json={"caps": ["certs.manage"]})
    assert r.status_code == 403 and "votre propre compte" in r.json()["detail"]                       # on ne se verrouille pas soi-même
    db.run("UPDATE users SET totp_enabled=1 WHERE username='root'")
    assert client.put(f"/api/nodes/{nid}/mfa-policy", headers=h, json={"caps": ["certs.manage", "security.automod"]}).status_code == 200
    assert client.put(f"/api/nodes/{nid}/mfa-policy", headers=h, json={"caps": ["droit.inconnu"]}).status_code == 422
    db.run("UPDATE users SET totp_enabled=0 WHERE username='root'")
    assert client.put(f"/api/nodes/{nid}/mfa-policy", headers=h, json={"caps": []}).status_code == 200  # retirer l'exigence reste toujours possible
    assert db.one("SELECT mfa_caps FROM nodes WHERE id=?", (nid,))["mfa_caps"] == ""


def test_a_member_without_2fa_loses_exactly_those_rights_until_it_is_enabled(world):
    h, nid, uid = world["h"], world["nid"], world["uid"]
    adm = world["adm"]()
    assert client.get(f"/api/traefik/certs", headers=adm).json()["canManage"] is True                    # avant : l'administrateur gère les certificats
    db.run("UPDATE users SET totp_enabled=1 WHERE username='root'")
    assert client.put(f"/api/nodes/{nid}/mfa-policy", headers=h, json={"caps": ["certs.manage", "security.automod"]}).status_code == 200
    node = client.get(f"/api/nodes/{nid}", headers=adm).json()                                           # même session : la 2FA se lit à chaque requête
    assert "certs.manage" not in node["caps"] and "security.automod" not in node["caps"] and "sites.edit" in node["caps"]   # seuls ces droits disparaissent
    assert set(node["mfaBlocked"]) == {"certs.manage", "security.automod"}
    r = client.post("/api/traefik/certs/purge", headers=adm, json={"items": [{"resolver": "x", "main": "a.exemple.fr"}]})
    assert r.status_code == 403 and "double authentification" in r.json()["detail"]                       # message qui dit quoi faire
    assert client.post("/api/obs/automod/run", headers=adm).status_code == 403
    assert client.get(f"/api/traefik/certs", headers=adm).json()["canManage"] is False                  # et l'interface le sait
    # il active la 2FA : les droits reviennent tout de suite
    db.run("UPDATE users SET totp_enabled=1 WHERE id=?", (uid,))
    node = client.get(f"/api/nodes/{nid}", headers=adm).json()
    assert "certs.manage" in node["caps"] and node["mfaBlocked"] == []
    db.run("UPDATE users SET totp_enabled=0 WHERE id=?", (uid,))


def test_only_owner_or_platform_admin_can_change_the_policy(world):
    nid = world["nid"]
    assert client.put(f"/api/nodes/{nid}/mfa-policy", headers=world["adm"](), json={"caps": []}).status_code == 403   # un administrateur du nœud ne l'affaiblit pas
    assert client.get(f"/api/nodes/{nid}/mfa-policy", headers=world["adm"]()).status_code == 200
    g = client.get(f"/api/nodes/{nid}/mfa-policy", headers=world["h"]).json()
    assert set(g["recommended"]) == set(caps.MFA_SENSITIVE) and any(m["username"] == "mfa-adm" and m["twoFactor"] is False for m in g["members"])
