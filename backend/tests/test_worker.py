"""De bout en bout : un vrai serveur (uvicorn) et le vrai code du worker qui s'y connecte."""
import asyncio
import base64
import json
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
import websockets.sync.client as wsc
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import db, worker
from app import worker_proto as proto
from app.main import app
from app.traefik_store import Store

PW = "un-mot-de-passe-solide"


# ------------------------------------------------------------------ outils
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def hub():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(5)


class Api:
    def __init__(self, base: str, username: str, password: str, node: str | None = None) -> None:
        self.base, self.node = base, node
        r = httpx.post(f"{base}/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        self.h = {"Authorization": "Bearer " + r.json()["access_token"]}

    def req(self, method: str, path: str, node: str | None = None, **kw):
        h = {**self.h, **({"X-Node": node} if node else {})}
        return httpx.request(method, self.base + "/api" + path, headers=h, timeout=40, **kw)

    def get(self, path, **kw): return self.req("GET", path, **kw)
    def post(self, path, **kw): return self.req("POST", path, **kw)
    def put(self, path, **kw): return self.req("PUT", path, **kw)
    def delete(self, path, **kw): return self.req("DELETE", path, **kw)


class RunningWorker:
    """Le vrai worker (enrôlement + boucle de connexion) dans un thread, avec son propre dossier Traefik."""

    def __init__(self, hub: str, token: str) -> None:
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "dynamic").mkdir()
        self.store = Store(self.dir / "dynamic", self.dir / "backups")
        worker.identity_path = lambda: self.dir / "identity.json"  # type: ignore[assignment]
        self.ident = worker.enroll(hub, token)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task = None
        self._hub = hub
        self.start()

    def start(self) -> None:
        ready = threading.Event()

        def run():
            self.loop = asyncio.new_event_loop()
            self.task = self.loop.create_task(worker.serve(self._hub, self.ident, self.store, ""))
            ready.set()
            try:
                self.loop.run_until_complete(self.task)
            except asyncio.CancelledError:
                pass

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        ready.wait(5)

    def stop(self) -> None:
        if self.loop and self.task:
            self.loop.call_soon_threadsafe(self.task.cancel)
            self.thread.join(5)


