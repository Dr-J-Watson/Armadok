"""Durcissement de l'authentification : verrou par compte, mémoire bornée, révocation durable, sessions invalidées, mots de passe."""
import time

import pytest
from fastapi.testclient import TestClient

from app import auth, db
from app.main import app

client = TestClient(app)
PW = "Un-Mot-De-Passe-Solide-9"


def login(u, p, ip="203.0.113.5"):
    return client.post("/api/auth/login", json={"username": u, "password": p}, headers={"X-Forwarded-For": ip})


@pytest.fixture()
def user():
    root = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
    h = {"Authorization": "Bearer " + root}
    name = f"ah-{int(time.time() * 1000) % 10**8}"
    assert client.post("/api/users", headers=h, json={"username": name, "password": PW}).status_code == 200
    auth._attempts.clear()
    auth._user_attempts.clear()
    yield name, h
    uid = db.one("SELECT id FROM users WHERE username=?", (name,))["id"]
    client.delete(f"/api/users/{uid}", headers=h)
    auth._attempts.clear()
    auth._user_attempts.clear()


def test_a_distributed_attack_on_one_account_is_slowed_down(user, monkeypatch):
    name, _ = user
    monkeypatch.setattr(auth, "_client_ip", lambda request: request.headers.get("x-real-ip", "?"))   # chaque essai vient d'une adresse différente
    codes = [client.post("/api/auth/login", json={"username": name, "password": f"mauvais-{i}"}, headers={"X-Real-IP": str(i)}).status_code for i in range(14)]
    assert codes[:auth.USER_FREE_TRIES] == [401] * auth.USER_FREE_TRIES and 429 in codes[auth.USER_FREE_TRIES:]      # verrou par compte, quelle que soit l'adresse
    assert client.post("/api/auth/login", json={"username": name, "password": PW}, headers={"X-Real-IP": "77"}).status_code == 429   # même le bon mot de passe attend


def test_tracking_tables_are_bounded(monkeypatch):
    monkeypatch.setattr(auth, "MAX_TRACKED", 50)
    auth._attempts.clear()
    for i in range(500):
        auth._fail(f"10.0.{i // 250}.{i % 250}", f"user{i}")
    assert len(auth._attempts) <= 100 and len(auth._user_attempts) <= 100
    auth._attempts.clear()
    auth._user_attempts.clear()


def test_password_change_signs_out_other_sessions_and_revocation_is_durable(user):
    name, h = user
    s1 = client.post("/api/auth/login", json={"username": name, "password": PW}).json()
    s2 = client.post("/api/auth/login", json={"username": name, "password": PW}).json()
    me1 = {"Authorization": "Bearer " + s1["access_token"]}
    assert client.get("/api/auth/me", headers=me1).status_code == 200
    new = "Autre-Mot-De-Passe-Solide-7"
    r = client.post("/api/auth/password", headers={"Authorization": "Bearer " + s2["access_token"]}, json={"current": PW, "new": new})
    assert r.status_code == 200 and "access_token" in r.json()                                                       # la session qui change reçoit un jeton neuf
    assert client.get("/api/auth/me", headers=me1).status_code == 401                                                # l'autre appareil est déconnecté
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + r.json()["access_token"]}).status_code == 200
    # révocation écrite en base : un jeton de rafraîchissement utilisé ne revient pas à la vie
    cookie = client.post("/api/auth/login", json={"username": name, "password": new}).cookies.get("refresh_token")
    client.cookies.set("refresh_token", cookie, path="/api/auth")
    assert client.post("/api/auth/refresh").status_code == 200
    client.cookies.set("refresh_token", cookie, path="/api/auth")
    assert client.post("/api/auth/refresh").status_code == 401
    assert db.one("SELECT COUNT(*) n FROM revoked_tokens")["n"] >= 1
    client.cookies.clear()


def test_logout_all_and_admin_reset_cut_sessions(user):
    name, h = user
    s = client.post("/api/auth/login", json={"username": name, "password": PW}).json()
    me = {"Authorization": "Bearer " + s["access_token"]}
    uid = db.one("SELECT id FROM users WHERE username=?", (name,))["id"]
    assert client.post(f"/api/users/{uid}/password", headers=h, json={"password": "Mot-De-Passe-Reinitialise-1"}).status_code == 200
    assert client.get("/api/auth/me", headers=me).status_code == 401


@pytest.mark.parametrize("pw,user_,ok", [("trop-court", "bob", False), ("a" * 73, "bob", False), ("aaaaaaaaaaaaaaaa", "bob", False), ("mon-nom-dutilisateur", "mon-nom-dutilisateur", False), ("Mot-De-Passe-Solide-3", "bob", True)])
def test_password_rules(pw, user_, ok):
    try:
        auth.check_new_password(pw, user_)
        got = True
    except Exception:
        got = False
    assert got is ok


def test_failed_login_audit_is_rate_limited(user):
    name, _ = user
    auth._LOGIN_AUDIT.clear()
    from app import audit
    before = len(audit.tail(1000))
    for i in range(6):
        client.post("/api/auth/login", json={"username": "inconnu", "password": "x"}, headers={"X-Real-IP": "1.2.3.4"})
    after = len(audit.tail(1000))
    assert after - before <= 1                                                                                       # une ligne par adresse et par minute, pas une par essai
