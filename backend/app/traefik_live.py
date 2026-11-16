"""Statut en direct via l'API Traefik d'un nœud (optionnel : l'URL se règle dans les réglages du nœud)."""
import json
import time
import urllib.request

_cache: dict[str, tuple[float, str, dict]] = {}


def _get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=2) as r:  # noqa: S310 (URL fournie par l'administrateur)
        return json.load(r)


def _remote(node: dict) -> dict:
    from . import worker_link
    hit = _cache.get(node["id"])
    if hit and time.time() - hit[0] < 5:
        return hit[2]
    try:
        data = worker_link.remote_store(node["id"]).live()
    except Exception as e:  # worker hors ligne ou en erreur : le statut en direct est facultatif
        data = {"available": False, "error": str(e)[:200]}
    _cache[node["id"]] = (time.time(), "", data)
    return data


def live_status(node: dict) -> dict:
    if node.get("kind") == "remote":
        return _remote(node) if node.get("connection") == "online" else {"available": False}
    base = (node.get("apiUrl") or "").rstrip("/")
    if not base:
        return {"available": False}
    hit = _cache.get(node["id"])
    if hit and time.time() - hit[0] < 5 and hit[1] == base:
        return hit[2]
    try:
        routers = {}
        for proto in ("http", "tcp", "udp"):
            try:
                for r in _get(base, f"/api/{proto}/routers?per_page=1000"):
                    routers[f"{proto}/{r['name']}"] = {"status": r.get("status"), "provider": r.get("provider"),
                                                       "error": r.get("error")}
            except Exception:
                pass
        data = {"available": True, "version": _get(base, "/api/version").get("Version"),
                "entrypoints": [e["name"] for e in _get(base, "/api/entrypoints")], "routers": routers}
    except Exception as e:
        data = {"available": False, "error": str(e)}
    _cache[node["id"]] = (time.time(), base, data)
    return data


def forget(node_id: str) -> None:
    _cache.pop(node_id, None)
