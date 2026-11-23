"""Supprimer UN service d'un projet (compose rm -f -s <service>) : jamais sans service, jamais dans une action personnalisée."""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, project_ops
from app.config import settings
from app.main import app
from app.traefik_store import StoreError
from test_projects import fakecli, pj, wait_job  # noqa: F401
from test_security_audit import sa  # noqa: F401

client = TestClient(app)


@pytest.fixture()
def proj(fakecli):  # noqa: F811
    base = Path(tempfile.mkdtemp(prefix="rm")).resolve()
    (base / "p").mkdir()
    (base / "p" / "docker-compose.yml").write_text("services:\n  web: {image: busybox}\n  db: {image: busybox}\n")
    old = settings.project_roots
    settings.project_roots = [str(base)]
    yield base / "p"
    settings.project_roots = old


def test_rm_targets_one_service_and_keeps_volumes(proj):
    _, argv, label = project_ops._compose_cmd({"dir": str(proj), "action": "rm", "services": ["web"]})
    assert argv[argv.index("rm"):] == ["rm", "-f", "-s", "--", "web"]    # -f : sans question ; -s : arrête d'abord ; pas de -v : les volumes restent
    assert label.endswith("rm -f -s web")


def test_rm_without_a_service_is_refused(proj):
    with pytest.raises(StoreError) as e:
        project_ops._compose_cmd({"dir": str(proj), "action": "rm"})
    assert e.value.status == 422
    with pytest.raises(StoreError):
        project_ops._compose_cmd({"dir": str(proj), "action": "rm", "services": ["absent"]})


def test_rm_is_not_a_custom_action_and_goes_through_the_self_helper():
    assert "rm" not in project_ops.STEP_ACTIONS
    assert "rm" in project_ops.SELF_ACTIONS     # un projet qui contient Armadok : la commande est confiée au conteneur auxiliaire


def test_api_rm_needs_a_service(pj, sa, fakecli):  # noqa: F811
    h, base = pj["h"], pj["base"]
    client.post("/api/projects/import", headers=h, json={"paths": [str(base / "site")]})
    pid = db.one("SELECT id FROM projects WHERE path=?", (str(base / "site"),))["id"]
    try:
        assert client.post(f"/api/projects/{pid}/compose", headers=h, json={"action": "rm"}).status_code == 422
        names = client.get(f"/api/projects/{pid}/actions", headers=h).json()["names"]
        assert "rm" not in names                                      # l'onglet « Avancé » ne propose pas cette action
        svc = client.get(f"/api/projects/{pid}", headers=h).json()["services"][0]["name"]
        ok = client.post(f"/api/projects/{pid}/compose", headers=h, json={"action": "rm", "services": [svc]})
        assert ok.status_code == 200 and ok.json()["label"].endswith(f"rm -f -s {svc}"), ok.text
        wait_job(h, pid, ok.json()["job"])
    finally:
        client.delete(f"/api/projects/{pid}", headers=h)
