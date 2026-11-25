"""Sauvegarde d'un nœud : les fichiers de sa machine, comparés puis restaurés fichier par fichier."""
import base64
import io
import json
import time
import zipfile

import pytest

from app import backup, caps, node_backup, node_backup_ops as ops
from app.config import settings
from app.traefik_store import StoreError
from test_projects import client, pj  # noqa: F401  (fixture)

BASE = "/api/node-backups"
Y = "http:\n  routers: {}\n"


@pytest.fixture(autouse=True)
def _scratch(pj):
    d = settings.dynamic_dir
    made = []

    def put(rel, text):
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        made.append(p)
    pj["put"] = put
    yield
    for p in made:
        p.unlink(missing_ok=True)
    import shutil
    shutil.rmtree(settings.data_dir / "sauvegardes-noeuds", ignore_errors=True)


def wait(pj, jid):
    for _ in range(120):
        r = client.get(f"{BASE}/jobs/{jid}", headers=pj["h"]).json()
        if r["done"]:
            return r
        time.sleep(0.25)
    raise AssertionError("tâche sans fin")


def make(pj, **body):
    r = client.post(BASE, headers=pj["h"], json={"components": ["traefik"], **body})
    assert r.status_code == 200, r.text
    done = wait(pj, r.json()["job"])
    assert not done["error"], done
    return done["result"]


def test_create_lists_and_contains_the_files_with_hashes(pj):
    pj["put"]("nb-a.yml", Y)
    pj["put"]("nb-dir/b.yml", Y + "# b\n")
    lst = client.get(BASE, headers=pj["h"]).json()
    assert lst["online"] is True and "traefik" in {c["id"] for c in lst["available"]} and lst["canRestore"] is True
    meta = make(pj, label="avant test")
    assert meta["name"].endswith("-avant-test.zip") and meta["scope"] == "node" and meta["node"]["id"] == lst["node"]["id"]
    z = zipfile.ZipFile(node_backup.store(lst["node"]["id"]) / meta["name"])
    man = json.loads(z.read("manifest.json"))
    assert "machine/traefik/nb-a.yml" in z.namelist() and man["scope"] == "node"
    assert man["files"]["machine/traefik/nb-dir/b.yml"] and len(man["files"]["machine/traefik/nb-a.yml"]) == 64     # empreinte de chaque fichier
    assert [b["name"] for b in client.get(BASE, headers=pj["h"]).json()["backups"]] == [meta["name"]]


def test_diff_then_selective_restore_with_a_safety_backup(pj):
    pj["put"]("nb-a.yml", Y)
    pj["put"]("nb-b.yml", Y)
    pj["put"]("nb-c.yml", Y)
    meta = make(pj)
    name = meta["name"]
    pj["put"]("nb-a.yml", Y + "# modifié\n")                              # modifié depuis
    (settings.dynamic_dir / "nb-b.yml").unlink()                          # disparu depuis
    pj["put"]("nb-new.yml", Y)                                            # apparu depuis
    d = client.post(f"{BASE}/{name}/diff", headers=pj["h"], json={}).json()
    st = {(e["rel"], e["status"]) for e in d["entries"] if e["component"] == "traefik" and e["rel"].startswith("nb-")}
    assert st == {("nb-a.yml", "modified"), ("nb-b.yml", "missing"), ("nb-new.yml", "extra")} and d["same"] >= 1
    fd = client.post(f"{BASE}/{name}/file-diff", headers=pj["h"], json={"component": "traefik", "rel": "nb-a.yml"}).json()
    assert fd["before"]["content"].endswith("# modifié\n") and fd["after"]["content"] == Y                          # l'avant et l'après, pour la comparaison
    fd = client.post(f"{BASE}/{name}/file-diff", headers=pj["h"], json={"component": "traefik", "rel": "nb-b.yml"}).json()
    assert fd["before"] is None and fd["after"]["content"] == Y
    # on ne restaure QUE ce qu'on coche : nb-a remis, nb-new retiré, nb-b laissé absent
    items = [{"component": "traefik", "rel": "nb-a.yml", "action": "restore"}, {"component": "traefik", "rel": "nb-new.yml", "action": "delete"}]
    assert client.post(f"{BASE}/{name}/restore", headers=pj["h"], json={"items": items, "confirm": "autre"}).status_code == 422   # nom retapé
    r = client.post(f"{BASE}/{name}/restore", headers=pj["h"], json={"items": items, "confirm": name})
    assert r.status_code == 200, r.text
    res = wait(pj, r.json()["job"])
    assert not res["error"] and res["result"]["restored"] == 1 and res["result"]["deleted"] == 1 and not res["result"]["errors"]
    assert (settings.dynamic_dir / "nb-a.yml").read_text() == Y
    assert not (settings.dynamic_dir / "nb-new.yml").exists() and not (settings.dynamic_dir / "nb-b.yml").exists()
    kinds = {b["name"]: b["kind"] for b in client.get(BASE, headers=pj["h"]).json()["backups"]}
    assert kinds[res["result"]["safety"]] == "avant restauration"                                                      # filet de sécurité créé avant de toucher à quoi que ce soit


