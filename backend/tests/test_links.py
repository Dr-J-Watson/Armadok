"""Liaison entre hubs : identité, appairage à code unique, empreinte à confirmer, canal signé dans les deux sens, versions, révocation, délai de grâce."""
import asyncio
import json
import secrets
import time

import pytest
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.websockets import WebSocketDisconnect

from app import db, hub_link, link_proto as proto, routes_links
from app.traefik_store import StoreError
from test_projects import client, pj  # noqa: F401  (fixture)

BASE = "/api/admin/links"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    def fake(p, code):   # le vrai contrôle (TOTP) est testé ailleurs : ici « 000000 » passe, tout le reste est refusé
        if code != "000000":
            raise StoreError("Code de double authentification invalide", 403)
    monkeypatch.setattr(routes_links, "need_second_factor", fake)
    db.run("DELETE FROM hub_links")
    hub_link._chans.clear()
    hub_link._fails.clear()
    yield
    db.run("DELETE FROM hub_links")
    hub_link._chans.clear()


def peer_identity() -> hub_link.Identity:
    return hub_link.Identity(secrets.token_hex(16), "Hub distant", Ed25519PrivateKey.generate())


def invite(pj, label="test"):
    r = client.post(f"{BASE}/invite", headers=pj["h"], json={"code": "000000", "label": label})
    assert r.status_code == 200, r.text
    return r.json()["link"], r.json()["code"]


def pair(code, peer, **over):
    body = {"code": code, "hubId": peer.hub_id, "name": peer.name, "publicKey": peer.public, "version": "9.9.9", "protocol": proto.PROTOCOL, "minProtocol": proto.MIN_PROTOCOL, **over}
    return client.post("/api/link/pair", json=body)


def paired(pj, confirm=True):
    link, code = invite(pj)
    peer = peer_identity()
    assert pair(code, peer).status_code == 200
    if confirm:
        assert client.post(f"{BASE}/{link['id']}/confirm", headers=pj["h"], json={"code": "000000"}).status_code == 200
    return link["id"], peer


def connect(peer, **hello):
    """Poignée de main côté appelant, contre le vrai point d'entrée de ce hub ; rend (ready, session) pour continuer à parler."""
    me = hub_link.identity()
    link_key = db.one("SELECT peer_key FROM hub_links LIMIT 1")
    assert link_key
    cm = client.websocket_connect("/api/link/ws")
    ws = cm.__enter__()

    async def send(s): ws.send_text(s)

    async def recv(): return ws.receive_text()

    ready = asyncio.run(hub_link.client_handshake(send, recv, peer, me.hub_id, me.public))
    return ready, ws, cm


# ------------------------------------------------------------------ protocole
def test_negotiation_picks_common_protocol_or_none():
    assert proto.negotiate(3, 1, 2, 1) == 2
    assert proto.negotiate(1, 1, 1, 1) == 1
    assert proto.negotiate(2, 2, 1, 1) is None    # l'un a abandonné le 1, l'autre n'a que le 1
    assert proto.negotiate(1, 1, 5, 4) is None    # l'autre n'accepte plus rien d'ancien
    assert proto.common_features(("state", "relay"), ["state", "guests", 3]) == ["state"]


def test_signed_messages_name_both_hubs_and_the_direction():
    a = proto.auth_message("srv", "A", "B", "n1", "n2")
    assert a != proto.auth_message("cli", "A", "B", "n1", "n2")     # une signature d'appelé ne vaut pas celle d'un appelant
    assert a != proto.auth_message("srv", "B", "A", "n1", "n2")
    assert a != proto.auth_message("srv", "A", "B", "n1", "n3")


def test_fingerprint_is_the_same_on_both_sides():
    k1, k2 = peer_identity().public, peer_identity().public
    assert proto.fingerprint(k1, k2) == proto.fingerprint(k2, k1)
    assert proto.fingerprint(k1, k2) != proto.fingerprint(k1, peer_identity().public)


def test_clean_url_requires_https_and_no_path(monkeypatch):
    monkeypatch.delenv("LINK_ALLOW_INSECURE", raising=False)
    assert hub_link.clean_url("https://hub.exemple.fr/") == "https://hub.exemple.fr"
    assert hub_link.clean_url("https://hub.exemple.fr:8443") == "https://hub.exemple.fr:8443"
    for bad in ("http://hub.exemple.fr", "https://u:p@hub.exemple.fr", "https://hub.exemple.fr/api", "https://hub.exemple.fr?x=1", "ftp://x.fr", "https://169.254.169.254", ""):
        with pytest.raises(hub_link.LinkError):
            hub_link.clean_url(bad)
    monkeypatch.setenv("LINK_ALLOW_INSECURE", "true")
    assert hub_link.clean_url("http://10.0.0.5:8000") == "http://10.0.0.5:8000"


