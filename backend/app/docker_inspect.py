"""Docker : détail d'une image, d'un réseau, d'un volume — et gestion des réseaux (créer, relier, détacher un conteneur).

Même règle que docker_ops : des opérations NOMMÉES, jamais de commande libre ; les valeurs de variables d'environnement ne sortent jamais (noms seulement).

L'INTÉRIEUR d'un volume ne se lit qu'avec un conteneur éphémère (sans réseau, volume monté en lecture seule, toutes les capacités retirées sauf la lecture)
qui exécute un court script de lecture : c'est un accès aux données de l'application, il est donc rangé avec le terminal (droit « docker.exec », préfixe
`d_exec_` : un worker le refuse sans WORKER_ALLOW_EXEC)."""
import ipaddress
import json
import re

from . import docker_api as api
from .docker_api import DockerError, ident
from .traefik_store import StoreError

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
BUILTIN_NETWORKS = ("bridge", "host", "none")   # comme docker_ops (pas d'import : docker_ops charge ce module)
MAX_ENTRIES = 500
MAX_FILE = 256 * 1024


def _list(all_: bool = True) -> list[dict]:
    from . import docker_ops   # importé à l'appel : docker_ops charge ce module
    return docker_ops._list(all_)


def _is_self(cid: str) -> bool:
    from . import docker_ops
    return docker_ops._is_self(cid)


def _short(h: str) -> str:
    return (h or "").replace("sha256:", "")[:12]


# ── images ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def d_image(a: dict) -> dict:
    iid = ident(a.get("id"))
    i = api.request("GET", f"/images/{iid}/json") or {}
    try:
        hist = api.request("GET", f"/images/{iid}/history") or []
    except DockerError:
        hist = []
    cfg = i.get("Config") or {}
    users = sorted(c["name"] for c in _list(True) if c.get("fullId") == i.get("Id") or c.get("imageId") == _short(i.get("Id")))
    return {
        "id": _short(i.get("Id")), "fullId": i.get("Id") or "", "tags": [t for t in i.get("RepoTags") or [] if t != "<none>:<none>"], "digests": i.get("RepoDigests") or [],
        "created": i.get("Created") or "", "size": i.get("Size") or 0, "os": i.get("Os") or "", "arch": i.get("Architecture") or "", "author": i.get("Author") or "",
        "dockerVersion": i.get("DockerVersion") or "", "user": cfg.get("User") or "", "workdir": cfg.get("WorkingDir") or "",
        "entrypoint": cfg.get("Entrypoint") or [], "cmd": cfg.get("Cmd") or [], "exposed": sorted((cfg.get("ExposedPorts") or {}).keys()),
        "volumes": sorted((cfg.get("Volumes") or {}).keys()), "envNames": sorted(e.split("=", 1)[0] for e in cfg.get("Env") or []),   # jamais les valeurs
        "labels": {k: str(v)[:300] for k, v in (cfg.get("Labels") or {}).items()},
        "layers": [{"by": re.sub(r"\s+", " ", str(h.get("CreatedBy") or ""))[:400], "size": h.get("Size") or 0, "created": h.get("Created") or 0, "tags": h.get("Tags") or []} for h in hist],
        "usedBy": users,
    }


# ── réseaux ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def d_network(a: dict) -> dict:
    n = api.request("GET", f"/networks/{ident(a.get('id'))}") or {}
    cfg = ((n.get("IPAM") or {}).get("Config")) or []
    cts = []
    for cid, v in (n.get("Containers") or {}).items():
        cts.append({"id": cid[:12], "name": v.get("Name") or cid[:12], "ip": (v.get("IPv4Address") or "").split("/")[0], "ip6": (v.get("IPv6Address") or "").split("/")[0],
                    "mac": v.get("MacAddress") or "", "self": _is_self(cid)})
    name = n.get("Name") or ""
    return {"id": (n.get("Id") or "")[:12], "name": name, "driver": n.get("Driver") or "", "scope": n.get("Scope") or "", "internal": bool(n.get("Internal")),
            "attachable": bool(n.get("Attachable")), "ipv6": bool(n.get("EnableIPv6")), "builtin": name in BUILTIN_NETWORKS, "created": n.get("Created") or "",
            "subnets": [{"subnet": c.get("Subnet") or "", "gateway": c.get("Gateway") or ""} for c in cfg], "options": n.get("Options") or {},
            "labels": {k: str(v)[:200] for k, v in (n.get("Labels") or {}).items()}, "containers": sorted(cts, key=lambda c: c["name"])}


