"""Temps réel : authentification du WebSocket, signaux filtrés par droits, surveillance des fichiers."""
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import db, events
from app.audit import audit
from app.main import app

PW = "un-mot-de-passe-solide"
client = TestClient(app)


def token(username, password=PW):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def world():
    root = {"Authorization": "Bearer " + token("root", "admin-password-1")}
    for name in ("ev-alice", "ev-bob"):
        client.post("/api/users", headers=root, json={"username": name, "password": PW})
    ids = {r["username"]: r["id"] for r in db.rows("SELECT id, username FROM users")}
    nid = str(uuid.uuid4())
    db.run("INSERT INTO nodes(id,name,kind,created_at) VALUES(?,?,?,?)", (nid, "Lyon", "remote", db.now()))
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (nid, ids["ev-alice"], "owner", db.now()))
    return {"nid": nid, "local": db.one("SELECT id FROM nodes WHERE kind='local'")["id"], "alice": token("ev-alice"), "bob": token("ev-bob"), "root": token("root", "admin-password-1")}


def open_ws(ws, tok):
    ws.send_text(json.dumps({"token": tok}))
    assert ws.receive_json() == {"t": "ready"}


def next_change(ws):
    for _ in range(5):
        m = ws.receive_json()
        if m.get("t") == "changed":
            return {(x["t"], x["node"]) for x in m["topics"]}
    raise AssertionError("aucun signal reçu")


