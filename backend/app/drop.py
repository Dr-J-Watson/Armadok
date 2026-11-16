"""Lanceur « PUID/PGID » : démarré en root, donne /data à cet utilisateur puis abandonne ses droits avant d'exécuter la commande.

Usage : python -m app.drop COMMANDE [ARGS…]. Sans PUID (ou hors root), la commande est lancée telle quelle.
PGROUPS (facultatif) : groupes supplémentaires à conserver, séparés par des virgules (ex. le groupe « docker » pour joindre le socket).
Utile quand l'identité du worker vit dans un volume nommé (Portainer) : on ne sait pas d'avance à quel utilisateur il appartiendra.
"""
import os
import sys


def own(path: str, uid: int, gid: int) -> None:
    os.chown(path, uid, gid)
    for root, dirs, files in os.walk(path):
        for n in dirs + files:
            try:
                os.chown(os.path.join(root, n), uid, gid, follow_symlinks=False)
            except OSError:
                pass


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit("usage : python -m app.drop COMMANDE [ARGS…]")
    puid = os.environ.get("PUID", "")
    if os.getuid() == 0 and puid:
        uid, gid = int(puid), int(os.environ.get("PGID") or puid)
        data = os.environ.get("DATA_DIR", "/data")
        os.makedirs(data, exist_ok=True)
        own(data, uid, gid)
        os.setgroups([int(g) for g in os.environ.get("PGROUPS", "").replace(" ", "").split(",") if g.isdigit()])
        os.setgid(gid)
        os.setuid(uid)
        os.environ.setdefault("HOME", "/tmp")
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main(sys.argv[1:])
