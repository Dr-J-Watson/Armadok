"""Clés SSH de déploiement pour Git : une paire Ed25519 générée ICI (le hub), dont la partie publique se donne au dépôt (« deploy key »).

La clé privée est chiffrée en base ; elle n'est jamais renvoyée au navigateur. Elle n'est écrite sur disque (fichier 0600, dossier personnel inscriptible de
la machine qui exécute git) que le temps d'une commande git, puis effacée."""
import base64
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HEADER = "-----BEGIN OPENSSH PRIVATE KEY-----"


def generate(comment: str = "armadok") -> tuple[str, str, str]:
    """(clé privée au format OpenSSH, clé publique « ssh-ed25519 AAAA… commentaire », empreinte « SHA256:… »)."""
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    comment = "".join(c for c in comment if c.isalnum() or c in "._-@")[:60]
    public = f"{pub} {comment}".strip()
    return private, public, fingerprint(public)


def fingerprint(public: str) -> str:
    try:
        blob = base64.b64decode(public.split()[1])
    except (IndexError, ValueError):
        return ""
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def looks_valid(private: str) -> bool:
    return isinstance(private, str) and private.startswith(HEADER) and len(private) <= 4096
