"""Opérations Docker NOMMÉES d'un nœud : conteneurs, journaux, statistiques, images, réseaux, volumes.

Le hub les exécute directement pour son propre nœud ; un worker les reçoit par le canal chiffré (liste fermée dans worker_proto.OPS_BY_MODULE["docker"],
jamais de commande libre). Les résultats sont du JSON déjà « nettoyé » : les valeurs de variables d'environnement n'en sortent jamais (noms seulement)."""
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import docker_api as api
from . import docker_exec
from .docker_api import DockerError, ident
from .traefik_store import StoreError

KEEP_LABELS = ("traefik.", "com.docker.compose.")   # les seuls labels dont l'interface a besoin dans les listes
ACTIONS = ("start", "stop", "restart")
BUILTIN_NETWORKS = ("bridge", "host", "none")
MAX_LOG_LINES = 2000
_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?Z ")


def _self_ids() -> tuple[str, ...]:
    """Début de l'identifiant du conteneur qui exécute l'interface (son nom d'hôte, sauf réglage particulier)."""
    h = socket.gethostname()
    return (h,) if re.fullmatch(r"[0-9a-f]{12,64}", h) else ()


def _is_self(cid: str) -> bool:
    return any(cid.startswith(h) or h.startswith(cid[:12]) for h in _self_ids())


def _name(c: dict) -> str:
    return ((c.get("Names") or ["/?"])[0] or "/?").lstrip("/")


def _ports(raw: list) -> list[dict]:
    seen, out = set(), []
    for p in raw or []:
        key = (p.get("PublicPort"), p.get("PrivatePort"), p.get("Type"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"ip": p.get("IP") or "", "private": p.get("PrivatePort"), "public": p.get("PublicPort"), "type": p.get("Type") or "tcp"})
    return sorted(out, key=lambda p: (p["private"] or 0, p["public"] or 0))


def norm_container(c: dict) -> dict:
    labels = {k: v for k, v in (c.get("Labels") or {}).items() if k.startswith(KEEP_LABELS)}
    nets = ((c.get("NetworkSettings") or {}).get("Networks")) or {}
    status = c.get("Status") or ""
    cid = c.get("Id") or ""
    return {
        "id": cid[:12], "fullId": cid, "name": _name(c), "image": c.get("Image") or "", "imageId": (c.get("ImageID") or "").replace("sha256:", "")[:12],
        "state": c.get("State") or "", "status": status, "created": c.get("Created") or 0,
        "health": "healthy" if "(healthy)" in status else "unhealthy" if "(unhealthy)" in status else "starting" if "(health: starting)" in status else "",
        "ports": _ports(c.get("Ports")),
        "networks": {n: {"ip": (v or {}).get("IPAddress") or "", "aliases": (v or {}).get("Aliases") or []} for n, v in nets.items()},
        "labels": labels,
        "project": labels.get("com.docker.compose.project", ""), "service": labels.get("com.docker.compose.service", ""),
        "workingDir": labels.get("com.docker.compose.project.working_dir", ""), "configFiles": labels.get("com.docker.compose.project.config_files", ""),
        "mounts": [{"type": m.get("Type"), "name": m.get("Name") or "", "source": m.get("Source") or "", "destination": m.get("Destination"), "rw": bool(m.get("RW"))} for m in c.get("Mounts") or []],
        "self": _is_self(cid),
    }


def _list(all_: bool = True) -> list[dict]:
    return sorted((norm_container(c) for c in api.request("GET", "/containers/json", {"all": 1 if all_ else 0}) or []), key=lambda c: c["name"])


# ------------------------------------------------------------------ conteneurs
def d_info(_a: dict) -> dict:
    v = api.request("GET", "/version") or {}
    i = api.request("GET", "/info") or {}
    return {"version": v.get("Version", ""), "api": v.get("ApiVersion", ""), "os": i.get("OperatingSystem", ""), "kernel": i.get("KernelVersion", ""),
            "hostname": i.get("Name", ""), "containers": i.get("Containers", 0), "running": i.get("ContainersRunning", 0), "images": i.get("Images", 0),
            "cpus": i.get("NCPU", 0), "memory": i.get("MemTotal", 0)}


