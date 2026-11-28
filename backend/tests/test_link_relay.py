"""Relais entre hubs : l'origine authentifie et transmet, l'accueil rejoue DANS SES routes au nom de l'invité (droits, plafond, audit de l'accueil), liste fermée de routes."""
import asyncio
import base64
import json
import secrets
import time

import pytest

from app import db, hub_link, link_guests, link_proto as proto, link_relay, nodes
from test_projects import client, pj  # noqa: F401  (fixture)


class Loopback:
    """Un canal factice : la requête part… et arrive tout de suite chez l'accueil (même processus, la base fait office des deux hubs)."""
    loop = None

    def __init__(self, lid):
        self.lid = lid
        self.seen = []

    def post(self, msg):
        self.seen.append(msg)

    def close_later(self):
        pass

    async def request(self, msg, timeout):
        self.seen.append(msg)
        return await link_relay.handle(self.lid, msg)


@pytest.fixture()
def rel(pj):
    root = db.one("SELECT id FROM users WHERE source='env'")["id"]
    lid = secrets.token_hex(16)
    db.run("INSERT INTO hub_links(id,peer_hub_id,peer_name,peer_key,we_call,status,confirmed_local,confirmed_peer,host_enabled,grace_hours,features,last_seen,created_at) "
           "VALUES(?,?,?,?,0,'paired',1,1,1,12,?,?,?)", (lid, secrets.token_hex(16), "Hub B", "k" * 44, "guests,state," + proto.RELAY_FEATURE, time.time(), time.time()))
    link_guests.apply_in(lid, {"type": "teams", "teams": [{"id": "t1", "name": "Ops", "members": [{"id": str(root), "username": "root"}]}]})
    gid = db.one("SELECT id FROM groups WHERE link_id=?", (lid,))["id"]
    # côté origine : le groupe local qui contient « root » est partagé sur la liaison
    local = db.run("INSERT INTO groups(name,owner_id,created_at) VALUES('Équipe origine',NULL,?)", (time.time(),))
    db.run("INSERT INTO group_members(group_id,user_id) VALUES(?,?)", (local, root))
    db.run("INSERT INTO link_shared_groups(link_id,group_id) VALUES(?,?)", (lid, local))
    chan = Loopback(lid)
    hub_link._chans[lid] = chan
    yield {"lid": lid, "gid": gid, "chan": chan, "h": pj["h"], "node": nodes.local_node_id()}
    hub_link._chans.pop(lid, None)
    db.run("DELETE FROM link_shared_groups WHERE link_id=?", (lid,))
    db.run("DELETE FROM groups WHERE id=?", (local,))
    for g in db.rows("SELECT id FROM groups WHERE link_id=?", (lid,)):
        db.run("DELETE FROM grants WHERE subject_type='group' AND subject_id=?", (g["id"],))
        db.run("DELETE FROM groups WHERE id=?", (g["id"],))
    db.run("DELETE FROM users WHERE guest_link=?", (lid,))
    db.run("DELETE FROM hub_links WHERE id=?", (lid,))


def grant(rel, builtin):
    db.run("INSERT INTO grants(subject_type,subject_id,role_id,node_id,created_at) VALUES('group',?,?,?,?)",
           (rel["gid"], db.one("SELECT id FROM roles WHERE builtin=?", (builtin,))["id"], rel["node"], time.time()))


def get(rel, path, node=True, **kw):
    h = {**rel["h"], **({"X-Node": rel["node"]} if node else {})}
    return client.get(f"/api/linked/{rel['lid']}{path}", headers=h, **kw)


