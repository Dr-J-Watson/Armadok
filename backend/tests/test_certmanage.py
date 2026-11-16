"""Gérer les certificats sans jamais lire une clé : l'outil isolé qui retire des entrées d'acme.json (Traefik arrêté), les demandes écrites par
l'interface (noms seulement), et la nouvelle demande d'un site sans certificat."""
import base64
import datetime as dt
import json
import os

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app import certpurge, db
from app.config import settings
from app.main import app

client = TestClient(app)
PW = "un-mot-de-passe-solide"


def make_cert(names, days=60):
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test CA")])).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=30)).not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False).sign(key, hashes.SHA256()))
    return (base64.b64encode(cert.public_bytes(serialization.Encoding.PEM)).decode(),
            base64.b64encode(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())).decode())


def acme(entries, resolver="le"):
    return {resolver: {"Account": {"Email": "x@y.z", "PrivateKey": "SECRET-ACCOUNT-KEY"}, "Certificates": [
        {"domain": {"main": n[0], "sans": n[1:]}, "certificate": c, "key": k, "Store": "default"} for n, (c, k) in entries]},
        "autre": {"Certificates": [{"domain": {"main": "blog.exemple.fr"}, "certificate": "AA", "key": "KK"}]}}


@pytest.fixture()
def acme_file(tmp_path):
    p = tmp_path / "acme.json"
    p.write_text(json.dumps(acme([(["blog.exemple.fr"], make_cert(["blog.exemple.fr"])), (["dead.exemple.fr"], make_cert(["dead.exemple.fr"], -5)),
                                  (["wild.exemple.fr", "*.wild.exemple.fr"], make_cert(["wild.exemple.fr", "*.wild.exemple.fr"]))])))
    os.chmod(p, 0o600)
    return p


# ---------- l'outil isolé ----------
def test_purge_removes_only_the_requested_entry_and_keeps_a_backup(acme_file):
    before = json.loads(acme_file.read_text())
    res = certpurge.apply(acme_file, [{"resolver": "le", "main": "dead.exemple.fr", "mode": "delete"}, {"resolver": "le", "main": "absent.exemple.fr", "mode": "delete"}])
    assert res["error"] is None and res["removed"] == [{"resolver": "le", "main": "dead.exemple.fr", "mode": "delete"}] and res["missing"] == [{"resolver": "le", "main": "absent.exemple.fr"}]
    after = json.loads(acme_file.read_text())
    assert [c["domain"]["main"] for c in after["le"]["Certificates"]] == ["blog.exemple.fr", "wild.exemple.fr"]
    assert after["le"]["Account"] == before["le"]["Account"] and after["autre"] == before["autre"]                    # le reste est intact, compte compris
    assert after["le"]["Certificates"][0] == before["le"]["Certificates"][0]                                          # même certificat, même clé, octet pour octet
    assert oct(acme_file.stat().st_mode & 0o777) == "0o600"                                                           # Traefik refuse un acme.json trop ouvert
    bak = acme_file.parent / res["backup"]
    assert bak.exists() and oct(bak.stat().st_mode & 0o777) == "0o600" and json.loads(bak.read_text()) == before     # retour en arrière possible


def test_purge_is_resolver_exact_and_does_nothing_when_nothing_matches(acme_file):
    res = certpurge.apply(acme_file, [{"resolver": "autre", "main": "dead.exemple.fr", "mode": "delete"}])            # bon nom, mauvais résolveur
    assert res["removed"] == [] and res["backup"] is None and not list(acme_file.parent.glob("acme.json.bak-*"))
    res = certpurge.apply(acme_file, [{"resolver": "autre", "main": "blog.exemple.fr", "mode": "renew"}])
    assert [r["resolver"] for r in res["removed"]] == ["autre"] and any(c["domain"]["main"] == "blog.exemple.fr" for c in json.loads(acme_file.read_text())["le"]["Certificates"])


