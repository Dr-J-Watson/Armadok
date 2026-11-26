"""Connexion directe d'un invité : lien à usage unique, mot de passe et 2FA définis sur le hub d'accueil, jamais transmis au hub d'origine ; tout se referme avec la liaison."""
import base64
import json
import secrets
import time

import pyotp
import pytest

from app import auth, db, hub_link, link_guests, link_proto as proto, link_relay, nodes
from test_projects import client, pj  # noqa: F401  (fixture)
from test_link_relay import Loopback

STRONG = "Un-mot-de-passe-solide-42"


def _unlock():   # nos essais ratés ne doivent pas verrouiller l'adresse de test pour les autres fichiers
    auth._attempts.clear()
    auth._user_attempts.clear()


@pytest.fixture()
def dr(pj):
    root = db.one("SELECT id FROM users WHERE source='env'")["id"]
    lid = secrets.token_hex(16)
    db.run("INSERT INTO hub_links(id,peer_hub_id,peer_name,peer_key,peer_ui_url,we_call,status,confirmed_local,confirmed_peer,host_enabled,direct_enabled,grace_hours,features,last_seen,created_at) "
           "VALUES(?,?,?,?,?,0,'paired',1,1,1,1,12,?,?,?)", (lid, secrets.token_hex(16), "Hub A", "k" * 44, "https://armadok.huba.fr", "guests,state," + proto.RELAY_FEATURE, time.time(), time.time()))
    link_guests.apply_in(lid, {"type": "teams", "teams": [{"id": "t1", "name": "Ops", "members": [{"id": str(root), "username": "alice"}]}]})
    gid = db.one("SELECT id FROM groups WHERE link_id=?", (lid,))["id"]
    db.run("INSERT INTO grants(subject_type,subject_id,role_id,node_id,created_at) VALUES('group',?,?,?,?)", (gid, db.one("SELECT id FROM roles WHERE builtin='admin'")["id"], nodes.local_node_id(), time.time()))
    local = db.run("INSERT INTO groups(name,owner_id,created_at) VALUES('Équipe origine',NULL,?)", (time.time(),))
    db.run("INSERT INTO group_members(group_id,user_id) VALUES(?,?)", (local, root))
    db.run("INSERT INTO link_shared_groups(link_id,group_id) VALUES(?,?)", (lid, local))
    hub_link._chans[lid] = Loopback(lid)
    _unlock()
    yield {"lid": lid, "h": pj["h"], "ref": str(root)}
    _unlock()
    hub_link._chans.pop(lid, None)
    db.run("DELETE FROM link_shared_groups WHERE link_id=?", (lid,))
    db.run("DELETE FROM groups WHERE id=?", (local,))
    link_guests.purge(lid)
    db.run("DELETE FROM hub_links WHERE id=?", (lid,))


def guest(lid):
    return db.one("SELECT * FROM users WHERE guest_link=?", (lid,))


def link_for(dr):
    r = client.post(f"/api/linked-setup/{dr['lid']}", headers=dr["h"])
    assert r.status_code == 200, r.text
    return r.json()


def test_the_setup_link_comes_from_the_host_through_the_origin(dr):
    out = link_for(dr)
    assert out["token"].startswith("gst_") and out["url"] == f"https://armadok.huba.fr/guest-setup#{out['token']}" and out["expiresIn"] == 600
    assert db.one("SELECT token_hash FROM guest_setup_tokens")["token_hash"] != out["token"]           # jamais en clair
    assert link_for(dr)["token"] != out["token"] and db.one("SELECT COUNT(*) c FROM guest_setup_tokens")["c"] == 1   # un seul lien actif par invité
    assert client.post(f"/api/linked-setup/{dr['lid']}").status_code == 401


def test_setup_link_refused_when_direct_mode_off_or_guest_not_active_or_not_in_a_shared_team(dr):
    db.run("UPDATE hub_links SET direct_enabled=0 WHERE id=?", (dr["lid"],))
    assert client.post(f"/api/linked-setup/{dr['lid']}", headers=dr["h"]).status_code == 403
    db.run("UPDATE hub_links SET direct_enabled=1 WHERE id=?", (dr["lid"],))
    db.run("UPDATE users SET guest_blocked=1, disabled=1 WHERE guest_link=?", (dr["lid"],))
    assert client.post(f"/api/linked-setup/{dr['lid']}", headers=dr["h"]).status_code == 403
    db.run("UPDATE users SET guest_blocked=0, disabled=0 WHERE guest_link=?", (dr["lid"],))
    db.run("DELETE FROM link_shared_groups WHERE link_id=?", (dr["lid"],))
    assert client.post(f"/api/linked-setup/{dr['lid']}", headers=dr["h"]).status_code == 403


def finish(dr, token, password=STRONG, code=None, secret=None):
    return client.post("/api/guest/setup/finish", json={"token": token, "password": password, "code": code or pyotp.TOTP(secret).now()})


