"""Journal d'accès de Traefik, lu par le WORKER et envoyé au hub (le hub ne peut pas lire un fichier d'une autre machine).

Le worker lit le fichier au fil de l'eau (TRAEFIK_ACCESS_LOG, monté en lecture seule), garde sa position sur disque (rotation et troncature gérées comme
au hub) et remet les lignes brutes au hub, qui les analyse et les range sous l'identifiant du nœud. Rien n'est conservé ici hormis la position de lecture."""
import hashlib
import json
import os
from pathlib import Path

from .config import settings

BATCH_LINES = 1500            # lignes par message (le hub en accepte 2000)
BATCH_BYTES = 1_500_000
FIRST_READ = 2_000_000        # première lecture : les derniers 2 Mo seulement (comme au hub)
MAX_LINE = 8192


def available() -> bool:
    p = settings.access_log
    return bool(p) and os.path.isfile(p) and os.access(p, os.R_OK)


def _state_path() -> Path:
    return settings.data_dir / "access-offset.json"


def _load() -> dict:
    try:
        d = json.loads(_state_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(inode: int, off: int, head: str) -> None:
    try:
        tmp = _state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps({"inode": inode, "off": off, "head": head}))
        os.replace(tmp, _state_path())
    except OSError:
        pass


def _head(f) -> str:
    pos = f.tell()
    try:
        f.seek(0)
        b = f.read(200)
    finally:
        f.seek(pos)
    return hashlib.sha1(b).hexdigest() if len(b) >= 200 else ""


class Reader:
    """Lit les nouvelles lignes ; `commit()` n'enregistre la position qu'après l'envoi réussi."""

    def __init__(self) -> None:
        self._pending: tuple[int, int, str] | None = None

    def read(self) -> list[str]:
        path = settings.access_log
        self._pending = None
        if not available():
            return []
        try:
            with open(path, "rb") as f:
                st = os.fstat(f.fileno())
                saved = _load()
                head = _head(f)
                if saved.get("inode") == st.st_ino and saved.get("off", 0) <= st.st_size and (not saved.get("head") or not head or saved["head"] == head):
                    f.seek(int(saved.get("off", 0)))
                elif saved.get("inode") == st.st_ino and head and saved.get("head") and saved["head"] != head:
                    f.seek(0)                                # même fichier, autre contenu : remplacé, on relit depuis le début
                elif saved.get("inode") == st.st_ino:
                    f.seek(0)
                else:                                        # première lecture ou fichier tourné
                    f.seek(max(0, st.st_size - FIRST_READ) if not saved else 0)
                    if f.tell():
                        f.readline()
                lines, size = [], 0
                while len(lines) < BATCH_LINES and size < BATCH_BYTES:
                    pos = f.tell()
                    raw = f.readline()
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):              # ligne en cours d'écriture : on la reprendra
                        f.seek(pos)
                        break
                    size += len(raw)
                    if len(raw) <= MAX_LINE:
                        lines.append(raw.decode("utf-8", "ignore").rstrip("\n"))
                self._pending = (st.st_ino, f.tell(), head or _head(f))
                return lines
        except OSError:
            return []

    def commit(self) -> None:
        if self._pending:
            _save(*self._pending)
            self._pending = None
