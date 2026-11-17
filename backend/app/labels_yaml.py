"""Conversion des labels Traefik (« traefik.http.routers.X.rule=… » dans un compose) ⇄ éléments d'un fichier YAML du fournisseur « fichier ».

Les deux disent la même chose : les labels sont la configuration aplatie en clés pointées. La conversion est donc mécanique, mais pas parfaite :
 - les labels ne portent que le PORT du service (l'adresse est celle du conteneur) : vers un fichier, il faut une cible « http://hôte:port » ;
 - les clés des labels sont insensibles à la casse (« entrypoints »), celles du YAML sont en camelCase (« entryPoints ») ;
 - dans un fichier compose, un « $ » s'écrit « $$ » (mots de passe bcrypt de basicAuth).
Tout ce qui n'est pas convertible sans perte est signalé dans `warnings`, jamais ignoré en silence. Fonctions pures."""
import re
from io import StringIO

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq

CAMEL = {k.lower(): k for k in (
    "entryPoints", "certResolver", "loadBalancer", "passHostHeader", "sourceRange", "basicAuth", "ipAllowList", "stripPrefix", "stripPrefixRegex", "addPrefix", "replacePath",
    "replacePathRegex", "redirectScheme", "redirectRegex", "rateLimit", "forwardAuth", "circuitBreaker", "inFlightReq", "contentType", "customRequestHeaders", "customResponseHeaders",
    "accessControlAllowOriginList", "accessControlAllowHeaders", "accessControlAllowMethods", "accessControlMaxAge", "stsSeconds", "stsIncludeSubdomains", "stsPreload", "forceSTSHeader",
    "frameDeny", "contentTypeNosniff", "browserXssFilter", "referrerPolicy", "sslRedirect", "permanentRedirect", "removeHeader", "usersFile", "headerField", "realm", "trustForwardHeader",
    "authResponseHeaders", "authRequestHeaders", "authResponseHeadersRegex", "maxBodyBytes", "memRequestBodyBytes", "sticky", "healthCheck", "serversTransport", "responseForwarding",
    "flushInterval", "insecureSkipVerify", "rootCAs", "serverName", "passTLSClientCert", "ipStrategy", "excludedIPs", "depth", "serversTransports", "notAfter", "scheme", "regex",
    "replacement", "average", "burst", "period", "sourceCriterion", "requestHeaderName", "requestHost", "permanent", "forwardBody", "tls", "options", "domains", "priority", "rule", "service", "middlewares")}
LIST_KEYS = {"entrypoints", "middlewares", "sourcerange", "users", "excludedips", "accesscontrolalloworiginlist", "accesscontrolallowheaders", "accesscontrolallowmethods",
             "authresponseheaders", "authrequestheaders", "authresponseheadersregex", "rootcas"}
KINDS = {"routers": "routers", "services": "services", "middlewares": "middlewares", "serverstransports": "serversTransports"}
LABEL_PREFIX = "traefik.http."


def _canon(seg: str) -> str:
    return CAMEL.get(seg.lower(), seg)


def _scalar(v: str):
    t = v.strip()
    if t.lower() in ("true", "false"):
        return t.lower() == "true"
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    return v


def _set(d: dict, path: list[str], val) -> None:
    for p in path[:-1]:
        nxt = d.get(p)
        if not isinstance(nxt, dict):
            nxt = {} if nxt in (None, True) else {"_": nxt}
            d[p] = nxt
        d = nxt
    d[path[-1]] = val


def _fix_tls(spec: dict) -> None:
    """« tls=true » sans réglage = « tls: {} » en YAML."""
    if spec.get("tls") is True:
        spec["tls"] = {}
    if isinstance(spec.get("tls"), dict):
        spec["tls"].pop("_", None)