def test_the_allowlist_is_closed():
    ok = [("GET", "/nodes"), ("GET", f"/nodes/{'a' * 36}"), ("GET", "/meta"), ("GET", "/traefik/overview"), ("PUT", "/traefik/site"), ("GET", "/docker/containers"), ("GET", "/projects")]
    for m, p in ok:
        assert link_relay.allowed(m, p), (m, p)
    for m, p in [("GET", "/admin/users"), ("GET", "/admin/links"), ("POST", "/admin/links/invite"), ("GET", "/auth/me"), ("POST", "/auth/login"), ("GET", "/rights/groups"),
                 ("GET", "/node-backups"), ("POST", "/nodes"), ("PUT", f"/nodes/{'a' * 36}"), ("DELETE", f"/nodes/{'a' * 36}"), ("GET", f"/nodes/{'a' * 36}/members"),
                 ("GET", "/users"), ("GET", "/dns/zones"), ("POST", "/worker/enroll"), ("GET", "/link/ws"), ("GET", "/linked/x/nodes"), ("GET", "/projects/templates"),
                 ("GET", "/meta/../admin/users"), ("GET", "//admin/users"), ("GET", "/traefik/overview?x=1"), ("GET", "traefik/overview"), ("TRACE", "/meta"), ("GET", "/meta\n"), ("GET", "/" + "a" * 500)]:
        assert not link_relay.allowed(m, p), (m, p)


def test_end_to_end_the_host_enforces_its_own_rights(rel):
    r = get(rel, "/nodes")
    assert r.status_code == 200 and r.json() == []                               # l'équipe n'a encore aucun droit chez l'accueil : elle ne voit rien
    assert get(rel, "/traefik/overview").status_code == 404                      # …et ne peut pas non plus viser un nœud par son identifiant
    grant(rel, "viewer")
    nodes_ = get(rel, "/nodes").json()
    assert [n["id"] for n in nodes_] == [rel["node"]] and "sites.view" in nodes_[0]["caps"]
    assert get(rel, "/traefik/overview").status_code == 200
    w = client.put(f"/api/linked/{rel['lid']}/traefik/site", headers={**rel["h"], "X-Node": rel["node"]}, json={})
    assert w.status_code in (403, 422) and w.status_code != 200                  # un lecteur n'écrit pas : c'est le droit de l'accueil qui tranche
    sent = rel["chan"].seen[-1]
    assert sent["as"]["id"] == str(db.one("SELECT id FROM users WHERE source='env'")["id"]) and "caps" not in json.dumps(sent) and "role" not in sent


def test_even_an_admin_role_granted_to_the_team_stays_capped(rel):
    grant(rel, "admin")
    n = get(rel, "/nodes").json()[0]
    assert "node.settings" not in n["caps"] and "node.backup" not in n["caps"] and "node.transfer" not in n["caps"] and "docker.exec" not in n["caps"]
    detail = get(rel, f"/nodes/{rel['node']}")
    assert detail.status_code == 200 and "node.members.add" not in detail.json()["caps"] and "apiUrl" not in detail.json()
    for path in ("/admin/users", f"/nodes/{rel['node']}/members", "/node-backups", "/rights/groups", "/auth/me"):
        assert get(rel, path).status_code == 403                                  # refusé dès l'origine (liste fermée)
    assert client.post(f"/api/linked/{rel['lid']}/nodes", headers=rel["h"], json={"name": "x"}).status_code == 403


def test_origin_side_checks(rel):
    grant(rel, "viewer")
    db.run("UPDATE hub_links SET features='guests,state' WHERE id=?", (rel["lid"],))   # l'autre hub n'a pas le même niveau d'API relayée
    r = get(rel, "/nodes")
    assert r.status_code == 409 and "compatible" in r.text
    db.run("UPDATE hub_links SET features=? WHERE id=?", ("guests,state," + proto.RELAY_FEATURE, rel["lid"]))
    db.run("DELETE FROM link_shared_groups WHERE link_id=?", (rel["lid"],))             # la personne n'est plus dans une équipe partagée
    assert get(rel, "/nodes").status_code == 403
    assert get(rel, "/nodes", ).headers["content-type"].startswith("application/json")
    hub_link._chans.pop(rel["lid"])
    db.run("INSERT INTO link_shared_groups(link_id,group_id) SELECT ?, id FROM groups WHERE name='Équipe origine'", (rel["lid"],))
    assert get(rel, "/nodes").status_code == 503                                        # hors ligne : message clair
    assert client.get(f"/api/linked/{'0' * 32}/nodes", headers=rel["h"]).status_code == 404
    assert client.get(f"/api/linked/{rel['lid']}/nodes").status_code == 401             # sans session, rien ne passe


