"""Relais des certificats : lit acme.json (que Traefik garde en mode 600) et écrit un relevé SANS clés pour l'interface.

À lancer dans un conteneur à part : acme.json en lecture seule, aucun réseau, système de fichiers en lecture seule, et l'interface
ne voit que le relevé (noms, émetteur, dates). Les clés privées ne quittent donc jamais ce conteneur.
"""
import json
import os
import signal
import sys
import time
from pathlib import Path

from .certs import MAX_FILE, from_acme

SRC = Path(os.environ.get("CERTSYNC_IN", "/certs-in/acme.json"))
DST = Path(os.environ.get("CERTSYNC_OUT", "/certs-out/certs.json"))
EVERY = max(5, int(os.environ.get("CERTSYNC_EVERY", "30")))


def snapshot(src: Path = SRC) -> dict:
    out = {"version": 1, "generated": time.time(), "certs": []}
    try:
        if src.stat().st_size > MAX_FILE:
            raise ValueError("trop gros")
        out["certs"] = from_acme(json.loads(src.read_text(encoding="utf-8")))
    except FileNotFoundError:
        out["error"] = f"{src.name} est introuvable dans le dossier monté pour le relais"
    except PermissionError:
        out["error"] = f"Le relais ne peut pas lire {src.name} (droits)"
    except (OSError, ValueError):
        out["error"] = f"{src.name} est illisible ou vide (Traefik n'a peut-être pas encore obtenu de certificat)"
    return out


def write(data: dict, dst: Path = DST) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, dst)


def main() -> None:
    running = True
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    last = None
    while running:
        data = snapshot()
        if data["certs"] != (last or {}).get("certs") or data.get("error") != (last or {}).get("error") or time.time() - data["generated"] > EVERY * 4:
            write(data)
            print(f"relevé : {len(data['certs'])} certificat(s)" + (f" · {data['error']}" if data.get("error") else ""), flush=True)
            last = data
        time.sleep(EVERY)


if __name__ == "__main__":
    main()