def labels_to_items(labels: dict, target: str = "") -> dict:
    """Labels d'un conteneur → éléments du fichier : {"items": [{proto, kind, name, spec}], "warnings": [...]}.
    `target` : adresse du conteneur vue par Traefik (son nom sur le réseau partagé), pour construire l'URL du service."""
    warnings: list[str] = []
    found: dict[tuple[str, str], dict] = {}
    for key, value in sorted((labels or {}).items()):
        k = str(key)
        if not k.lower().startswith(LABEL_PREFIX):
            if k.lower().startswith("traefik.") and not k.lower().startswith(("traefik.enable", "traefik.docker.", "traefik.constraint")):
                warnings.append(f"Label non converti (hors HTTP) : {k}")
            continue
        parts = k[len(LABEL_PREFIX):].split(".")
        if len(parts) < 3 or parts[0].lower() not in KINDS:
            warnings.append(f"Label non reconnu : {k}")
            continue
        kind, name, path = KINDS[parts[0].lower()], parts[1], parts[2:]
        low = [p.lower() for p in path]
        val = str(value).replace("$$", "$")
        spec = found.setdefault((kind, name), {})
        if low[-1] in LIST_KEYS:
            _set(spec, [_canon(p) for p in path], [x.strip() for x in val.split(",") if x.strip()])
        else:
            _set(spec, [_canon(p) for p in path], _scalar(val))
    items = []
    for (kind, name), spec in found.items():
        _fix_tls(spec)
        if kind == "services":
            lb = spec.get("loadBalancer") if isinstance(spec.get("loadBalancer"), dict) else {}
            srv = lb.pop("server", None) if isinstance(lb, dict) else None
            port = (srv or {}).get("port") if isinstance(srv, dict) else None
            scheme = ((srv or {}).get("scheme") if isinstance(srv, dict) else None) or "http"
            if port is not None:
                host = target or name
                if not target:
                    warnings.append(f"Service « {name} » : adresse de cible inconnue, « {host} » a été supposé (nom du conteneur sur le réseau de Traefik)")
                lb["servers"] = [{"url": f"{scheme}://{host}:{port}"}]
                spec["loadBalancer"] = lb
            else:
                warnings.append(f"Service « {name} » : pas de port (loadbalancer.server.port) : à compléter")
        items.append({"proto": "http", "kind": kind, "name": name, "spec": spec})
    by = {(i["kind"], i["name"]) for i in items}
    for i in items:   # un routeur sans service nommé : Traefik en crée un du même nom côté Docker, il faut l'écrire explicitement
        if i["kind"] == "routers" and not i["spec"].get("service"):
            svcs = [n for (k, n) in by if k == "services"]
            if len(svcs) == 1:
                i["spec"]["service"] = svcs[0]
            else:
                warnings.append(f"Routeur « {i['name']} » : aucun service désigné (service=…) : à compléter")
        if i["kind"] == "routers" and not i["spec"].get("rule"):
            warnings.append(f"Routeur « {i['name']} » : pas de règle (rule=…)")
    order = {"routers": 0, "services": 1, "middlewares": 2, "serversTransports": 3}
    return {"items": sorted(items, key=lambda i: (order[i["kind"]], i["name"])), "warnings": warnings}


def _flat(prefix: str, node, out: dict, warnings: list[str], compose: bool) -> None:
    if isinstance(node, dict):
        if not node and prefix.endswith(".tls"):
            out[prefix] = "true"
        for k, v in node.items():
            _flat(f"{prefix}.{str(k).lower()}", v, out, warnings, compose)
    elif isinstance(node, list):
        if all(isinstance(x, (str, int, float, bool)) for x in node):
            joined = ",".join(str(x) for x in node)
            out[prefix] = joined.replace("$", "$$") if compose else joined
        else:
            warnings.append(f"« {prefix} » est une liste d'objets : elle ne se représente pas en labels")
    elif isinstance(node, bool):
        out[prefix] = "true" if node else "false"
    elif node is not None:
        out[prefix] = str(node).replace("$", "$$") if compose else str(node)


