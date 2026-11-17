"""Conteneur lancé « à la main » (docker run) → projet docker compose : un service et un .env.

Fonction pure : prend le « spec » d'un conteneur (voir docker_inspect.d_spec : ce que `docker inspect` dit, déjà débarrassé de ce que l'image fournit
d'elle-même) et produit le texte du `docker-compose.yml` et du `.env`. Les variables d'environnement vont dans le `.env` (le compose n'y fait que
référence, `${NOM}`) ; les volumes nommés sont repris en `external` pour garder les données. Tout ce qui ne se traduit pas sans perte est signalé dans
`warnings`, jamais ignoré en silence."""
import re
from datetime import date
from io import StringIO

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

_PLAIN = re.compile(r"^[A-Za-z0-9_./:@%+=,-]*$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def service_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9_-]", "-", str(name or "").lower().lstrip("/")).strip("-_")
    return n[:60] or "app"


def env_value(v: str) -> str:
    """Valeur écrite dans un .env lu par Docker Compose : brute si elle est simple, entre apostrophes sinon (rien n'y est interprété)."""
    if _PLAIN.match(v):
        return v
    if "'" not in v and "\n" not in v and "\r" not in v:
        return f"'{v}'"
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "") + '"'


def _seq(items) -> CommentedSeq:
    s = CommentedSeq(items)
    return s


def _ns_to_s(ns) -> str:
    try:
        n = int(ns)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    s = n / 1e9
    return f"{int(s)}s" if s == int(s) else f"{s:g}s"


