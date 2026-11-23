"""Assistant de rangement : propose où déplacer les blocs / fichiers existants selon leur type (jamais appliqué sans validation)."""
import posixpath
import re
from collections import Counter, defaultdict

MAX_STEPS = 500


def _cls(i: dict) -> str:
    if i["kind"] == "middlewares":
        return "mw"
    if i["kind"] == "serversTransports":
        return "tr"
    if i["proto"] == "tls":
        return "tls"
    return "app"


def _dir(path: str) -> str:
    return posixpath.dirname(path)


def _service_name(router: dict) -> str | None:
    svc = (router.get("spec") or {}).get("service")
    if not isinstance(svc, str):
        return None
    name, _, provider = svc.partition("@")
    return name if provider in ("", "file") and name else None


# Sous-dossiers des middlewares, d'après leur type Traefik
MW_GROUPS = {
    "auth": ("basicAuth", "digestAuth", "forwardAuth"),
    "headers": ("headers",),
    "ip": ("ipAllowList", "ipWhiteList"),
    "chains": ("chain",),
    "redirections": ("redirectScheme", "redirectRegex", "addPrefix", "stripPrefix", "stripPrefixRegex", "replacePath", "replacePathRegex"),
    "limites": ("rateLimit", "inFlightReq", "buffering", "retry", "circuitBreaker"),
}
_SLD = {"co", "com", "org", "net", "gov", "ac", "edu"}


def _mw_group(i: dict) -> str | None:
    spec = i.get("spec")
    if isinstance(spec, dict):
        for g, types in MW_GROUPS.items():
            if any(t in spec for t in types):
                return g
    return None


def _domain(router: dict) -> str | None:
    """Domaine principal d'un routeur (le premier Host(`…`) de sa règle) : « a.b.example.com » -> « example.com »."""
    m = re.search(r"Host\(\s*`([^`]+)`", str((router.get("spec") or {}).get("rule", "")))
    if not m:
        return None
    parts = [p for p in m.group(1).lower().split(".") if p]
    if len(parts) < 2 or not all(re.fullmatch(r"[a-z0-9-]+", p) for p in parts):
        return None
    k = 3 if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _SLD else 2
    return ".".join(parts[-k:])


def _composite_refs(svc: dict) -> list[str]:
    """Services cités par un service composite (pondéré, miroir, secours) : ils vont ensemble."""
    spec = svc.get("spec") or {}
    out = [e.get("name") for e in (spec.get("weighted") or {}).get("services") or [] if isinstance(e, dict)]
    mir = spec.get("mirroring") or {}
    out += [mir.get("service")] + [e.get("name") for e in mir.get("mirrors") or [] if isinstance(e, dict)]
    fo = spec.get("failover") or {}
    out += [fo.get("service"), fo.get("fallback")]
    return [n.partition("@")[0] for n in out if isinstance(n, str) and n.partition("@")[2] in ("", "file")]


def is_managed(path: str) -> bool:
    """Un fichier dont le nom commence par « zz » est géré par l'interface elle-même : on ne le range, ne le déplace et ne le supprime pas."""
    return posixpath.basename(path or "").lower().startswith("zz")