def test_jeton_obligatoire():
    with client.websocket_connect("/api/events") as ws:
        ws.send_text(json.dumps({"token": "nimporte-quoi"}))
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4401
    with client.websocket_connect("/api/events") as ws:   # un jeton de rafraîchissement n'est pas un jeton d'accès
        ws.send_text(json.dumps({"token": ""}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_signal_pour_un_noeud_visible_seulement(world):
    with client.websocket_connect("/api/events") as alice, client.websocket_connect("/api/events") as bob:
        open_ws(alice, world["alice"])
        open_ws(bob, world["bob"])
        audit("ev-alice", "traefik.site.save", "x -> y", node=world["nid"])
        assert ("traefik", world["nid"]) in next_change(alice)             # alice possède ce nœud
        audit("ev-alice", "dns.record.add", "z")                            # événement global : tout le monde
        assert ("domains", None) in next_change(bob)
        events.publish("traefik", world["nid"])                            # bob n'a pas accès : rien pour lui
        events.publish("nodes")
        got = next_change(bob)
        assert ("traefik", world["nid"]) not in got and ("nodes", None) in got


def test_signaux_regroupes(world):
    with client.websocket_connect("/api/events") as ws:
        open_ws(ws, world["root"])
        for _ in range(20):
            events.publish("domains")
        got = next_change(ws)
        assert got == {("domains", None)}                                  # 20 signaux identiques, un seul message


def test_plateforme_reservee_aux_admins(world):
    with client.websocket_connect("/api/events") as alice, client.websocket_connect("/api/events") as root:
        open_ws(alice, world["alice"])
        open_ws(root, world["root"])
        events.publish("users")
        assert ("users", None) in next_change(root)
        events.publish("audit")                                             # sans nœud : plateforme
        events.publish("domains")
        assert next_change(alice) == {("domains", None)}                   # alice n'a reçu ni « users » ni « audit »


def test_modification_de_fichier_detectee(world, tmp_path):
    from app.config import settings
    f = settings.dynamic_dir / "watch-me.yml"
    with client.websocket_connect("/api/events") as ws:
        open_ws(ws, world["root"])
        time.sleep(2.5)                                                    # la surveillance prend son état de départ
        f.write_text("http: {}\n")
        deadline = time.time() + 8
        seen = set()
        while time.time() < deadline and ("traefik", world["local"]) not in seen:
            seen |= next_change(ws)
        assert ("traefik", world["local"]) in seen
    f.unlink()


def test_deconnexion_nettoie_la_liste(world):
    before = events.listeners()
    with client.websocket_connect("/api/events") as ws:
        open_ws(ws, world["root"])
        assert events.listeners() == before + 1
    time.sleep(0.3)
    assert events.listeners() == before


def test_csp_autorise_le_websocket_de_cet_hote():
    r = client.get("/api/health", headers={"host": "ui.example.com"})
    assert "connect-src 'self' ws://ui.example.com wss://ui.example.com" in r.headers["content-security-policy"]
    r = client.get("/api/health", headers={"host": "x;evil"})
    assert "evil" not in r.headers["content-security-policy"]


def test_noeud_cree_pendant_la_session_est_suivi_aussitot(world):
    """Le créateur d'un nœud reçoit ensuite les signaux de ce nœud (connexion du worker) sans se reconnecter."""
    root = {"Authorization": "Bearer " + world["root"]}
    with client.websocket_connect("/api/events") as ws:
        open_ws(ws, world["root"])
        r = client.post("/api/nodes", headers=root, json={"name": "Nouveau"})
        assert r.status_code == 200, r.text
        nid = r.json()["id"]
        assert ("nodes", None) in next_change(ws)                          # la création elle-même
        events.publish("node", nid)                                        # ex. le worker vient de se connecter
        assert ("node", nid) in next_change(ws)
    client.delete(f"/api/nodes/{nid}", headers=root)                       # ne pas laisser de nœud aux autres tests


def test_avis_d_urgence_arrive_au_proprietaire_seulement(world):
    """Un avis (accès d'urgence, suspension…) s'affiche aussitôt chez son destinataire, et chez personne d'autre."""
    from app import admin_nodes
    alice_id = db.one("SELECT id FROM users WHERE username='ev-alice'")["id"]
    node = db.one("SELECT * FROM nodes WHERE id=?", (world["nid"],))
    with client.websocket_connect("/api/events") as alice, client.websocket_connect("/api/events") as bob:
        open_ws(alice, world["alice"])
        open_ws(bob, world["bob"])
        admin_nodes.notify(node, [alice_id], "emergency", "L'administrateur est intervenu sur votre nœud.")
        assert ("notices", None) in next_change(alice)
        events.publish("domains")                                           # marqueur : bob reçoit ceci, mais pas l'avis
        assert next_change(bob) == {("domains", None)}


def test_acces_d_urgence_expire_signale(world):
    """Quand un accès d'urgence arrive à échéance, les écrans (bandeau, droits) se mettent à jour sans action de personne."""
    root_id = db.one("SELECT id FROM users WHERE username='root'")["id"]
    now = time.time()
    db.run("INSERT INTO emergency_sessions(node_id,admin_id,reason,started_at,expires_at) VALUES(?,?,?,?,?)",
           (world["nid"], root_id, "test d'expiration", now - 60, now + 3))
    with client.websocket_connect("/api/events") as ws:
        open_ws(ws, world["alice"])
        seen, end = set(), time.time() + 10
        while time.time() < end and ("node", world["nid"]) not in seen:
            seen |= next_change(ws)
        assert ("node", world["nid"]) in seen and ("nodes", None) in seen
    db.run("UPDATE emergency_sessions SET ended_at=? WHERE node_id=?", (time.time(), world["nid"]))


def test_compte_desactive_deconnecte_aussitot(world):
    """Désactiver un compte ferme ses sessions temps réel tout de suite, sans attendre l'expiration du jeton."""
    root = {"Authorization": "Bearer " + world["root"]}
    uid = db.one("SELECT id FROM users WHERE username='ev-bob'")["id"]
    with client.websocket_connect("/api/events") as ws:
        open_ws(ws, world["bob"])
        assert client.put(f"/api/users/{uid}", headers=root, json={"disabled": True}).status_code == 200
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(4):
                ws.receive_json()
        assert e.value.code == 4401
    client.put(f"/api/users/{uid}", headers=root, json={"disabled": False})


def test_compte_modifie_previent_l_utilisateur(world):
    root = {"Authorization": "Bearer " + world["root"]}
    uid = db.one("SELECT id FROM users WHERE username='ev-alice'")["id"]
    with client.websocket_connect("/api/events") as ws:
        open_ws(ws, world["alice"])
        assert client.put(f"/api/users/{uid}", headers=root, json={"canAddDomains": True}).status_code == 200
        assert ("session", None) in next_change(ws)