def test_dry_run_unreadable_file_and_backup_pruning(acme_file, tmp_path):
    before = acme_file.read_text()
    assert certpurge.apply(acme_file, [{"resolver": "le", "main": "dead.exemple.fr", "mode": "delete"}], dry=True)["removed"] and acme_file.read_text() == before
    bad = tmp_path / "pas-acme.json"
    bad.write_text("pas du json")
    r = certpurge.apply(bad, [{"resolver": "le", "main": "x", "mode": "delete"}])
    assert r["error"] and bad.read_text() == "pas du json" and r["backup"] is None
    assert "introuvable" in certpurge.apply(tmp_path / "rien.json", [{"resolver": "a", "main": "b", "mode": "delete"}])["error"]
    for i in range(8):                                                                                                # 8 passes : on ne garde que les 5 dernières copies
        acme_file.write_text(json.dumps(acme([([f"s{i}.exemple.fr"], make_cert([f"s{i}.exemple.fr"]))])))
        certpurge.apply(acme_file, [{"resolver": "le", "main": f"s{i}.exemple.fr", "mode": "delete"}], now=1_700_000_000 + i * 10)
    assert len(list(acme_file.parent.glob("acme.json.bak-*"))) == 5


def test_requests_are_cleaned():
    got = certpurge.clean_requests({"requests": [{"resolver": "le", "main": "A.Exemple.fr"}, {"resolver": "le", "main": "a.exemple.fr"}, {"main": ""}, "x", {"main": "b.fr", "mode": "renew"}, {"main": 5}]})
    assert got == [{"resolver": "le", "main": "a.exemple.fr", "mode": "delete"}, {"resolver": "", "main": "b.fr", "mode": "renew"}]


def _fake_docker(tmp_path, stop_status=204):
    import socketserver
    import threading
    from http.server import BaseHTTPRequestHandler
    calls = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path)
            self.send_response(stop_status if "/stop" in self.path else 204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    class S(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    sock = str(tmp_path / "d.sock")
    srv = S(sock, H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, sock, calls


def test_run_stops_purges_and_restarts_traefik(acme_file, tmp_path):
    srv, sock, calls = _fake_docker(tmp_path)
    try:
        res = certpurge.run(acme_file, [{"resolver": "le", "main": "dead.exemple.fr", "mode": "delete"}], sock, "traefik")
    finally:
        srv.shutdown()
    assert calls == ["/containers/traefik/stop?t=30", "/containers/traefik/start"]
    assert res["error"] is None and res["restarted"] and res["removed"][0]["main"] == "dead.exemple.fr" and "dead.exemple.fr" not in acme_file.read_text()


def test_run_touches_nothing_if_stop_fails_and_always_restarts(acme_file, tmp_path):
    srv, sock, calls = _fake_docker(tmp_path, stop_status=500)
    before = acme_file.read_text()
    try:
        res = certpurge.run(acme_file, [{"resolver": "le", "main": "dead.exemple.fr", "mode": "delete"}], sock, "traefik")
    finally:
        srv.shutdown()
    assert "pas pu être arrêté" in res["error"] and acme_file.read_text() == before and calls == ["/containers/traefik/stop?t=30"] and not res["restarted"]
    srv, sock, calls = _fake_docker(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path)
    try:
        res = certpurge.run(tmp_path / "absent.json", [{"resolver": "le", "main": "a.fr", "mode": "delete"}], sock, "traefik")   # fichier illisible : Traefik repart quand même
    finally:
        srv.shutdown()
    assert calls[-1] == "/containers/traefik/start" and res["restarted"] and res["error"]
    assert "injoignable" in certpurge.run(acme_file, [{"resolver": "le", "main": "dead.exemple.fr", "mode": "delete"}], str(tmp_path / "none.sock"), "traefik")["error"]


def test_unavailable_reasons(acme_file, tmp_path):
    sock = tmp_path / "s.sock"
    sock.write_text("")
    assert certpurge.unavailable(str(acme_file), str(sock)) is None
    assert "TRAEFIK_CERTS_FILE" in certpurge.unavailable(None, str(sock))
    assert "socket Docker" in certpurge.unavailable(str(acme_file), str(tmp_path / "nope.sock"))
    relay = tmp_path / "certs.json"
    relay.write_text(json.dumps({"version": 1, "certs": []}))
    assert "pas un acme.json" in certpurge.unavailable(str(relay), str(sock))


# ---------- l'interface ----------
@pytest.fixture()
def world(acme_file, tmp_path):
    srv, sock, calls = _fake_docker(tmp_path)
    old, old_sock = settings.certs_file, settings.docker_socket
    settings.certs_file, settings.docker_socket = str(acme_file), sock
    root = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
    nid = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    h = {"Authorization": "Bearer " + root, "X-Node": nid}
    if not db.one("SELECT 1 FROM users WHERE username='cm-ed'"):
        client.post("/api/users", headers=h, json={"username": "cm-ed", "password": PW})
        uid = db.one("SELECT id FROM users WHERE username='cm-ed'")["id"]
        db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (nid, uid, "editor", db.now()))
    ed = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"username": "cm-ed", "password": PW}).json()["access_token"], "X-Node": nid}
    yield {"h": h, "ed": ed, "nid": nid, "calls": calls, "acme": acme_file}
    srv.shutdown()
    settings.certs_file, settings.docker_socket = old, old_sock
    (settings.dynamic_dir / "zz-webui-nudge.yml").unlink(missing_ok=True)