def test_host_side_rejects_what_is_not_a_live_guest(rel):
    grant(rel, "viewer")
    base = {"type": "req", "id": "1", "method": "GET", "path": "/nodes", "query": "", "node": "", "ctype": "", "body": ""}
    root = str(db.one("SELECT id FROM users WHERE source='env'")["id"])

    def run(**over):
        msg = {**base, "as": {"id": root, "username": "root"}, **over}
        return asyncio.run(link_relay.handle(rel["lid"], msg))
    assert run()["status"] == 200
    assert run(**{"as": {"id": "inconnu"}})["status"] == 403                              # personne que l'autre hub n'a pas déclarée
    assert run(**{"as": {"id": "../x"}})["status"] == 403
    assert run(method="TRACE")["status"] == 403 and run(path="/admin/users")["status"] == 403
    assert run(body="@@pas-du-base64@@")["status"] == 400
    assert run(body=base64.b64encode(b"x" * (link_relay.MAX_BODY + 1)).decode())["status"] == 413
    uid = db.one("SELECT id FROM users WHERE guest_link=?", (rel["lid"],))["id"]
    db.run("UPDATE users SET guest_blocked=1, disabled=1 WHERE id=?", (uid,))
    assert run()["status"] == 403                                                          # écarté : plus rien ne passe
    db.run("UPDATE users SET guest_blocked=0, disabled=0 WHERE id=?", (uid,))
    assert run()["status"] == 200
    db.run("UPDATE hub_links SET host_enabled=0 WHERE id=?", (rel["lid"],))
    assert run()["status"] == 403
    db.run("UPDATE hub_links SET host_enabled=1, confirmed_peer=0 WHERE id=?", (rel["lid"],))
    assert run()["status"] == 403                                                          # plus confirmée des deux côtés
    hub_link._chans.pop(rel["lid"])                                                         # la connexion est tombée
    db.run("UPDATE hub_links SET confirmed_peer=1, grace_hours=0, last_seen=? WHERE id=?", (time.time() - 60, rel["lid"]))
    assert run()["status"] == 403                                                          # coupée au-delà du délai de grâce
    db.run("UPDATE hub_links SET grace_hours=12 WHERE id=?", (rel["lid"],))
    assert run()["status"] == 200


def test_linked_nodes_aggregates_per_link_with_errors_instead_of_failing(rel):
    grant(rel, "viewer")
    out = client.get("/api/linked-nodes", headers=rel["h"]).json()
    assert len(out) == 1 and out[0]["hub"] == "Hub B" and out[0]["error"] == ""
    n = out[0]["nodes"][0]
    assert n["id"] == f"L:{rel['lid']}:{rel['node']}" and n["remoteId"] == rel["node"] and n["kind"] == "linked" and n["linkId"] == rel["lid"]
    hub_link._chans.pop(rel["lid"])
    assert client.get("/api/linked-nodes", headers=rel["h"]).json()[0]["error"] == "Hub hors ligne."
    db.run("DELETE FROM link_shared_groups WHERE link_id=?", (rel["lid"],))
    assert client.get("/api/linked-nodes", headers=rel["h"]).json() == []                  # personne hors des équipes partagées : l'encart n'existe pas pour elle


def test_channel_request_response_roundtrip_and_failure_on_disconnect():
    async def go():
        sent = []

        async def send(text): sent.append(json.loads(text))
        async def close(): pass
        ch = hub_link.Channel("x" * 32, asyncio.get_running_loop(), send, close)
        task = asyncio.create_task(ch.request({"type": "req", "id": "r1"}, 5))
        await asyncio.sleep(0.05)
        assert sent[0]["id"] == "r1"
        ch.resolve({"type": "res", "id": "other"})                                       # une réponse inconnue ne débloque rien
        ch.resolve({"type": "res", "id": "r1", "status": 200})
        assert (await task)["status"] == 200
        t2 = asyncio.create_task(ch.request({"type": "req", "id": "r2"}, 5))
        await asyncio.sleep(0.05)
        ch.fail_all()
        with pytest.raises(ConnectionError):
            await t2
        with pytest.raises(asyncio.TimeoutError):
            await ch.request({"type": "req", "id": "r3"}, 0.1)
        assert not ch.pending
    asyncio.run(go())