# ------------------------------------------------------------------ appairage
def test_identity_is_stable_and_private_key_is_encrypted():
    a = hub_link.identity()
    b = hub_link.identity()
    assert a.hub_id == b.hub_id and a.public == b.public
    raw = db.one("SELECT private_key FROM hub_identity")["private_key"]
    assert raw.startswith("enc:") or raw.startswith("gAAAA") or "=" not in raw[:3]   # chiffré par ENCRYPTION_KEY, jamais le clair
    import base64
    clear = base64.b64encode(a.key.private_bytes_raw()).decode()
    assert clear not in raw


def test_admin_only_and_overview(pj):
    assert client.get(BASE).status_code == 401
    r = client.get(BASE, headers=pj["h"]).json()
    assert r["identity"]["hubId"] and r["defaultGraceHours"] == 12 and r["links"] == []


def test_invite_needs_2fa_and_code_is_never_listed(pj):
    assert client.post(f"{BASE}/invite", headers=pj["h"], json={"code": "123456"}).status_code == 403
    link, code = invite(pj)
    assert link["state"] == "invited" and link["codeExpires"] > time.time()
    assert code.startswith("lnk_") and code not in json.dumps(client.get(BASE, headers=pj["h"]).json())
    assert db.one("SELECT code_hash FROM hub_links WHERE id=?", (link["id"],))["code_hash"] != code


def test_pairing_burns_the_code_and_requires_both_confirmations(pj):
    link, code = invite(pj)
    peer = peer_identity()
    assert pair("lnk_mauvais-code-123", peer).status_code == 401
    r = pair(code, peer)
    assert r.status_code == 200
    me = hub_link.identity()
    assert r.json()["hubId"] == me.hub_id and r.json()["publicKey"] == me.public and r.json()["linkId"] == link["id"]
    assert pair(code, peer_identity()).status_code == 401                    # à usage unique
    row = [x for x in client.get(BASE, headers=pj["h"]).json()["links"] if x["id"] == link["id"]][0]
    assert row["state"] == "to_confirm" and row["peerHubId"] == peer.hub_id
    assert row["fingerprint"] == proto.fingerprint(me.public, peer.public)    # c'est ce que l'autre administrateur verra aussi
    assert client.post(f"{BASE}/{link['id']}/confirm", headers=pj["h"], json={"code": "999999"}).status_code == 403
    done = client.post(f"{BASE}/{link['id']}/confirm", headers=pj["h"], json={"code": "000000"}).json()
    assert done["state"] == "wait_peer"                                         # pas utilisable tant que l'autre n'a pas confirmé
    assert not hub_link.usable(hub_link.get(link["id"]))


def test_pairing_refuses_expired_code_self_link_and_duplicates(pj):
    link, code = invite(pj)
    db.run("UPDATE hub_links SET code_expires=? WHERE id=?", (time.time() - 1, link["id"]))
    assert pair(code, peer_identity()).status_code == 401
    link, code = invite(pj)
    assert pair(code, hub_link.identity()).status_code == 409                    # un hub ne se lie pas à lui-même
    link2, code2 = invite(pj)
    peer = peer_identity()
    assert pair(code2, peer).status_code == 200
    link3, code3 = invite(pj)
    assert pair(code3, peer).status_code == 409                                  # déjà lié à ce hub
    assert pair(code3, peer_identity(), publicKey="pas-une-cle" * 5).status_code == 422


def test_failed_pairings_are_rate_limited(pj):
    invite(pj)
    for _ in range(5):
        assert pair("lnk_mauvais-code-123", peer_identity()).status_code == 401
    assert pair("lnk_mauvais-code-123", peer_identity()).status_code == 429


