"""Équipes partagées et comptes invités : accueil explicite, plafond de droits, délai de grâce, révocation, aucune confusion avec les comptes locaux."""
import json
import secrets
import time

import pytest

from app import auth, caps as capmod, db, hub_link, link_guests, nodes, perms, routes_links
from app.traefik_store import StoreError
from test_projects import client, pj  # noqa: F401  (fixture)

BASE = "/api/admin/links"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    def fake(p, code):
        if code != "000000":
            raise StoreError("Code de double authentification invalide", 403)
    monkeypatch.setattr(routes_links, "need_second_factor", fake)

    def wipe():
        for g in db.rows("SELECT id FROM groups WHERE link_id<>''"):
            db.run("DELETE FROM grants WHERE subject_type='group' AND subject_id=?", (g["id"],))
            db.run("DELETE FROM groups WHERE id=?", (g["id"],))
        db.run("DELETE FROM users WHERE source='guest'")
        db.run("DELETE FROM users WHERE username LIKE 'alice@%' AND source<>'env'")
        db.run("DELETE FROM hub_links")
        link_guests._sent.clear()
    wipe()
    yield
    wipe()


def usable_link(host=True, grace=12, last_seen=None):
    lid = secrets.token_hex(16)
    db.run("INSERT INTO hub_links(id,peer_hub_id,peer_name,peer_key,we_call,status,confirmed_local,confirmed_peer,host_enabled,grace_hours,features,last_seen,created_at) "
           "VALUES(?,?,?,?,0,'paired',1,1,?,?,?,?,?)", (lid, secrets.token_hex(16), "Hub B", "k" * 44, 1 if host else 0, grace, "guests,state", last_seen if last_seen is not None else time.time(), time.time()))
    return lid


def teams(*specs):
    return {"type": "teams", "teams": [{"id": tid, "name": name, "members": [{"id": uid, "username": un} for uid, un in members]} for tid, name, members in specs]}


def guest(name):
    return db.one("SELECT * FROM users WHERE username=?", (name,))


def test_hosting_must_be_allowed_and_link_confirmed_by_both():
    lid = usable_link(host=False)
    assert link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice")])))["accepted"] is False
    assert db.one("SELECT COUNT(*) c FROM users WHERE source='guest'")["c"] == 0
    db.run("UPDATE hub_links SET host_enabled=1, confirmed_peer=0 WHERE id=?", (lid,))
    assert link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice")])))["accepted"] is False       # pas confirmée par l'autre
    db.run("UPDATE hub_links SET confirmed_peer=1, features='state' WHERE id=?", (lid,))
    assert link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice")])))["accepted"] is False       # il ne parle pas des invités
    assert db.one("SELECT COUNT(*) c FROM users WHERE source='guest'")["c"] == 0


def test_teams_become_external_groups_and_members_passwordless_guests(pj):
    lid = usable_link()
    ack = link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice"), ("u2", "bob")]), ("t2", "Dev", [("u2", "bob")])))
    assert ack == {"type": "teams_ack", "accepted": True, "teams": 2, "guests": 2}
    a = guest("alice@hub-b")
    assert a["source"] == "guest" and a["role"] == "user" and a["disabled"] == 0 and a["guest_link"] == lid and a["max_nodes"] == 0
    assert a["password_hash"] == link_guests.GUEST_PASSWORD
    gs = {g["peer_ref"]: g for g in db.rows("SELECT * FROM groups WHERE link_id=?", (lid,))}
    assert gs["t1"]["name"] == "Ops · Hub B" and gs["t1"]["owner_id"] is None
    assert {m["user_id"] for m in db.rows("SELECT user_id FROM group_members WHERE group_id=?", (gs["t2"]["id"],))} == {guest("bob@hub-b")["id"]}
    # aucune connexion par mot de passe, quoi qu'on essaie
    for pw in ("!invité", "", "x" * 20):
        assert client.post("/api/auth/login", json={"username": "alice@hub-b", "password": pw or "vide-vide-vide"}).status_code == 401
    # ils ne sont ni dans la liste des comptes locaux, ni dans les annuaires, ni ajoutables comme membres
    assert "alice@hub-b" not in json.dumps(client.get("/api/admin/users", headers=pj["h"]).json())
    assert "alice@hub-b" not in json.dumps(client.get("/api/users-directory", headers=pj["h"]).json())
    assert client.get("/api/users-lookup", params={"username": "alice@hub-b"}, headers=pj["h"]).status_code == 404
    nid = nodes.local_node_id()
    assert client.post(f"/api/nodes/{nid}/members", headers=pj["h"], json={"username": "alice@hub-b", "role": "viewer"}).status_code == 404
    g = client.post("/api/rights/grants", headers=pj["h"], json={"subjectType": "user", "subjectId": a["id"], "roleId": db.one("SELECT id FROM roles WHERE builtin='viewer'")["id"], "nodeId": nid})
    assert g.status_code == 409 and "équipe" in g.text


