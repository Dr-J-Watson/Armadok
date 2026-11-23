"""Explorateur de fichiers d'un projet : importer des fichiers du poste (par morceaux) et télécharger une sélection (fichier seul ou archive zip)."""
import base64
import io
import zipfile

import pytest

from app import db, project_ops
from test_projects import client, pid_of, pj  # noqa: F401  (fixture)


@pytest.fixture(scope="module", autouse=True)
def _leave_no_project_behind(pj):
    yield
    db.run("DELETE FROM projects")


def url(pid, what):
    return f"/api/projects/{pid}/{what}"


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def up(pj, pid, rel, data: bytes, chunk=None, overwrite=False):
    """Envoie `data` comme le fait l'interface : morceau par morceau, le dernier marqué « last »."""
    step = chunk or project_ops.CHUNK
    pos, r = 0, None
    while True:
        part = data[pos:pos + step]
        last = pos + len(part) >= len(data)
        r = client.post(url(pid, "upload"), headers=pj["h"], json={"rel": rel, "b64": b64(part), "offset": pos, "last": last, "overwrite": overwrite})
        if r.status_code != 200 or last:
            return r
        pos += len(part)


def test_upload_in_several_chunks_rebuilds_the_exact_file(pj, monkeypatch):
    pid, root = pid_of(pj, "site"), pj["base"] / "site"
    monkeypatch.setattr(project_ops, "CHUNK", 1000)
    data = bytes(range(256)) * 12 + b"\x00fin"   # binaire, plus de 3 morceaux
    assert up(pj, pid, "img/logo.bin", data).status_code == 200
    assert (root / "img" / "logo.bin").read_bytes() == data       # dossier créé au besoin
    assert not list(root.rglob("*.armadok-part"))                  # aucun résidu


def test_upload_empty_file_and_existing_file(pj):
    pid, root = pid_of(pj, "site"), pj["base"] / "site"
    assert up(pj, pid, "vide.txt", b"").status_code == 200 and (root / "vide.txt").read_bytes() == b""
    assert up(pj, pid, "vide.txt", b"x").status_code == 409                       # existe déjà, pas d'écrasement par surprise
    assert up(pj, pid, "vide.txt", b"nouveau", overwrite=True).status_code == 200
    assert (root / "vide.txt").read_text() == "nouveau"


def test_upload_refuses_paths_that_escape_and_dotenv_without_right(pj):
    pid = pid_of(pj, "site")
    for bad in ("../dehors.txt", "/etc/x", ".git/config", "a/../../b", "a\\b"):
        assert up(pj, pid, bad, b"x").status_code == 422, bad
    assert up(pj, pid, "conf.txt/../../x", b"x").status_code == 422
    assert not (pj["base"] / "dehors.txt").exists()


def test_interrupted_upload_is_detected_and_can_be_aborted(pj):
    pid, root = pid_of(pj, "site"), pj["base"] / "site"
    h = pj["h"]
    assert client.post(url(pid, "upload"), headers=h, json={"rel": "gros.bin", "b64": b64(b"abc"), "offset": 0, "last": False}).status_code == 200
    assert (root / "gros.bin.armadok-part").exists() and not (root / "gros.bin").exists()   # jamais à moitié écrit sous son vrai nom
    assert client.post(url(pid, "upload"), headers=h, json={"rel": "gros.bin", "b64": b64(b"def"), "offset": 99, "last": True}).status_code == 409   # morceau manquant
    assert client.post(url(pid, "upload"), headers=h, json={"rel": "gros.bin", "abort": True}).status_code == 200
    assert not (root / "gros.bin.armadok-part").exists()