def _wait(h):
    import time
    for _ in range(100):
        p = client.get("/api/traefik/certs/pending", headers=h).json()
        if not p["running"]:
            return p
        time.sleep(0.05)
    raise AssertionError("l'opération ne se termine pas")


def test_ui_deletes_a_certificate_by_itself_without_ever_sending_a_key(world):
    h = world["h"]
    d = client.get("/api/traefik/certs", headers=h).json()
    assert d["canManage"] and d["purgeSupported"] and d["purgeWhy"] is None and "SECRET" not in json.dumps(d) and "key" not in {k for c in d["certs"] for k in c}
    body = {"items": [{"resolver": "le", "main": "dead.exemple.fr"}]}
    assert client.post("/api/traefik/certs/purge", headers=world["ed"], json=body).status_code == 403                                                          # droit dédié
    assert client.post("/api/traefik/certs/purge", headers=h, json={"items": [{"resolver": "le", "main": "pas-dans-le-releve.fr"}]}).status_code == 422       # pas de texte libre
    assert client.post("/api/traefik/certs/purge", headers=h, json={**body, "mode": "boom"}).status_code == 422
    assert client.post("/api/traefik/certs/purge", headers=h, json=body).status_code == 202
    p = _wait(h)
    assert p["result"]["error"] is None and p["result"]["restarted"] and p["result"]["removed"][0]["main"] == "dead.exemple.fr" and p["result"]["backup"]
    assert "key" not in json.dumps(p).lower() and "SECRET" not in json.dumps(p)
    assert world["calls"] == ["/containers/traefik/stop?t=30", "/containers/traefik/start"]
    assert "dead.exemple.fr" not in world["acme"].read_text()
    assert client.get("/api/traefik/certs/pending", headers=world["ed"]).status_code == 200                                                                    # voir : droit de lecture
    assert client.post("/api/traefik/certs/purge", headers=h, json=body).status_code == 422                                                                   # déjà retiré : le relevé ne le connaît plus


def test_purge_refused_with_a_reason_when_not_possible(world):
    old = settings.docker_socket
    settings.docker_socket = "/nulle/part.sock"
    try:
        d = client.get("/api/traefik/certs", headers=world["h"]).json()
        assert d["purgeSupported"] is False and "socket Docker" in d["purgeWhy"]
        r = client.post("/api/traefik/certs/purge", headers=world["h"], json={"items": [{"resolver": "le", "main": "dead.exemple.fr"}]})
        assert r.status_code == 422 and "socket Docker" in r.json()["detail"]
    finally:
        settings.docker_socket = old


