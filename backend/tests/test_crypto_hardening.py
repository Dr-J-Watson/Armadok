"""Chiffrement : adresse de webhook chiffrée en base, ancienne valeur en clair migrée, clé de chiffrement trop courte refusée."""
import pytest
from fastapi.testclient import TestClient

from app import crypto, db
from app.config import settings
from app.main import app

client = TestClient(app)


@pytest.fixture()
def root():
    tok = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
    nid = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    yield {"Authorization": "Bearer " + tok, "X-Node": nid}, nid
    db.run("UPDATE nodes SET webhook_url='' WHERE id=?", (nid,))


def test_webhook_url_is_encrypted_at_rest(root):
    h, nid = root
    url = "https://hooks.exemple.org/services/T000/B000/SECRETTOKEN"
    r = client.put(f"/api/nodes/{nid}", headers=h, json={"webhookUrl": url})
    assert r.status_code == 200 and r.json()["webhookSet"] is True
    raw = db.one("SELECT webhook_url FROM nodes WHERE id=?", (nid,))["webhook_url"]
    assert raw != url and "SECRETTOKEN" not in raw and crypto.is_encrypted(raw)                       # rien d'exploitable dans la base
    assert crypto.reveal(raw) == url                                                                  # mais le serveur sait le relire
    assert "SECRETTOKEN" not in str(client.get(f"/api/nodes/{nid}", headers=h).json())                 # et ne le renvoie jamais
    client.put(f"/api/nodes/{nid}", headers=h, json={"webhookUrl": ""})
    assert db.one("SELECT webhook_url FROM nodes WHERE id=?", (nid,))["webhook_url"] == ""


def test_plaintext_webhook_from_an_older_install_is_encrypted_at_startup(root):
    _, nid = root
    db.run("UPDATE nodes SET webhook_url=? WHERE id=?", ("https://hooks.exemple.org/ancien-jeton", nid))
    assert crypto.reveal("https://hooks.exemple.org/ancien-jeton") == "https://hooks.exemple.org/ancien-jeton"   # une ancienne base reste lisible
    assert crypto.encrypt_plaintext() >= 1
    raw = db.one("SELECT webhook_url FROM nodes WHERE id=?", (nid,))["webhook_url"]
    assert crypto.is_encrypted(raw) and crypto.reveal(raw) == "https://hooks.exemple.org/ancien-jeton"
    assert crypto.encrypt_plaintext() == 0                                                              # idempotent


def test_short_encryption_key_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("ENCRYPTION_KEY", "court")
    with pytest.raises(RuntimeError, match="trop courte"):
        settings.validate()
    monkeypatch.setenv("ENCRYPTION_KEY", "e" * 32)
    settings.validate()


def test_server_credentials_column_is_covered_by_the_key_migration():
    assert "secret" in dict(crypto._ENCRYPTED)["credentials"] and "webhook_url" in dict(crypto._ENCRYPTED)["nodes"]
