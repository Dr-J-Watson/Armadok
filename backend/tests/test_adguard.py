"""AdGuard Home comme compte DNS : les réécritures DNS servent d'enregistrements A / AAAA / CNAME (serveur AdGuard simulé en HTTP)."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app import db
from app.dns_providers import capabilities, clean_server_url
from app.main import app
from app.ovh_client import OvhError

client = TestClient(app)
PW = "un-mot-de-passe-solide"
GOOD = "Basic " + base64.b64encode(b"admin:s3cret").decode()
STATE: dict = {"rewrites": [], "seen_auth": [], "redirect": False}


class FakeAdGuard(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        STATE["seen_auth"].append(self.headers.get("Authorization"))
        if self.headers.get("Authorization") != GOOD:
            return self._send(401, b"Unauthorized", "text/plain")
        if STATE["redirect"]:
            self.send_response(302)
            self.send_header("Location", "https://elsewhere.invalid/")
            self.end_headers()
            return
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"null") if n else None
        if self.path == "/control/status":
            return self._send(200, json.dumps({"version": "v0.107.52", "running": True}).encode())
        if self.path == "/control/rewrite/list":
            return self._send(200, json.dumps(STATE["rewrites"]).encode())
        if self.path == "/control/rewrite/add":
            if body in STATE["rewrites"]:
                return self._send(400, b"rewrite already exists", "text/plain")
            STATE["rewrites"].append({"domain": body["domain"], "answer": body["answer"]})
            return self._send(200)
        if self.path == "/control/rewrite/delete":
            STATE["rewrites"] = [r for r in STATE["rewrites"] if (r["domain"], r["answer"]) != (body["domain"], body["answer"])]
            return self._send(200)
        return self._send(404, b"not found", "text/plain")

    do_GET = do_POST = _handle


@pytest.fixture(scope="module")
def adguard():
    srv = HTTPServer(("127.0.0.1", 0), FakeAdGuard)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def login(name, pw=PW):
    r = client.post("/api/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture(scope="module")
def world(adguard):
    root = login("root", "admin-password-1")
    for name, can in (("agpat", True), ("agrae", False)):
        client.post("/api/users", headers=root, json={"username": name, "password": PW, "canAddDomains": can})
    local = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    client.post(f"/api/nodes/{local}/members", headers=root, json={"username": "agpat", "role": "editor"})
    client.post(f"/api/nodes/{local}/members", headers=root, json={"username": "agrae", "role": "editor"})
    return {"root": root, "pat": login("agpat"), "rae": login("agrae"), "url": adguard}


def creds(url, **kw):
    return {"url": url, "username": "admin", "password": "s3cret", **kw}


def test_url_cleaning_and_capabilities():
    assert clean_server_url("192.168.1.5:3000") == "http://192.168.1.5:3000"
    assert clean_server_url("https://dns.maison.lan/control/rewrite/list") == "https://dns.maison.lan"
    assert clean_server_url("http://adguard:3000/") == "http://adguard:3000"
    for bad in ("", "ftp://x", "http://169.254.169.254/latest", "http://0.0.0.0:80"):
        with pytest.raises(OvhError):
            clean_server_url(bad)
    c = capabilities("adguard")
    assert c["records"] and c["types"] == ["A", "AAAA", "CNAME"] and not c["ttl"] and not c["publish"] and not c["zoneTools"]
    assert capabilities("ovh")["types"] is None and capabilities("ovh")["ttl"]


def test_connection_errors_are_explained_and_credentials_never_leak(world):
    pat = world["pat"]
    r = client.post("/api/credentials/adguard/test", headers=pat, json={**creds(world["url"]), "password": "mauvais"})
    assert r.status_code == 422 and "identifiant ou le mot de passe" in r.json()["detail"] and "mauvais" not in r.text
    r = client.post("/api/credentials/adguard/test", headers=pat, json=creds("http://127.0.0.1:9"))
    assert r.status_code == 502 and "injoignable" in r.json()["detail"]
    STATE["redirect"] = True
    r = client.post("/api/credentials/adguard/test", headers=pat, json=creds(world["url"]))
    STATE["redirect"] = False
    assert r.status_code == 422 and "redirige" in r.json()["detail"]
    ok = client.post("/api/credentials/adguard/test", headers=pat, json=creds(world["url"]))
    assert ok.status_code == 200 and ok.json()["version"] == "v0.107.52"


def test_only_those_who_may_add_accounts(world):
    assert client.post("/api/credentials/adguard", headers=world["rae"], json={**creds(world["url"]), "label": "x"}).status_code == 403
    assert client.post("/api/credentials/adguard/test", headers=world["rae"], json=creds(world["url"])).status_code == 403


def test_add_account_with_domains_and_manage_records(world):
    pat, url = world["pat"], world["url"]
    STATE["rewrites"] = [{"domain": "vieux.lab.agtest.fr", "answer": "192.168.1.20"}, {"domain": "autre.fr", "answer": "10.0.0.1"},
                         {"domain": "*.lab.agtest.fr", "answer": "192.168.1.10"}, {"domain": "ipv6.lab.agtest.fr", "answer": "fd00::5"},
                         {"domain": "alias.lab.agtest.fr", "answer": "vieux.lab.agtest.fr"}]
    bad = client.post("/api/credentials/adguard", headers=pat, json={**creds(url), "label": "AG", "domains": ["pas un nom"]})
    assert bad.status_code == 422
    r = client.post("/api/credentials/adguard", headers=pat, json={**creds(url), "label": "AdGuard maison", "domains": ["lab.agtest.fr"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["provider"] == "adguard" and out["added"] == ["lab.agtest.fr"] and out["url"] == url
    assert "s3cret" not in r.text and "admin" not in json.dumps({k: v for k, v in out.items() if k != "label"})   # identifiants jamais renvoyés
    cid = out["id"]
    row = db.one("SELECT * FROM credentials WHERE id=?", (cid,))
    assert "s3cret" not in (row["secret"] or "") and row["app_key"] != "admin"                                       # chiffrés en base

    zones = {z["name"]: z for z in client.get("/api/dns/zones", headers=pat).json()}
    z = zones["lab.agtest.fr"]
    assert z["provider"] == "adguard" and z["capabilities"]["types"] == ["A", "AAAA", "CNAME"]
    rr = client.get("/api/dns/zones/lab.agtest.fr/records", headers=pat)
    assert rr.status_code == 200, rr.text
    recs = rr.json()
    recs = client.get("/api/dns/zones/lab.agtest.fr/records", headers=pat).json()
    by = {(r["subDomain"], r["fieldType"]): r for r in recs}
    assert by[("vieux", "A")]["target"] == "192.168.1.20" and by[("*", "A")]["target"] == "192.168.1.10"
    assert by[("ipv6", "AAAA")]["target"] == "fd00::5" and by[("alias", "CNAME")]["target"] == "vieux.lab.agtest.fr"
    assert all(r["subDomain"] != "" or r["fieldType"] for r in recs) and not any("autre" in r["subDomain"] for r in recs)   # autre.fr n'est pas dans cette zone

    # création : valeurs contrôlées, doublon refusé
    mk = lambda **b: client.post("/api/dns/zones/lab.agtest.fr/records", headers=pat, json=b)
    assert mk(fieldType="MX", subDomain="x", target="10 mx.fr.").status_code == 422
    assert mk(fieldType="A", subDomain="x", target="pas-une-ip").status_code == 422
    assert mk(fieldType="A", subDomain="bad name", target="192.168.1.9").status_code == 422
    new = mk(fieldType="A", subDomain="grafana", target="192.168.1.30")
    assert new.status_code == 200 and {"domain": "grafana.lab.agtest.fr", "answer": "192.168.1.30"} in STATE["rewrites"]
    assert mk(fieldType="A", subDomain="grafana", target="192.168.1.30").status_code == 409
    assert mk(fieldType="A", subDomain="", target="192.168.1.2").status_code == 200   # la racine du domaine
    assert {"domain": "lab.agtest.fr", "answer": "192.168.1.2"} in STATE["rewrites"]

    # modification = remplacement (pas de doublon, pas de trou)
    rid = new.json()["id"]
    up = client.put(f"/api/dns/zones/lab.agtest.fr/records/{rid}", headers=pat, json={"fieldType": "A", "subDomain": "grafana", "target": "192.168.1.31"})
    assert up.status_code == 200
    gr = [r for r in STATE["rewrites"] if r["domain"] == "grafana.lab.agtest.fr"]
    assert gr == [{"domain": "grafana.lab.agtest.fr", "answer": "192.168.1.31"}]

    # suppression
    rid2 = next(r["id"] for r in client.get("/api/dns/zones/lab.agtest.fr/records?force=true", headers=pat).json() if r["subDomain"] == "grafana")
    assert client.delete(f"/api/dns/zones/lab.agtest.fr/records/{rid2}", headers=pat).status_code == 200
    assert not [r for r in STATE["rewrites"] if r["domain"] == "grafana.lab.agtest.fr"]
    assert client.delete(f"/api/dns/zones/lab.agtest.fr/records/{rid2}", headers=pat).status_code == 404     # déjà supprimé

    # les outils de zone n'existent que chez OVH ; « appliquer » est un simple succès sans effet
    assert client.get("/api/dns/zones/lab.agtest.fr/export", headers=pat).status_code == 400
    assert client.get("/api/dns/zones/lab.agtest.fr/history", headers=pat).status_code == 400
    assert client.post("/api/dns/zones/lab.agtest.fr/refresh", headers=pat).status_code == 200
    world["cid"] = cid


def test_sync_knows_missing_and_existing_local_names(world):
    pat = world["pat"]
    rows = {r["host"]: r for r in client.post("/api/sync/check", headers=pat, json={"hosts": ["vieux.lab.agtest.fr", "nouveau.lab.agtest.fr", "x.y.lab.agtest.fr"]}).json()}
    assert rows["vieux.lab.agtest.fr"]["status"] == "ok" and rows["vieux.lab.agtest.fr"]["provider"] == "adguard"
    assert rows["nouveau.lab.agtest.fr"]["status"] == "wildcard"      # *.lab.agtest.fr existe
    STATE["rewrites"] = [r for r in STATE["rewrites"] if not r["domain"].startswith("*.")]
    client.post(f"/api/dns/zones/lab.agtest.fr/refresh", headers=pat)
    rows = {r["host"]: r for r in client.post("/api/sync/check", headers=pat, json={"hosts": ["nouveau.lab.agtest.fr"]}).json()}
    # plus de joker : l'enregistrement manque ; AdGuard ne suppose jamais l'adresse publique du nœud
    import time
    time.sleep(0.01)
    r = client.get("/api/dns/zones/lab.agtest.fr/records?force=true", headers=pat)
    assert r.status_code == 200
    rows = {r["host"]: r for r in client.post("/api/sync/check", headers=pat, json={"hosts": ["nouveau.lab.agtest.fr"]}).json()}
    assert rows["nouveau.lab.agtest.fr"]["status"] == "missing"
    assert rows["nouveau.lab.agtest.fr"]["suggestion"]["target"] == "192.168.1.2"   # l'adresse de la racine de la zone (créée plus haut), jamais celle du nœud


def test_edit_credential_and_test_and_more_names(world):
    pat, cid = world["pat"], world["cid"]
    assert client.post(f"/api/credentials/{cid}/test", headers=pat).json()["version"] == "v0.107.52"
    bad = client.put(f"/api/credentials/{cid}", headers=pat, json={"password": "faux"})
    assert bad.status_code == 422                                                       # re-test à l'enregistrement
    ok = client.put(f"/api/credentials/{cid}", headers=pat, json={"label": "Renommé", "password": "s3cret"})
    assert ok.status_code == 200 and ok.json()["label"] == "Renommé" and "password" not in ok.text
    more = client.post(f"/api/credentials/{cid}/names", headers=pat, json={"names": ["ag-home.lan", "lab.agtest.fr", "nul"]})
    assert more.status_code == 200 and more.json()["added"] == ["ag-home.lan"] and len(more.json()["skipped"]) == 2
    assert client.post(f"/api/credentials/{cid}/names", headers=world["rae"], json={"names": ["x.lan"]}).status_code in (403, 404)
    assert client.delete(f"/api/credentials/{cid}", headers=pat).status_code == 200
    assert client.get("/api/dns/zones/lab.agtest.fr/records", headers=pat).status_code in (403, 404)