def put_site(h, name, host, resolver=None):
    router = {"rule": f"Host(`{host}`)", "entryPoints": ["websecure"], "service": name}
    if resolver:
        router["tls"] = {"certResolver": resolver}
    return client.put("/api/traefik/site", headers=h, json={"name": name, "router": router, "service": {"loadBalancer": {"servers": [{"url": "http://x:80"}]}}})


def test_ask_again_for_a_site_without_certificate(world):
    h = world["h"]
    assert put_site(h, "cm-nores", "nores.exemple.fr").status_code == 200
    assert put_site(h, "cm-ok", "blog.exemple.fr", "le").status_code == 200
    assert put_site(h, "cm-new", "neuf.exemple.fr", "le").status_code == 200
    ask = lambda host, hh=h: client.post("/api/traefik/certs/request", headers=hh, json={"host": host})
    assert ask("inconnu.exemple.fr").status_code == 404
    r = ask("nores.exemple.fr")
    assert r.status_code == 422 and "résolveur" in r.json()["detail"]                                                  # on dit POURQUOI rien ne se passera
    assert ask("blog.exemple.fr").status_code == 409                                                                  # déjà un certificat valide
    r = ask("dead.exemple.fr")                                                                                        # certificat EXPIRÉ encore dans acme.json : redemander ne servirait à rien
    assert put_site(h, "cm-dead", "dead.exemple.fr", "le").status_code == 200
    r = ask("dead.exemple.fr")
    assert r.status_code == 409 and "Renouveler" in r.json()["detail"]
    assert ask("neuf.exemple.fr", world["ed"]).status_code == 403
    import app.routes_certs as rc
    rc._last_nudge.clear()
    ok = ask("neuf.exemple.fr")
    assert ok.status_code == 200 and ok.json()["resolver"] == "le"
    nudge = (settings.dynamic_dir / "zz-webui-nudge.yml").read_text()
    assert "webui-nudge" in nudge and "stripPrefix" in nudge
    assert ask("neuf.exemple.fr").status_code == 429                                                                  # pas de rafale vers Let's Encrypt
    rc._last_nudge.clear()
    first = nudge
    import time
    time.sleep(1.1)
    assert ask("neuf.exemple.fr").status_code == 200 and (settings.dynamic_dir / "zz-webui-nudge.yml").read_text() != first   # le contenu change à chaque demande : Traefik recharge
    for n in ("cm-nores", "cm-ok", "cm-new", "cm-dead"):
        assert client.post("/api/traefik/site/delete", headers=h, json={"name": n}).status_code == 200


def test_the_new_cap_is_in_the_catalog_and_off_for_other_roles():
    from app import caps
    assert "certs.manage" in caps.CATALOG and "certs.manage" in caps.PRESETS["admin"] and "certs.manage" not in caps.PRESETS["editor"] | caps.PRESETS["viewer"]
    assert "certs.manage" not in caps.effective_closure({"certs.manage", "certs.view"})                                # dépend de « voir tout Traefik »


def test_several_certificates_at_once_cost_a_single_restart(world):
    h = world["h"]
    names = ["dead.exemple.fr", "blog.exemple.fr"]
    items = [{"resolver": "le", "main": n} for n in names]
    assert client.post("/api/traefik/certs/purge", headers=h, json={"items": items, "mode": "renew"}).status_code == 202
    p = _wait(h)
    assert p["result"]["error"] is None and sorted(r["main"] for r in p["result"]["removed"]) == sorted(names)
    assert world["calls"] == ["/containers/traefik/stop?t=30", "/containers/traefik/start"]   # un seul arrêt / redémarrage pour les deux
    left = [c["domain"]["main"] for c in json.loads(world["acme"].read_text())["le"]["Certificates"]]
    assert left == ["wild.exemple.fr"]                                                         # le résolveur « autre » (même nom, autre résolveur) n'est pas touché
    assert json.loads(world["acme"].read_text())["autre"]["Certificates"][0]["domain"]["main"] == "blog.exemple.fr"