def test_download_one_file_as_is_and_a_selection_as_zip(pj):
    pid, root = pid_of(pj, "site"), pj["base"] / "site"
    h = pj["h"]
    client.put(url(pid, "file"), headers=h, json={"rel": "doc/a.txt", "content": "AAA", "create": True})
    client.put(url(pid, "file"), headers=h, json={"rel": "doc/sub/b.txt", "content": "BBB", "create": True})
    client.put(url(pid, "file"), headers=h, json={"rel": "c.txt", "content": "CCC", "create": True})

    def fetch(rels):
        m = client.post(url(pid, "pack"), headers=h, json={"rels": rels})
        assert m.status_code == 200, m.text
        m = m.json()
        out, off = b"", 0
        while True:
            r = client.post(url(pid, "pack/read"), headers=h, json={"token": m["token"], "offset": off}).json()
            part = base64.b64decode(r["b64"])
            out += part
            off += len(part)
            if r["done"]:
                return m, out

    m, raw = fetch(["c.txt"])
    assert (m["name"], raw) == ("c.txt", b"CCC")                       # un seul fichier : tel quel, pas de zip
    m, raw = fetch(["doc", "c.txt"])
    assert m["name"].endswith(".zip")
    z = zipfile.ZipFile(io.BytesIO(raw))
    assert sorted(z.namelist()) == ["c.txt", "doc/a.txt", "doc/sub/b.txt"]   # arborescence conservée
    assert z.read("doc/sub/b.txt") == b"BBB"
    m, raw = fetch(["doc"])
    assert m["name"] == "doc.zip"
    assert client.post(url(pid, "pack/read"), headers=h, json={"token": m["token"], "offset": 0}).status_code == 410   # jeton à usage unique, aucune archive laissée


def test_download_refuses_what_is_outside_the_project_or_secret(pj, tmp_path):
    pid, root = pid_of(pj, "site"), pj["base"] / "site"
    h = pj["h"]
    for bad in ("../x", "/etc/passwd", ".git", "a/../../b"):
        assert client.post(url(pid, "pack"), headers=h, json={"rels": [bad]}).status_code in (403, 404, 422), bad
    assert client.post(url(pid, "pack"), headers=h, json={"rels": ["absent.txt"]}).status_code == 404
    assert client.post(url(pid, "pack"), headers=h, json={"rels": []}).status_code == 422


def test_dotenv_is_left_out_of_archives_without_the_secrets_right(pj):
    import base64 as b
    import io
    import zipfile
    from app.traefik_store import StoreError
    root = pj["base"] / "site"
    (root / "sec").mkdir(exist_ok=True)
    (root / "sec" / "ok.txt").write_text("ok")
    (root / "sec" / ".env").write_text("SECRET=1")
    with pytest.raises(StoreError) as e:
        project_ops.p_pack({"dir": str(root), "rels": ["sec/.env"], "canEnv": False})     # le .env seul : refusé
    assert e.value.status == 403
    m = project_ops.p_pack({"dir": str(root), "rels": ["sec"], "canEnv": False})          # dans un dossier : simplement omis
    z = zipfile.ZipFile(io.BytesIO(b.b64decode(project_ops.p_pack_read({"token": m["token"]})["b64"])))
    assert z.namelist() == ["sec/ok.txt"]
    m = project_ops.p_pack({"dir": str(root), "rels": ["sec"], "canEnv": True})
    z = zipfile.ZipFile(io.BytesIO(b.b64decode(project_ops.p_pack_read({"token": m["token"]})["b64"])))
    assert sorted(z.namelist()) == ["sec/.env", "sec/ok.txt"]


def test_the_new_operations_are_exposed_to_workers():
    from app import worker_proto as proto
    for op in ("p_pack", "p_pack_read", "p_upload"):
        assert op in proto.OPS_BY_MODULE["docker"]