def test_full_direct_flow_then_login_with_2fa_and_capped_rights(dr):
    token = link_for(dr)["token"]
    assert client.post("/api/guest/setup/start", json={"token": "gst_inconnu-inconnu"}).status_code == 401
    st = client.post("/api/guest/setup/start", json={"token": token}).json()
    assert st["username"] == "alice@hub-a" and st["secret"] and "<svg" in st["qr"]
    assert finish(dr, token, "court", secret=st["secret"]).status_code == 422                            # règles de mot de passe habituelles
    assert finish(dr, token, code="000000").status_code == 400                                           # mauvais code
    ok = finish(dr, token, secret=st["secret"])
    assert ok.status_code == 200 and len(ok.json()["recoveryCodes"]) == 10
    assert finish(dr, token, secret=st["secret"]).status_code == 401                                     # à usage unique
    g = guest(dr["lid"])
    assert g["guest_direct"] == 1 and g["totp_enabled"] == 1 and g["password_hash"] != link_guests.GUEST_PASSWORD
    # connexion directe : mot de passe PUIS code
    r = client.post("/api/auth/login", json={"username": "alice@hub-a", "password": STRONG}).json()
    assert r["mfa_required"] is True
    s = client.post("/api/auth/login/2fa", json={"mfaToken": r["mfa_token"], "code": pyotp.TOTP(st["secret"]).now()})
    assert s.status_code == 200, s.text
    h = {"Authorization": "Bearer " + s.json()["access_token"]}
    mine = client.get("/api/nodes", headers=h).json()
    assert [n["id"] for n in mine] == [nodes.local_node_id()] and "node.settings" not in mine[0]["caps"] and "node.backup" not in mine[0]["caps"]   # admin du nœud, mais plafonné
    assert client.get("/api/users", headers=h).status_code == 403 and client.get("/api/admin/links", headers=h).status_code == 403
    assert client.get("/api/admin/backups", headers=h).status_code == 403
    off = client.post("/api/auth/2fa/disable", headers=h, json={"password": STRONG, "code": pyotp.TOTP(st["secret"]).now()})
    assert off.status_code == 403 and guest(dr["lid"])["totp_enabled"] == 1                              # la 2FA d'un invité ne se retire pas
    bad = client.post("/api/auth/login", json={"username": "alice@hub-a", "password": "pas-le-bon-mot-de-passe"})
    assert bad.status_code == 401


def direct(dr):
    token = link_for(dr)["token"]
    st = client.post("/api/guest/setup/start", json={"token": token}).json()
    assert finish(dr, token, secret=st["secret"]).status_code == 200
    return st["secret"]


def can_login(dr):
    return client.post("/api/auth/login", json={"username": "alice@hub-a", "password": STRONG}).status_code == 200


def test_direct_access_closes_with_the_link_the_host_setting_and_the_grace(dr):
    direct(dr)
    assert can_login(dr)
    db.run("UPDATE hub_links SET direct_enabled=0 WHERE id=?", (dr["lid"],))
    assert not can_login(dr)                                                                              # le mode est retiré : refusé tout de suite
    db.run("UPDATE hub_links SET direct_enabled=1 WHERE id=?", (dr["lid"],))
    assert can_login(dr)
    db.run("UPDATE users SET guest_blocked=1 WHERE guest_link=?", (dr["lid"],))
    assert not can_login(dr)
    db.run("UPDATE users SET guest_blocked=0 WHERE guest_link=?", (dr["lid"],))
    hub_link._chans.pop(dr["lid"])
    db.run("UPDATE hub_links SET grace_hours=1, last_seen=? WHERE id=?", (time.time() - 3 * 3600, dr["lid"]))
    assert not can_login(dr)                                                                              # liaison coupée au-delà de la grâce : le mot de passe propre ne sauve rien
    link_guests.enforce()
    assert guest(dr["lid"])["disabled"] == 1
    db.run("UPDATE hub_links SET last_seen=? WHERE id=?", (time.time(), dr["lid"]))
    link_guests.enforce()
    assert can_login(dr)
    hub_link.revoke(dr["lid"], "root")
    assert guest(dr["lid"]) is None and not can_login(dr)                                                 # tout disparaît avec la liaison


def test_removing_direct_mode_erases_the_guest_credentials(dr):
    direct(dr)
    hub_link.update(dr["lid"], "root", direct=False)
    g = guest(dr["lid"])
    assert g["guest_direct"] == 0 and g["password_hash"] == link_guests.GUEST_PASSWORD and g["totp_enabled"] == 0 and not g["totp_secret"]
    assert client.post("/api/guest/setup/start", json={"token": "gst_inconnu-inconnu"}).status_code == 401


def test_setup_endpoints_are_rate_limited_and_expired_links_die(dr):
    token = link_for(dr)["token"]
    db.run("UPDATE guest_setup_tokens SET expires_at=?", (time.time() - 1,))
    assert client.post("/api/guest/setup/start", json={"token": token}).status_code == 401
    codes = [client.post("/api/guest/setup/start", json={"token": "gst_devine-devine-devine"}).status_code for _ in range(12)]
    assert 429 in codes
    auth._attempts.clear()
