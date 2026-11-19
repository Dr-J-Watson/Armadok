"""Plusieurs fichiers compose (override, jeux au choix, fichiers du dernier lancement) et cibles de Makefile."""
import json

import pytest

from app import db, project_ops
from app.traefik_store import StoreError
from test_projects import client, pid_of, pj  # noqa: F401  (fixture)


@pytest.fixture(scope="module", autouse=True)
def _leave_no_project_behind(pj):
    yield
    db.run("DELETE FROM projects")


BASE = "services:\n  web:\n    image: nginx:1\n    ports: ['80:80']\n"
OVERRIDE = "services:\n  web:\n    image: nginx:2\n    ports: ['8080:80']\n  dev:\n    image: busybox\n"
PROD = "services:\n  web:\n    image: nginx:prod\n"


def test_override_is_part_of_the_default_set(tmp_path):
    (tmp_path / "docker-compose.yml").write_text(BASE)
    (tmp_path / "docker-compose.override.yml").write_text(OVERRIDE)
    (tmp_path / "docker-compose.prod.yml").write_text(PROD)
    (tmp_path / "notes.yml").write_text("x: 1")
    assert project_ops.compose_candidates(tmp_path) == ["docker-compose.yml", "docker-compose.override.yml", "docker-compose.prod.yml"]
    assert project_ops.default_files(tmp_path) == ["docker-compose.yml", "docker-compose.override.yml"]
    _, c = project_ops.read_compose(tmp_path)                                  # base + override fusionnés
    web = next(s for s in c["services"] if s["name"] == "web")
    assert web["image"] == "nginx:2" and set(web["ports"]) == {"80:80", "8080:80"} and {s["name"] for s in c["services"]} == {"web", "dev"}
    _, p = project_ops.read_compose(tmp_path, ["docker-compose.yml", "docker-compose.prod.yml"])
    assert next(s for s in p["services"])["image"] == "nginx:prod" and len(p["services"]) == 1
    with pytest.raises(StoreError):
        project_ops.pick_files(tmp_path, ["../evil.yml"])
    with pytest.raises(StoreError):
        project_ops.pick_files(tmp_path, ["notes.yml"])


def test_compose_command_uses_every_chosen_file(pj, monkeypatch):
    d = pj["base"] / "site"
    (d / "docker-compose.override.yml").write_text(OVERRIDE)
    (d / "docker-compose.prod.yml").write_text(PROD)
    seen = {}
    monkeypatch.setattr(project_ops, "_spawn", lambda dd, argv, label, action, **k: seen.update(argv=argv, label=label) or {"job": "x", "label": label})
    monkeypatch.setattr(project_ops, "_cli", lambda: "docker")
    project_ops.p_compose({"dir": str(d), "action": "up"})
    assert [a for a in seen["argv"] if a.endswith(".yml")] == [str(d / "docker-compose.yml"), str(d / "docker-compose.override.yml")]
    project_ops.p_compose({"dir": str(d), "action": "up", "files": ["docker-compose.yml", "docker-compose.prod.yml"]})
    assert str(d / "docker-compose.prod.yml") in seen["argv"] and str(d / "docker-compose.override.yml") not in seen["argv"]


def test_choice_is_stored_and_restart_follows_last_launch(pj, monkeypatch):
    from app import routes_projects as rp
    h, pid = pj["h"], pid_of(pj, "site")
    d = str(pj["base"] / "site")
    assert client.put(f"/api/projects/{pid}/compose-files", headers=h, json={"files": ["docker-compose.yml", "docker-compose.prod.yml"]}).status_code == 200
    assert client.put(f"/api/projects/{pid}/compose-files", headers=h, json={"files": ["nope.yml"]}).status_code == 422
    det = client.get(f"/api/projects/{pid}", headers=h).json()["compose"]
    assert det["choice"] == ["docker-compose.yml", "docker-compose.prod.yml"] and det["effective"] == det["choice"]
    assert "docker-compose.prod.yml" in det["candidates"]
    assert client.put(f"/api/projects/{pid}/compose-files", headers=h, json={"files": []}).status_code == 200   # retour à l'automatique
    assert rp._last_files(d, [{"state": "running", "configFiles": f"{d}/docker-compose.yml,{d}/docker-compose.prod.yml"}]) == ["docker-compose.yml", "docker-compose.prod.yml"]
    assert rp._last_files(d, [{"state": "running", "configFiles": "/ailleurs/docker-compose.yml"}]) == []   # hors du dossier : ignoré


def test_a_vanished_chosen_file_falls_back_to_automatic(pj, monkeypatch):
    h, pid = pj["h"], pid_of(pj, "site")
    d = pj["base"] / "site"
    (d / "docker-compose.proxy.yml").write_text(PROD)
    assert client.put(f"/api/projects/{pid}/compose-files", headers=h, json={"files": ["docker-compose.yml", "docker-compose.proxy.yml"]}).status_code == 200
    (d / "docker-compose.proxy.yml").unlink()                                   # le fichier choisi disparaît
    r = client.get(f"/api/projects/{pid}", headers=h)
    assert r.status_code == 200, r.text                                         # le projet reste accessible
    assert r.json()["compose"]["choice"] == [] and r.json()["compose"]["effective"] == r.json()["compose"]["default"]
    assert db.one("SELECT compose_files FROM projects WHERE id=?", (pid,))["compose_files"] == ""
    # même chose au lancement : le choix périmé est oublié et la commande part avec la règle automatique
    (d / "docker-compose.proxy.yml").write_text(PROD)
    assert client.put(f"/api/projects/{pid}/compose-files", headers=h, json={"files": ["docker-compose.yml", "docker-compose.proxy.yml"]}).status_code == 200
    (d / "docker-compose.proxy.yml").unlink()
    seen = {}
    monkeypatch.setattr(project_ops, "_spawn", lambda dd, argv, label, action, **k: seen.update(argv=argv) or {"job": "x", "label": label})
    monkeypatch.setattr(project_ops, "_cli", lambda: "docker")
    assert client.post(f"/api/projects/{pid}/compose", headers=h, json={"action": "up"}).status_code == 200
    assert "docker-compose.proxy.yml" not in " ".join(seen["argv"]) and db.one("SELECT compose_files FROM projects WHERE id=?", (pid,))["compose_files"] == ""
    # un choix demandé explicitement, lui, reste refusé
    assert client.post(f"/api/projects/{pid}/compose", headers=h, json={"action": "up", "files": ["nope.yml"]}).status_code == 422


