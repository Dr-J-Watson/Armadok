"""Pi-hole 6 comme compte DNS : « DNS local » (IP nom) et CNAME servent d'enregistrements A / AAAA / CNAME (API simulée en HTTP)."""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app import db
from app.dns_providers import capabilities, clean_server_url
from app.main import app

client = TestClient(app)
PW = "un-mot-de-passe-solide"
S: dict = {"hosts": [], "cnames": [], "sid": "SID-1", "logins": 0, "n": 1}


class FakePiHole(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if obj is not None:
            self.wfile.write(json.dumps(obj).encode())

    def _h(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        path = self.path
        if path == "/api/auth" and self.command == "POST":
            if body.get("password") != "s3cret":
                return self._send(401, {"session": {"valid": False, "sid": None}})
            S["logins"] += 1
            S["n"] += 1
            S["sid"] = f"SID-{S['n']}"
            return self._send(200, {"session": {"valid": True, "sid": S["sid"], "csrf": "c", "validity": 300, "totp": False}})
        if self.headers.get("X-FTL-SID") != S["sid"]:
            return self._send(401, {"error": {"key": "unauthorized"}})
        m = path.split("/")
        if path in ("/api/config/dns/hosts", "/api/config/dns/cnameRecords") and self.command == "GET":
            key = "hosts" if path.endswith("hosts") else "cnameRecords"
            return self._send(200, {"config": {"dns": {key: S["hosts" if key == "hosts" else "cnames"]}}})
        if len(m) == 6 and m[3] == "dns" and m[4] in ("hosts", "cnameRecords"):
            lst = S["hosts" if m[4] == "hosts" else "cnames"]
            el = urllib.parse.unquote(m[5])
            if self.command == "PUT":
                if el in lst:
                    return self._send(400, {"error": {"message": "Item already present"}})
                lst.append(el)
                return self._send(201)
            if self.command == "DELETE":
                if el not in lst:
                    return self._send(404, {"error": {"message": "Item not found"}})
                lst.remove(el)
                return self._send(204)
        return self._send(404, {"error": {"key": "not_found"}})

    do_GET = do_POST = do_PUT = do_DELETE = _h


@pytest.fixture(scope="module")
def phole():
    srv = HTTPServer(("127.0.0.1", 0), FakePiHole)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def login(name, pw=PW):
    r = client.post("/api/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture(scope="module")
def world(phole):
    root = login("root", "admin-password-1")
    client.post("/api/users", headers=root, json={"username": "phpat", "password": PW, "canAddDomains": True})
    local = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    client.post(f"/api/nodes/{local}/members", headers=root, json={"username": "phpat", "role": "editor"})
    return {"pat": login("phpat"), "url": phole}


def creds(url, **kw):
    return {"url": url, "username": "", "password": "s3cret", **kw}


def test_url_and_capabilities():
    assert clean_server_url("http://pi.hole/admin/", "pihole") == "http://pi.hole"
    assert clean_server_url("192.168.1.2/api/docs", "pihole") == "http://192.168.1.2"
    c = capabilities("pihole")
    assert c["records"] and c["types"] == ["A", "AAAA", "CNAME"] and c["wildcards"] is False and not c["ttl"] and not c["publish"]
    assert capabilities("adguard")["wildcards"] is True


def test_login_errors_and_test_button(world):
    pat, url = world["pat"], world["url"]
    bad = client.post("/api/credentials/pihole/test", headers=pat, json={**creds(url), "password": "faux"})
    assert bad.status_code == 422 and "identifiant ou le mot de passe" in bad.json()["detail"] and "faux" not in bad.text
    assert client.post("/api/credentials/pihole/test", headers=pat, json=creds("http://127.0.0.1:9")).status_code == 502
    assert client.post("/api/credentials/pihole/test", headers=pat, json=creds(url)).status_code == 200


def test_records_create_update_delete(world):
    pat, url = world["pat"], world["url"]
    S["hosts"][:] = ["192.168.1.20 vieux.pi.exemple.fr multi.pi.exemple.fr", "10.9.9.9 autre.fr", "fd00::5 six.pi.exemple.fr"]
    S["cnames"][:] = ["alias.pi.exemple.fr,vieux.pi.exemple.fr"]
    r = client.post("/api/credentials/pihole", headers=pat, json={**creds(url), "label": "Pi-hole maison", "domains": ["pi.exemple.fr"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["provider"] == "pihole" and out["added"] == ["pi.exemple.fr"] and "s3cret" not in r.text
    cid = out["id"]
    row = db.one("SELECT * FROM credentials WHERE id=?", (cid,))
    assert "s3cret" not in (row["secret"] or "")                                           # chiffré en base

    recs = client.get("/api/dns/zones/pi.exemple.fr/records", headers=pat).json()
    by = {(r["subDomain"], r["fieldType"]): r["target"] for r in recs}
    assert by == {("vieux", "A"): "192.168.1.20", ("multi", "A"): "192.168.1.20", ("six", "AAAA"): "fd00::5", ("alias", "CNAME"): "vieux.pi.exemple.fr"}

    mk = lambda **b: client.post("/api/dns/zones/pi.exemple.fr/records", headers=pat, json=b)
    assert mk(fieldType="A", subDomain="*", target="192.168.1.9").status_code == 422        # pas de joker chez Pi-hole
    assert mk(fieldType="TXT", subDomain="t", target="x").status_code == 422
    new = mk(fieldType="A", subDomain="grafana", target="192.168.1.30")
    assert new.status_code == 200 and "192.168.1.30 grafana.pi.exemple.fr" in S["hosts"]
    assert mk(fieldType="A", subDomain="grafana", target="192.168.1.30").status_code == 409
    assert mk(fieldType="CNAME", subDomain="wiki", target="grafana.pi.exemple.fr").status_code == 200
    assert "wiki.pi.exemple.fr,grafana.pi.exemple.fr" in S["cnames"]

    # la session est réutilisée : une seule connexion pour toutes ces opérations
    assert S["logins"] <= 3

    # modifier = remplacer
    up = client.put(f"/api/dns/zones/pi.exemple.fr/records/{new.json()['id']}", headers=pat, json={"fieldType": "A", "subDomain": "grafana", "target": "192.168.1.31"})
    assert up.status_code == 200 and "192.168.1.31 grafana.pi.exemple.fr" in S["hosts"] and "192.168.1.30 grafana.pi.exemple.fr" not in S["hosts"]

    # supprimer UN nom d'une ligne qui en porte deux : l'autre reste
    rid = next(r["id"] for r in client.get("/api/dns/zones/pi.exemple.fr/records?force=true", headers=pat).json() if r["subDomain"] == "vieux" and r["fieldType"] == "A")
    assert client.delete(f"/api/dns/zones/pi.exemple.fr/records/{rid}", headers=pat).status_code == 200
    assert "192.168.1.20 multi.pi.exemple.fr" in S["hosts"] and not any("vieux.pi.exemple.fr" in h.split()[1:] for h in S["hosts"])
    assert client.delete(f"/api/dns/zones/pi.exemple.fr/records/{rid}", headers=pat).status_code == 404
    world["cid"] = cid


def test_session_expiry_is_handled(world):
    pat = world["pat"]
    before = S["logins"]
    S["sid"] = "SID-REVOKED"                                    # Pi-hole a oublié notre session
    r = client.get("/api/dns/zones/pi.exemple.fr/records?force=true", headers=pat)
    assert r.status_code == 200 and S["logins"] == before + 1   # reconnexion automatique, une seule fois


def test_account_management(world):
    pat, cid = world["pat"], world["cid"]
    assert client.post(f"/api/credentials/{cid}/test", headers=pat).status_code == 200
    assert client.put(f"/api/credentials/{cid}", headers=pat, json={"password": "faux"}).status_code == 422
    assert client.put(f"/api/credentials/{cid}", headers=pat, json={"label": "Renommé", "password": "s3cret"}).status_code == 200
    more = client.post(f"/api/credentials/{cid}/names", headers=pat, json={"names": ["ph-home.lan"]})
    assert more.status_code == 200 and more.json()["added"] == ["ph-home.lan"]
    assert client.get("/api/dns/zones/pi.exemple.fr/export", headers=pat).status_code == 400
    assert client.delete(f"/api/credentials/{cid}", headers=pat).status_code == 200
