"""Journal d'audit chaîné, et annuaire d'utilisateurs qui ne permet pas d'énumérer les comptes."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import audit as a
from app import db
from app.config import settings
from app.main import app

PW = "un-mot-de-passe-solide"
client = TestClient(app)


@pytest.fixture()
def log(tmp_path, monkeypatch):
    """Un journal neuf, isolé de celui des autres tests."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    a.reset_cache()
    yield tmp_path / "audit.log"
    monkeypatch.undo()
    a.reset_cache()
    a._save_head(a._load_state())  # la tête enregistrée en base redevient celle du vrai journal


def lines(path):
    return path.read_text().splitlines()


def test_untouched_log_is_valid(log):
    for i in range(5):
        a.audit("alice", "traefik.file.save", f"f{i}.yml")
    v = a.verify()
    assert v["ok"] and v["chained"] == 5 and v["legacy"] == 0


def test_old_unsigned_lines_are_tolerated(log):
    log.write_text(json.dumps({"ts": "2026-10-01T10:00:00+0000", "user": "x", "action": "login", "detail": ""}) + "\n")
    a.audit("alice", "login", "1.2.3.4")
    v = a.verify()
    assert v["ok"] and v["legacy"] == 1 and v["chained"] == 1


def test_modified_line_is_detected(log):
    for i in range(4):
        a.audit("alice", "dns.create", f"x{i}")
    rows = lines(log)
    e = json.loads(rows[1])
    e["detail"] = "falsifié"
    rows[1] = json.dumps(e, ensure_ascii=False)
    log.write_text("\n".join(rows) + "\n")
    v = a.verify()
    assert not v["ok"] and v["problem"]["line"] == 2 and "modifié" in v["problem"]["reason"]


def test_deleted_middle_line_is_detected(log):
    for i in range(4):
        a.audit("alice", "dns.create", f"x{i}")
    rows = lines(log)
    del rows[1]
    log.write_text("\n".join(rows) + "\n")
    v = a.verify()
    assert not v["ok"] and "chaînage" in v["problem"]["reason"]


def test_truncated_tail_is_detected(log):
    for i in range(4):
        a.audit("alice", "dns.create", f"x{i}")
    log.write_text("\n".join(lines(log)[:-2]) + "\n")  # on retire les deux dernières lignes : la chaîne reste cohérente
    v = a.verify()
    assert not v["ok"] and "tronqué" in v["problem"]["reason"]


def test_inserted_unsigned_line_is_detected(log):
    a.audit("alice", "login", "")
    with open(log, "a") as f:
        f.write(json.dumps({"ts": "x", "user": "pirate", "action": "admin.node.delete", "detail": "ni vu ni connu"}) + "\n")
    v = a.verify()
    assert not v["ok"] and "sans signature" in v["problem"]["reason"]


def test_forged_chain_without_the_key_fails(log):
    a.audit("alice", "login", "")
    e = json.loads(lines(log)[0])
    e["user"] = "bob"
    e["h"] = "0" * 64  # quelqu'un qui n'a pas la clé ne peut pas recalculer la signature
    log.write_text(json.dumps(e) + "\n")
    assert not a.verify()["ok"]


def test_verify_endpoint_is_admin_only():
    root = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
    r = client.get("/api/admin/audit/verify", headers={"Authorization": "Bearer " + root})
    assert r.status_code == 200 and "ok" in r.json()
    client.post("/api/users", headers={"Authorization": "Bearer " + root}, json={"username": "kim", "password": PW})
    kim = client.post("/api/auth/login", json={"username": "kim", "password": PW}).json()["access_token"]
    assert client.get("/api/admin/audit/verify", headers={"Authorization": "Bearer " + kim}).status_code == 403