def test_external_groups_are_listed_for_granting_but_never_editable(pj):
    lid = usable_link()
    link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice")])))
    gid = db.one("SELECT id FROM groups WHERE link_id=?", (lid,))["id"]
    out = [g for g in client.get("/api/rights/groups", headers=pj["h"]).json() if g["id"] == gid][0]
    assert out["external"] == {"linkId": lid, "hub": "Hub B"} and out["members"][0]["username"] == "alice@hub-b"
    assert client.put(f"/api/rights/groups/{gid}", headers=pj["h"], json={"name": "x"}).status_code == 409
    assert client.put(f"/api/rights/groups/{gid}/members", headers=pj["h"], json={"userIds": []}).status_code == 409
    assert client.delete(f"/api/rights/groups/{gid}", headers=pj["h"]).status_code == 409
    # …mais l'administrateur du nœud peut lui donner des droits chez lui
    nid = nodes.local_node_id()
    r = client.post("/api/rights/grants", headers=pj["h"], json={"subjectType": "group", "subjectId": gid, "roleId": db.one("SELECT id FROM roles WHERE builtin='viewer'")["id"], "nodeId": nid})
    assert r.status_code == 200, r.text


def test_guest_rights_are_capped_whatever_the_host_grants():
    lid = usable_link()
    link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice")])))
    a, g = guest("alice@hub-b"), db.one("SELECT id FROM groups WHERE link_id=?", (lid,))
    admin_role = db.one("SELECT id FROM roles WHERE builtin='admin'")["id"]
    node = db.one("SELECT * FROM nodes WHERE id=?", (nodes.local_node_id(),))
    db.run("INSERT INTO grants(subject_type,subject_id,role_id,node_id,created_at) VALUES('group',?,?,?,?)", (g["id"], admin_role, node["id"], time.time()))
    ctx = nodes._make_ctx(perms.principal_from_row(a), node)
    assert ctx and "sites.view" in ctx.caps and "docker.control" in ctx.caps                         # ce qui sert à travailler est là
    assert not (ctx.caps & capmod.GUEST_FORBIDDEN)                                                  # jamais propriété, membres, connexion, secrets, sauvegardes, exécution
    assert not ctx.caps & {"node.members.add", "node.settings", "node.backup", "node.restore", "docker.exec", "projects.env", "node.transfer", "node.delete"}
    local = nodes._make_ctx(perms.principal_from_row(db.one("SELECT * FROM users WHERE source='env'")), node)
    assert "node.settings" in local.caps                                                            # …alors qu'un compte local administrateur les a


def test_changes_at_the_origin_propagate_removals_and_grants_fall_with_the_team():
    lid = usable_link()
    link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice"), ("u2", "bob")])))
    gid = db.one("SELECT id FROM groups WHERE link_id=?", (lid,))["id"]
    db.run("INSERT INTO grants(subject_type,subject_id,role_id,node_id,created_at) VALUES('group',?,?,?,?)", (gid, db.one("SELECT id FROM roles WHERE builtin='viewer'")["id"], nodes.local_node_id(), time.time()))
    link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice")])))
    assert guest("bob@hub-b") is None and guest("alice@hub-b")                                      # sorti de toutes les équipes : son compte disparaît
    assert db.one("SELECT COUNT(*) c FROM grants WHERE subject_type='group' AND subject_id=?", (gid,))["c"] == 1
    link_guests.apply_in(lid, teams())                                                              # plus aucune équipe partagée
    assert db.one("SELECT COUNT(*) c FROM groups WHERE link_id=?", (lid,))["c"] == 0 and guest("alice@hub-b") is None
    assert db.one("SELECT COUNT(*) c FROM grants WHERE subject_type='group' AND subject_id=?", (gid,))["c"] == 0