def d_containers(a: dict) -> dict:
    return {"containers": _list(bool(a.get("all", True)))}


def d_container(a: dict) -> dict:
    i = api.request("GET", f"/containers/{ident(a.get('id'))}/json") or {}
    cfg, st, hc, ns = i.get("Config") or {}, i.get("State") or {}, i.get("HostConfig") or {}, i.get("NetworkSettings") or {}
    ports = []
    for key, binds in (ns.get("Ports") or {}).items():
        priv, _, typ = key.partition("/")
        for b in binds or [None]:
            ports.append({"private": int(priv) if priv.isdigit() else priv, "type": typ or "tcp", "ip": (b or {}).get("HostIp", ""), "public": int((b or {}).get("HostPort") or 0) or None})
    cid = i.get("Id") or ""
    return {
        "id": cid[:12], "fullId": cid, "name": (i.get("Name") or "").lstrip("/"), "image": cfg.get("Image") or "", "imageId": (i.get("Image") or "").replace("sha256:", "")[:12],
        "command": " ".join((cfg.get("Entrypoint") or []) + (cfg.get("Cmd") or [])), "user": cfg.get("User") or "", "workdir": cfg.get("WorkingDir") or "",
        "created": i.get("Created") or "", "started": st.get("StartedAt") or "", "finished": st.get("FinishedAt") or "",
        "state": st.get("Status") or "", "exitCode": st.get("ExitCode"), "oom": bool(st.get("OOMKilled")), "restartCount": i.get("RestartCount", 0),
        "health": ((st.get("Health") or {}).get("Status")) or "", "restart": (hc.get("RestartPolicy") or {}).get("Name") or "no", "tty": bool(cfg.get("Tty")),
        "memoryLimit": hc.get("Memory") or 0, "cpus": (hc.get("NanoCpus") or 0) / 1e9, "privileged": bool(hc.get("Privileged")),
        "envNames": sorted(e.split("=", 1)[0] for e in cfg.get("Env") or []),   # jamais les valeurs
        "mounts": [{"type": m.get("Type"), "name": m.get("Name") or "", "source": m.get("Source") or "", "destination": m.get("Destination"), "rw": bool(m.get("RW"))} for m in i.get("Mounts") or []],
        "networks": {n: {"ip": v.get("IPAddress") or "", "gateway": v.get("Gateway") or "", "aliases": v.get("Aliases") or []} for n, v in (ns.get("Networks") or {}).items()},
        "networkMode": hc.get("NetworkMode") or "", "ports": ports, "labels": cfg.get("Labels") or {}, "self": _is_self(cid),
    }


def d_action(a: dict) -> dict:
    action, cid = str(a.get("action")), ident(a.get("id"))
    if action not in ACTIONS:
        raise DockerError("Action non autorisée", 422)
    if action == "stop" and _is_self(str(a.get("id"))):
        raise DockerError("Ce conteneur est celui de l'interface : l'arrêter la couperait sans moyen de la relancer d'ici (utilisez « Redémarrer »)", 409)
    r = api.request("POST", f"/containers/{cid}/{action}", {"t": 10} if action != "start" else None, timeout=40)
    return {"ok": True, "unchanged": bool(r and r.get("notModified"))}


def d_remove_container(a: dict) -> dict:
    if _is_self(str(a.get("id"))):
        raise DockerError("Ce conteneur est celui de l'interface : il ne peut pas se supprimer lui-même", 409)
    api.request("DELETE", f"/containers/{ident(a.get('id'))}", {"force": 1 if a.get("force") else 0, "v": 1 if a.get("volumes") else 0}, timeout=40)
    return {"ok": True}


