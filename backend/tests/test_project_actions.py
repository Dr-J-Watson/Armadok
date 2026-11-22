"""Actions personnalisées d'un projet : plusieurs étapes d'affilée, jamais de shell, droit du terminal pour tout ce qui n'est pas du compose."""
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, project_ops
from app.config import settings
from app.main import app
from app.traefik_store import StoreError
from test_projects import fakecli, pid_of, pj, wait_job  # noqa: F401
from test_security_audit import PW, login, sa  # noqa: F401

client = TestClient(app)


@pytest.fixture()
def proj():
    base = Path(tempfile.mkdtemp(prefix="pa")).resolve()
    (base / "p").mkdir()
    (base / "p" / "docker-compose.yml").write_text("services:\n  web: {image: busybox}\n")
    old = settings.project_roots
    settings.project_roots = [str(base)]
    yield base / "p"
    settings.project_roots = old


def _wait(jid, limit=20):
    end = time.time() + limit
    while time.time() < end:
        j = project_ops.p_job({"id": jid})
        if j["done"]:
            return j
        time.sleep(0.05)
    raise AssertionError("tâche trop longue")


def test_steps_run_in_order_and_stop_at_the_first_failure(proj):
    r = project_ops.p_exec_steps({"dir": str(proj), "action": "up", "steps": [{"kind": "cmd", "line": "echo un"}, {"kind": "cmd", "line": "false"}, {"kind": "cmd", "line": "echo trois"}]})
    j = _wait(r["job"])
    text = "\n".join(j["lines"])
    assert "un" in text and "trois" not in text and j["code"] == 1 and "Étape 2 en échec" in text


def test_all_steps_succeed(proj):
    j = _wait(project_ops.p_exec_steps({"dir": str(proj), "action": "up", "steps": [{"kind": "cmd", "line": "echo a"}, {"kind": "cmd", "line": "echo b"}]})["job"])
    assert j["code"] == 0 and "Étape 2/2" in "\n".join(j["lines"])


def test_no_shell_is_used(proj):
    marker = proj / "pwned"
    j = _wait(project_ops.p_exec_steps({"dir": str(proj), "action": "up", "steps": [{"kind": "cmd", "line": f"echo a ; touch {marker} && echo $(id)"}]})["job"])
    out = "\n".join(j["lines"])
    assert not marker.exists() and "uid=" not in out and "; touch" in out   # les caractères du shell sont des arguments, rien d'autre


def test_arguments_are_not_shown_in_the_label(proj):
    j = _wait(project_ops.p_exec_steps({"dir": str(proj), "action": "up", "steps": [{"kind": "cmd", "line": "echo motdepasse-secret"}]})["job"])
    assert "motdepasse-secret" not in j["lines"][0]


@pytest.mark.parametrize("steps", [
    [{"kind": "cmd", "line": ""}], [{"kind": "cmd", "line": "echo 'ouvert"}], [{"kind": "cmd", "line": "a\nb"}], [{"kind": "cmd", "line": 3}],
    [{"kind": "nope"}], ["echo"], [], [{"kind": "cmd", "line": "true"}] * (project_ops.MAX_STEPS + 1),
    [{"kind": "compose", "action": "config"}], [{"kind": "compose", "action": "ps"}], [{"kind": "compose", "action": "rm -rf"}], [{"kind": "make", "target": "absente"}],
])
def test_bad_steps_are_refused_before_anything_runs(proj, steps):
    with pytest.raises(StoreError):
        project_ops.p_exec_steps({"dir": str(proj), "action": "up", "steps": steps})


def test_compose_only_chain_refuses_commands(proj):
    with pytest.raises(StoreError) as e:
        project_ops.p_chain({"dir": str(proj), "action": "up", "steps": [{"kind": "cmd", "line": "echo x"}, {"kind": "compose", "action": "pull"}]})
    assert e.value.status == 403
    with pytest.raises(StoreError):
        project_ops.p_chain({"dir": str(proj), "action": "up", "steps": [{"kind": "make", "target": "x"}]})