def test_hostile_payloads_are_bounded_and_never_take_over_a_local_account():
    lid = usable_link()
    db.run("INSERT INTO users(username,password_hash,role,source,created_at) VALUES('alice@hub-b','x','user','local',?)", (time.time(),))
    assert link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice")])))["accepted"]
    local = db.one("SELECT * FROM users WHERE username='alice@hub-b'")
    assert local["source"] == "local" and local["guest_link"] == ""                                 # le compte local n'est ni écrasé ni réutilisé
    assert db.one("SELECT username FROM users WHERE guest_link=?", (lid,))["username"].startswith("alice@hub-b~")
    bad = {"type": "teams", "teams": "pas une liste"}
    assert link_guests.apply_in(lid, bad)["accepted"] is False
    assert link_guests.apply_in(lid, {"type": "teams", "teams": [{"id": "t", "name": "n", "members": "x"}]})["accepted"] is False
    big = teams(*[(f"t{i}", f"T{i}", [(f"u{i}-{j}", "x") for j in range(12)]) for i in range(90)])   # 1080 personnes : au-delà de la limite
    assert link_guests.apply_in(lid, big)["accepted"] is False
    assert link_guests.apply_in(lid, {"type": "teams", "teams": [{"id": "t" * 300, "name": "N\nom<b>", "members": [{"id": "1", "username": "ev il/../x"}]}]})["accepted"]
    names = [u["username"] for u in db.rows("SELECT username FROM users WHERE guest_link=?", (lid,))]
    assert all("/" not in n and " " not in n and "\n" not in n for n in names)


def test_grace_period_suspends_then_restores_but_never_unblocks():
    lid = usable_link(grace=2)
    link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice"), ("u2", "bob")])))
    bob = guest("bob@hub-b")
    link_guests.set_blocked(lid, bob["id"], True, "root")
    assert guest("bob@hub-b")["disabled"] == 1
    link_guests.enforce()
    assert guest("alice@hub-b")["disabled"] == 0                                                    # liaison coupée depuis un instant : dans le délai
    db.run("UPDATE hub_links SET last_seen=? WHERE id=?", (time.time() - 3 * 3600, lid))            # coupée depuis 3 h, grâce de 2 h
    tv = db.one("SELECT token_version t FROM users WHERE username='alice@hub-b'")["t"]
    link_guests.enforce()
    a = guest("alice@hub-b")
    assert a["disabled"] == 1 and a["guest_suspended"] == 1 and a["token_version"] == tv + 1         # suspendu, et ses sessions ouvertes sont invalidées
    db.run("UPDATE hub_links SET last_seen=? WHERE id=?", (time.time(), lid))                       # la liaison revient
    link_guests.enforce()
    assert guest("alice@hub-b")["disabled"] == 0 and guest("bob@hub-b")["disabled"] == 1           # celui qu'on avait écarté le reste


def test_zero_grace_cuts_at_once_and_revocation_or_hosting_removal_purges():
    lid = usable_link(grace=0, last_seen=time.time() - 5)
    link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice")])))
    link_guests.enforce()
    assert guest("alice@hub-b")["disabled"] == 1                                                    # 0 h : dès que la connexion n'est plus là
    db.run("UPDATE hub_links SET grace_hours=12 WHERE id=?", (lid,))
    link_guests.enforce()
    assert guest("alice@hub-b")["disabled"] == 0
    db.run("UPDATE hub_links SET host_enabled=0 WHERE id=?", (lid,))
    link_guests.enforce()
    assert guest("alice@hub-b") is None and db.one("SELECT COUNT(*) c FROM groups WHERE link_id=?", (lid,))["c"] == 0
    link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice")])))
    db.run("UPDATE hub_links SET host_enabled=1 WHERE id=?", (lid,))
    link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice")])))
    hub_link.revoke(lid, "root")
    assert guest("alice@hub-b") is None


