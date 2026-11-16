"""Détection de changements sur disque (sans dépendance) : empreinte d'un dossier, comparée d'un passage à l'autre."""
import os
from pathlib import Path


def snapshot(root: Path) -> tuple:
    """((chemin relatif, mtime_ns, taille), …) des fichiers sous `root` ; les dossiers et fichiers cachés (sauvegardes, .tmp) sont ignorés."""
    out = []
    try:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in sorted(files):
                if f.startswith("."):
                    continue
                p = os.path.join(dirpath, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out.append((os.path.relpath(p, root), st.st_mtime_ns, st.st_size))
    except OSError:
        pass
    return tuple(out)


def stamp(path) -> tuple:
    """(mtime_ns, taille) d'un fichier, ou () s'il n'existe pas."""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except (OSError, TypeError):
        return ()