def test_big_files_open_only_on_demand_and_in_read_only(pj):
    pid, root = pid_of(pj, "site"), pj["base"] / "site"
    h = pj["h"]
    (root / "gros.log").write_text("x" * (project_ops.MAX_FILE + 10))
    r = client.get(url(pid, "file") + "?rel=gros.log", headers=h)
    assert r.status_code == 413                                                   # pas sans le demander
    r = client.get(url(pid, "file") + "?rel=gros.log&force=true", headers=h)
    assert r.status_code == 200 and r.json()["large"] is True
    (root / "enorme.log").write_text("x" * (project_ops.MAX_FORCE + 10))
    assert client.get(url(pid, "file") + "?rel=enorme.log&force=true", headers=h).status_code == 413   # même sur demande, il y a une limite
    (root / "petit.txt").write_text("ok")
    assert client.get(url(pid, "file") + "?rel=petit.txt", headers=h).json()["large"] is False


# ------------------------------------------------------------------ cible make interactive, dans un terminal
def _drive(sid, send=None, rounds=40):
    """Lit la sortie d'un terminal jusqu'à sa fin (en répondant `send` au premier signe de vie) : (texte, code de sortie)."""
    import base64 as b
    out, code, sent = b"", None, send is None
    for _ in range(rounds):
        r = project_ops.p_exec_term_read({"sid": sid, "wait": 0.5})
        out += b.b64decode(r["data"])
        if not sent and out:
            project_ops.p_exec_term_write({"sid": sid, "data": b.b64encode(send.encode()).decode()})
            sent = True
        if r["eof"]:
            return out.decode(errors="replace"), r.get("code")
    raise AssertionError("la commande ne s'est pas terminée : " + out.decode(errors="replace"))


def _fake_make(monkeypatch, script):
    """L'image de test n'a pas « make » : on remplace la commande par un petit script (la validation de la cible est testée à part, dans test_projects)."""
    monkeypatch.setattr(project_ops, "_make_cmd", lambda a: (["sh", "-c", script], "make test"))


def test_interactive_make_target_gets_the_typed_answer(pj, monkeypatch):
    root = pj["base"] / "site"
    _fake_make(monkeypatch, 'printf "Votre nom ? "; read n; echo "salut $n"')
    sid = project_ops.p_exec_term_open({"dir": str(root), "target": "ask", "vars": {}, "cols": 80, "rows": 24})["sid"]
    out, code = _drive(sid, send="Ada\n")
    assert "Votre nom ?" in out and "salut Ada" in out and code == 0
    _fake_make(monkeypatch, "exit 3")
    sid = project_ops.p_exec_term_open({"dir": str(root), "target": "fail", "vars": {}})["sid"]
    assert _drive(sid)[1] == 3                                                   # le code de sortie remonte
    assert not project_ops._terms                                                # session libérée


def test_make_terminal_one_at_a_time_and_closing_stops_the_command(pj, monkeypatch):
    from app.traefik_store import StoreError
    root = pj["base"] / "site"
    _fake_make(monkeypatch, "sleep 30")
    sid = project_ops.p_exec_term_open({"dir": str(root), "target": "wait", "vars": {}})["sid"]
    with pytest.raises(StoreError) as e:
        project_ops.p_exec_term_open({"dir": str(root), "target": "wait", "vars": {}})
    assert e.value.status == 409                                                  # une seule commande à la fois par projet
    proc = project_ops._terms[sid]["proc"]
    project_ops.p_exec_term_close({"sid": sid})
    proc.wait(timeout=10)                                                         # fermer la fenêtre arrête la commande
    assert proc.returncode is not None and sid not in project_ops._terms


def test_ctrl_c_reaches_the_command(pj, monkeypatch):
    import base64 as b
    root = pj["base"] / "site"
    _fake_make(monkeypatch, "trap 'echo interrompu; exit 7' INT; echo pret; while true; do sleep 0.1; done")
    sid = project_ops.p_exec_term_open({"dir": str(root), "target": "x", "vars": {}})["sid"]
    out = b""
    while b"pret" not in out:
        out += b.b64decode(project_ops.p_exec_term_read({"sid": sid, "wait": 0.5})["data"])
    project_ops.p_exec_term_write({"sid": sid, "data": b.b64encode(b"\x03").decode()})   # Ctrl-C tapé dans le terminal
    text, code = _drive(sid)
    assert "interrompu" in text and code == 7