def test_password_protected_node_backup(pj):
    pj["put"]("nb-p.yml", Y)
    meta = make(pj, passphrase="un-bon-mot-de-passe")
    assert meta["encrypted"] and meta["name"].endswith(".armbak")
    assert client.post(f"{BASE}/{meta['name']}/diff", headers=pj["h"], json={}).status_code == 422                     # mot de passe exigé
    assert client.post(f"{BASE}/{meta['name']}/diff", headers=pj["h"], json={"passphrase": "un-bon-mot-de-passe"}).status_code == 200
    assert client.post(BASE, headers=pj["h"], json={"components": ["traefik"], "passphrase": "court"}).status_code == 422


def test_download_ticket_upload_and_scope(pj, monkeypatch):
    pj["put"]("nb-d.yml", Y)
    meta = make(pj)
    nid = client.get(BASE, headers=pj["h"]).json()["node"]["id"]
    t = client.post(f"{BASE}/{meta['name']}/ticket", headers=pj["h"]).json()["ticket"]
    got = client.get(f"{BASE}/download", params={"ticket": t})
    assert got.status_code == 200 and got.content == (node_backup.store(nid) / meta["name"]).read_bytes()
    assert client.get(f"{BASE}/download", params={"ticket": t}).status_code == 403                                      # à usage unique
    data = got.content
    backup.delete(meta["name"], node_backup.store(nid))
    monkeypatch.setattr(backup, "UPLOAD_CHUNK", 1000)
    pos, last = 0, False
    while not last:
        part = data[pos:pos + 1000]
        last = pos + len(part) >= len(data)
        r = client.post(f"{BASE}/upload", headers=pj["h"], json={"filename": meta["name"], "b64": base64.b64encode(part).decode(), "offset": pos, "last": last})
        assert r.status_code == 200, r.text
        pos += len(part)
    assert [b["name"] for b in client.get(BASE, headers=pj["h"]).json()["backups"]] == [meta["name"]]
    # une sauvegarde COMPLÈTE du hub n'entre pas dans un nœud (et inversement) : on ne mélange pas
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": 1, "components": {}}))
    r = client.post(f"{BASE}/upload", headers=pj["h"], json={"filename": "armadok-hub.zip", "b64": base64.b64encode(out.getvalue()).decode(), "offset": 0, "last": True})
    assert r.status_code == 422 and "hub" in r.json()["detail"]


def test_the_limit_per_node_and_deleting(pj, monkeypatch):
    pj["put"]("nb-l.yml", Y)
    monkeypatch.setattr(node_backup, "MAX_PER_NODE", 1)
    meta = make(pj)
    r = client.post(BASE, headers=pj["h"], json={"components": ["traefik"]})
    assert r.status_code == 200 and wait(pj, r.json()["job"])["error"].startswith("Ce nœud a déjà 1 sauvegardes")        # au-delà : on dit lesquelles supprimer
    assert client.delete(f"{BASE}/{meta['name']}", headers=pj["h"]).status_code == 200
    assert client.delete(f"{BASE}/..%2Fx.zip", headers=pj["h"]).status_code in (404, 422)


def test_machine_operations_stay_inside_their_folders(pj, tmp_path):
    mods = ("traefik",)
    for bad in ("../evil.yml", "/etc/x.yml", "a/../../b.yml", "a\\b.yml", ""):
        with pytest.raises(StoreError):
            ops.nb_put({"component": "traefik", "rel": bad, "b64": base64.b64encode(Y.encode()).decode(), "offset": 0, "last": True}, mods)
    with pytest.raises(StoreError, match="YAML"):                                                                       # Traefik ne pourrait pas lire ce fichier
        ops.nb_put({"component": "traefik", "rel": "nb-bad.yml", "b64": base64.b64encode(b"a: [unclosed").decode(), "offset": 0, "last": True}, mods)
    assert not (settings.dynamic_dir / "nb-bad.yml").exists() and not (settings.dynamic_dir / "nb-bad.yml.armadok-part").exists()
    outside = tmp_path / "dehors.yml"
    outside.write_text("x: 1\n")
    link = settings.dynamic_dir / "nb-lien.yml"
    link.symlink_to(outside)
    try:
        with pytest.raises(StoreError):
            ops.nb_put({"component": "traefik", "rel": "nb-lien.yml", "b64": base64.b64encode(Y.encode()).decode(), "offset": 0, "last": True}, mods)
        assert outside.read_text() == "x: 1\n"
    finally:
        link.unlink()
    with pytest.raises(StoreError, match="module absent"):
        ops.nb_state({"components": ["project_history"]}, mods)                                                         # un élément d'un module que le nœud n'a pas


def test_rights_node_restore_is_not_given_by_default():
    assert "node.backup" in caps.PRESETS["admin"] and "node.restore" not in caps.PRESETS["admin"]                       # l'administrateur sauvegarde, le propriétaire restaure
    assert {"node.backup", "node.restore"} <= caps.PRESETS["owner"]
    assert "node.backup" not in caps.PRESETS["editor"] and "node.backup" not in caps.PRESETS["viewer"]
    assert caps.closure({"node.restore"}) == frozenset()                                                                # restaurer suppose pouvoir sauvegarder
