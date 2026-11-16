"""Certificats : lecture d'acme.json / du relevé sans clés, couverture (joker), état des sites, droits."""
import base64
import datetime as dt
import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app import certs as certlib
from app import certsync, worker
from app.config import settings
from app.main import app
from app.traefik_store import Store

client = TestClient(app)
PW = "un-mot-de-passe-solide"


def make_cert(names, days=60, issuer="Test CA"):
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    iss = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, issuer)])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(iss).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=30)).not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False).sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return base64.b64encode(pem).decode(), base64.b64encode(priv).decode()


def acme(entries):
    return {"letsencrypt": {"Account": {"Email": "x@y.z", "PrivateKey": "SECRET-ACCOUNT-KEY"}, "Certificates": [
        {"domain": {"main": names[0], "sans": names[1:]}, "certificate": c, "key": k, "Store": "default"}
        for names, (c, k) in entries]}}


@pytest.fixture()
def acme_file(tmp_path):
    entries = [(["blog.example.com"], make_cert(["blog.example.com"], 60)),
               (["example.org", "*.example.org"], make_cert(["example.org", "*.example.org"], 80)),
               (["old.example.com"], make_cert(["old.example.com"], 5)),
               (["dead.example.com"], make_cert(["dead.example.com"], -3))]
    p = tmp_path / "acme.json"
    p.write_text(json.dumps(acme(entries)))
    return p


def test_parse_acme_without_ever_returning_a_key(acme_file):
    r = certlib.load(acme_file)
    assert r["configured"] and r["error"] is None and len(r["certs"]) == 4
    blob = json.dumps(r)
    assert "PRIVATE" not in blob and "SECRET-ACCOUNT-KEY" not in blob and "UFJJVkFURS" not in blob and '"key"' not in blob.lower()
    c = next(c for c in r["certs"] if c["main"] == "example.org")
    assert c["names"] == ["*.example.org", "example.org"] and c["issuer"] == "Test CA" and c["resolver"] == "letsencrypt"


def test_relay_snapshot_has_no_key_and_is_read_back(acme_file, tmp_path):
    out = tmp_path / "out" / "certs.json"
    certsync.write(certsync.snapshot(acme_file), out)
    raw = out.read_text()
    assert "PRIVATE" not in raw and "key" not in json.loads(raw)["certs"][0] and oct(out.stat().st_mode & 0o777) == "0o644"
    r = certlib.load(out)
    assert r["error"] is None and len(r["certs"]) == 4 and r["updated"]
    # acme.json absent / illisible : un message clair, pas une erreur
    bad = certsync.snapshot(tmp_path / "nope.json")
    assert bad["certs"] == [] and "introuvable" in bad["error"]
    (tmp_path / "broken.json").write_text("{oops")
    assert "illisible" in certsync.snapshot(tmp_path / "broken.json")["error"]
    certsync.write(bad, out)
    assert certlib.load(out)["error"] and certlib.load(out)["certs"] == []


def test_load_errors_are_explicit(tmp_path):
    assert certlib.load(None)["configured"] is False
    assert "introuvable" in certlib.load(tmp_path / "x.json")["error"]
    (tmp_path / "a.json").write_text("[1,2")
    assert "illisible" in certlib.load(tmp_path / "a.json")["error"]


def test_wildcard_covers_one_label_only():
    c = {"names": ["*.example.org", "example.org"], "main": "example.org"}
    assert certlib.covers(c, "a.example.org") and certlib.covers(c, "example.org")
    assert not certlib.covers(c, "a.b.example.org") and not certlib.covers(c, "example.org.evil.com") and not certlib.covers(c, "xexample.org")


def test_site_status(acme_file):
    certs = certlib.load(acme_file)["certs"]
    now = __import__("time").time()
    item = lambda name, rule, tls: {"id": f"http/routers/{name}", "proto": "http", "kind": "routers", "name": name, "spec": {"rule": rule, "tls": tls}}  # noqa: E731
    items = [item("blog", "Host(`blog.example.com`)", {"certResolver": "le"}), item("wild", "Host(`app.example.org`)", {"certResolver": "le"}),
             item("old", "Host(`old.example.com`)", {"certResolver": "le"}), item("dead", "Host(`dead.example.com`)", {"certResolver": "le"}),
             item("new", "Host(`new.example.com`)", {"certResolver": "le"}), item("manual", "Host(`m.example.com`)", {}),
             item("plain", "Host(`p.example.com`)", None), item("pass", "Host(`x.example.com`)", {"passthrough": True}),
             {**item("off", "Host(`off.example.com`)", {"certResolver": "le"}), "disabled": True}]
    by = {s["name"]: s for s in certlib.sites(items, certs, now)}
    assert by["blog"]["worst"] == "ok" and by["blog"]["hosts"][0]["days"] >= 59
    assert by["wild"]["worst"] == "ok" and by["wild"]["hosts"][0]["wildcard"] is True
    assert by["old"]["worst"] == "expiring" and by["dead"]["worst"] == "expired" and by["new"]["worst"] == "missing"
    assert by["manual"]["worst"] == "unmanaged"
    assert "plain" not in by and "pass" not in by and "off" not in by                                # pas de TLS / passthrough / désactivé
    assert list(by)[:3] == ["dead", "new", "old"]                                                    # les plus urgents d'abord


def _login(name, pw=PW):
    r = client.post("/api/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_api_and_scoped_visibility(acme_file, monkeypatch):
    from app import db
    root = _login("root", "admin-password-1")
    monkeypatch.setattr(settings, "certs_file", None)
    assert client.get("/api/traefik/certs", headers=root).json()["configured"] is False
    monkeypatch.setattr(settings, "certs_file", str(acme_file))
    client.put("/api/traefik/file", headers=root, json={"path": "cert-site.yml", "create": True, "content": "http:\n  routers:\n    cs:\n      rule: Host(`blog.example.com`)\n      service: noop@internal\n      tls:\n        certResolver: le\n"})
    r = client.get("/api/traefik/certs", headers=root).json()
    assert r["configured"] and len(r["certs"]) == 4 and any(s["name"] == "cs" and s["worst"] == "ok" for s in r["sites"])
    assert r["certs"][0]["daysLeft"] <= r["certs"][-1]["daysLeft"] and r["warnings"] >= 0
    assert "PRIVATE" not in json.dumps(r)
    # un éditeur sans domaine concerné ne voit aucun certificat
    local = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    client.post("/api/users", headers=root, json={"username": "cora", "password": PW})
    client.post(f"/api/nodes/{local}/members", headers=root, json={"username": "cora", "role": "editor"})
    assert client.get("/api/traefik/certs", headers=_login("cora")).json()["certs"] == []
    client.delete("/api/traefik/file", headers=root, params={"path": "cert-site.yml"})


def test_worker_op_returns_the_same_metadata(acme_file, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "certs_file", str(acme_file))
    store = Store(tmp_path / "d", tmp_path / "b")
    r = worker.handle(store, "certs", {})
    assert r["configured"] and len(r["certs"]) == 4 and "PRIVATE" not in json.dumps(r)