def _ts(raw: str) -> float:
    m = _TS.match(raw)
    if not m:
        return 0.0
    frac = (m.group(2) or ".0")[:7]
    return datetime.strptime(m.group(1) + frac, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc).timestamp()


def d_logs(a: dict) -> dict:
    tail = max(1, min(int(a.get("tail") or 300), MAX_LOG_LINES))
    since = float(a.get("since") or 0)
    params = {"stdout": 1, "stderr": 1, "timestamps": 1, "tail": tail}
    if since:
        params["since"] = int(since)
    data = api.request("GET", f"/containers/{ident(a.get('id'))}/logs", params, raw=True, timeout=25)
    lines = []
    for stream, text in api.demux(data or b""):
        for line in text.splitlines():
            ts = _ts(line)
            if since and ts and ts <= since:
                continue
            m = _TS.match(line)
            lines.append({"ts": ts, "s": stream, "m": (line[m.end():] if m else line)[:4000]})
    lines = lines[-MAX_LOG_LINES:]
    return {"lines": lines, "since": max([l["ts"] for l in lines] + [since])}


_stat_cache: dict = {"at": 0.0, "key": None, "val": None}


def _stat(cid: str) -> tuple[str, dict | None]:
    try:
        s = api.request("GET", f"/containers/{cid}/stats", {"stream": 0}, timeout=12) or {}
    except DockerError:
        return cid, None
    cpu, pre = s.get("cpu_stats") or {}, s.get("precpu_stats") or {}
    d_cpu = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (pre.get("cpu_usage") or {}).get("total_usage", 0)
    d_sys = (cpu.get("system_cpu_usage") or 0) - (pre.get("system_cpu_usage") or 0)
    ncpu = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    mem = s.get("memory_stats") or {}
    stats = mem.get("stats") or {}
    used = (mem.get("usage") or 0) - (stats.get("inactive_file") if "inactive_file" in stats else stats.get("cache", 0))
    nets = s.get("networks") or {}
    return cid, {"cpu": round(d_cpu / d_sys * ncpu * 100, 2) if d_sys > 0 and d_cpu >= 0 else 0.0, "mem": max(used, 0), "memLimit": mem.get("limit") or 0,
                 "netRx": sum(n.get("rx_bytes", 0) for n in nets.values()), "netTx": sum(n.get("tx_bytes", 0) for n in nets.values())}


def d_stats(a: dict) -> dict:
    ids = [str(i) for i in (a.get("ids") or [c["id"] for c in _list(False)])][:80]
    key = tuple(sorted(ids))
    if _stat_cache["key"] == key and time.time() - _stat_cache["at"] < 4:
        return _stat_cache["val"]
    with ThreadPoolExecutor(max_workers=10) as pool:
        res = {cid: st for cid, st in pool.map(lambda i: _stat(ident(i)), ids) if st}
    out = {"stats": res}
    _stat_cache.update(at=time.time(), key=key, val=out)
    return out


# ------------------------------------------------------------------ images, réseaux, volumes
def d_images(_a: dict) -> dict:
    users: dict[str, list[str]] = {}
    for c in api.request("GET", "/containers/json", {"all": 1}) or []:
        users.setdefault(c.get("ImageID") or "", []).append(_name(c))
    out = []
    for i in api.request("GET", "/images/json") or []:
        tags = [t for t in i.get("RepoTags") or [] if t != "<none>:<none>"]
        out.append({"id": (i.get("Id") or "").replace("sha256:", "")[:12], "fullId": i.get("Id") or "", "tags": tags, "size": i.get("Size") or 0,
                    "created": i.get("Created") or 0, "digests": i.get("RepoDigests") or [], "usedBy": sorted(users.get(i.get("Id") or "", [])), "dangling": not tags})
    return {"images": sorted(out, key=lambda i: (i["tags"][0] if i["tags"] else "~" + i["id"]))}


