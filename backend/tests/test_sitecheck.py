"""Test en un clic d'un site : seules les adresses des routeurs du nœud sont contactées."""
from fastapi.testclient import TestClient

from app import sitecheck
from app.main import app

client = TestClient(app)


def _login():
    t = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
    return {"Authorization": "Bearer " + t}


def test_site_check_uses_router_hosts_only(monkeypatch):
    h = _login()
    seen = []
    monkeypatch.setattr(sitecheck, "check", lambda hosts: seen.append(hosts) or [{"host": x, "ok": True} for x in hosts])
    r = client.put("/api/traefik/site", headers=h, json={"name": "chk", "router": {"rule": "Host(`chk.example.org`) || Host(`www.chk.example.org`)", "service": "chk-svc", "entryPoints": ["websecure"]},
                                                          "serviceName": "chk-svc", "service": {"loadBalancer": {"servers": [{"url": "http://10.0.0.5:80"}]}}})
    assert r.status_code == 200, r.text
    res = client.get("/api/traefik/site/check?name=chk", headers=h)
    assert res.status_code == 200 and seen[-1] == ["chk.example.org", "www.chk.example.org"]
    assert client.get("/api/traefik/site/check?name=inconnu", headers=h).status_code == 404            # jamais une adresse choisie par l'appelant
    assert client.get("/api/traefik/site/check?name=chk").status_code in (401, 403)


def test_checks_report_each_step(monkeypatch):
    monkeypatch.setattr(sitecheck, "_dns", lambda h: {"ok": False, "detail": "le nom ne se résout pas"})
    out = sitecheck.check(["nope.example.org"])[0]
    assert out["ok"] is False and out["tls"]["detail"] == "non testé" and out["http"]["detail"] == "non testé"
    monkeypatch.setattr(sitecheck, "_dns", lambda h: {"ok": True, "detail": "1.2.3.4"})
    monkeypatch.setattr(sitecheck, "_tls", lambda h: {"ok": True, "days": 30, "detail": "ok"})
    monkeypatch.setattr(sitecheck, "_http", lambda h: {"ok": True, "status": 401, "detail": "HTTP 401"})
    assert sitecheck.check(["a.example.org"])[0]["ok"] is True
    assert len(sitecheck.check([f"h{i}.example.org" for i in range(20)])) == sitecheck.MAX_HOSTS