def d_network_create(a: dict) -> dict:
    name = str(a.get("name") or "").strip()
    if not _NAME.match(name) or name in BUILTIN_NETWORKS:
        raise StoreError("Nom de réseau invalide (lettres, chiffres, « - », « _ » et « . », 63 caractères au plus)", 422)
    body: dict = {"Name": name, "Driver": "bridge", "Internal": bool(a.get("internal")), "Attachable": True, "CheckDuplicate": True}
    subnet = str(a.get("subnet") or "").strip()
    if subnet:
        try:
            net = ipaddress.ip_network(subnet, strict=True)
        except ValueError:
            raise StoreError("Sous-réseau invalide (exemple : 172.30.0.0/24)", 422)
        body["IPAM"] = {"Driver": "default", "Config": [{"Subnet": str(net)}]}
        body["EnableIPv6"] = net.version == 6
    r = api.request("POST", "/networks/create", body=body) or {}
    return {"ok": True, "id": (r.get("Id") or "")[:12], "name": name}


def _aliases(raw) -> list[str]:
    out = [str(x).strip() for x in (raw or []) if str(x).strip()][:10]
    if any(not _NAME.match(x) for x in out):
        raise StoreError("Alias invalide (lettres, chiffres, « - », « _ » et « . »)", 422)
    return out


def d_network_connect(a: dict) -> dict:
    net, ct = str(a.get("network") or ""), str(a.get("container") or "")
    if net in ("host", "none"):
        raise StoreError("Ce réseau spécial ne se partage pas", 409)
    ident(ct)
    body: dict = {"Container": ct}
    al = _aliases(a.get("aliases"))
    if al:
        body["EndpointConfig"] = {"Aliases": al}
    api.request("POST", f"/networks/{ident(net)}/connect", body=body)
    return {"ok": True}


def d_network_disconnect(a: dict) -> dict:
    net, ct = str(a.get("network") or ""), str(a.get("container") or "")
    if _is_self(ct):
        raise StoreError("Cette interface ne se détache pas elle-même d'un réseau : elle s'enfermerait dehors", 409)
    api.request("POST", f"/networks/{ident(net)}/disconnect", body={"Container": ct, "Force": bool(a.get("force"))})
    return {"ok": True}


# ── volumes ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def d_volume(a: dict) -> dict:
    name = str(a.get("id") or "")
    v = api.request("GET", f"/volumes/{ident(name)}") or {}
    users = sorted({c["name"] for c in _list(True) for m in c["mounts"] if m["type"] == "volume" and m["name"] == name})
    return {"name": v.get("Name") or name, "driver": v.get("Driver") or "", "scope": v.get("Scope") or "", "mountpoint": v.get("Mountpoint") or "", "created": v.get("CreatedAt") or "",
            "options": v.get("Options") or {}, "labels": {k: str(x)[:200] for k, x in (v.get("Labels") or {}).items()}, "usedBy": users}


_SCRIPT = r'''
import json, os, sys
base = "/v"
mode, rel = os.environ["MODE"], os.environ.get("P", "")
p = os.path.realpath(os.path.join(base, rel.lstrip("/")))
if p != base and not p.startswith(base + "/"):
    print(json.dumps({"error": "Chemin hors du volume"})); sys.exit()
if mode == "ls":
    if not os.path.isdir(p):
        print(json.dumps({"error": "Ce n'est pas un dossier"})); sys.exit()
    out = []
    with os.scandir(p) as it:
        for e in it:
            try:
                st = e.stat(follow_symlinks=False)
                kind = "dir" if e.is_dir(follow_symlinks=False) else "link" if e.is_symlink() else "file"
                out.append({"name": e.name, "type": kind, "size": st.st_size, "mtime": int(st.st_mtime), "mode": oct(st.st_mode & 0o777), "uid": st.st_uid, "gid": st.st_gid})
            except OSError:
                out.append({"name": e.name, "type": "file", "size": 0, "mtime": 0, "mode": "", "uid": 0, "gid": 0})
    out.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    print(json.dumps({"path": "/" + os.path.relpath(p, base) if p != base else "/", "entries": out[:%(max)d], "more": len(out) > %(max)d}))
else:
    if not os.path.isfile(p):
        print(json.dumps({"error": "Ce n'est pas un fichier"})); sys.exit()
    size = os.path.getsize(p)
    with open(p, "rb") as f:
        data = f.read(%(maxf)d)
    if b"\0" in data[:8192]:
        print(json.dumps({"binary": True, "size": size})); sys.exit()
    print(json.dumps({"size": size, "truncated": size > len(data), "text": data.decode("utf-8", "replace")}))
''' % {"max": MAX_ENTRIES, "maxf": MAX_FILE}