def test_join_calls_the_other_hub_and_records_it(pj, monkeypatch):
    other = peer_identity()
    lid = secrets.token_hex(16)
    seen = {}

    def fake_post(url, body):
        seen.update(url=url, body=body)
        return 200, {"linkId": lid, "hubId": other.hub_id, "name": "Hub B", "publicKey": other.public, "version": "1.2.3", "protocol": 1, "minProtocol": 1}
    monkeypatch.setattr(hub_link, "_post_json", fake_post)
    monkeypatch.setenv("LINK_ALLOW_INSECURE", "")
    r = client.post(f"{BASE}/join", headers=pj["h"], json={"code": "000000", "invitation": "https://armadok.hubb.fr#lnk_abcdef123456", "label": "B"})
    assert r.status_code == 200, r.text
    assert seen["url"] == "https://armadok.hubb.fr/api/link/pair" and seen["body"]["code"] == "lnk_abcdef123456"
    assert seen["body"]["publicKey"] == hub_link.identity().public and "privateKey" not in json.dumps(seen["body"])
    j = r.json()
    assert j["weCall"] and j["peerName"] == "Hub B" and j["state"] == "to_confirm" and j["peerUrl"] == "https://armadok.hubb.fr"
    again = client.post(f"{BASE}/join", headers=pj["h"], json={"code": "000000", "invitation": "https://armadok.hubb.fr#lnk_abcdef123456"})
    assert again.status_code == 409                                              # une seule liaison par couple de hubs
    monkeypatch.setattr(hub_link, "_post_json", lambda u, b: (401, {"detail": "Code invalide, expiré ou déjà utilisé"}))
    bad = client.post(f"{BASE}/join", headers=pj["h"], json={"code": "000000", "invitation": "https://autre.exemple.fr#lnk_abcdef123456"})
    assert bad.status_code == 400 and "Code invalide" in bad.text
    assert client.post(f"{BASE}/join", headers=pj["h"], json={"code": "123456", "invitation": "https://x.fr#lnk_abcdef123456"}).status_code == 403