MAKEFILE = """# Variables
IMAGE ?= x
.PHONY: help up build

# Affiche l'aide
help: ## Liste les commandes
\t@echo hi

up: build ## Démarre tout
\tdocker compose up -d

# Construit l'image
build:
\tdocker build .

%.o: %.c
\tcc $<
VAR := a:b
.hidden: x
"""


def test_make_targets_are_parsed_with_their_descriptions():
    t = {x["name"]: x for x in project_ops.parse_make(MAKEFILE)}
    assert set(t) == {"help", "up", "build"}
    assert t["help"]["doc"] == "Liste les commandes" and t["up"]["doc"] == "Démarre tout" and t["up"]["deps"] == ["build"]
    assert t["build"]["doc"] == "Construit l'image" and t["build"]["phony"] is True


def test_make_run_only_accepts_targets_of_the_file(pj, monkeypatch):
    d = pj["base"] / "site"
    (d / "Makefile").write_text(MAKEFILE)
    r = project_ops.p_make_targets({"dir": str(d)})
    assert r["file"] == "Makefile" and [x["name"] for x in r["targets"]] == ["help", "up", "build"]
    seen = {}
    monkeypatch.setattr(project_ops, "_spawn", lambda dd, argv, label, action, **k: seen.update(argv=argv) or {"job": "x", "label": label})
    monkeypatch.setattr(project_ops.shutil, "which", lambda n: "/usr/bin/make")
    project_ops.p_exec_make_run({"dir": str(d), "target": "up"})
    assert seen["argv"][:4] == ["make", "-f", "Makefile", "--no-print-directory"] and seen["argv"][-1] == "up"
    for bad in ("rm -rf /", "up; id", "../x", "inconnue", ""):
        with pytest.raises(StoreError):
            project_ops.p_exec_make_run({"dir": str(d), "target": bad})
    (d / "Makefile").unlink()


def test_compose_on_the_project_that_contains_armadok_runs_in_a_helper_container(pj, monkeypatch):
    """Lancé par le conteneur d'Armadok sur SON projet, `compose up` mourait avec lui (nouveau conteneur « Created », jamais démarré) : un conteneur auxiliaire le fait."""
    d = pj["base"] / "site"
    calls, spawned = [], []

    def fake(method, path, params=None, body=None, **k):
        calls.append((method, path, body))
        if path == "/containers/create":
            return {"Id": "cafe" * 16}
        if path.endswith("/json") and "cafe" in path:
            return {"State": {"Running": False, "ExitCode": 0}}
        if path.endswith("/logs"):
            return b""
        return [] if path == "/containers/json" else {}

    me = {"Id": "a" * 64, "Image": "sha256:abc", "Config": {"Image": "armadok:1", "Labels": {"com.docker.compose.project.working_dir": str(d)}},
          "Mounts": [{"Destination": project_ops.settings.docker_socket, "Source": "/var/run/docker.sock"}], "HostConfig": {"GroupAdd": ["984"]}}
    monkeypatch.setattr(project_ops.dapi, "request", fake)
    monkeypatch.setattr(project_ops, "_self_container", lambda: me)
    monkeypatch.setattr(project_ops, "_cli", lambda: "/usr/local/bin/docker")
    monkeypatch.setattr(project_ops, "_spawn", lambda *a, **k: spawned.append(a) or {"job": "x", "label": "x"})
    r = project_ops.p_compose({"dir": str(d), "action": "up"})
    assert not spawned and r["job"]                                              # pas de sous-processus : un conteneur auxiliaire
    create = next(b for m, p, b in calls if p == "/containers/create")
    assert create["Entrypoint"] == ["/usr/local/bin/docker"] and create["Cmd"][:3] == ["compose", "--project-directory", str(d)] and create["Cmd"][-3:] == ["up", "-d", "--remove-orphans"]
    hc = create["HostConfig"]
    assert hc["NetworkMode"] == "none" and hc["CapDrop"] == ["ALL"] and "/var/run/docker.sock:/var/run/docker.sock" in hc["Binds"] and f"{d}:{d}:ro" in hc["Binds"]
    assert create["Labels"] == {"armadok.helper": "compose"} and "984" in hc["GroupAdd"] and create["Image"] == "armadok:1"
    # une action qui ne touche pas aux conteneurs (pull) reste un simple sous-processus
    project_ops.p_compose({"dir": str(d), "action": "pull"})
    assert spawned
    # un autre projet : comportement habituel
    monkeypatch.setattr(project_ops, "_self_container", lambda: {**me, "Config": {"Image": "x", "Labels": {"com.docker.compose.project.working_dir": "/ailleurs"}}})
    spawned.clear()
    project_ops.p_compose({"dir": str(d), "action": "up"})
    assert spawned
