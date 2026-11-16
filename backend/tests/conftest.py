"""Environnement de test : base et dossier Traefik temporaires, OVH simulé."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ.update(
    JWT_SECRET="j" * 40, ENCRYPTION_KEY="e" * 40, ADMIN_USER="root", ADMIN_PASSWORD="admin-password-1",
    DATA_DIR=f"{_tmp}/data", TRAEFIK_DYNAMIC_DIR=f"{_tmp}/dynamic", OVH_MOCK="1", COOKIE_SECURE="0",
    TRAEFIK_ENTRYPOINTS="web,websecure", TRAEFIK_CERT_RESOLVERS="le", DNS_ALLOW_LOOPBACK="1",
)
Path(f"{_tmp}/data").mkdir()
Path(f"{_tmp}/dynamic").mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
