"""Modèles de projet : variables du .env, enregistrement d'un projet comme modèle (secrets vidés), création à partir d'un modèle."""
import pytest

from app import db, project_templates as pt
from test_projects import client, pid_of, pj  # noqa: F401  (fixture)


@pytest.fixture(scope="module", autouse=True)
def _clean(pj):
    yield
    db.run("DELETE FROM projects")
    db.run("DELETE FROM project_templates")


ENV = "# Port\nPORT=25565\n# Mot de passe\nRCON_PASSWORD=sup3r secret\nMOTD=Mon serveur\nEMPTY=\n"


def test_parse_env_keeps_help_and_flags_secrets():
    v = {x["key"]: x for x in pt.parse_env(ENV)}
    assert v["PORT"]["help"] == "Port" and v["PORT"]["value"] == "25565" and not v["PORT"]["secret"]
    assert v["RCON_PASSWORD"]["secret"] and v["RCON_PASSWORD"]["value"] == "sup3r secret" and v["EMPTY"]["value"] == ""


def test_fill_and_blank_keep_comments():
    out = pt.fill_env(ENV, {"PORT": "30000", "MOTD": "a b"})
    assert "# Port\nPORT=30000\n" in out and "MOTD='a b'" in out and "RCON_PASSWORD=sup3r secret" in out
    b = pt.blank_secrets(ENV)
    assert "RCON_PASSWORD=\n" in b and "PORT=25565" in b
    assert "MOTD=\n" in pt.blank_secrets(ENV, {"MOTD"}, secrets_too=False)


def test_render_replaces_name_everywhere():
    f = {"docker-compose.yml": "services:\n  a:\n    container_name: {{name}}\n", ".env": "NAME={{name}}\nP=1\n"}
    r = pt.render(f, "survie", {"P": "2"})
    assert "container_name: survie" in r["docker-compose.yml"] and "NAME=survie" in r[".env"] and "P=2" in r[".env"]


@pytest.mark.parametrize("files", [{}, {"a.txt": "x"}, {"../x/docker-compose.yml": "services: {}"}, {"/abs.yml": "x"}, {".git/config": "x", "docker-compose.yml": "services: {}"}])
def test_validate_refuses_bad_templates(files):
    with pytest.raises(ValueError):
        pt.validate_files(files)


def test_builtins_are_valid_and_listed(pj):
    for b in pt.BUILTIN:
        assert pt.validate_files(b["files"])
    h = pj["h"]
    lst = client.get("/api/projects/templates/list", headers=h).json()
    assert {"builtin:base", "builtin:minecraft"} <= {t["id"] for t in lst["templates"]}
    mc = next(t for t in lst["templates"] if t["id"] == "builtin:minecraft")
    assert {v["key"] for v in mc["vars"]} >= {"MC_EULA", "MC_PORT", "MC_RCON_PASSWORD"} and next(v for v in mc["vars"] if v["key"] == "MC_RCON_PASSWORD")["secret"]


def test_create_from_builtin_and_save_a_project_as_template(pj):
    h = pj["h"]
    r = client.post("/api/projects/templates/builtin:minecraft/create", headers=h, json={"name": "mc-survie", "values": {"MC_PORT": "25570", "MC_EULA": "TRUE", "BIDON": "x"}})
    assert r.status_code == 200, r.text
    d = pj["base"] / "mc-survie"
    env = (d / ".env").read_text()
    assert "MC_PORT=25570" in env and "MC_EULA=TRUE" in env and "MC_NAME=mc-survie" in env and "BIDON" not in env
    assert oct((d / ".env").stat().st_mode & 0o777) == "0o600" and r.json()["id"]
    # ce nouveau projet devient un modèle : le secret est vidé, le nom du projet devient {{name}}
    (d / ".env").write_text(env.replace("MC_RCON_PASSWORD=", "MC_RCON_PASSWORD=topsecret"))
    pid = r.json()["id"]
    s = client.post("/api/projects/templates", headers=h, json={"name": "Mon Minecraft", "description": "mes serveurs", "projectId": pid, "files": ["docker-compose.yml", ".env"]})
    assert s.status_code == 200, s.text
    tid = s.json()["id"]
    det = client.get(f"/api/projects/templates/{tid}", headers=h).json()
    secret = next(v for v in det["vars"] if v["key"] == "MC_RCON_PASSWORD")
    assert secret["secret"] and secret["default"] == "" and det["mine"]
    stored = db.one("SELECT data FROM project_templates WHERE id=?", (tid,))["data"]
    assert "topsecret" not in stored                                                          # chiffré, et le secret n'y est de toute façon pas
    assert client.post("/api/projects/templates", headers=h, json={"name": "mon minecraft", "projectId": pid, "files": [".env"]}).status_code == 409   # nom déjà pris
    again = client.post(f"/api/projects/templates/{tid}/create", headers=h, json={"name": "mc-second", "values": {"MC_RCON_PASSWORD": "autre"}})
    assert again.status_code == 200 and "MC_RCON_PASSWORD=autre" in (pj["base"] / "mc-second" / ".env").read_text()
    assert client.put(f"/api/projects/templates/{tid}", headers=h, json={"description": "autre"}).status_code == 200
    assert client.put("/api/projects/templates/builtin:base", headers=h, json={"description": "x"}).status_code == 403
    assert client.delete(f"/api/projects/templates/{tid}", headers=h).status_code == 200
    assert client.get(f"/api/projects/templates/{tid}", headers=h).status_code == 404
