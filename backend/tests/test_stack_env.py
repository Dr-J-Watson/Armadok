"""Variables de la pile Portainer : hachage en base64 et groupes conservés par le lanceur PUID/PGID."""
import base64

import pytest

from app import config


def _settings(monkeypatch, **env):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_PASSWORD_HASH_B64", "ADMIN_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return config.Settings()


def test_hash_can_be_given_in_base64(monkeypatch):
    h = "$2b$12$abcdefghijklmnopqrstuuABCDEFGHIJKLMNOPQRSTUVWXYZ0123"
    s = _settings(monkeypatch, ADMIN_PASSWORD_HASH_B64=base64.b64encode(h.encode()).decode())
    assert s.admin_password_hash == h
    # la valeur « normale » garde la priorité
    s = _settings(monkeypatch, ADMIN_PASSWORD_HASH="$2b$12$direct", ADMIN_PASSWORD_HASH_B64=base64.b64encode(h.encode()).decode())
    assert s.admin_password_hash == "$2b$12$direct"


def test_invalid_base64_is_reported_at_validation(monkeypatch):
    s = _settings(monkeypatch, ADMIN_PASSWORD_HASH_B64="pas du base64 !!")
    s.jwt_secret = "j" * 40
    with pytest.raises(RuntimeError, match="base64"):
        s.validate()


def test_drop_keeps_requested_groups(monkeypatch):
    from app import drop
    calls = {}
    monkeypatch.setattr(drop.os, "getuid", lambda: 0)
    monkeypatch.setattr(drop, "own", lambda *a: None)
    monkeypatch.setattr(drop.os, "makedirs", lambda *a, **k: None)
    monkeypatch.setattr(drop.os, "setgroups", lambda g: calls.setdefault("groups", g))
    monkeypatch.setattr(drop.os, "setgid", lambda g: calls.setdefault("gid", g))
    monkeypatch.setattr(drop.os, "setuid", lambda u: calls.setdefault("uid", u))
    monkeypatch.setattr(drop.os, "execvp", lambda *a: calls.setdefault("exec", a))
    monkeypatch.setenv("PUID", "1001"); monkeypatch.setenv("PGID", "1002"); monkeypatch.setenv("PGROUPS", "984, 999")
    drop.main(["uvicorn", "app.main:app"])
    assert calls["groups"] == [984, 999] and calls["uid"] == 1001 and calls["gid"] == 1002 and calls["exec"][0] == "uvicorn"
    calls.clear(); monkeypatch.delenv("PGROUPS")
    drop.main(["x"])
    assert calls["groups"] == []                                                   # comme avant : aucun groupe en plus