def test_directory_only_shows_people_you_already_work_with():
    root = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]}
    for n in ("hana", "igor", "jade"):
        client.post("/api/users", headers=root, json={"username": n, "password": PW})
    ids = {r["username"]: r["id"] for r in db.rows("SELECT id, username FROM users")}
    nid = str(uuid.uuid4())
    db.run("INSERT INTO nodes(id,name,kind,created_at) VALUES(?,?,?,?)", (nid, "Dir", "remote", db.now()))
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?, 'owner', ?)", (nid, ids["hana"], db.now()))
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?, 'viewer', ?)", (nid, ids["igor"], db.now()))
    tok = lambda u: {"Authorization": "Bearer " + client.post("/api/auth/login", json={"username": u, "password": PW}).json()["access_token"]}
    hana, jade = tok("hana"), tok("jade")
    assert [u["username"] for u in client.get("/api/users-directory", headers=hana).json()] == ["igor"]  # jade, inconnue, n'apparaît pas
    assert client.get("/api/users-directory", headers=jade).json() == []
    assert len(client.get("/api/users-directory", headers=root).json()) >= 4  # l'administrateur voit tout le monde
    # recherche par nom exact
    assert client.get("/api/users-lookup", params={"username": "jade"}, headers=hana).json()["username"] == "jade"
    assert client.get("/api/users-lookup", params={"username": "jad"}, headers=hana).status_code == 404
    assert client.get("/api/users-lookup", params={"username": "hana"}, headers=hana).status_code == 404  # soi-même : rien
    # limitée en débit
    codes = [client.get("/api/users-lookup", params={"username": f"x{i}"}, headers=hana).status_code for i in range(25)]
    assert 429 in codes
    db.run("DELETE FROM nodes WHERE id=?", (nid,))


def test_l_activite_d_un_noeud_ne_montre_que_ce_noeud(log):
    a.audit("frozox", "login", "1.2.3.4")                       # plateforme
    a.audit("frozox", "credential.add", "frozox.fr")            # plateforme
    a.audit("watson", "traefik.site.save", "vieux")             # d'avant les nœuds : nœud principal
    a.audit("watson", "traefik.site.save", "x", node="local")
    a.audit("toto", "traefik.site.save", "y", node="autre")
    local = [e["action"] + ":" + e["detail"] for e in a.tail(50, node="local", include_unscoped=True)]
    assert sorted(local) == ["traefik.site.save:vieux", "traefik.site.save:x"]
    assert [e["detail"] for e in a.tail(50, node="autre", include_unscoped=False)] == ["y"]
    assert sorted(e["action"] for e in a.tail(50, platform=True)) == ["credential.add", "login"]


# ---------- rotation, lecture bornée, erreurs visibles ----------
def test_log_rotates_and_the_chain_continues_across_files(log, monkeypatch):
    monkeypatch.setattr(a, "MAX_BYTES", 600)
    for i in range(12):
        a.audit("alice", "traefik.file.save", f"f{i}.yml")
    archives = sorted(log.parent.glob("audit-*.log"))
    assert archives and log.exists()                                                                   # le fichier a été archivé
    v = a.verify()
    assert v["ok"] and v["chained"] == 12 and v["files"] == len(archives) + 1                          # la chaîne traverse les fichiers
    assert [e["detail"] for e in a.tail(3)] == ["f11.yml", "f10.yml", "f9.yml"]                         # la consultation lit les archives si besoin
    assert len(a.tail(100)) == 12
    # on retire une archive : détecté
    archives[0].unlink()
    assert not a.verify()["ok"]


def test_tail_reads_from_the_end_and_is_bounded(log, monkeypatch):
    for i in range(300):
        a.audit("bob", "login", f"n{i}")
    got = a.tail(5)
    assert [e["detail"] for e in got] == ["n299", "n298", "n297", "n296", "n295"]
    monkeypatch.setattr(a, "MAX_SCAN", 50)
    assert len(a.tail(1000)) <= 50                                                                     # jamais plus de MAX_SCAN lignes relues
    assert a.tail(5, node="inconnu") == []


def test_write_failure_is_loud_not_silent(log, monkeypatch, capsys):
    before = a.failures
    monkeypatch.setattr(a, "_path", lambda: log.parent / "pas-de-dossier" / "audit.log")
    a.audit("carol", "login", "x")
    assert a.failures == before + 1 and "ERREUR AUDIT" in capsys.readouterr().err
    assert a.verify()["failures"] >= before + 1


def test_restart_does_not_rescan_a_consistent_log(log):
    for i in range(5):
        a.audit("dave", "login", str(i))
    a.reset_cache()
    st = a._load_state()
    assert st["n"] == 5
    a.audit("dave", "login", "6")
    assert a.verify()["ok"] and a.verify()["chained"] == 6
