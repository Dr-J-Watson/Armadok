"""Sauvegarde complète du hub : créer, chiffrer, télécharger, téléverser, restaurer."""
import base64
import json
import sqlite3
import time
import zipfile

import pytest

from app import backup, routes_backup
from app.traefik_store import StoreError
from test_projects import client, pj  # noqa: F401  (fixture)

BASE = "/api/admin/backups"


@pytest.fixture(autouse=True)
def _no_2fa_and_clean(monkeypatch, pj):
    def fake(p, code):   # le vrai contrôle (TOTP) est testé ailleurs : ici « 000000 » passe, tout le reste est refusé
        if code != "000000":
            raise StoreError("Code de double authentification invalide", 403)
    monkeypatch.setattr(routes_backup, "need_second_factor", fake)
    yield
    for f in backup.store_dir().iterdir():
        f.unlink()


def wait_job(pj, jid, tries=120):
    for _ in range(tries):
        r = client.get(f"{BASE}/jobs/{jid}", headers=pj["h"]).json()
        if r["done"]:
            return r
        time.sleep(0.25)
    raise AssertionError("la tâche ne se termine pas")


def make(pj, **body):
    r = client.post(BASE, headers=pj["h"], json={"components": ["database", "history"], **body})
    assert r.status_code == 200, r.text
    done = wait_job(pj, r.json()["job"])
    assert not done["error"], done
    return done["result"]


def test_create_list_and_inspect(pj):
    meta = make(pj, label="avant test")
    assert meta["name"].endswith("-avant-test.zip") and not meta["encrypted"]
    lst = client.get(BASE, headers=pj["h"]).json()
    assert [b["name"] for b in lst["backups"]] == [meta["name"]] and lst["backups"][0]["components"]["database"]["files"] >= 1
    assert lst["free"] > 0 and {c["id"] for c in lst["components"]} == set(backup.COMPONENTS)
    z = zipfile.ZipFile(backup.store_dir() / meta["name"])
    assert "manifest.json" in z.namelist() and "db/app.db" in z.namelist() and "secrets/encryption_key" not in z.namelist()   # la clé n'y est jamais sans demande
    snap = backup.tmp_dir() / "check.db"
    snap.write_bytes(z.read("db/app.db"))
    assert sqlite3.connect(snap).execute("PRAGMA integrity_check").fetchone()[0] == "ok"                       # copie cohérente et utilisable
    snap.unlink()
    info = client.post(f"{BASE}/{meta['name']}/inspect", headers=pj["h"], json={}).json()
    assert info["keyStatus"] == "same" and set(info["manifest"]["components"]) == {"database", "history"}