def _mem(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    for unit, div in (("g", 1 << 30), ("m", 1 << 20), ("k", 1 << 10)):
        if n % div == 0:
            return f"{n // div}{unit}"
    return str(n)


def convert(spec: dict, project: str = "") -> dict:
    """spec → {"service", "compose", "env", "envKeys", "warnings", "volumes", "networks"}."""
    warnings: list[str] = []
    name = str(spec.get("name") or "").lstrip("/")
    svc_name = service_name(name)
    svc = CommentedMap()
    svc["image"] = spec.get("image") or ""
    if name:
        svc["container_name"] = name
    if spec.get("entrypoint") and spec.get("entrypoint") != spec.get("imageEntrypoint"):
        svc["entrypoint"] = _seq(spec["entrypoint"])
    if spec.get("cmd") and spec.get("cmd") != spec.get("imageCmd"):
        svc["command"] = _seq(spec["cmd"])
    if spec.get("user"):
        svc["user"] = spec["user"]
    if spec.get("workdir"):
        svc["working_dir"] = spec["workdir"]
    if spec.get("hostname") and not ((spec.get("networkMode") or "") in ("host", "none") or (spec.get("networkMode") or "").startswith("container:")):
        svc["hostname"] = spec["hostname"]   # avec le réseau de la machine, le nom d'hôte est celui de la machine : il ne se redéfinit pas
    if spec.get("domainname"):
        svc["domainname"] = spec["domainname"]
    if spec.get("tty"):
        svc["tty"] = True
    if spec.get("stdinOpen"):
        svc["stdin_open"] = True
    r = spec.get("restart") or {}
    if r.get("name") and r["name"] != "no":
        svc["restart"] = r["name"] + (f":{r['max']}" if r["name"] == "on-failure" and r.get("max") else "")

    # variables d'environnement : dans le .env, le compose y renvoie
    env_lines, env_keys = [], []
    envmap = CommentedMap()
    for k, v in spec.get("env") or []:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k)):
            warnings.append(f"Variable « {k} » : nom inhabituel, laissée telle quelle dans le compose")
            envmap[str(k)] = str(v).replace("$", "$$")
            continue
        env_keys.append(str(k))
        env_lines.append(f"{k}={env_value(str(v))}")
        envmap[str(k)] = "${" + str(k) + "}"
        if "\n" in str(v) or ("'" in str(v) and "$" in str(v)):
            warnings.append(f"Variable {k} : valeur complexe (retour à la ligne, apostrophe et $) : vérifiez-la dans le .env")
    if envmap:
        svc["environment"] = envmap

    # réseau et ports
    nm = spec.get("networkMode") or ""
    if nm in ("host", "none") or nm.startswith("container:"):
        svc["network_mode"] = nm
        if spec.get("ports"):
            warnings.append("Mode réseau particulier : les ports publiés sont ignorés par Docker, ils ne sont pas repris")
    else:
        ports = []
        for p in spec.get("ports") or []:
            host = f"{p['ip']}:{p['host']}" if p.get("ip") and p["ip"] not in ("0.0.0.0", "::") else str(p.get("host") or "")
            if not p.get("host"):
                continue
            proto = f"/{p['proto']}" if p.get("proto") and p["proto"] != "tcp" else ""
            ports.append(f"{host}:{p['container']}{proto}")
        if ports:
            svc["ports"] = _seq(ports)
    networks: list[str] = []
    nets = spec.get("networks") or {}
    custom = {n: v for n, v in nets.items() if n not in ("bridge", "host", "none")}
    if custom and "network_mode" not in svc:
        nmap = CommentedMap()
        for n, v in custom.items():
            networks.append(n)
            cfg = CommentedMap()
            if v.get("aliases"):
                cfg["aliases"] = _seq(v["aliases"])
            if v.get("ipv4"):
                cfg["ipv4_address"] = v["ipv4"]
            nmap[n] = cfg if len(cfg) else None
        svc["networks"] = nmap
    elif nets.get("bridge") is not None and "network_mode" not in svc and not custom:
        warnings.append("Le conteneur était sur le réseau « bridge » par défaut : le projet crée son propre réseau (les conteneurs s'y joignent par leur nom). Un autre conteneur qui le joignait par son adresse IP devra changer.")

    # volumes
    vols, top_vols = [], []
    for m in spec.get("mounts") or []:
        dest, ro = m.get("destination"), ":ro" if m.get("rw") is False else ""
        if m.get("type") == "bind":
            vols.append(f"{m.get('source')}:{dest}{ro}")
        elif m.get("type") == "volume" and m.get("name"):
            vols.append(f"{m['name']}:{dest}{ro}")
            if m["name"] not in top_vols:
                top_vols.append(m["name"])
            if _HEX64.match(m["name"]):
                warnings.append(f"Volume anonyme {m['name'][:12]}… monté sur {dest} : repris tel quel (externe) pour garder les données ; donnez-lui un nom pour le retrouver facilement")
        elif m.get("type") == "tmpfs":
            svc.setdefault("tmpfs", _seq([])).append(dest)
        else:
            warnings.append(f"Montage de type « {m.get('type')} » sur {dest} : non converti")
    if vols:
        svc["volumes"] = _seq(vols)
    if spec.get("tmpfs"):
        t = svc.get("tmpfs") or CommentedSeq()
        for d in spec["tmpfs"]:
            if d not in t:
                t.append(d)
        svc["tmpfs"] = t

    # labels (ceux de l'utilisateur ; ceux de l'image et ceux de Docker sont déjà retirés)
    labs = spec.get("labels") or {}
    if labs:
        svc["labels"] = _seq([f"{k}={str(v).replace('$', '$$')}" for k, v in sorted(labs.items())])

    # options d'exécution
    if spec.get("privileged"):
        svc["privileged"] = True
        warnings.append("Conteneur privilégié (privileged: true) : accès quasi total à la machine, à reprendre en connaissance de cause")
    for key, src in (("cap_add", "capAdd"), ("cap_drop", "capDrop"), ("devices", "devices"), ("extra_hosts", "extraHosts"), ("dns", "dns"), ("dns_search", "dnsSearch"),
                     ("security_opt", "securityOpt"), ("group_add", "groupAdd")):
        if spec.get(src):
            svc[key] = _seq([str(x)[4:] if key in ("cap_add", "cap_drop") and str(x).startswith("CAP_") else str(x) for x in spec[src]])
    if spec.get("sysctls"):
        svc["sysctls"] = CommentedMap({str(k): str(v) for k, v in spec["sysctls"].items()})
    if spec.get("readOnly"):
        svc["read_only"] = True
    if spec.get("init"):
        svc["init"] = True
    for key, src in (("pid", "pid"), ("ipc", "ipc"), ("stop_signal", "stopSignal")):
        if spec.get(src):
            svc[key] = spec[src]
    if spec.get("stopTimeout"):
        svc["stop_grace_period"] = f"{int(spec['stopTimeout'])}s"
    if _mem(spec.get("shmSize")):
        svc["shm_size"] = _mem(spec["shmSize"])
    if _mem(spec.get("memory")):
        svc["mem_limit"] = _mem(spec["memory"])
    if _mem(spec.get("memoryReservation")):
        svc["mem_reservation"] = _mem(spec["memoryReservation"])
    if spec.get("nanoCpus"):
        svc["cpus"] = round(int(spec["nanoCpus"]) / 1e9, 3)
    if spec.get("pidsLimit") and int(spec["pidsLimit"]) > 0:
        svc["pids_limit"] = int(spec["pidsLimit"])
    hc = spec.get("healthcheck")
    if hc and hc.get("Test"):
        h = CommentedMap()
        t = hc["Test"]
        if t == ["NONE"]:
            h["disable"] = True
        else:
            h["test"] = _seq(t)
            for k, dk in (("interval", "Interval"), ("timeout", "Timeout"), ("start_period", "StartPeriod")):
                if _ns_to_s(hc.get(dk)):
                    h[k] = _ns_to_s(hc[dk])
            if hc.get("Retries"):
                h["retries"] = int(hc["Retries"])
        svc["healthcheck"] = h
    lc = spec.get("logConfig") or {}
    if lc.get("Type") and (lc["Type"] != "json-file" or lc.get("Config")):
        lg = CommentedMap()
        lg["driver"] = lc["Type"]
        if lc.get("Config"):
            lg["options"] = CommentedMap({str(k): str(v) for k, v in lc["Config"].items()})
        svc["logging"] = lg
    if spec.get("ulimits"):
        ul = CommentedMap()
        for u in spec["ulimits"]:
            ul[u["Name"]] = CommentedMap({"soft": u["Soft"], "hard": u["Hard"]}) if u.get("Soft") != u.get("Hard") else u["Soft"]
        svc["ulimits"] = ul
    if spec.get("links"):
        warnings.append("Liens (--link) : abandonnés par Docker ; les conteneurs d'un même réseau se joignent par leur nom")
    if spec.get("volumesFrom"):
        warnings.append("volumes_from : non converti, montez les volumes explicitement")

    doc = CommentedMap()
    if project:
        doc["name"] = project
    doc["services"] = CommentedMap({svc_name: svc})
    if top_vols:
        doc["volumes"] = CommentedMap({v: CommentedMap({"external": True}) for v in top_vols})
    if networks:
        doc["networks"] = CommentedMap({n: CommentedMap({"external": True}) for n in networks})
    y = YAML()
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    buf = StringIO()
    buf.write(f"# Converti depuis le conteneur « {name} » le {date.today().isoformat()}. Relisez avant de déployer.\n")
    if top_vols:
        buf.write("# Les volumes « external » existent déjà (vos données sont gardées) : retirez « external: true » pour que Compose en crée de nouveaux.\n")
    y.dump(doc, buf)
    env = ""
    if env_lines:
        env = f"# Variables d'environnement de « {name} » (déplacées hors du compose). Ce fichier contient des secrets : droits 600, hors dépôt Git.\n" + "\n".join(env_lines) + "\n"
    return {"service": svc_name, "compose": buf.getvalue(), "env": env, "envKeys": env_keys, "warnings": warnings, "volumes": top_vols, "networks": networks}
