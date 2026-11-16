"""Limites : taille des requêtes, en-têtes de sécurité, cache des requêtes coûteuses, base d'adresses bornée."""
import time

from fastapi.testclient import TestClient

from app import accesslog, db
from app.main import app
from app.traefik_store import StoreError

client = TestClient(app)


def test_huge_body_is_refused():
    r = client.post("/api/auth/login", content=b"x" * (5 * 1024 * 1024), headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert client.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code == 401      # une requête normale passe


def test_security_headers(monkeypatch):
    from app.config import settings
    r = client.get("/api/health")
    assert "camera=()" in r.headers["permissions-policy"] and r.headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" not in r.headers                                                     # les tests tournent en HTTP simple (COOKIE_SECURE=0)
    monkeypatch.setattr(settings, "cookie_secure", True)
    assert "max-age" in client.get("/api/health").headers["strict-transport-security"]                     # en production (HTTPS) : HSTS


def test_oversized_yaml_file_is_refused(tmp_path):
    from app.traefik_store import Store
    s = Store(tmp_path / "d", tmp_path / "b")
    s.root.mkdir()
    s.write("ok.yml", "http: {}\n", create=True)
    try:
        s.write("gros.yml", "# " + "x" * 1_100_000 + "\nhttp: {}\n", create=True)
        assert False, "aurait dû être refusé"
    except StoreError as e:
        assert e.status == 413


def test_response_cache_runs_the_work_once():
    from app import routes_obs
    calls = []
    f = lambda: calls.append(1) or {"ok": True}
    a = routes_obs._cached(("t", 1), 5, f)
    b = routes_obs._cached(("t", 1), 5, f)
    assert a == b and len(calls) == 1
    routes_obs._cached(("t", 2), 5, f)
    assert len(calls) == 2                                                                                   # une autre clé refait le travail
    try:
        routes_obs._cached(("err", 1), 5, lambda: 1 / 0)
    except ZeroDivisionError:
        pass
    assert ("err", 1) not in routes_obs._cache                                                               # les erreurs ne sont pas mémorisées


def test_tracked_addresses_are_capped(monkeypatch):
    nid = "node-cap-test"
    monkeypatch.setattr(accesslog, "MAX_IPS", 20)
    now = time.time()
    for i in range(60):
        db.run("INSERT INTO ip_seen(node_id, ip, first_seen, last_seen, last_host, last_path, last_status, last_ua) VALUES(?,?,?,?,?,?,?,?)",
               (nid, f"45.0.{i // 250}.{i % 250}", now - 100, now - 60 + i, "h", "/", 200, ""))
    accesslog.purge(now)
    left = [r["ip"] for r in db.rows("SELECT ip FROM ip_seen WHERE node_id=? ORDER BY last_seen DESC", (nid,))]
    assert len(left) == 20 and left[0] == "45.0.0.59"                                                          # les plus récentes restent
    db.run("DELETE FROM ip_seen WHERE node_id=?", (nid,))