def _helper_image() -> str:
    """Image du conteneur d'aide : celle qui fait tourner cette interface (déjà présente, contient python), sinon une image Python locale."""
    for c in _list(True):
        if _is_self(c["fullId"]):
            return c["image"]
    raise StoreError("Impossible de choisir une image pour lire le volume (l'interface ne retrouve pas son propre conteneur)", 503)


def _run_in_volume(volume: str, mode: str, path: str) -> dict:
    v = api.request("GET", f"/volumes/{ident(volume)}") or {}
    if not v.get("Name"):
        raise StoreError("Volume introuvable", 404)
    cid = ""
    try:
        r = api.request("POST", "/containers/create", body={
            "Image": _helper_image(), "Entrypoint": ["sleep"], "Cmd": ["60"], "User": "0", "NetworkDisabled": True, "Labels": {"armadok.helper": "volume-reader"},
            "HostConfig": {"Binds": [f"{v['Name']}:/v:ro"], "ReadonlyRootfs": True, "CapDrop": ["ALL"], "CapAdd": ["DAC_READ_SEARCH"], "Memory": 96 * 1024 * 1024,
                           "SecurityOpt": ["no-new-privileges:true"], "NetworkMode": "none", "PidsLimit": 64}}, timeout=40) or {}
        cid = r.get("Id") or ""
        api.request("POST", f"/containers/{cid}/start", timeout=30)
        e = api.request("POST", f"/containers/{cid}/exec", body={"AttachStdout": True, "AttachStderr": False, "Tty": True, "User": "0",
                                                                  "Env": [f"MODE={mode}", f"P={path}"], "Cmd": ["python", "-I", "-c", _SCRIPT]}) or {}
        out = api.request("POST", f"/exec/{e.get('Id')}/start", body={"Detach": False, "Tty": True}, raw=True, timeout=40, ok=(200,)) or b""
        txt = out.decode("utf-8", "replace").strip()
        try:
            return json.loads(txt.splitlines()[-1]) if txt else {"error": "Aucune réponse du lecteur de volume"}
        except ValueError:
            raise StoreError("Lecture du volume impossible : " + txt[:160], 502)
    finally:
        if cid:
            try:
                api.request("DELETE", f"/containers/{cid}", {"force": 1, "v": 1}, timeout=20)
            except DockerError:
                pass


def d_exec_volls(a: dict) -> dict:
    r = _run_in_volume(str(a.get("id") or ""), "ls", str(a.get("path") or "")[:1000])
    if r.get("error"):
        raise StoreError(r["error"], 404)
    return r


def d_exec_volread(a: dict) -> dict:
    r = _run_in_volume(str(a.get("id") or ""), "cat", str(a.get("path") or "")[:1000])
    if r.get("error"):
        raise StoreError(r["error"], 404)
    return r



