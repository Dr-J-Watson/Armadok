import os
import subprocess
import sys


def test_sans_puid_lance_la_commande(tmp_path):
    r = subprocess.run([sys.executable, "-m", "app.drop", sys.executable, "-c", "print('ok')"], capture_output=True, text=True,
                       env={**os.environ, "PUID": ""})
    assert r.stdout.strip() == "ok"


def test_root_donne_data_et_abandonne_les_droits(tmp_path):
    if os.getuid() != 0:
        import pytest
        pytest.skip("nécessite root")
    import tempfile
    from pathlib import Path
    d = Path(tempfile.mkdtemp(dir="/tmp"))  # traversable par l'utilisateur de destination (tmp_path est en 0700)
    d.chmod(0o755)
    (d / "f").write_text("x")
    code = f"import os;print(os.getuid(), os.stat('{d}').st_uid, os.stat('{d}/f').st_gid)"
    r = subprocess.run([sys.executable, "-m", "app.drop", sys.executable, "-c", code], capture_output=True, text=True,
                       env={**os.environ, "PUID": "990", "PGID": "1000", "DATA_DIR": str(d)})
    assert r.stdout.split() == ["990", "990", "1000"], r.stderr