def test_origin_shares_only_chosen_local_teams_and_announces_changes(pj):
    lid = usable_link()
    a = db.run("INSERT INTO users(username,password_hash,role,source,created_at) VALUES('mine-1','x','user','local',?)", (time.time(),))
    g1 = db.run("INSERT INTO groups(name,owner_id,created_at) VALUES('Équipe A',NULL,?)", (time.time(),))
    g2 = db.run("INSERT INTO groups(name,owner_id,created_at) VALUES('Secrète',NULL,?)", (time.time(),))
    ext = db.run("INSERT INTO groups(name,owner_id,created_at,link_id,peer_ref) VALUES('Reçue',NULL,?,?,?)", (time.time(), lid, "z"))
    db.run("INSERT INTO group_members(group_id,user_id) VALUES(?,?)", (g1, a))
    db.run("INSERT INTO group_members(group_id,user_id) VALUES(?,?)", (g2, a))
    try:
        assert client.put(f"{BASE}/{lid}/teams", headers=pj["h"], json={"groupIds": [g1], "code": "123456"}).status_code == 403
        assert client.put(f"{BASE}/{lid}/teams", headers=pj["h"], json={"groupIds": [ext], "code": "000000"}).status_code == 400    # jamais une équipe reçue d'un autre hub
        assert client.put(f"{BASE}/{lid}/teams", headers=pj["h"], json={"groupIds": [g1], "code": "000000"}).json() == {"shared": [g1]}
        p = link_guests.payload(lid)
        assert [t["name"] for t in p["teams"]] == ["Équipe A"] and p["teams"][0]["members"] == [{"id": str(a), "username": "mine-1"}]
        sent = []

        class Fake:
            def post(self, m): sent.append(m)
        hub_link._chans[lid] = Fake()
        link_guests._sent.clear()
        link_guests.sync_out(lid)
        link_guests.sync_out(lid)
        assert len(sent) == 1 and sent[0]["type"] == "teams"                                          # rien n'est renvoyé tant que rien ne change
        db.run("DELETE FROM group_members WHERE group_id=?", (g1,))
        link_guests.sync_out(lid)
        assert len(sent) == 2 and sent[1]["teams"][0]["members"] == []
        view = client.get(f"{BASE}/{lid}/teams", headers=pj["h"]).json()
        assert {t["name"]: t["shared"] for t in view["mine"]}["Équipe A"] is True and all(t["name"] != "Reçue" for t in view["mine"])
    finally:
        hub_link._chans.pop(lid, None)
        db.run("DELETE FROM users WHERE id=?", (a,))
        db.run("DELETE FROM groups WHERE id IN (?,?)", (g1, g2))


def test_host_admin_settings_need_2fa_and_guests_can_be_blocked(pj):
    lid = usable_link(host=False)
    assert client.patch(f"{BASE}/{lid}", headers=pj["h"], json={"hostEnabled": True, "code": "123456"}).status_code == 403
    r = client.patch(f"{BASE}/{lid}", headers=pj["h"], json={"hostEnabled": True, "code": "000000"})
    assert r.status_code == 200 and hub_link.get(lid)["host_enabled"] == 1
    link_guests.apply_in(lid, teams(("t1", "Ops", [("u1", "alice")])))
    uid = guest("alice@hub-b")["id"]
    assert client.post(f"{BASE}/{lid}/guests/{uid}/block", headers=pj["h"], json={"blocked": True}).json()["blocked"] is True
    assert guest("alice@hub-b")["disabled"] == 1
    assert client.post(f"{BASE}/{lid}/guests/{uid}/block", headers=pj["h"], json={"blocked": False, "code": "123456"}).status_code == 403
    assert client.post(f"{BASE}/{lid}/guests/{uid}/block", headers=pj["h"], json={"blocked": False, "code": "000000"}).json()["blocked"] is False
    listing = client.get(f"{BASE}/{lid}/guests", headers=pj["h"]).json()
    assert listing[0]["username"] == "alice@hub-b" and listing[0]["teams"] == ["Ops · Hub B"]
    client.patch(f"{BASE}/{lid}", headers=pj["h"], json={"hostEnabled": False, "code": "000000"})
    assert guest("alice@hub-b") is None                                                             # accueil retiré : tout disparaît