def wait_for(cond, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("délai dépassé")


def node_state(api: Api, nid: str) -> str:
    return next(n["connection"] for n in api.get("/nodes").json() if n["id"] == nid)


@pytest.fixture(scope="module")
def world(hub):
    root = Api(hub, "root", "admin-password-1")
    for name, quota in (("wendy", 1), ("xavier", 0), ("yann", 3)):
        r = root.post("/users", json={"username": name, "password": PW, "maxNodes": quota})
        assert r.status_code == 200, r.text
    return {"root": root, "wendy": Api(hub, "wendy", PW), "xavier": Api(hub, "xavier", PW), "yann": Api(hub, "yann", PW), "hub": hub}


# ------------------------------------------------------------------ quotas
def test_quota_and_right_to_create(world):
    assert world["xavier"].post("/nodes", json={"name": "x"}).status_code == 403  # pas le droit
    r = world["wendy"].post("/nodes", json={"name": "Salon"})
    assert r.status_code == 200 and r.json()["token"].startswith("enr_") and r.json()["connection"] == "pending"
    assert world["wendy"].post("/nodes", json={"name": "Cave"}).status_code == 403  # quota de 1 atteint
    world["nid"], world["token"] = r.json()["id"], r.json()["token"]
    assert world["root"].post("/nodes", json={"name": "Admin 1"}).status_code == 200  # un administrateur : illimité
    assert db.one("SELECT token_hash FROM node_enroll_tokens WHERE token_hash=?", (world["token"],)) is None  # jamais en clair


def test_token_is_single_use_and_checked(world):
    hub = world["hub"]
    bad = httpx.post(hub + "/api/worker/enroll", json={"token": "enr_" + "x" * 40, "publicKey": base64.b64encode(b"k" * 32).decode()})
    assert bad.status_code == 401
    short = httpx.post(hub + "/api/worker/enroll", json={"token": world["token"], "publicKey": "AAAA" * 11})
    assert short.status_code == 422  # clé invalide : le jeton n'est pas consommé


# ------------------------------------------------------------------ un worker réel
def test_real_worker_end_to_end(world):
    w = RunningWorker(world["hub"], world["token"])
    world["worker"] = w
    nid, owner = world["nid"], world["wendy"]
    wait_for(lambda: node_state(owner, nid) == "online")
    # le même jeton ne sert plus
    again = httpx.post(world["hub"] + "/api/worker/enroll", json={"token": world["token"], "publicKey": worker.Identity.public_b64(Ed25519PrivateKey.generate())})
    assert again.status_code == 401
    # éditer les fichiers du nœud distant depuis le hub
    r = owner.put("/traefik/file", node=nid, json={"path": "site.yml", "create": True, "content": "http:\n  routers:\n    r1:\n      rule: Host(`a.example.org`)\n      service: s1\n  services:\n    s1:\n      loadBalancer:\n        servers:\n          - url: http://10.0.0.5:80\n"})
    assert r.status_code == 200, r.text
    assert (w.dir / "dynamic" / "site.yml").is_file()  # écrit sur la machine du worker, pas sur le hub
    assert not (Path(__import__("app.config", fromlist=["settings"]).settings.dynamic_dir) / "site.yml").exists()
    ov = owner.get("/traefik/overview", node=nid).json()
    assert [i["name"] for i in ov["items"] if i["kind"] == "routers"] == ["r1"]
    # un site créé depuis le constructeur
    r = owner.put("/traefik/site", node=nid, json={"name": "blog", "router": {"rule": "Host(`b.example.org`)", "service": "blog"}, "service": {"loadBalancer": {"servers": [{"url": "http://10.0.0.6:80"}]}}})
    assert r.status_code == 200, r.text
    assert "blog" in (w.dir / "dynamic" / "blog.yml").read_text()
    # historique conservé côté worker
    owner.put("/traefik/file", node=nid, json={"path": "site.yml", "content": "http: {}\n"})
    assert owner.get("/traefik/history", node=nid, params={"path": "site.yml"}).json()
    # une erreur de syntaxe revient avec sa ligne
    r = owner.put("/traefik/file", node=nid, json={"path": "site.yml", "content": "http:\n  - [\n"})
    assert r.status_code == 400 and r.json().get("line")
    # un chemin hors du dossier est refusé par le worker lui-même
    assert owner.get("/traefik/file", node=nid, params={"path": "../../etc/passwd.yml"}).status_code == 400


def test_certificates_come_through_the_worker(world, monkeypatch, tmp_path):
    """Un nœud distant a aussi son onglet Certificats : le worker lit le relevé de SA machine, le hub n'en reçoit que les métadonnées."""
    from test_certs import acme, make_cert
    from app.config import settings
    nid, owner = world["nid"], world["wendy"]
    f = tmp_path / "certs-source.json"
    f.write_text(json.dumps(acme([(["secure.example.org"], make_cert(["secure.example.org"], 60)), (["old.example.org"], make_cert(["old.example.org"], 4))])))
    monkeypatch.setattr(settings, "certs_file", str(f))
    for host, name in (("secure.example.org", "secure"), ("old.example.org", "old"), ("none.example.org", "none")):
        r = owner.put("/traefik/site", node=nid, json={"name": name, "router": {"rule": f"Host(`{host}`)", "service": name, "tls": {"certResolver": "le"}},
                                                        "service": {"loadBalancer": {"servers": [{"url": "http://10.0.0.9:80"}]}}})
        assert r.status_code == 200, r.text
    r = owner.get("/traefik/certs", node=nid)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["configured"] and d["error"] is None and len(d["certs"]) == 2 and "PRIVATE" not in r.text
    by = {s["name"]: s["worst"] for s in d["sites"]}
    assert by == {"none": "missing", "old": "expiring", "secure": "ok"} and d["warnings"] == 2
    monkeypatch.setattr(settings, "certs_file", None)                                                  # non configuré côté worker
    assert owner.get("/traefik/certs", node=nid).json()["configured"] is False


def test_remote_file_change_reaches_the_browser_in_real_time(world):
    """Un fichier modifié à la main sur la machine du worker est signalé au hub, puis aux navigateurs qui ont accès à ce nœud."""
    nid, owner, w = world["nid"], world["wendy"], world["worker"]
    url = world["hub"].replace("http://", "ws://") + "/api/events"
    with wsc.connect(url) as ws:
        ws.send(json.dumps({"token": owner.h["Authorization"].split()[1]}))
        assert json.loads(ws.recv(5)) == {"t": "ready"}
        time.sleep(2.5)                                                   # le worker prend l'état de départ de son dossier
        (w.dir / "dynamic" / "modifie-a-la-main.yml").write_text("http: {}\n")
        got, end = set(), time.time() + 10
        while time.time() < end and ("traefik", nid) not in got:
            try:
                m = json.loads(ws.recv(2))
            except TimeoutError:
                continue
            got |= {(x["t"], x["node"]) for x in m.get("topics", [])} if m.get("t") == "changed" else set()
        assert ("traefik", nid) in got
    # quelqu'un qui n'a pas accès à ce nœud ne reçoit rien
    stranger = world.get("xavier")
    if stranger:
        with wsc.connect(url) as ws:
            ws.send(json.dumps({"token": stranger.h["Authorization"].split()[1]}))
            assert json.loads(ws.recv(5)) == {"t": "ready"}
            (w.dir / "dynamic" / "encore.yml").write_text("http: {}\n")
            with pytest.raises(TimeoutError):
                for _ in range(3):
                    m = json.loads(ws.recv(2))
                    assert not (m.get("t") == "changed" and any(x["node"] == nid for x in m["topics"]))


def test_other_users_cannot_reach_the_remote_node(world):
    nid = world["nid"]
    for who in ("xavier", "yann"):
        assert world[who].get("/traefik/overview", node=nid).status_code == 404
        assert world[who].get(f"/nodes/{nid}").status_code == 404
        assert world[who].post(f"/nodes/{nid}/worker/token").status_code == 404
        assert world[who].delete(f"/nodes/{nid}").status_code == 404
    # l'administrateur de la plateforme n'a pas d'accès implicite à un nœud distant
    assert world["root"].get("/traefik/overview", node=nid).status_code == 404


def test_member_roles_apply_on_remote_node(world):
    nid, owner = world["nid"], world["wendy"]
    assert owner.post(f"/nodes/{nid}/members", json={"username": "yann", "role": "viewer"}).status_code == 200
    yann = world["yann"]
    assert yann.get("/traefik/overview", node=nid).status_code == 200  # lecteur : voit (sur ses domaines)
    assert yann.put("/traefik/file", node=nid, json={"path": "z.yml", "create": True, "content": "http: {}\n"}).status_code == 403
    assert yann.delete(f"/nodes/{nid}").status_code == 403


def test_hub_cannot_make_the_worker_do_unexpected_things(world):
    w = world["worker"]
    for op, args in (("items", {}), ("write", {"path": "a.yml", "content": "x" * (proto.MAX_FILE + 10), "create": True}),
                     ("write", {"path": "/etc/cron.d/x.yml", "content": "a: 1", "create": True}),
                     ("write", {"path": ".hidden.yml", "content": "a: 1", "create": True}),
                     ("exec", {"cmd": "id"}), ("upsert", {"proto": "http", "kind": "nope", "name": "x", "spec": {}})):
        try:
            worker.handle(w.store, op, args)
            assert op == "items"
        except Exception as e:  # noqa: BLE001
            assert op != "items" and getattr(e, "status", 0) in (400, 413, 422), (op, e)
    assert not Path("/etc/cron.d/x.yml").exists()


def test_worker_announces_its_modules_and_the_hub_enforces_them(world):
    nid, owner, w = world["nid"], world["wendy"], world["worker"]
    me = next(n for n in owner.get("/nodes").json() if n["id"] == nid)
    assert me["modules"] == ["traefik"]                                  # un worker sans WORKER_MODULES : « traefik », comme avant
    assert worker.worker_modules() == ("traefik",)
    # le worker lui-même refuse ce que ses modules ne couvrent pas
    for modules in (("docker",), ()):
        with pytest.raises(Exception) as e:
            worker.handle(w.store, "items", {}, "", modules)
        assert getattr(e.value, "status", 0) == 400
    assert worker.handle(w.store, "items", {}, "", ("traefik", "docker"))["items"] is not None
    # le hub aussi : un nœud « Docker seul » ne reçoit jamais d'ordre Traefik
    db.run("UPDATE nodes SET modules='docker' WHERE id=?", (nid,))
    try:
        assert owner.get("/traefik/overview", node=nid).status_code == 409
    finally:
        db.run("UPDATE nodes SET modules='traefik' WHERE id=?", (nid,))
    assert owner.get("/traefik/overview", node=nid).status_code == 200


def test_authentication_rejects_bad_signature(world):
    nid, hub = world["nid"], world["hub"].replace("http://", "ws://")
    with wsc.connect(hub + "/api/worker/ws") as ws:
        ws.send(json.dumps({"type": "hello", "nodeId": nid, "version": "x", "protocol": proto.PROTOCOL}))
        ch = json.loads(ws.recv(5))
        assert ch["type"] == "challenge"
        forged = Ed25519PrivateKey.generate().sign(proto.auth_message(nid, ch["nonce"]))
        ws.send(json.dumps({"type": "auth", "signature": base64.b64encode(forged).decode()}))
        with pytest.raises(Exception) as e:
            ws.recv(5)
        assert "4401" in str(e.value)
    with wsc.connect(hub + "/api/worker/ws") as ws:  # nœud inconnu
        ws.send(json.dumps({"type": "hello", "nodeId": "inconnu", "protocol": proto.PROTOCOL}))
        with pytest.raises(Exception) as e:
            ws.recv(5)
        assert "4401" in str(e.value)
    assert node_state(world["wendy"], nid) == "online"  # le vrai worker n'a pas été délogé


def test_untrusted_worker_replies_are_sanitized(world):
    """Un worker malveillant ne peut pas faire planter le hub avec des réponses mal formées."""
    root = world["root"]
    r = root.post("/nodes", json={"name": "Hostile"})
    nid, token = r.json()["id"], r.json()["token"]
    key = Ed25519PrivateKey.generate()
    pub = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    node_id = httpx.post(world["hub"] + "/api/worker/enroll", json={"token": token, "publicKey": pub}).json()["nodeId"]
    stop = threading.Event()

    def evil():
        with wsc.connect(world["hub"].replace("http://", "ws://") + "/api/worker/ws") as ws:
            ws.send(json.dumps({"type": "hello", "nodeId": node_id, "protocol": proto.PROTOCOL}))
            nonce = json.loads(ws.recv(5))["nonce"]
            ws.send(json.dumps({"type": "auth", "signature": base64.b64encode(key.sign(proto.auth_message(node_id, nonce))).decode()}))
            assert json.loads(ws.recv(5))["type"] == "ready"
            while not stop.is_set():
                try:
                    msg = json.loads(ws.recv(0.5))
                except TimeoutError:
                    continue
                except Exception:
                    return
                if msg.get("type") == "request":
                    junk = {"items": {"items": [{"id": 1}, "x", {"id": "http/routers/a", "proto": "http", "kind": "routers", "name": "a", "file": "a.yml", "spec": {"rule": "<img src=x>"}, "extra": 1}], "errors": {"f": "e" * 5000}},
                            "list_files": "pas une liste", "list_dirs": [1, "ok"], "live": 42}.get(msg["op"], {"x": 1})
                    ws.send(json.dumps({"type": "response", "id": msg["id"], "ok": True, "result": junk}))

    t = threading.Thread(target=evil, daemon=True)
    t.start()
    wait_for(lambda: node_state(root, nid) == "online")
    # ce propriétaire (administrateur de la plateforme) est propriétaire de ce nœud
    r = root.get("/traefik/overview", node=nid)
    assert r.status_code in (200, 502), r.text
    if r.status_code == 200:
        names = [i["name"] for i in r.json()["items"]]
        assert names in ([], ["a"])  # les éléments mal formés ont été écartés
    assert root.get("/meta", node=nid).status_code == 200
    stop.set()
    t.join(5)
    assert root.delete(f"/nodes/{nid}").status_code == 200


# ------------------------------------------------------------------ hors ligne, révocation, suppression
def test_offline_then_regenerated_worker(world):
    nid, owner, w = world["nid"], world["wendy"], world["worker"]
    w.stop()
    wait_for(lambda: node_state(owner, nid) == "offline", 80)
    r = owner.get("/traefik/overview", node=nid)
    assert r.status_code == 503 and "hors ligne" in r.json()["detail"]
    # régénérer : l'ancien worker est révoqué, un nouveau s'enrôle avec le nouveau jeton
    r = owner.post(f"/nodes/{nid}/worker/token")
    assert r.status_code == 200 and r.json()["revoked"] is True
    assert node_state(owner, nid) == "pending"
    w2 = RunningWorker(world["hub"], r.json()["token"])
    wait_for(lambda: node_state(owner, nid) == "online")
    assert owner.put("/traefik/file", node=nid, json={"path": "n.yml", "create": True, "content": "http: {}\n"}).status_code == 200
    world["worker"] = w2


def test_revoked_identity_cannot_reconnect(world):
    nid = world["nid"]
    old_key = Ed25519PrivateKey.generate()
    with wsc.connect(world["hub"].replace("http://", "ws://") + "/api/worker/ws") as ws:
        ws.send(json.dumps({"type": "hello", "nodeId": nid, "protocol": proto.PROTOCOL}))
        nonce = json.loads(ws.recv(5))["nonce"]
        ws.send(json.dumps({"type": "auth", "signature": base64.b64encode(old_key.sign(proto.auth_message(nid, nonce))).decode()}))
        with pytest.raises(Exception) as e:
            ws.recv(5)
        assert "4401" in str(e.value)


def test_delete_node(world):
    nid, owner = world["nid"], world["wendy"]
    assert owner.delete(f"/nodes/{nid}").status_code == 200
    assert node_state_missing(owner, nid)
    assert db.one("SELECT 1 FROM node_workers WHERE node_id=?", (nid,)) is None
    assert owner.get("/traefik/overview", node=nid).status_code == 404
    world["worker"].stop()
    # le quota est libéré
    assert owner.post("/nodes", json={"name": "Nouveau"}).status_code == 200


def node_state_missing(api: Api, nid: str) -> bool:
    return nid not in [n["id"] for n in api.get("/nodes").json()]


def test_quota_applies_to_transfer(world):
    owner, root = world["yann"], world["root"]
    r = owner.post("/nodes", json={"name": "Yann 1"}).json()
    assert world["root"].put(f"/users/{db.one('SELECT id FROM users WHERE username=?', ('xavier',))['id']}", json={"maxNodes": 0}).status_code == 200
    owner.post(f"/nodes/{r['id']}/members", json={"username": "xavier", "role": "admin"})
    xid = db.one("SELECT id FROM users WHERE username='xavier'")["id"]
    t = owner.post(f"/nodes/{r['id']}/transfer", json={"userId": xid})
    assert t.status_code == 403 and "nœud" in t.json()["detail"]  # xavier n'a pas le droit d'en posséder


def test_user_without_right_sees_no_creation_in_profile(world):
    me = world["xavier"].get("/auth/me").json()
    assert me["maxNodes"] == 0 and world["root"].get("/auth/me").json()["maxNodes"] == -1


def test_suspended_node_worker_is_kept_out(world):
    root = world["root"]
    r = root.post("/nodes", json={"name": "A suspendre"})
    nid, token = r.json()["id"], r.json()["token"]
    key = Ed25519PrivateKey.generate()
    pub = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    assert httpx.post(world["hub"] + "/api/worker/enroll", json={"token": token, "publicKey": pub}).status_code == 200
    ws_url = world["hub"].replace("http://", "ws://") + "/api/worker/ws"

    def hello():
        ws = wsc.connect(ws_url)
        ws.send(json.dumps({"type": "hello", "nodeId": nid, "protocol": proto.PROTOCOL}))
        return ws

    db.run("UPDATE nodes SET status='suspended' WHERE id=?", (nid,))
    with hello() as ws:  # connu mais suspendu : refusé avant même le défi
        with pytest.raises(Exception) as e:
            ws.recv(5)
        assert "4430" in str(e.value)
    db.run("UPDATE nodes SET status='active' WHERE id=?", (nid,))
    with hello() as ws:  # repris : le défi est de nouveau proposé
        assert json.loads(ws.recv(5))["type"] == "challenge"
    assert root.delete(f"/nodes/{nid}").status_code == 200