def test_terminal_operations_are_worker_gated():
    from app import worker_proto as proto
    for op in ("p_exec_term_open", "p_exec_term_read", "p_exec_term_write", "p_exec_term_resize", "p_exec_term_close"):
        assert op in proto.OPS_BY_MODULE["docker"] and op.startswith("p_exec_")    # refusées par un worker sans WORKER_ALLOW_EXEC


def test_make_terminal_over_websocket_end_to_end(pj, monkeypatch):
    """Le navigateur : ouvre la connexion, tape sa réponse, lit la sortie puis le code de fin."""
    import json
    root, pid = pj["base"] / "site", pid_of(pj, "site")
    _fake_make(monkeypatch, 'printf "mot de passe ? "; read p; echo "recu:$p"; exit 5')
    token = pj["h"]["Authorization"].split()[1]
    with client.websocket_connect("/api/projects/make/term") as ws:
        ws.send_text(json.dumps({"token": token, "node": pj["h"]["X-Node"], "project": pid, "target": "ask", "vars": {}, "cols": 80, "rows": 24}))
        assert json.loads(ws.receive_text()) == {"t": "ready"}
        ws.send_text(json.dumps({"i": "secret\n"}))
        out, code = b"", None
        for _ in range(50):
            m = ws.receive()
            if m.get("bytes"):
                out += m["bytes"]
            elif m.get("text") and json.loads(m["text"]).get("t") == "exit":
                code = json.loads(m["text"])["code"]
                break
    assert b"recu:secret" in out and code == 5


def test_make_terminal_needs_the_exec_right(pj):
    import json
    from starlette.websockets import WebSocketDisconnect
    with client.websocket_connect("/api/projects/make/term") as ws:
        ws.send_text(json.dumps({"token": "pas-un-jeton", "node": pj["h"]["X-Node"], "project": 1, "target": "x"}))
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4401


def test_limits_apply_without_the_nolimit_right_and_lift_with_it(pj, monkeypatch):
    from app.traefik_store import StoreError
    from app import caps
    root = pj["base"] / "site"
    monkeypatch.setattr(project_ops, "MAX_PACK", 10)
    (root / "gros.dat").write_bytes(b"x" * 50)
    with pytest.raises(StoreError) as e:                                                   # téléchargement trop gros sans le droit
        project_ops.p_pack({"dir": str(root), "rels": ["gros.dat"], "canEnv": True})
    assert e.value.status == 413 and "sans limite" in str(e.value)
    assert project_ops.p_pack({"dir": str(root), "rels": ["gros.dat"], "canEnv": True, "unlimited": True})["size"] == 50
    monkeypatch.setattr(project_ops, "MAX_UPLOAD", 5)                                      # import trop gros sans le droit
    with pytest.raises(StoreError):
        project_ops.p_upload({"dir": str(root), "rel": "i.bin", "b64": b64(b"abcdef"), "offset": 0, "last": False})
        project_ops.p_upload({"dir": str(root), "rel": "i.bin", "b64": b64(b"abcdef"), "offset": 6, "last": True})
    project_ops.p_upload({"dir": str(root), "rel": "j.bin", "b64": b64(b"abcdef"), "offset": 0, "last": False, "unlimited": True})
    project_ops.p_upload({"dir": str(root), "rel": "j.bin", "b64": b64(b"abcdef"), "offset": 6, "last": True, "unlimited": True})
    assert (root / "j.bin").read_bytes() == b"abcdefabcdef"
    for f in ("gros.dat", "i.bin", "j.bin", "i.bin.armadok-part", "j.bin.armadok-part"):
        (root / f).unlink(missing_ok=True)
    # le droit existe : les administrateurs l'ont d'office, les autres rôles non
    assert "projects.nolimit" in caps.PRESETS["admin"] and "projects.nolimit" not in caps.PRESETS["editor"] and "projects.nolimit" not in caps.PRESETS["viewer"]
