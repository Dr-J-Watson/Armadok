"""Worker natif (sans Docker) : le script d'installation est syntaxiquement correct et expose ses commandes ; le Makefile les branche."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "native-worker.sh"
bash = shutil.which("bash")


@pytest.mark.skipif(not SCRIPT.is_file() or not bash, reason="dépôt sans scripts/ ou sans bash")
def test_native_script_is_valid_bash_and_rejects_unknown_commands():
    assert subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True).returncode == 0
    r = subprocess.run([bash, str(SCRIPT), "nimporte-quoi"], capture_output=True, text=True, timeout=20)
    assert r.returncode != 0 and "Commande inconnue" in r.stderr


@pytest.mark.skipif(not (ROOT / "Makefile").is_file(), reason="dépôt sans Makefile")
def test_makefile_wires_the_native_targets_and_the_install_menu():
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("worker-native", "native-check", "native-status", "native-update", "native-uninstall"):
        assert f"\n{target}:" in mk and target in mk.split(".PHONY:")[1].splitlines()[0]
    assert "3|native|n) exec $(MAKE) --no-print-directory worker-native" in mk      # « make install » propose le worker natif
    install = mk.split("\ninstall:")[1].split("\nhub:")[0]
    assert "need_docker" not in install                                              # le choix 3 ne demande pas Docker