def build_plan(items: list[dict], dirs: list[str], errors: dict[str, str], scope: str = "root",
               layout: str = "sites", sub: bool = False, existing: list[str] | None = None, empty: list[str] | None = None, empty_dirs: list[str] | None = None) -> dict:
    """Propose un rangement ; rien n'est écrit sans validation.
    scope « root » : ne touche que ce qui est à la racine ; « all » : reclasse aussi les middlewares / TLS rangés ailleurs.
    layout, pour les routeurs et services de la racine :
      « sites » (défaut) un fichier par site : les routeurs qui pointent vers le même service sont TOUJOURS ensemble avec lui ;
                 si une partie du groupe est déjà rangée dans un dossier, le reste la rejoint ;
      « split »  un fichier par routeur (routers/) et un par service (services/) ;
      « keep »   fichiers entiers déplacés tels quels.
    sub : sous-dossiers (middlewares par type, sites par domaine). existing : tous les fichiers du nœud. empty / empty_dirs : fichiers sans contenu, dossiers sans fichier."""
    top = {d for d in dirs if "/" not in d}
    off_files = {i["file"] for i in items if i["file"].lower().endswith(".off")}   # fichier .off : intouchable
    # Fichiers gérés par l'interface (zz-webui-bans.yml, zz-webui-nudge.yml…) : le préfixe « zz » les marque, le rangement n'y touche jamais.
    off_files |= {i["file"] for i in items if is_managed(i["file"])}
    empty = [f for f in (empty or []) if not is_managed(f)]
    # Les blocs en pause (« #~ ») d'un fichier actif se rangent comme les autres : le moteur les réactive le temps du déplacement.
    items = [i for i in items if i["file"] not in errors and i["file"] not in off_files]
    taken = {i["file"] for i in items} | set(errors) | off_files | set(existing or []) | set(empty or [])

    def dominant(pred) -> str | None:
        c = Counter(_dir(i["file"]) for i in items if pred(i) and _dir(i["file"]))
        return c.most_common(1)[0][0] if c else None

    def dedicated(c: str) -> str | None:
        """Un dossier qui ne contient que ce type de blocs (un « infra/ » mixte ne devient pas le dossier des middlewares)."""
        kinds: dict[str, set[str]] = defaultdict(set)
        for i in items:
            if _dir(i["file"]):
                kinds[_dir(i["file"])].add(_cls(i))
        mine = Counter(_dir(i["file"]) for i in items if _cls(i) == c and kinds.get(_dir(i["file"])) == {c})
        return mine.most_common(1)[0][0] if mine else None

    target = {
        "mw": "middlewares" if "middlewares" in top else (dedicated("mw") or "middlewares"),
        "tls": "tls" if "tls" in top else (dedicated("tls") or "tls"),
        "tr": "transports" if "transports" in top else (dedicated("tr") or "transports"),
        # « tout ranger » : destination stable (« apps »), sinon le dossier dominant changerait à chaque passage ; à la racine seule, on suit l'existant
        "app": "apps" if ("apps" in top or (scope == "all" and layout in ("sites", "split"))) else (dominant(lambda i: i["kind"] == "routers") or "apps"),
    }

    def unique(path: str) -> str:
        stem, ext = posixpath.splitext(path)
        n, out = 2, path
        while out in taken:
            out, n = f"{stem}-{n}{ext}", n + 1
        taken.add(out)
        return out

    def want(i: dict) -> str:
        """Dossier où ce bloc (middleware, TLS, transport) doit vivre."""
        c = _cls(i)
        if sub and c == "mw" and (g := _mw_group(i)):
            return f"{target[c]}/{g}"
        return target[c]

    def candidate(i: dict) -> bool:
        c, d = _cls(i), _dir(i["file"])
        if c == "app":
            return d == "" or (scope == "all" and layout in ("sites", "split"))   # « tout » : même ce qui est déjà dans un dossier
        if d == "":
            return True
        if scope == "root":
            return False
        if sub and c == "mw" and d == target[c] and want(i) != d:
            return True
        return d != target[c] and not d.startswith(target[c] + "/")

    byfile: dict[str, list[dict]] = defaultdict(list)
    for i in items:
        byfile[i["file"]].append(i)
    by_id = {i["id"]: i for i in items}
    steps: list[dict] = []
    handled: set[str] = set()

    def label(i: dict) -> dict:
        return {"id": i["id"], "kind": i["kind"], "name": i["name"]}

    def move_group(group: list[dict], dst: str, kind: str, reason: str, whole_ok: bool = True) -> None:
        """Déplace `group` vers `dst`. Un fichier qui ne contient que ce groupe part entier ; sinon on extrait les blocs."""
        srcs = sorted({i["file"] for i in group})
        ids = {i["id"] for i in group}
        if whole_ok and len(srcs) == 1 and ids == {i["id"] for i in byfile[srcs[0]]}:
            taken.discard(dst)   # le fichier garde son nom d'origine (c'est celui que l'utilisateur lui a donné)
            dst = unique(f"{posixpath.dirname(dst)}/{posixpath.basename(srcs[0])}")
            steps.append({"kind": "file", "from": srcs[0], "to": dst, "ids": sorted(ids), "items": [label(i) for i in group], "group": kind, "reason": reason})
        else:
            for src in srcs:
                part = [i for i in group if i["file"] == src]
                steps.append({"kind": "blocks", "from": src, "to": dst, "ids": [i["id"] for i in part], "items": [label(i) for i in part], "group": kind, "reason": reason})
        handled.update(ids)

    # 1. routeurs et services : un fichier par site (ou par routeur / par service), ou fichiers entiers
    apps = [i for i in items if i["kind"] in ("routers", "services") and i["proto"] in ("http", "tcp", "udp")]
    if layout == "split":
        for i in apps:
            if candidate(i):
                d = "routers" if i["kind"] == "routers" else "services"
                if _dir(i["file"]) == d and len(byfile[i["file"]]) == 1:   # déjà seul dans son fichier du bon dossier
                    handled.add(i["id"])
                    continue
                move_group([i], unique(f"{d}/{_safe(i['name'])}.yml"), d, "Un fichier par routeur" if d == "routers" else "Un fichier par service", whole_ok=False)
    elif layout == "sites":
        parent = {i["id"]: i["id"] for i in apps}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for r in apps:
            if r["kind"] == "routers" and (sn := _service_name(r)) and (sid := f"{r['proto']}/services/{sn}") in parent:
                parent[find(r["id"])] = find(sid)
            elif r["kind"] == "services":
                for ref in _composite_refs(r):
                    if (sid := f"{r['proto']}/services/{ref}") in parent:
                        parent[find(r["id"])] = find(sid)
        comps: dict[str, list[dict]] = defaultdict(list)
        for i in apps:
            comps[find(i["id"])].append(i)
        for members in comps.values():
            movers = [i for i in members if candidate(i)]
            if not movers:
                continue
            fixed = [i for i in members if not candidate(i)]
            if fixed:  # une partie du site est déjà rangée : le reste la rejoint
                anchor = Counter(i["file"] for i in fixed).most_common(1)[0][0]
                move_group(movers, anchor, "pairs", f"Rangé avec le reste du site dans {anchor}", whole_ok=False)
                continue
            rs = sorted((i for i in movers if i["kind"] == "routers"), key=lambda i: i["name"])
            svcs = sorted((i for i in movers if i["kind"] == "services"), key=lambda i: i["name"])
            dom = next((d for r in rs if (d := _domain(r))), None) if sub else None
            reason = ("Routeur" + ("s" if len(rs) > 1 else "") + " + service qui vont ensemble") if rs and svcs else \
                "Routeur sans service dans ces fichiers" if rs else "Service sans routeur dans ces fichiers"
            base = f"{target['app']}/{dom}" if dom else target["app"]
            files_of = {i["file"] for i in movers}
            if len(files_of) == 1 and _dir(next(iter(files_of))) == base and {i["id"] for i in movers} == {i["id"] for i in byfile[next(iter(files_of))]}:
                handled.update(i["id"] for i in movers)   # ce site a déjà son fichier, au bon endroit
                continue
            move_group(movers, unique(f"{base}/{_safe((svcs or rs)[0]['name'])}.yml"), "sites", reason)

    # 2. layout « keep » (ou ce qui reste) : jointure 1-pour-1 d'un service isolé avec son routeur déjà rangé
    if layout == "keep":
        users: dict[str, list[dict]] = defaultdict(list)
        for r in items:
            if r["kind"] == "routers" and (s := _service_name(r)):
                users[f"{r['proto']}/services/{s}"].append(r)
        joins: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for sid, rs in users.items():
            svc = by_id.get(sid)
            if not svc or len(rs) != 1:
                continue
            r = rs[0]
            for mover, anchor in ((svc, r), (r, svc)):
                if candidate(mover) and _dir(anchor["file"]) and not candidate(anchor):
                    joins[(mover["file"], anchor["file"])].append(mover)
                    handled.add(mover["id"])
                    break
        for (src, dst), ms in joins.items():
            steps.append({"kind": "blocks", "from": src, "to": dst, "ids": [m["id"] for m in ms], "items": [label(m) for m in ms],
                          "group": "pairs", "reason": f"Rangé avec son {'routeur' if ms[0]['kind'] == 'services' else 'service'} dans {dst}"})

    # 3. le reste (middlewares, TLS, transports, et fichiers entiers en mode « keep »), fichier par fichier
    for path in sorted(byfile):
        fitems = byfile[path]
        cands = [i for i in fitems if candidate(i) and i["id"] not in handled]
        if not cands:
            continue
        stays = [i for i in fitems if i["id"] not in handled and i not in cands]
        movers = [i for i in cands if _cls(i) != "app"] if (stays or any(_cls(i) == "app" for i in cands) or len({_cls(i) for i in cands}) > 1
                                                            or len({want(i) for i in cands}) > 1) else []
        remaining = [i for i in cands if i not in movers]
        for i in movers:  # extraits dans un fichier à eux, dans le dossier de leur type
            c = _cls(i)
            steps.append({"kind": "blocks", "from": path, "to": unique(f"{want(i)}/{_safe(i['name'])}.yml"), "ids": [i["id"]], "items": [label(i)],
                          "group": c, "reason": {"mw": "Middleware isolé", "tls": "Réglage TLS isolé", "tr": "Transport serveur isolé"}[c]})
        if remaining and not stays:  # tout le fichier part, intact
            c = _cls(remaining[0])
            wants = {want(i) for i in remaining}
            steps.append({"kind": "file", "from": path, "to": unique(f"{wants.pop() if len(wants) == 1 else target[c]}/{posixpath.basename(path)}"),
                          "ids": [i["id"] for i in remaining], "items": [label(i) for i in remaining], "group": c,
                          "reason": {"app": "Fichier d'application", "mw": "Fichier de middlewares", "tls": "Fichier TLS", "tr": "Fichier de transports serveur"}[c]})

    # 4. fichiers sans aucun contenu
    for path in sorted(empty or []):
        steps.append({"kind": "delete", "from": path, "to": "", "ids": [], "items": [], "group": "empty", "reason": "Fichier vide (aucun bloc) : supprimé, une copie reste dans l'historique"})
    for d in sorted(empty_dirs or []):
        steps.append({"kind": "rmdir", "from": d, "to": "", "ids": [], "items": [], "group": "emptydirs", "reason": "Dossier vide (aucun fichier dedans)"})
    in_dirs = sum(1 for i in items if _cls(i) == "app" and _dir(i["file"]))
    return {"steps": [dict(s, n=n) for n, s in enumerate(steps[:MAX_STEPS])], "folders": target, "truncated": len(steps) > MAX_STEPS,
            "stats": {"blocks": len(items), "sitesInFolders": in_dirs, "moved": sum(len(s["ids"]) for s in steps)}}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", name).strip(".-") or "element"
