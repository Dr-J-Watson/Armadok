"""Plusieurs dossiers de stockage : le choix de la racine à la création d'un projet (jamais en dehors des racines autorisées)."""
import tempfile
from pathlib import Path

import pytest

from app import project_ops
from app.config import settings
from app.traefik_store import StoreError


@pytest.fixture()
def two_roots():
    a, b = Path(tempfile.mkdtemp(prefix="ra")).resolve(), Path(tempfile.mkdtemp(prefix="rb")).resolve()
    old = settings.project_roots
    settings.project_roots = [str(a), str(b)]
    yield a, b
    settings.project_roots = old


FILES = {"docker-compose.yml": "services: {}\n"}


def test_default_is_first_root(two_roots):
    a, _ = two_roots
    r = project_ops.p_create({"name": "p1", "files": FILES})
    assert r["path"] == str(a / "p1")


def test_chosen_root(two_roots):
    _, b = two_roots
    r = project_ops.p_create({"name": "p2", "files": FILES, "root": str(b)})
    assert r["path"] == str(b / "p2") and (b / "p2" / "docker-compose.yml").is_file()


def test_same_name_allowed_in_other_root_only(two_roots):
    a, b = two_roots
    project_ops.p_create({"name": "dup", "files": FILES, "root": str(a)})
    project_ops.p_create({"name": "dup", "files": FILES, "root": str(b)})
    with pytest.raises(StoreError) as e:
        project_ops.p_create({"name": "dup", "files": FILES, "root": str(b)})
    assert e.value.status == 409


@pytest.mark.parametrize("bad", ["/tmp", "/etc", "../..", "/"])
def test_root_outside_the_list_is_refused(two_roots, bad):
    with pytest.raises(StoreError) as e:
        project_ops.p_create({"name": "evil", "files": FILES, "root": bad})
    assert e.value.status in (403, 422)


def test_subfolder_of_a_root_is_not_a_root(two_roots):
    a, _ = two_roots
    (a / "sub").mkdir()
    with pytest.raises(StoreError) as e:
        project_ops.p_create({"name": "x", "files": FILES, "root": str(a / "sub")})
    assert e.value.status == 403