def items_to_labels(items: list[dict], compose: bool = True) -> dict:
    """Éléments d'un fichier (routeurs, services, middlewares) → labels : {"labels": {clé: valeur}, "warnings": [...]}."""
    labels: dict[str, str] = {}
    warnings: list[str] = []
    for it in items:
        if it.get("proto") != "http":
            warnings.append(f"« {it.get('name')} » n'est pas HTTP : non converti")
            continue
        kind, name, spec = it["kind"], it["name"], dict(it.get("spec") or {})
        base = f"{LABEL_PREFIX}{kind.lower()}.{name}"
        if kind == "services":
            lb = dict(spec.get("loadBalancer") or {})
            servers = lb.pop("servers", None) or []
            if len(servers) > 1:
                warnings.append(f"Service « {name} » : {len(servers)} serveurs, les labels n'en portent qu'un (port) : seul le premier est gardé")
            if servers and isinstance(servers[0], dict) and servers[0].get("url"):
                m = re.match(r"^(https?)://[^/:]+(?::(\d+))?", str(servers[0]["url"]))
                if m:
                    if m.group(2):
                        labels[f"{base}.loadbalancer.server.port"] = m.group(2)
                    if m.group(1) == "https":
                        labels[f"{base}.loadbalancer.server.scheme"] = "https"
                warnings.append(f"Service « {name} » : l'adresse de la cible ({servers[0]['url']}) est perdue, seul le port est gardé : en labels, Traefik vise le conteneur lui-même")
            _flat(f"{base}.loadbalancer", lb, labels, warnings, compose)
            rest = {k: v for k, v in spec.items() if k != "loadBalancer"}
            _flat(base, rest, labels, warnings, compose)
        else:
            _flat(base, spec, labels, warnings, compose)
    return {"labels": dict(sorted(labels.items())), "warnings": warnings}


def check_labels(declared: dict[str, dict], items: list[dict]) -> list[dict]:
    """Contrôles croisés des labels d'un projet avec les fichiers de Traefik.
    declared : {nom du service compose: {label: valeur}}. Renvoie [{"level": "warn"|"err", "service", "text"}]."""
    out = []
    file_routers = {i["name"] for i in items if i.get("proto") == "http" and i.get("kind") == "routers"}
    file_mw = {i["name"] for i in items if i.get("proto") == "http" and i.get("kind") == "middlewares"}
    seen: dict[str, str] = {}
    for svc, labels in declared.items():
        if str(labels.get("traefik.enable", "")).lower() == "false":
            continue
        conv = labels_to_items(labels, target=svc)
        routers = [i for i in conv["items"] if i["kind"] == "routers"]
        mws = {i["name"] for i in conv["items"] if i["kind"] == "middlewares"}
        for r in routers:
            if r["name"] in seen and seen[r["name"]] != svc:
                out.append({"level": "err", "service": svc, "text": f"Le routeur « {r['name']} » est déclaré aussi par le service « {seen[r['name']]} » : un seul sera gardé par Traefik"})
            seen[r["name"]] = svc
            if r["name"] in file_routers:
                out.append({"level": "warn", "service": svc, "text": f"Le routeur « {r['name']} » existe aussi dans un fichier de Traefik : deux définitions du même site (l'une en « @docker », l'autre en « @file »)"})
            if not r["spec"].get("rule"):
                out.append({"level": "err", "service": svc, "text": f"Le routeur « {r['name']} » n'a pas de règle (rule) : Traefik l'ignore"})
            for mw in r["spec"].get("middlewares") or []:
                base, _, prov = str(mw).partition("@")
                if prov == "file" and base not in file_mw:
                    out.append({"level": "err", "service": svc, "text": f"Le routeur « {r['name']} » utilise « {mw} », qui n'existe dans aucun fichier de Traefik"})
                elif not prov and base not in mws and base not in file_mw:
                    out.append({"level": "warn", "service": svc, "text": f"Le routeur « {r['name']} » utilise « {mw} », introuvable dans les labels de ce service"})
        if routers and not any(i["kind"] == "services" for i in conv["items"]):
            out.append({"level": "warn", "service": svc, "text": "Aucun port de service (loadbalancer.server.port) : Traefik prendra le seul port exposé par l'image, s'il n'y en a qu'un"})
    return out


# ------------------------------------------------------------------ écrire dans / lire un compose, en gardant sa mise en forme
def _rt() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _service(data, service: str):
    sv = data.get("services") if isinstance(data, dict) else None
    if not isinstance(sv, dict) or service not in sv or not isinstance(sv[service], dict):
        raise ValueError(f"Service « {service} » introuvable dans ce fichier")
    return sv[service]


def _kv(item) -> tuple[str, str]:
    k, _, v = str(item).partition("=")
    return k.strip(), v