def d_networks(_a: dict) -> dict:
    users: dict[str, list[str]] = {}
    for c in _list(True):
        for n in c["networks"]:
            users.setdefault(n, []).append(c["name"])
    out = []
    for n in api.request("GET", "/networks") or []:
        cfg = ((n.get("IPAM") or {}).get("Config")) or []
        out.append({"id": (n.get("Id") or "")[:12], "name": n.get("Name") or "", "driver": n.get("Driver") or "", "scope": n.get("Scope") or "",
                    "internal": bool(n.get("Internal")), "subnets": [c.get("Subnet") for c in cfg if c.get("Subnet")], "builtin": n.get("Name") in BUILTIN_NETWORKS,
                    "usedBy": sorted(users.get(n.get("Name") or "", []))})
    return {"networks": sorted(out, key=lambda n: n["name"])}


def d_volumes(_a: dict) -> dict:
    users: dict[str, list[str]] = {}
    for c in _list(True):
        for m in c["mounts"]:
            if m["type"] == "volume" and m["name"]:
                users.setdefault(m["name"], []).append(c["name"])
    vols = (api.request("GET", "/volumes") or {}).get("Volumes") or []
    return {"volumes": sorted(({"name": v.get("Name") or "", "driver": v.get("Driver") or "", "mountpoint": v.get("Mountpoint") or "", "created": v.get("CreatedAt") or "",
                                "labels": {k: x for k, x in (v.get("Labels") or {}).items() if k.startswith("com.docker.compose.")}, "usedBy": sorted(users.get(v.get("Name") or "", []))}
                               for v in vols), key=lambda v: v["name"])}


def d_remove_image(a: dict) -> dict:
    api.request("DELETE", f"/images/{ident(a.get('id'))}", {"force": 1 if a.get("force") else 0}, timeout=40)
    return {"ok": True}


def d_remove_network(a: dict) -> dict:
    name = str(a.get("id") or "")
    if name in BUILTIN_NETWORKS:
        raise DockerError("Réseau intégré à Docker : il ne se supprime pas", 409)
    api.request("DELETE", f"/networks/{ident(name)}")
    return {"ok": True}


def d_remove_volume(a: dict) -> dict:
    api.request("DELETE", f"/volumes/{ident(a.get('id'))}", {"force": 1 if a.get("force") else 0})
    return {"ok": True}


def d_prune(a: dict) -> dict:
    what = str(a.get("what"))
    if what == "containers":
        r = api.request("POST", "/containers/prune", timeout=60) or {}
    elif what == "images":
        r = api.request("POST", "/images/prune", {"filters": '{"dangling":["false"]}' if a.get("all") else '{"dangling":["true"]}'}, timeout=120) or {}
    elif what == "networks":
        r = api.request("POST", "/networks/prune", timeout=60) or {}
    elif what == "volumes":
        r = api.request("POST", "/volumes/prune", {"filters": '{"all":["true"]}'}, timeout=120) or {}
    else:
        raise DockerError("Type de nettoyage inconnu", 422)
    deleted = r.get("ContainersDeleted") or r.get("ImagesDeleted") or r.get("NetworksDeleted") or r.get("VolumesDeleted") or []
    return {"deleted": len(deleted), "spaceReclaimed": r.get("SpaceReclaimed", 0)}


OPS = {"d_info": d_info, "d_containers": d_containers, "d_container": d_container, "d_action": d_action, "d_remove_container": d_remove_container,
       "d_logs": d_logs, "d_stats": d_stats, "d_images": d_images, "d_networks": d_networks, "d_volumes": d_volumes,
       "d_remove_image": d_remove_image, "d_remove_network": d_remove_network, "d_remove_volume": d_remove_volume, "d_prune": d_prune, **docker_exec.OPS}


def handle(op: str, a: dict):
    """Exécute UNE opération Docker nommée. Les erreurs Docker deviennent des StoreError (même forme que le reste du protocole worker)."""
    fn = OPS.get(op)
    if not fn or not isinstance(a, dict):
        raise StoreError("Opération non autorisée", 400)
    try:
        return fn(a)
    except DockerError as e:
        raise StoreError(str(e), e.status)
