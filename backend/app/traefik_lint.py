"""Analyse de cohérence de la configuration dynamique (références cassées, doublons, cycles…)."""
import re
from collections import defaultdict

HOST_RE = re.compile(r"Host(?:SNI)?\(([^)]*)\)")
STR_RE = re.compile(r"[`\"']([^`\"']+)[`\"']")
INTERNAL_SERVICES = {"api@internal", "dashboard@internal", "acme-http@internal", "noop@internal", "ping@internal",
                     "prometheus@internal", "rest@internal"}


def extract_hosts(rule: str) -> list[str]:
    hosts = []
    for m in HOST_RE.finditer(rule or ""):
        hosts += [h.lower() for h in STR_RE.findall(m.group(1)) if "*" not in h and "{" not in h]
    return hosts


def _refs(item: dict) -> dict:
    """Retourne les références sortantes d'un élément : {'services': [...], 'middlewares': [...]}"""
    spec, kind = item["spec"] or {}, item["kind"]
    out = {"services": [], "middlewares": []}
    if kind == "routers":
        if spec.get("service"):
            out["services"].append(spec["service"])
        out["middlewares"] += [m for m in (spec.get("middlewares") or []) if isinstance(m, str)]
    elif kind == "middlewares":
        out["middlewares"] += list((spec.get("chain") or {}).get("middlewares") or [])
        if (spec.get("errors") or {}).get("service"):
            out["services"].append(spec["errors"]["service"])
    elif kind == "services":
        w = spec.get("weighted") or {}
        out["services"] += [s.get("name") for s in w.get("services") or [] if isinstance(s, dict) and s.get("name")]
        mir = spec.get("mirroring") or {}
        if mir.get("service"):
            out["services"].append(mir["service"])
        out["services"] += [m.get("name") for m in mir.get("mirrors") or [] if isinstance(m, dict) and m.get("name")]
        fo = spec.get("failover") or {}
        out["services"] += [fo[k] for k in ("service", "fallback") if fo.get(k)]
    return out


def used_by(items: list[dict]) -> dict[str, list[str]]:
    """id d'un service/middleware -> ids de ceux qui l'utilisent (les éléments désactivés ne comptent pas)."""
    items = [i for i in items if not i.get("disabled")]
    index = {(i["proto"], i["kind"], i["name"]): i["id"] for i in items}
    result = defaultdict(list)
    for it in items:
        if it["proto"] not in ("http", "tcp", "udp"):
            continue
        for kind, names in _refs(it).items():
            for n in names:
                base = n.split("@")[0] if n.endswith("@file") else n
                key = (it["proto"], kind, base)
                if "@" not in base and key in index:
                    result[index[key]].append(it["id"])
    return result


def lint(items: list[dict], file_errors: dict[str, str], known_entrypoints: list[str], known_resolvers: list[str]) -> list[dict]:
    issues: list[dict] = []
    items = [i for i in items if not i.get("disabled")]  # un élément désactivé n'existe pas pour Traefik

    def add(level, msg, item=None, file=None):
        issues.append({"level": level, "message": msg, "item": item["id"] if item else None,
                       "file": file or (item["file"] if item else None)})

    for f, e in file_errors.items():
        add("error", e, file=f)

    seen = defaultdict(list)
    for it in items:
        seen[(it["proto"], it["kind"], it["name"])].append(it)
    for (_, _, name), group in seen.items():
        if len(group) > 1:
            files = ", ".join(g["file"] for g in group)
            add("error", f"« {name} » est défini plusieurs fois ({files}) : Traefik n'en garde qu'un.", group[0])

    index = {(i["proto"], i["kind"], i["name"]) for i in items}
    for it in items:
        if it["proto"] not in ("http", "tcp", "udp"):
            continue
        spec = it["spec"] or {}
        proto, kind = it["proto"], it["kind"]
        if kind == "routers":
            if proto != "udp" and not spec.get("rule") and not (proto == "tcp" and (spec.get("tls") or {}).get("passthrough")):
                add("error", "Routeur sans règle (rule).", it)
            if not spec.get("service"):
                add("error", "Routeur sans service.", it)
            if not spec.get("entryPoints"):
                add("warning", "Aucun entryPoint : le routeur écoute sur tous les entrypoints.", it)
            for ep in spec.get("entryPoints") or []:
                if known_entrypoints and ep not in known_entrypoints:
                    add("warning", f"EntryPoint « {ep} » inconnu (connus : {', '.join(known_entrypoints)}). Ajoutez-le dans « Ce nœud » → EntryPoints (le .env ne sert qu'au premier démarrage).", it)
            res = (spec.get("tls") or {}).get("certResolver")
            if res and known_resolvers and res not in known_resolvers:
                add("warning", f"CertResolver « {res} » inconnu (connus : {', '.join(known_resolvers)}). Ajoutez-le dans « Ce nœud » → Résolveurs de certificats (le .env ne sert qu'au premier démarrage).", it)
            if re.search(r"HostRegexp\(`[^`]*\{", spec.get("rule") or ""):
                add("warning", "Syntaxe HostRegexp v2 ({name:regex}) non supportée par Traefik v3.", it)
        for kind2, names in _refs(it).items():
            for n in names:
                if n in INTERNAL_SERVICES or n.endswith("@internal"):
                    continue
                if "@" in n:
                    base, prov = n.rsplit("@", 1)
                    if prov != "file":
                        continue
                    n = base
                if (proto, kind2, n) not in index:
                    add("error", f"Référence cassée : {kind2[:-1]} « {n} » introuvable.", it)
        if kind == "middlewares":
            if len(spec) != 1:
                add("error", "Un middleware doit avoir exactement un type (ex. headers, ipAllowList…).", it)
            if "ipWhiteList" in spec:
                add("warning", "ipWhiteList est déprécié : utilisez ipAllowList.", it)
        if kind == "services" and len(spec) != 1:
            add("error", "Un service doit avoir exactement un type (loadBalancer, weighted…).", it)

    # cycles de chaînes de middlewares
    chains = {i["name"]: [m.split("@")[0] for m in ((i["spec"] or {}).get("chain") or {}).get("middlewares") or []]
              for i in items if i["proto"] == "http" and i["kind"] == "middlewares" and "chain" in (i["spec"] or {})}

    def cyc(n, path):
        if n in path:
            return True
        return any(cyc(c, path | {n}) for c in chains.get(n, []))

    for it in items:
        if it["id"].startswith("http/middlewares/") and it["name"] in chains and cyc(it["name"], frozenset()):
            add("error", "Boucle dans la chaîne de middlewares.", it)

    # éléments inutilisés
    usage = used_by(items)
    for it in items:
        if it["proto"] in ("http", "tcp", "udp") and it["kind"] in ("services", "middlewares") and not usage.get(it["id"]):
            if it["name"] not in ("errorpages",):
                add("info", f"{'Service' if it['kind'] == 'services' else 'Middleware'} jamais utilisé dans vos fichiers.", it)
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(issues, key=lambda i: order[i["level"]])
