"""Journal d'audit : une ligne JSON par action, chaînée par HMAC pour que toute modification soit détectable.

Chaque ligne porte `p` (la signature de la précédente) et `h` = HMAC(clé, p | ligne). Supprimer, modifier ou insérer une
ligne casse la chaîne. La signature de la dernière ligne et le nombre de lignes sont aussi gardés dans la base : retirer la
fin du fichier est détecté. Sans la clé (ENCRYPTION_KEY, à défaut JWT_SECRET), on ne peut pas refaire une chaîne valide.
Les lignes écrites avant l'existence de la chaîne restent lisibles ; elles sont comptées comme « non protégées »."""
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from . import db
from .config import env, settings

_lock = threading.Lock()
_state: dict | None = None  # {"h": dernière signature, "n": lignes chaînées}
GENESIS = "genesis"
MAX_BYTES = max(1, int(env("AUDIT_MAX_MB", "50") or 50)) * 1024 * 1024   # au-delà, le fichier est archivé (audit-000001.log…) et la chaîne continue dans un fichier neuf
MAX_SCAN = 300_000   # lignes relues au plus pour une consultation filtrée : la page « Activité » ne peut pas saturer le serveur
failures = 0         # écritures du journal qui ont échoué (visible dans la vérification)


def _key() -> bytes:
    return hashlib.sha256(("ovh-webui-audit:" + (env("ENCRYPTION_KEY") or settings.jwt_secret)).encode()).digest()