def test_exec_ops_need_the_terminal_right_on_a_worker():
    from app import worker, worker_proto as proto
    assert "p_exec_steps" in proto.OPS_BY_MODULE["docker"] and "p_chain" in proto.OPS_BY_MODULE["docker"]
    assert "p_exec_steps".startswith(("d_exec_", "p_exec_"))   # le worker la refuse sans WORKER_ALLOW_EXEC, comme le terminal et make


# ---------- côté API : droits ----------
def test_api_rights_and_override(pj, sa, fakecli):  # noqa: F811
    h = pj["h"]
    base = pj["base"]
    client.post("/api/projects/import", headers=h, json={"paths": [str(base / "site")]})
    pid = db.one("SELECT id FROM projects WHERE path=?", (str(base / "site"),))["id"]
    try:
        steps = [{"kind": "cmd", "line": "echo perso"}, {"kind": "compose", "action": "pull"}]
        r = client.put(f"/api/projects/{pid}/actions/up", headers=h, json={"steps": steps})
        assert r.status_code == 200, r.text
        got = client.get(f"/api/projects/{pid}/actions", headers=h).json()
        assert got["actions"]["up"] == steps and "config" not in got["names"]
        # un lancement normal d'« up » exécute la suite ; sur un service précis, la commande normale reste
        run = client.post(f"/api/projects/{pid}/compose", headers=h, json={"action": "up"})
        assert run.status_code == 200 and "2 étape" in run.json()["label"]
        wait_job(h, pid, run.json()["job"])
        one = client.post(f"/api/projects/{pid}/compose", headers=h, json={"action": "up", "services": ["web"]})
        assert one.status_code == 200 and one.json()["label"].startswith("compose")
        wait_job(h, pid, one.json()["job"])
        # actions inconnues ou étapes invalides
        assert client.put(f"/api/projects/{pid}/actions/config", headers=h, json={"steps": steps}).status_code == 404
        assert client.put(f"/api/projects/{pid}/actions/up", headers=h, json={"steps": [{"kind": "cmd", "line": ""}]}).status_code == 422
        # sans le droit du terminal : on peut régler du compose, pas écrire ni retirer une commande
        role = client.post("/api/rights/roles", headers=sa["h_sa-owner"], json={"name": "Actions sans terminal", "description": "", "caps": ["docker.view", "projects.view", "projects.files", "projects.edit"]})
        assert role.status_code == 200, role.text
        g = client.post("/api/rights/grants", headers=sa["h_sa-owner"], json={"subjectType": "user", "subjectId": sa["ids"]["sa-ops"], "roleId": role.json()["id"], "nodeId": pj["h"]["X-Node"]}); assert g.status_code == 200, g.text
        ho = {**login("sa-ops"), "X-Node": pj["h"]["X-Node"]}
        assert client.put(f"/api/projects/{pid}/actions/stop", headers=ho, json={"steps": [{"kind": "cmd", "line": "echo x"}]}).status_code == 403
        rr = client.put(f"/api/projects/{pid}/actions/stop", headers=ho, json={"steps": [{"kind": "compose", "action": "down"}]}); assert rr.status_code == 200, rr.text
        assert client.put(f"/api/projects/{pid}/actions/up", headers=ho, json={"steps": []}).status_code == 403   # la suite de « up » contient une commande
        # rétablir le comportement normal
        assert client.put(f"/api/projects/{pid}/actions/up", headers=h, json={"steps": []}).status_code == 200
        assert list(client.get(f"/api/projects/{pid}/actions", headers=h).json()["actions"]) == ["stop"]
        assert client.put(f"/api/projects/{pid}/actions/stop", headers=h, json={"steps": []}).status_code == 200
        assert client.get(f"/api/projects/{pid}/actions", headers=h).json()["actions"] == {}
    finally:
        db.run("DELETE FROM project_actions WHERE project_id=?", (pid,))
        client.delete(f"/api/projects/{pid}", headers=h)
