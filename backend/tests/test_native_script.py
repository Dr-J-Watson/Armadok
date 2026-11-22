"""Worker natif (sans Docker) : tout vit dans le Makefile (NATIVE_SCRIPT) ; le texte est du bash valide, expose ses commandes, et les cibles le branchent."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
bash = shutil.which("bash")
make = shutil.which("make")
pytestmark = pytest.mark.skipif(not MAKEFILE.is_file(), reason="dépôt sans Makefile")


def embedded() -> str:
    """Le texte de NATIVE_SCRIPT tel que make le donne au shell : « $$ » devient « $ », et un « \\ » en fin de ligne joint la ligne suivante."""
    mk = MAKEFILE.read_text(encoding="utf-8")
    body = mk.split("define NATIVE_SCRIPT\n", 1)[1].split("\nendef\n", 1)[0]
    return re.sub(r"[ \t]*\\\n[ \t]*", " ", body.replace("$$", "$")) + "\n"


def test_no_separate_script_is_needed():
    assert not (ROOT / "scripts" / "native-worker.sh").exists()                     # tout est dans le Makefile
    assert "native-worker.sh" not in MAKEFILE.read_text(encoding="utf-8")          # et rien n'est téléchargé à côté


@pytest.mark.skipif(not bash, reason="sans bash")
def test_embedded_script_is_valid_bash_and_rejects_unknown_commands(tmp_path):
    f = tmp_path / "native.sh"
    f.write_text(embedded(), encoding="utf-8")
    assert subprocess.run([bash, "-n", str(f)], capture_output=True).returncode == 0
    r = subprocess.run([bash, str(f), "nimporte-quoi"], capture_output=True, text=True, timeout=20)
    assert r.returncode != 0 and "Commande inconnue" in r.stderr
    for cmd in ("check", "install", "update", "status", "logs", "start", "stop", "restart", "backup", "uninstall"):
        assert cmd in r.stderr                                                       # le message liste toutes les commandes


@pytest.mark.skipif(not (bash and make), reason="sans bash ou make")
def test_make_gives_the_shell_exactly_the_embedded_text():
    out = subprocess.run([make, "-s", "-C", str(ROOT), "native-script"], capture_output=True, text=True, timeout=30).stdout
    assert out == embedded()


def test_makefile_wires_the_native_targets_and_the_install_menu():
    mk = MAKEFILE.read_text(encoding="utf-8")
    phony = mk.split(".PHONY:")[1].splitlines()[0]
    commands = {"worker-native": "install", "native-check": "check", "native-status": "status", "native-logs": "logs", "native-restart": "restart",
                "native-stop": "stop", "native-start": "start", "native-backup": "backup", "native-update": "update", "native-uninstall": "uninstall"}
    for target, cmd in commands.items():
        assert f"\n{target}:" in mk and target in phony
        assert f"$(call NATIVE_RUN,{cmd})" in mk.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]
    assert "3|native|n) exec $(MAKE) --no-print-directory worker-native" in mk      # « make install » propose le worker natif
    install = mk.split("\ninstall:")[1].split("\nhub:")[0]
    assert "need_docker" not in install                                              # le choix 3 ne demande pas Docker
    assert "\ncontinue:" in mk and "continue" in phony                               # « make continue » reprend une installation interrompue