def _sign(prev: str, entry: dict) -> str:
    body = json.dumps({k: v for k, v in entry.items() if k not in ("h",)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hmac.new(_key(), f"{prev}|{body}".encode(), hashlib.sha256).hexdigest()


def _path():
    return settings.data_dir / "audit.log"


def _archives() -> list[Path]:
    return sorted(settings.data_dir.glob("audit-[0-9]*.log"))


def _files() -> list[Path]:
    """Du plus ancien au plus récent : les archives, puis le fichier courant."""
    return [*_archives(), _path()]


def _lines(path: Path) -> Iterator[str]:
    """Lignes d'un fichier, une à une (jamais tout le fichier en mémoire)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                yield line.rstrip("\n")
    except OSError:
        return


def _lines_reversed(path: Path, chunk: int = 65536) -> Iterator[str]:
    """Lignes d'un fichier de la dernière à la première, en lisant par blocs depuis la fin."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos, rest = f.tell(), b""
            while pos > 0:
                step = min(chunk, pos)
                pos -= step
                f.seek(pos)
                rest = f.read(step) + rest
                parts = rest.split(b"\n")
                rest = parts[0]
                for raw in reversed(parts[1:]):
                    if raw:
                        yield raw.decode("utf-8", "replace")
            if rest:
                yield rest.decode("utf-8", "replace")
    except OSError:
        return


def _rotate_if_needed() -> None:
    p = _path()
    try:
        if p.stat().st_size < MAX_BYTES:
            return
    except OSError:
        return
    dest = settings.data_dir / f"audit-{len(_archives()) + 1:06d}.log"
    p.rename(dest)
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass


def _last_signature() -> str | None:
    """Signature de la dernière ligne chaînée du journal (lue depuis la fin : instantané, même pour un gros fichier)."""
    for path in reversed(_files()):
        for line in _lines_reversed(path):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if "h" in e:
                return e["h"]
    return None


def _load_state() -> dict:
    try:
        head = db.one("SELECT h, n FROM audit_head WHERE id=1")
        if head and head["h"] == _last_signature():   # la tête enregistrée colle au fichier : inutile de tout relire au démarrage
            return {"h": head["h"], "n": head["n"]}
    except Exception:  # noqa: BLE001 - base pas encore prête
        pass
    h, n = GENESIS, 0
    for path in _files():
        for line in _lines(path):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if "h" in e:
                h, n = e["h"], n + 1
    return {"h": h, "n": n}


def _save_head(state: dict) -> None:
    try:
        db.run("INSERT INTO audit_head(id,h,n) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET h=excluded.h, n=excluded.n", (state["h"], state["n"]))
    except Exception:  # base pas encore prête : la tête sera réécrite à la prochaine ligne
        pass


def audit(user: str, action: str, detail: str = "", node: str | None = None, session: int | None = None) -> None:
    global _state
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "user": user, "action": action, "detail": detail}
    if node:
        entry["node"] = node
    if session:
        entry["session"] = session  # action faite pendant un accès d'urgence : rattachée à l'avis envoyé au propriétaire
    try:
        with _lock:
            if _state is None:
                _state = _load_state()
            entry["p"] = _state["h"]
            entry["h"] = _sign(_state["h"], entry)
            _rotate_if_needed()
            with open(_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            _state = {"h": entry["h"], "n": _state["n"] + 1}
            _save_head(_state)
    except OSError as e:
        global failures
        failures += 1   # une action sans trace ne doit pas passer inaperçue : le journal du serveur et la vérification le disent
        print(f"ERREUR AUDIT : écriture impossible ({e.strerror or e}) : l'action « {action} » n'est pas dans le journal", file=sys.stderr)
    try:
        from .events import on_audit   # temps réel : ce que cette action change à l'écran
        on_audit(action, node)
    except Exception:  # noqa: BLE001 - le journal ne doit jamais échouer à cause du temps réel
        pass


def reset_cache() -> None:
    """Pour les tests : oublie l'état en mémoire (il sera relu depuis le fichier)."""
    global _state
    _state = None


def _legacy_node_action(e: dict) -> bool:
    """Action sur Traefik écrite avant les nœuds (sans identifiant de nœud) : elle concerne le nœud principal."""
    return "node" not in e and str(e.get("action", "")).startswith("traefik.")


def tail(n: int = 200, node: str | None = None, include_unscoped: bool = False, session: int | None = None,
         platform: bool = False) -> list[dict]:
    """Dernières entrées (la plus récente d'abord). `node` filtre sur un nœud ; `include_unscoped` y ajoute les anciennes
    actions Traefik sans nœud. `platform` ne garde que les événements de la plateforme (connexions, comptes, DNS…), liés à aucun nœud.
    Le fichier est lu depuis la fin, par blocs, et au plus MAX_SCAN lignes : la mémoire et le temps restent bornés quelle que soit sa taille."""
    out, scanned = [], 0
    for path in reversed(_files()):
        for line in _lines_reversed(path):
            scanned += 1
            if scanned > MAX_SCAN:
                return out
            try:
                e = json.loads(line)
            except ValueError:
                continue
            e.pop("h", None)
            e.pop("p", None)
            if platform and ("node" in e or _legacy_node_action(e)):
                continue
            if node is not None and e.get("node") != node and not (include_unscoped and _legacy_node_action(e)):
                continue
            if session is not None and e.get("session") != session:
                continue
            out.append(e)
            if len(out) >= n:
                return out
    return out


def verify() -> dict:
    """Relit tout le journal (archives comprises), en flux. {ok, entries, chained, legacy, problem: {line, reason} | None, files, failures}."""
    prev, chained, legacy, problem, total = GENESIS, 0, 0, None, 0
    files = _files()
    for path in files:
        for line in _lines(path):
            total += 1
            try:
                e = json.loads(line)
            except ValueError:
                problem = problem or {"line": total, "reason": "ligne illisible"}
                continue
            if "h" not in e:
                if chained:
                    problem = problem or {"line": total, "reason": "ligne ajoutée sans signature"}
                else:
                    legacy += 1
                continue
            if e.get("p") != prev:
                problem = problem or {"line": total, "reason": "chaînage rompu : une ligne a été supprimée, déplacée ou insérée"}
            elif not hmac.compare_digest(_sign(e.get("p", ""), e), e["h"]):
                problem = problem or {"line": total, "reason": "contenu modifié"}
            prev, chained = e["h"], chained + 1
    head = None
    try:
        head = db.one("SELECT h, n FROM audit_head WHERE id=1")
    except Exception:
        pass
    if not problem and head:
        if head["n"] > chained:
            problem = {"line": total, "reason": f"journal tronqué : {head['n'] - chained} ligne(s) manquante(s) à la fin"}
        elif head["n"] == chained and head["h"] != prev:
            problem = {"line": total, "reason": "la dernière ligne ne correspond pas à la signature enregistrée"}
    return {"ok": problem is None, "entries": total, "chained": chained, "legacy": legacy, "problem": problem,
            "files": len([f for f in files if f.exists()]), "failures": failures}