# ------------------------------------------------------------------ canal côté appelé
def test_channel_handshake_is_mutual_and_reports_state(pj):
    lid, peer = paired(pj)
    ready, ws, cm = connect(peer)
    try:
        assert ready["type"] == "ready" and ready["protocol"] == proto.PROTOCOL and "state" in ready["features"] and ready["state"]["confirmed"] is True
        assert hub_link.online(lid)
        assert client.get(BASE, headers=pj["h"]).json()["links"][0]["state"] == "wait_peer"   # il n'a pas encore dit qu'il confirmait
        ws.send_text(json.dumps({"type": "state", "confirmed": True, "name": "Hub distant\n<b>x</b>", "version": "9.9.9", "protocol": 1, "minProtocol": 1}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text())["type"] == "pong"
        row = client.get(BASE, headers=pj["h"]).json()["links"][0]
        assert row["state"] == "online" and row["peerVersion"] == "9.9.9" and row["protocol"] == 1 and "\n" not in row["peerName"]
        assert hub_link.usable(hub_link.get(lid))
    finally:
        cm.__exit__(None, None, None)
    time.sleep(0.2)
    assert not hub_link.online(lid)
    assert client.get(BASE, headers=pj["h"]).json()["links"][0]["state"] == "offline"


def test_channel_refuses_unknown_hub_and_bad_signature(pj):
    lid, peer = paired(pj)
    stranger = peer_identity()
    me = hub_link.identity()
    for who in (stranger, hub_link.Identity(peer.hub_id, "usurpateur", Ed25519PrivateKey.generate())):   # inconnu, ou connu mais sans la bonne clé
        with client.websocket_connect("/api/link/ws") as ws:
            async def send(s): ws.send_text(s)
            async def recv(): return ws.receive_text()
            with pytest.raises((WebSocketDisconnect, ConnectionError)):
                asyncio.run(hub_link.client_handshake(send, recv, who, me.hub_id, me.public))
                ws.receive_text()
    assert not hub_link.online(lid)


def test_caller_refuses_an_impostor_server():
    """Le hub APPELÉ doit prouver son identité : un faux serveur qui signe avec une autre clé est refusé avant que l'appelant ne s'authentifie."""
    me, real, fake = peer_identity(), peer_identity(), peer_identity()
    sent = []

    async def send(s): sent.append(json.loads(s))

    async def recv():
        hello = sent[0]
        ns = secrets.token_urlsafe(32)
        return json.dumps({"type": "challenge", "hubId": real.hub_id, "nonce": ns, "signature": fake.sign(proto.auth_message("srv", real.hub_id, me.hub_id, hello["nonce"], ns))})
    with pytest.raises(PermissionError):
        asyncio.run(hub_link.client_handshake(send, recv, me, real.hub_id, real.public))
    assert [m["type"] for m in sent] == ["hello"]                                  # il n'a rien signé pour l'imposteur


def test_signature_cannot_be_replayed_in_the_other_direction_or_connection():
    a, b = peer_identity(), peer_identity()
    nc, ns = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    sig = a.sign(proto.auth_message("cli", a.hub_id, b.hub_id, nc, ns))
    assert hub_link.verify(a.public, proto.auth_message("cli", a.hub_id, b.hub_id, nc, ns), sig)
    assert not hub_link.verify(a.public, proto.auth_message("srv", a.hub_id, b.hub_id, nc, ns), sig)
    assert not hub_link.verify(a.public, proto.auth_message("cli", a.hub_id, b.hub_id, nc, secrets.token_urlsafe(32)), sig)
    assert not hub_link.verify(a.public, b"x", "pas-du-base64")


def test_incompatible_versions_are_reported_not_silently_ignored(pj):
    lid, peer = paired(pj)
    me = hub_link.identity()
    with client.websocket_connect("/api/link/ws") as ws:
        async def send(s):
            m = json.loads(s)
            if m["type"] == "hello":
                m.update(protocol=7, minProtocol=5, version="7.0.0")      # un hub du futur qui a abandonné nos protocoles
            ws.send_text(json.dumps(m))
        async def recv(): return ws.receive_text()
        out = asyncio.run(hub_link.client_handshake(send, recv, peer, me.hub_id, me.public))
        assert out["type"] == "incompatible" and out["minProtocol"] == proto.MIN_PROTOCOL
    row = client.get(BASE, headers=pj["h"]).json()["links"][0]
    assert row["state"] == "incompatible" and "7.0.0" in row["lastError"] and "mettez à jour" in row["lastError"]
    assert not hub_link.online(lid)


def test_newer_peer_with_overlapping_range_negotiates_down(pj):
    lid, peer = paired(pj)
    me = hub_link.identity()
    with client.websocket_connect("/api/link/ws") as ws:
        async def send(s):
            m = json.loads(s)
            if m["type"] == "hello":
                m.update(protocol=4, minProtocol=1, features=["state", "relay", "guests"])
            ws.send_text(json.dumps(m))
        async def recv(): return ws.receive_text()
        ready = asyncio.run(hub_link.client_handshake(send, recv, peer, me.hub_id, me.public))
        assert ready["type"] == "ready" and ready["protocol"] == proto.PROTOCOL            # on parle le plus récent COMMUN
    assert hub_link.get(lid)["features"] == "state"                                         # seulement ce que les deux comprennent


# ------------------------------------------------------------------ révocation, grâce
def test_revoke_tells_the_connected_peer_and_closes_the_door(pj):
    lid, peer = paired(pj)
    ready, ws, cm = connect(peer)
    try:
        assert client.post(f"{BASE}/{lid}/revoke", headers=pj["h"], json={"code": "123456"}).status_code == 403
        assert client.post(f"{BASE}/{lid}/revoke", headers=pj["h"], json={"code": "000000"}).json()["state"] == "revoked"
        assert json.loads(ws.receive_text())["type"] == "revoke"
    finally:
        cm.__exit__(None, None, None)
    # plus tard, le hub distant se reconnecte : une fois authentifié, il apprend que c'est fini, sans jamais être « prêt »
    me = hub_link.identity()
    with client.websocket_connect("/api/link/ws") as ws2:
        async def send(s): ws2.send_text(s)
        async def recv(): return ws2.receive_text()
        assert asyncio.run(hub_link.client_handshake(send, recv, peer, me.hub_id, me.public))["type"] == "revoke"
    assert not hub_link.online(lid) and not hub_link.usable(hub_link.get(lid))
    assert client.delete(f"{BASE}/{lid}", headers=pj["h"]).status_code == 200


def test_peer_revocation_is_applied_and_paired_link_cannot_be_deleted(pj):
    lid, peer = paired(pj)
    assert client.delete(f"{BASE}/{lid}", headers=pj["h"]).status_code == 409
    ready, ws, cm = connect(peer)
    try:
        ws.send_text(json.dumps({"type": "revoke"}))
        for _ in range(40):
            if hub_link.get(lid)["status"] == "revoked":
                break
            time.sleep(0.05)
    finally:
        cm.__exit__(None, None, None)
    r = hub_link.get(lid)
    assert r["status"] == "revoked" and r["revoked_by"] == "l'autre hub"


def test_grace_period_is_per_link_bounded_and_applied(pj):
    lid, peer = paired(pj)
    db.run("UPDATE hub_links SET confirmed_peer=1 WHERE id=?", (lid,))
    assert client.get(BASE, headers=pj["h"]).json()["links"][0]["graceHours"] == 12                    # 12 h par défaut
    assert client.patch(f"{BASE}/{lid}", headers=pj["h"], json={"graceHours": 2, "code": "123456"}).status_code == 403   # engage la sécurité : 2FA
    assert client.patch(f"{BASE}/{lid}", headers=pj["h"], json={"graceHours": 2, "code": "000000"}).json()["graceHours"] == 2
    assert client.patch(f"{BASE}/{lid}", headers=pj["h"], json={"graceHours": 100000, "code": "000000"}).status_code == 422
    assert client.patch(f"{BASE}/{lid}", headers=pj["h"], json={"label": "Hub B"}).json()["label"] == "Hub B"   # un simple libellé n'exige pas de code
    now = time.time()
    db.run("UPDATE hub_links SET last_seen=? WHERE id=?", (now - 3600, lid))
    assert hub_link.within_grace(hub_link.get(lid), now)                      # coupée depuis 1 h, grâce de 2 h
    db.run("UPDATE hub_links SET last_seen=? WHERE id=?", (now - 3 * 3600, lid))
    assert not hub_link.within_grace(hub_link.get(lid), now)                  # 3 h : plus rien
    client.patch(f"{BASE}/{lid}", headers=pj["h"], json={"graceHours": 0, "code": "000000"})
    db.run("UPDATE hub_links SET last_seen=? WHERE id=?", (now - 5, lid))
    assert not hub_link.within_grace(hub_link.get(lid), now)                  # 0 = coupure immédiate
    db.run("UPDATE hub_links SET confirmed_peer=0 WHERE id=?", (lid,))
    db.run("UPDATE hub_links SET grace_hours=12, last_seen=? WHERE id=?", (now, lid))
    assert not hub_link.within_grace(hub_link.get(lid), now)                  # jamais confirmée des deux côtés : aucun effet


# ------------------------------------------------------------------ côté appelant, de bout en bout
def test_caller_session_against_a_real_peer(pj):
    """Ce hub APPELLE un faux hub (vrai serveur WebSocket, mêmes règles) : poignée de main, état, ping, et révocation reçue."""
    other = peer_identity()
    me = hub_link.identity()
    lid = secrets.token_hex(16)
    log = []

    async def handler(ws):
        hello = json.loads(await ws.recv())
        ns = secrets.token_urlsafe(32)
        await ws.send(json.dumps({"type": "challenge", "hubId": other.hub_id, "nonce": ns, "signature": other.sign(proto.auth_message("srv", other.hub_id, me.hub_id, hello["nonce"], ns))}))
        auth = json.loads(await ws.recv())
        assert hub_link.verify(me.public, proto.auth_message("cli", me.hub_id, other.hub_id, hello["nonce"], ns), auth["signature"])
        await ws.send(json.dumps({"type": "ready", "protocol": 1, "features": ["state", "relay"], "state": {"type": "state", "confirmed": True, "name": "Hub B", "version": "3.0.0", "protocol": 1}}))
        log.append(json.loads(await ws.recv()))                      # son état
        log.append(json.loads(await ws.recv()))                      # un ping
        await ws.send(json.dumps({"type": "revoke"}))
        await asyncio.sleep(0.2)

    async def main():
        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            db.run("INSERT INTO hub_links(id,peer_hub_id,peer_name,peer_key,peer_url,we_call,status,confirmed_local,created_at) VALUES(?,?,?,?,?,1,'paired',1,?)",
                   (lid, other.hub_id, "Hub B", other.public, f"http://127.0.0.1:{port}", time.time()))
            task = asyncio.create_task(hub_link._caller_session(hub_link.get(lid)))
            for _ in range(60):
                await asyncio.sleep(0.1)
                if task.done():
                    break
            await task
    old = proto.PING_EVERY
    proto.PING_EVERY = 0.2
    try:
        asyncio.run(main())
    finally:
        proto.PING_EVERY = old
    assert log[0]["type"] == "state" and log[0]["confirmed"] is True and log[1]["type"] == "ping"
    r = hub_link.get(lid)
    assert r["status"] == "revoked" and r["revoked_by"] == "l'autre hub"                  # l'ordre de révocation reçu est appliqué
    assert r["peer_version"] == "3.0.0" and r["features"] == "state" and r["protocol"] == 1 and r["confirmed_peer"] == 1
    assert not hub_link.online(lid)
