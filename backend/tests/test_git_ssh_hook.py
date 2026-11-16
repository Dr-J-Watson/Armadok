"""Git en SSH (clé de déploiement générée ici, jamais renvoyée) et déclenchement par le dépôt (webhook signé)."""
import hashlib
import hmac
import json
import os
import stat
import subprocess
import time

import pytest

from app import db, gitssh, project_ops, routes_projects
from test_projects import client, fakecli, git, pid_of, pj, repo, wait_job  # noqa: F401  (fixtures)


@pytest.fixture(scope="module", autouse=True)
def _leave_no_project_behind(pj):
    """Les projets importés ici ne doivent pas se retrouver dans les tests d'un autre fichier (même base de données)."""
    yield
    db.run("DELETE FROM projects")


def test_generated_keys_are_real_openssh_keys(tmp_path):
    private, public, fp = gitssh.generate("mon projet!")
    assert private.startswith(gitssh.HEADER) and public.startswith("ssh-ed25519 ") and public.endswith("monprojet") and fp.startswith("SHA256:")
    (tmp_path / "k.pub").write_text(public + "\n")
    out = subprocess.run(["ssh-keygen", "-l", "-f", str(tmp_path / "k.pub")], capture_output=True, text=True)
    assert out.returncode == 0 and fp in out.stdout                                    # l'empreinte calculée est celle d'OpenSSH
    (tmp_path / "k").write_text(private)
    os.chmod(tmp_path / "k", 0o600)
    out = subprocess.run(["ssh-keygen", "-y", "-f", str(tmp_path / "k")], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.split()[1] == public.split()[1]          # la privée correspond à la publique


def test_project_key_is_encrypted_and_never_leaves(pj, repo):
    h = pj["h"]
    pid = pid_of(pj, "gitproj")
    st = client.get(f"/api/projects/{pid}/git", headers=h).json()
    assert st["ssh"]["source"] is None
    r = client.post(f"/api/projects/{pid}/git/ssh-key", headers=h)
    assert r.status_code == 200 and r.json()["publicKey"].startswith("ssh-ed25519 ") and "PRIVATE" not in r.text
    st = client.get(f"/api/projects/{pid}/git", headers=h)
    assert st.json()["ssh"]["source"] == "project" and st.json()["ssh"]["fingerprint"] == r.json()["fingerprint"] and "PRIVATE" not in st.text
    assert "OPENSSH" not in db.one("SELECT private FROM project_ssh WHERE project_id=?", (pid,))["private"]   # chiffrée en base
    first = r.json()["publicKey"]
    assert client.post(f"/api/projects/{pid}/git/ssh-key", headers=h).json()["publicKey"] != first                  # régénérer remplace
    assert client.delete(f"/api/projects/{pid}/git/ssh-key", headers=h).status_code == 200
    assert client.get(f"/api/projects/{pid}/git", headers=h).json()["ssh"]["source"] is None


def test_node_key_is_used_by_projects_without_their_own(pj, repo):
    h = pj["h"]
    pid = pid_of(pj, "gitproj")
    assert client.get("/api/projects/git/node-key", headers=h).json()["set"] is False
    n = client.post("/api/projects/git/node-key", headers=h).json()
    assert n["set"] and n["publicKey"].startswith("ssh-ed25519 ")
    st = client.get(f"/api/projects/{pid}/git", headers=h).json()["ssh"]
    assert st["source"] == "node" and st["publicKey"] == n["publicKey"]
    client.post(f"/api/projects/{pid}/git/ssh-key", headers=h)
    assert client.get(f"/api/projects/{pid}/git", headers=h).json()["ssh"]["source"] == "project"                  # la clé du projet passe avant
    client.delete(f"/api/projects/{pid}/git/ssh-key", headers=h)
    assert client.delete("/api/projects/git/node-key", headers=h).status_code == 200
    assert client.get("/api/projects/git/node-key", headers=h).json()["set"] is False


def test_key_file_is_private_and_removed_after_the_command(tmp_path, monkeypatch):
    private, _, _ = gitssh.generate()
    env = project_ops._git_env(None, ssh_key=private)
    path = env["OVH_WEBUI_KEYFILE"]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and open(path).read().startswith(gitssh.HEADER)
    assert path in env["GIT_SSH_COMMAND"] and "BatchMode=yes" in env["GIT_SSH_COMMAND"] and "IdentitiesOnly=yes" in env["GIT_SSH_COMMAND"]
    assert private not in " ".join(env.values())                                                                    # la clé n'est jamais dans l'environnement, seulement son chemin
    project_ops._drop_key(env)
    assert not os.path.exists(path)
    assert "-i" not in project_ops._git_env(None)["GIT_SSH_COMMAND"].split()                                      # sans clé : pas de -i
    with pytest.raises(project_ops.StoreError):
        project_ops._git_env(None, ssh_key="-----BEGIN OPENSSH PRIVATE KEY-----\n" + "x" * 5000)                  # trop longue / invalide


def test_clone_over_ssh_gives_git_the_node_key_then_cleans_up(pj, tmp_path, monkeypatch):
    h = pj["h"]
    fake = tmp_path / "bin" / "ssh"
    fake.parent.mkdir()
    log = tmp_path / "ssh.log"
    fake.write_text(f'#!/bin/sh\necho "ARGS: $@" >> {log}\nfor a in "$@"; do if [ "$p" = "-i" ]; then cat "$a" >> {log}; fi; p="$a"; done\nexit 1\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    client.post("/api/projects/git/node-key", headers=h)
    r = client.post("/api/projects/git/clone", headers=h, json={"url": "git@example.com:moi/depot.git", "name": "viassh"})
    assert r.status_code == 200
    j = wait_job(h, 0, r.json()["job"], path="clone-jobs")
    assert j["code"] != 0                                                                                           # le faux ssh refuse : seul compte ce qu'il a reçu
    text = log.read_text()
    assert "BatchMode=yes" in text and "StrictHostKeyChecking=accept-new" in text and "git@example.com" in text
    assert "BEGIN OPENSSH PRIVATE KEY" in text                                                                      # git a bien reçu la clé du nœud
    time.sleep(0.5)
    ssh_dir = project_ops._env()["HOME"] + "/.ssh"
    assert not [f for f in os.listdir(ssh_dir) if f.startswith("k-")]                                              # la clé temporaire est effacée
    client.delete("/api/projects/git/node-key", headers=h)


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_pulls_when_signed_and_refuses_everything_else(pj, repo, fakecli):
    h = pj["h"]
    pid = pid_of(pj, "gitproj")
    routes_projects._hook_last.clear()
    routes_projects._hook_fails.clear()
    made = client.post(f"/api/projects/{pid}/git/hook", headers=h, json={"deploy": False})
    secret = made.json()["secret"]
    assert made.status_code == 200 and made.json()["path"] == f"/api/hooks/git/{pid}"
    assert secret not in db.one("SELECT secret FROM project_hook WHERE project_id=?", (pid,))["secret"]            # chiffré en base
    st = client.get(f"/api/projects/{pid}/git", headers=h).json()["hook"]
    assert st["set"] and not st["deploy"] and "secret" not in json.dumps(st)
    body = b'{"ref":"refs/heads/main"}'
    # refusé : sans signature, mauvaise signature, projet inconnu (même réponse : rien n'est révélé)
    assert client.post(f"/api/hooks/git/{pid}", content=body).status_code == 401
    assert client.post(f"/api/hooks/git/{pid}", content=body, headers={"X-Hub-Signature-256": sign("autre", body)}).status_code == 401
    assert client.post("/api/hooks/git/99999", content=body, headers={"X-Hub-Signature-256": sign(secret, body)}).status_code == 401
    assert client.post(f"/api/hooks/git/{pid}", content=body, headers={"X-Hub-Signature-256": sign(secret, body), "X-GitHub-Event": "ping"}).json() == {"ok": True, "pong": True}
    # un nouveau commit côté dépôt, puis le webhook signé : la récupération se fait toute seule
    (repo["src"] / "docker-compose.yml").write_text("services: {a: {image: nginx:2}}\n")
    git(repo["src"], "commit", "-qam", "deux"); git(repo["src"], "push", "-q", "origin", "HEAD:main")
    r = client.post(f"/api/hooks/git/{pid}", content=body, headers={"X-Hub-Signature-256": sign(secret, body)})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["job"]
    assert wait_job(h, pid, r.json()["job"])["code"] == 0
    assert "nginx:2" in (repo["dir"] / "docker-compose.yml").read_text()
    deadline = time.time() + 5
    while time.time() < deadline and client.get(f"/api/projects/{pid}/git", headers=h).json()["hook"]["lastResult"] != "récupéré":
        time.sleep(0.2)
    assert client.get(f"/api/projects/{pid}/git", headers=h).json()["hook"]["lastResult"] == "récupéré"
    again = client.post(f"/api/hooks/git/{pid}", content=body, headers={"X-Hub-Signature-256": sign(secret, body)}).json()
    assert again.get("skipped")                                                                                     # deux envois rapprochés : le second est ignoré
    # GitLab : le secret dans X-Gitlab-Token
    routes_projects._hook_last.clear()
    assert client.post(f"/api/hooks/git/{pid}", content=body, headers={"X-Gitlab-Token": secret}).json()["ok"]
    client.delete(f"/api/projects/{pid}/git/hook", headers=h)
    assert client.post(f"/api/hooks/git/{pid}", content=body, headers={"X-Hub-Signature-256": sign(secret, body)}).status_code == 401   # supprimé : plus rien ne passe


def test_webhook_can_redeploy_after_the_pull(pj, repo, fakecli):
    h = pj["h"]
    pid = pid_of(pj, "gitproj")
    routes_projects._hook_last.clear()
    (repo["src"] / "docker-compose.yml").write_text("services:\n  a:\n    image: nginx:3\n")             # un compose que « up » accepte
    git(repo["src"], "commit", "-qam", "trois"); git(repo["src"], "push", "-q", "origin", "HEAD:main")
    secret = client.post(f"/api/projects/{pid}/git/hook", headers=h, json={"deploy": True}).json()["secret"]
    body = b"{}"
    assert client.post(f"/api/hooks/git/{pid}", content=body, headers={"X-Hub-Signature-256": sign(secret, body)}).json()["ok"]
    deadline = time.time() + 20
    while time.time() < deadline and client.get(f"/api/projects/{pid}/git", headers=h).json()["hook"]["lastResult"] != "récupéré et redéployé":
        time.sleep(0.3)
    assert client.get(f"/api/projects/{pid}/git", headers=h).json()["hook"]["lastResult"] == "récupéré et redéployé"
    client.delete(f"/api/projects/{pid}/git/hook", headers=h)


def test_webhook_failures_lock_the_source(pj, repo):
    routes_projects._hook_fails.clear()
    pid = pid_of(pj, "gitproj")
    codes = [client.post(f"/api/hooks/git/{pid}", content=b"x").status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]                                                    # trop d'échecs : la source est mise en attente
    routes_projects._hook_fails.clear()


def test_hook_and_keys_need_the_git_right(pj, repo):
    pid = pid_of(pj, "gitproj")
    for method, path in (("post", f"/api/projects/{pid}/git/hook"), ("post", f"/api/projects/{pid}/git/ssh-key"), ("post", "/api/projects/git/node-key")):
        assert getattr(client, method)(path, json={}).status_code == 401, path                                      # sans connexion