def set_service_labels(text: str, service: str, labels: dict, overwrite: bool = False) -> tuple[str, list[str], list[str]]:
    """Ajoute des labels au service d'un compose. Renvoie (nouveau texte, clés écrites, clés en conflit laissées telles quelles).
    Une clé qui existe déjà avec une AUTRE valeur n'est remplacée que si `overwrite`. Le style existant (liste « k=v » ou dictionnaire) est conservé."""
    y = _rt()
    data = y.load(text)
    svc = _service(data, service)
    cur = svc.get("labels")
    added, conflicts = [], []
    if isinstance(cur, dict):
        for k, v in labels.items():
            if k in cur and str(cur[k]) != str(v) and not overwrite:
                conflicts.append(k)
                continue
            if k not in cur or str(cur[k]) != str(v):
                added.append(k)
            cur[k] = str(v)
    elif cur is None or isinstance(cur, list):
        seq = cur if cur is not None else CommentedSeq()
        idx = {_kv(x)[0]: i for i, x in enumerate(seq)}
        for k, v in labels.items():
            line = f"{k}={v}"
            if k in idx:
                if _kv(seq[idx[k]])[1] == str(v):
                    continue
                if not overwrite:
                    conflicts.append(k)
                    continue
                seq[idx[k]] = line
            else:
                seq.append(line)
            added.append(k)
        if cur is None and seq:
            svc["labels"] = seq
    else:
        raise ValueError("Les labels de ce service ont une forme inattendue")
    buf = StringIO()
    y.dump(data, buf)
    return buf.getvalue(), added, conflicts


def remove_traefik_labels(text: str, service: str) -> tuple[str, list[str]]:
    """Retire les labels « traefik.* » d'un service (les autres labels restent). Renvoie (nouveau texte, clés retirées)."""
    y = _rt()
    data = y.load(text)
    svc = _service(data, service)
    cur = svc.get("labels")
    removed: list[str] = []
    if isinstance(cur, dict):
        for k in [k for k in cur if str(k).startswith("traefik.")]:
            removed.append(str(k))
            del cur[k]
    elif isinstance(cur, list):
        for i in range(len(cur) - 1, -1, -1):
            if _kv(cur[i])[0].startswith("traefik."):
                removed.insert(0, _kv(cur[i])[0])
                del cur[i]
    if cur is not None and len(cur) == 0:
        del svc["labels"]
    buf = StringIO()
    y.dump(data, buf)
    return buf.getvalue(), removed


def suggest_service(urls: list[str], services: list[dict]) -> list[dict]:
    """Classe les services d'un compose ({file, service, containerName, image, ports}) selon leur ressemblance avec la cible d'un site :
    l'hôte et le port de son URL. 100 = l'hôte est le nom du conteneur, 90 = le nom du service ; moins : un nom voisin ou l'image."""
    hosts, ports = [], set()
    for u in urls:
        m = re.match(r"^https?://([^/:]+)(?::(\d+))?", str(u or ""))
        if m:
            hosts.append(m.group(1).lower())
            if m.group(2):
                ports.add(m.group(2))
    out = []
    for sv in services:
        cname, sname, image = (sv.get("containerName") or "").lower(), (sv.get("service") or "").lower(), (sv.get("image") or "").lower()
        score, why = 0, ""
        for h in hosts:
            if cname and h == cname:
                score, why = max(score, 100), f"la cible du site vise « {h} », le nom de ce conteneur"
            elif h == sname:
                if score < 90:
                    score, why = 90, f"la cible du site vise « {h} », le nom de ce service"
            elif score < 50 and any(n and (h.startswith(n + "-") or h.startswith(n + "_") or n.startswith(h + "-")) for n in (cname, sname)):
                score, why = 50, f"« {h} » ressemble au nom de ce service"
            elif score < 30 and image and h in image:
                score, why = 30, f"« {h} » apparaît dans le nom de l'image"
        if ports and any(re.search(r"(^|:)(" + "|".join(map(re.escape, ports)) + r")(/\w+)?$", str(pt)) for pt in sv.get("ports") or []) and score:
            score += 10
            why += f", et il publie le port {sorted(ports)[0]}"
        out.append({**sv, "score": score, "reason": why})
    return sorted(out, key=lambda x: (-x["score"], x["service"]))