# ── conteneur « lancé à la main » → spec pour un projet compose ─────────────────────────────────────────────────────────────────
def d_spec(a: dict) -> dict:
    """Tout ce qu'il faut pour refaire ce conteneur dans un docker-compose.yml (compose_convert.convert) : la configuration du conteneur, débarrassée de ce que
    l'image fournit d'elle-même. ATTENTION : contrairement à d_container, les VALEURS des variables d'environnement y figurent (elles vont dans un .env) :
    l'appelant doit avoir le droit de lire les .env."""
    i = api.request("GET", f"/containers/{ident(a.get('id'))}/json") or {}
    cfg, hc, ns, st = i.get("Config") or {}, i.get("HostConfig") or {}, i.get("NetworkSettings") or {}, i.get("State") or {}
    try:
        img = api.request("GET", f"/images/{ident(i.get('Image') or '')}/json") or {}
    except DockerError:
        img = {}
    icfg = img.get("Config") or {}
    base_env = {e for e in icfg.get("Env") or []}
    env = [e.split("=", 1) if "=" in e else [e, ""] for e in cfg.get("Env") or [] if e not in base_env]
    base_labels = icfg.get("Labels") or {}
    labels = {k: v for k, v in (cfg.get("Labels") or {}).items() if not k.startswith("com.docker.") and base_labels.get(k) != v}
    ports = []
    for key, binds in (hc.get("PortBindings") or {}).items():
        cport, _, proto = key.partition("/")
        for b in binds or []:
            ports.append({"ip": b.get("HostIp") or "", "host": b.get("HostPort") or "", "container": cport, "proto": proto or "tcp"})
    cid = i.get("Id") or ""
    nets = {}
    for n, v in (ns.get("Networks") or {}).items():
        al = [x for x in (v.get("Aliases") or []) if x != cid[:12] and x != (i.get("Name") or "").lstrip("/") and x != cfg.get("Hostname")]
        nets[n] = {"aliases": al, "ipv4": ((v.get("IPAMConfig") or {}).get("IPv4Address") or "")}
    hcheck = cfg.get("Healthcheck")
    if hcheck == icfg.get("Healthcheck"):
        hcheck = None
    rp = hc.get("RestartPolicy") or {}
    return {
        "name": (i.get("Name") or "").lstrip("/"), "image": cfg.get("Image") or "", "cmd": cfg.get("Cmd") or [], "entrypoint": cfg.get("Entrypoint") or [],
        "imageCmd": icfg.get("Cmd") or [], "imageEntrypoint": icfg.get("Entrypoint") or [], "env": env, "labels": labels, "user": cfg.get("User") or "",
        "workdir": cfg.get("WorkingDir") if cfg.get("WorkingDir") != icfg.get("WorkingDir") else "", "hostname": cfg.get("Hostname") if cfg.get("Hostname") not in (cid[:12], "") else "",
        "domainname": cfg.get("Domainname") or "", "tty": bool(cfg.get("Tty")), "stdinOpen": bool(cfg.get("OpenStdin")), "stopSignal": cfg.get("StopSignal") or "",
        "stopTimeout": cfg.get("StopTimeout") or 0, "restart": {"name": rp.get("Name") or "no", "max": rp.get("MaximumRetryCount") or 0}, "ports": ports,
        "networkMode": hc.get("NetworkMode") if hc.get("NetworkMode") not in ("default", "bridge", "") else "",
        "mounts": [{"type": m.get("Type"), "name": m.get("Name") or "", "source": m.get("Source") or "", "destination": m.get("Destination"), "rw": bool(m.get("RW"))} for m in i.get("Mounts") or []],
        "tmpfs": sorted((hc.get("Tmpfs") or {}).keys()), "networks": nets, "privileged": bool(hc.get("Privileged")), "capAdd": hc.get("CapAdd") or [], "capDrop": hc.get("CapDrop") or [],
        "devices": [f"{d.get('PathOnHost')}:{d.get('PathInContainer')}:{d.get('CgroupPermissions') or 'rwm'}" for d in hc.get("Devices") or []],
        "extraHosts": hc.get("ExtraHosts") or [], "dns": hc.get("Dns") or [], "dnsSearch": hc.get("DnsSearch") or [], "securityOpt": hc.get("SecurityOpt") or [],
        "groupAdd": hc.get("GroupAdd") or [], "sysctls": hc.get("Sysctls") or {}, "readOnly": bool(hc.get("ReadonlyRootfs")), "init": bool(hc.get("Init")),
        "pid": hc.get("PidMode") or "", "ipc": hc.get("IpcMode") if hc.get("IpcMode") not in ("", "private", "shareable") else "", "shmSize": hc.get("ShmSize") if hc.get("ShmSize") not in (None, 67108864) else 0,
        "memory": hc.get("Memory") or 0, "memoryReservation": hc.get("MemoryReservation") or 0, "nanoCpus": hc.get("NanoCpus") or 0, "pidsLimit": hc.get("PidsLimit") or 0,
        "healthcheck": hcheck, "logConfig": hc.get("LogConfig") or {}, "ulimits": hc.get("Ulimits") or [], "links": hc.get("Links") or [], "volumesFrom": hc.get("VolumesFrom") or [],
        "composeProject": (cfg.get("Labels") or {}).get("com.docker.compose.project", ""), "running": bool(st.get("Running")), "self": _is_self(cid), "fullId": cid,
    }


def d_rename(a: dict) -> dict:
    """Renomme un conteneur (sert à garder l'ancien sous un autre nom quand un projet reprend le sien)."""
    new = str(a.get("name") or "")
    if not _NAME.match(new):
        raise StoreError("Nom de conteneur invalide", 422)
    api.request("POST", f"/containers/{ident(a.get('id'))}/rename", {"name": new})
    return {"ok": True, "name": new}


OPS = {"d_spec": d_spec, "d_rename": d_rename, "d_image": d_image, "d_network": d_network, "d_network_create": d_network_create, "d_network_connect": d_network_connect,
       "d_network_disconnect": d_network_disconnect, "d_volume": d_volume, "d_exec_volls": d_exec_volls, "d_exec_volread": d_exec_volread}
