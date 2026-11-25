"""L'outil en ligne de commande des sauvegardes (utilisé par le Makefile) : même code, même format que l'interface."""
import sqlite3

import pytest

from app import backup
from app.tools import backup as cli


@pytest.fixture()
def scratch(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "home").mkdir(parents=True)
    (root / "home" / "known_hosts").write_text("hote A")
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
    monkeypatch.delenv("BACKUP_PASSWORD", raising=False)
    return root, dyn


def run(capsys, *args):
    code = cli.main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_create_list_and_inspect(scratch, capsys):
    code, out, _ = run(capsys, "create", "--label", "ligne de commande", "--components", "database,home")
    assert code == 0 and out.startswith("OK\t") and out.split("\t")[1].endswith("-ligne-de-commande.zip")
    code, out, _ = run(capsys, "list", "--tsv")
    row = out.strip().split("\t")
    assert code == 0 and row[0].endswith(".zip") and row[3] == "0" and row[5] == "database,home" and row[2].isdigit()      # sortie lisible par un script
    code, out, _ = run(capsys, "inspect", row[0])
    assert code == 0 and '"keyStatus": "same"' in out and '"database"' in out


def test_password_comes_from_the_environment_never_the_arguments(scratch, capsys, monkeypatch):
    monkeypatch.setenv("BACKUP_PASSWORD", "un-bon-mot-de-passe")
    code, out, _ = run(capsys, "create", "--components", "database")
    name = out.strip().split("\t")[1]
    assert code == 0 and name.endswith(".armbak")
    monkeypatch.delenv("BACKUP_PASSWORD")
    code, _, err = run(capsys, "inspect", name)
    assert code == 1 and "protégée" in err                                                                               # sans mot de passe : refusé, message clair
    monkeypatch.setenv("BACKUP_PASSWORD", "un-bon-mot-de-passe")
    assert run(capsys, "inspect", name)[0] == 0
    monkeypatch.setenv("BACKUP_PASSWORD", "court")
    assert run(capsys, "create", "--components", "database")[0] == 1                                                      # 8 caractères au moins


def test_restore_puts_files_back_and_keeps_a_safety_copy(scratch, capsys):
    root, dyn = scratch
    name = run(capsys, "create")[1].strip().split("\t")[1]
    c = sqlite3.connect(root / "app.db")
    c.execute("UPDATE users SET name='apres'")
    c.commit()
    c.close()
    (dyn / "site.yml").write_text("a: 2\n")
    code, out, err = run(capsys, "restore", name, "--components", "database,traefik")
    assert code == 0 and "SAFETY\t" in err and out.startswith("OK\t")
    assert sqlite3.connect(root / "app.db").execute("SELECT name FROM users").fetchone()[0] == "avant" and (dyn / "site.yml").read_text() == "a: 1\n"
    assert any(b["kind"] == "avant restauration" for b in backup.listing())                                              # on peut annuler la restauration
    code, _, err = run(capsys, "restore", "nimporte.zip")
    assert code == 1 and "ERREUR" in err


def test_import_validates_the_file_and_delete(scratch, capsys, tmp_path):
    name = run(capsys, "create", "--components", "database")[1].strip().split("\t")[1]
    outside = tmp_path / "copie-externe.zip"
    outside.write_bytes((backup.store_dir() / name).read_bytes())
    code, out, _ = run(capsys, "import", str(outside))
    assert code == 0 and out.strip().split("\t")[1] == "copie-externe.zip"
    bad = tmp_path / "faux.zip"
    bad.write_bytes(b"pas un zip")
    code, _, err = run(capsys, "import", str(bad))
    assert code == 1 and "manifeste" in err and not (backup.store_dir() / "faux.zip").exists()
    assert run(capsys, "import", str(tmp_path / "absent.zip"))[0] == 1
    assert run(capsys, "delete", "copie-externe.zip")[0] == 0 and not (backup.store_dir() / "copie-externe.zip").exists()
    assert run(capsys, "delete", "../app.db")[0] == 1                                                                   # aucun chemin ne sort de la liste


def test_key_reports_whether_encryption_key_is_defined(scratch, capsys, monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "k" * 40)
    code, out, _ = run(capsys, "key")
    assert code == 0 and out.startswith("1\t") and len(out.strip().split("\t")[1]) == 16                                 # empreinte seulement, jamais la clé
    monkeypatch.delenv("ENCRYPTION_KEY")
    assert run(capsys, "key")[1].startswith("0\t")


def test_inspect_kv_is_readable_from_a_shell_script(scratch, capsys):
    name = run(capsys, "create", "--components", "database,home")[1].strip().split("\t")[1]
    code, out, _ = run(capsys, "inspect", name, "--kv")
    lines = dict(l.split("=", 1) for l in out.splitlines() if not l.startswith("component="))
    comps = [l.split("=", 1)[1].split("\t") for l in out.splitlines() if l.startswith("component=")]
    assert code == 0 and lines["keystatus"] == "same" and lines["includeskey"] == "0" and {c[0] for c in comps} == {"database", "home"} and comps[0][2].isdigit()