def test_password_protected_backup(pj):
    meta = make(pj, passphrase="un-bon-mot-de-passe")
    assert meta["encrypted"] and meta["name"].endswith(".armbak")
    raw = (backup.store_dir() / meta["name"]).read_bytes()
    assert raw.startswith(backup.MAGIC) and b"SQLite format" not in raw and b"manifest" not in raw                 # rien de lisible dans le fichier
    assert client.post(f"{BASE}/{meta['name']}/inspect", headers=pj["h"], json={}).status_code == 422                # mot de passe exigé
    assert client.post(f"{BASE}/{meta['name']}/inspect", headers=pj["h"], json={"passphrase": "mauvais-mot-de-passe"}).status_code == 422
    assert client.post(f"{BASE}/{meta['name']}/inspect", headers=pj["h"], json={"passphrase": "un-bon-mot-de-passe"}).status_code == 200
    # une archive tronquée est refusée
    (backup.store_dir() / "tronquee.armbak").write_bytes(raw[:len(raw) // 2])
    with pytest.raises(StoreError):
        backup.Opened("tronquee.armbak", "un-bon-mot-de-passe")


def test_encryption_key_only_with_password_and_second_factor(pj):
    r = client.post(BASE, headers=pj["h"], json={"components": ["database"], "includeKey": True, "code": "000000"})
    assert r.status_code == 422 and "mot de passe" in r.json()["detail"]                                            # la clé exige un mot de passe
    r = client.post(BASE, headers=pj["h"], json={"components": ["database"], "includeKey": True, "passphrase": "un-bon-mot-de-passe", "code": "111111"})
    assert r.status_code == 403                                                                                      # et un code 2FA
    meta = make(pj, passphrase="un-bon-mot-de-passe", includeKey=True, code="000000")
    assert meta["includesKey"] is True


def test_short_password_and_empty_selection_refused(pj):
    assert client.post(BASE, headers=pj["h"], json={"components": ["database"], "passphrase": "court"}).status_code == 422
    assert client.post(BASE, headers=pj["h"], json={"components": ["nimporte"]}).status_code == 422
    with pytest.raises(StoreError):
        backup.create(["database"], "x", passphrase="court")
    with pytest.raises(StoreError):
        backup.create(["nimporte"], "x")


def test_download_needs_a_code_and_a_fresh_single_use_ticket(pj):
    meta = make(pj)
    url = f"{BASE}/{meta['name']}/ticket"
    assert client.post(url, headers=pj["h"], json={"code": "123456"}).status_code == 403
    t = client.post(url, headers=pj["h"], json={"code": "000000"}).json()["ticket"]
    got = client.get(f"{BASE}/download", params={"ticket": t})                                                      # sans en-tête d'authentification : le ticket suffit
    assert got.status_code == 200 and got.content == (backup.store_dir() / meta["name"]).read_bytes()
    assert client.get(f"{BASE}/download", params={"ticket": t}).status_code == 403                                  # à usage unique
    assert client.get(f"{BASE}/download", params={"ticket": "nimporte"}).status_code == 403


def test_upload_in_chunks_registers_the_backup(pj, monkeypatch):
    meta = make(pj)
    data = (backup.store_dir() / meta["name"]).read_bytes()
    backup.delete(meta["name"])
    monkeypatch.setattr(backup, "UPLOAD_CHUNK", 1000)
    pos, last = 0, False
    while not last:
        part = data[pos:pos + 1000]
        last = pos + len(part) >= len(data)
        r = client.post(f"{BASE}/upload", headers=pj["h"], json={"filename": meta["name"], "b64": base64.b64encode(part).decode(), "offset": pos, "last": last})
        assert r.status_code == 200, r.text
        pos += len(part)
    assert r.json()["name"] == meta["name"]
    assert [b["name"] for b in client.get(BASE, headers=pj["h"]).json()["backups"]] == [meta["name"]]
    assert client.post(f"{BASE}/upload", headers=pj["h"], json={"filename": "../evil.zip", "b64": "", "offset": 0, "last": True}).status_code == 422
    bad = client.post(f"{BASE}/upload", headers=pj["h"], json={"filename": "pas-une-sauvegarde.zip", "b64": base64.b64encode(b"zzz").decode(), "offset": 0, "last": True})
    assert bad.status_code == 422                                                                                    # un zip sans manifeste est refusé
    assert not (backup.store_dir() / "pas-une-sauvegarde.zip").exists()


def test_names_cannot_escape_and_delete(pj):
    for bad in ("..%2F..%2Fapp.db", "x.txt", "../app.db"):
        assert client.delete(f"{BASE}/{bad}", headers=pj["h"]).status_code in (404, 422), bad
    meta = make(pj)
    assert client.delete(f"{BASE}/{meta['name']}", headers=pj["h"]).status_code == 200
    assert client.get(BASE, headers=pj["h"]).json()["backups"] == []


def test_restore_needs_code_and_retyped_name(pj):
    meta = make(pj)
    url = f"{BASE}/{meta['name']}/restore"
    body = {"code": "000000", "components": ["database"], "confirm": meta["name"]}
    assert client.post(url, headers=pj["h"], json={**body, "code": "111111"}).status_code == 403
    assert client.post(url, headers=pj["h"], json={**body, "confirm": "autre.zip"}).status_code == 422
    assert not backup.pending_dir().exists()                                                                         # rien n'a été préparé


def test_full_restore_cycle_in_a_scratch_data_dir(tmp_path, monkeypatch):
    """Créer, abîmer, préparer, appliquer : les fichiers reviennent, l'état d'avant est gardé, une archive piégée est refusée."""
    root = tmp_path / "data"
    (root / "project-history" / "h1").mkdir(parents=True)
    (root / "project-history" / "h1" / "1.txt").write_text("version A")
    (root / "home").mkdir()
    (root / "home" / "known_hosts").write_text("hote A")
    (root / "audit.log").write_text('{"a":1}\n')
    dyn = tmp_path / "dyn"
    dyn.mkdir()
    (dyn / "site.yml").write_text("a: 1\n")
    c = sqlite3.connect(root / "app.db")
    c.execute("CREATE TABLE users(id INTEGER, name TEXT)")
    c.execute("INSERT INTO users VALUES (1,'avant')")
    c.commit()
    c.close()
    monkeypatch.setattr(backup, "base", lambda: root)
    monkeypatch.setattr(backup.settings, "dynamic_dir", dyn)
    meta = backup.create(list(backup.COMPONENTS), "admin")
    # on « casse » tout après la sauvegarde
    c = sqlite3.connect(root / "app.db")
    c.execute("UPDATE users SET name='apres'")
    c.commit()
    c.close()
    (root / "project-history" / "h1" / "1.txt").write_text("version B")
    (root / "home" / "known_hosts").write_text("hote B")
    (root / "audit.log").write_text('{"a":2}\n')
    (dyn / "site.yml").write_text("a: 2\n")
    plan = backup.stage_restore(meta["name"], "", ["database", "history", "home", "traefik"], "admin")
    assert plan["files"] >= 5 and (root / "app.db").exists() and sqlite3.connect(root / "app.db").execute("SELECT name FROM users").fetchone()[0] == "apres"   # rien n'a encore bougé
    assert "restauration de" in backup.apply_pending()
    assert sqlite3.connect(root / "app.db").execute("SELECT name FROM users").fetchone()[0] == "avant"
    assert (root / "project-history" / "h1" / "1.txt").read_text() == "version A" and (root / "home" / "known_hosts").read_text() == "hote A"
    assert (root / "audit.log").read_text() == '{"a":1}\n' and (dyn / "site.yml").read_text() == "a: 1\n"
    assert sqlite3.connect(backup.before_dir() / "app.db").execute("SELECT name FROM users").fetchone()[0] == "apres"   # l'état d'avant est conservé
    assert (backup.before_dir() / "home" / "known_hosts").read_text() == "hote B" and not backup.pending_dir().exists()
    assert backup.apply_pending() == ""                                                                              # rien à refaire au démarrage suivant


def test_trapped_or_broken_archives_are_refused(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(backup, "base", lambda: root)
    store = backup.store_dir()
    manifest = json.dumps({"format": 1, "components": {"history": {"files": 1}, "database": {"files": 1}}, "keyFingerprint": backup.key_fingerprint()})
    with zipfile.ZipFile(store / "armadok-piege.zip", "w") as z:
        z.writestr("manifest.json", manifest)
        z.writestr("history/../../evil.txt", "x")
    with pytest.raises(StoreError, match="Chemin refusé"):
        backup.stage_restore("armadok-piege.zip", "", ["history"], "admin")
    assert not backup.pending_dir().exists() and not (tmp_path / "evil.txt").exists()
    with zipfile.ZipFile(store / "armadok-bdd.zip", "w") as z:
        z.writestr("manifest.json", manifest)
        z.writestr("db/app.db", b"ce n'est pas une base")
    with pytest.raises(StoreError, match="abîmée"):
        backup.stage_restore("armadok-bdd.zip", "", ["database"], "admin")
    assert not backup.pending_dir().exists()                                                                         # échec : rien n'est resté préparé
    with zipfile.ZipFile(store / "armadok-futur.zip", "w") as z:
        z.writestr("manifest.json", json.dumps({"format": 99, "components": {}}))
    with pytest.raises(StoreError, match="plus récente"):
        backup.inspect("armadok-futur.zip")
    with pytest.raises(StoreError, match="ne contient pas"):
        backup.stage_restore("armadok-piege.zip", "", ["traefik"], "admin")
