"""Renommer un élément (middleware, service, transport, option TLS) renomme aussi toutes les références à lui :
dans les blocs actifs, dans les blocs mis en pause (« #~ »), dans les fichiers désactivés (.off) et dans les notes « #% ref »."""
import json
import re

REF_KEY = {"middlewares": "middlewares", "options": "options"}   # clé que vise une note « #% ref chemin = [...] »


def _sub(v, old: str, new: str):
    """Nouvelle valeur si `v` désigne l'élément (nom seul ou avec @file), sinon None."""
    if isinstance(v, str):
        if v == old:
            return new
        if v == old + "@file":
            return new + "@file"
    return None


def _maps(node):
    return [v for v in (node.values() if hasattr(node, "values") else []) if hasattr(v, "get")]


def rename_in_doc(doc, proto: str, kind: str, old: str, new: str) -> int:
    """Remplace les références dans un document YAML chargé (les commentaires sont conservés). Retourne le nombre de remplacements."""
    n = 0

    def scalar(parent, key):
        nonlocal n
        if hasattr(parent, "get") and (r := _sub(parent.get(key), old, new)) is not None:
            parent[key] = r
            n += 1

    def names(parent, key):
        nonlocal n
        lst = parent.get(key) if hasattr(parent, "get") else None
        if isinstance(lst, list):
            for i, v in enumerate(lst):
                if (r := _sub(v, old, new)) is not None:
                    lst[i] = r
                    n += 1

    if (proto, kind) == ("tls", "options"):
        for p in ("http", "tcp"):
            for r in _maps((doc.get(p) or {}).get("routers") or {}):
                scalar(r.get("tls"), "options")
        return n
    if proto not in ("http", "tcp", "udp"):
        return 0
    sec = doc.get(proto) or {}
    if kind == "middlewares":
        for r in _maps(sec.get("routers") or {}):
            names(r, "middlewares")
        for m in _maps(sec.get("middlewares") or {}):
            names(m.get("chain"), "middlewares")
    elif kind == "services":
        for r in _maps(sec.get("routers") or {}):
            scalar(r, "service")
        for m in _maps(sec.get("middlewares") or {}):
            scalar(m.get("errors"), "service")
        for s in _maps(sec.get("services") or {}):
            for e in (s.get("weighted") or {}).get("services") or []:
                scalar(e, "name")
            mir = s.get("mirroring") or {}
            scalar(mir, "service")
            for e in mir.get("mirrors") or []:
                scalar(e, "name")
            for k in ("service", "fallback"):
                scalar(s.get("failover"), k)
    elif kind == "serversTransports":
        for s in _maps(sec.get("services") or {}):
            scalar(s.get("loadBalancer"), "serversTransport")
    return n


_Q = r"""(["']?)"""


def rename_text(text: str, proto: str, kind: str, old: str, new: str) -> str:
    """Même remplacement dans le texte des lignes mises en commentaire (« #~ ») et des notes « #% ref »."""
    o = re.escape(old)
    value = lambda key: re.compile(rf"^(\s*(?:- )?(?:{key}):\s*){_Q}{o}(@file)?\2\s*$")                          # noqa: E731
    item = re.compile(rf"^(\s*- ){_Q}{o}(@file)?\2\s*$")
    flow = re.compile(r"^(\s*middlewares:\s*\[)(.*)(\]\s*)$")
    keys = {"services": value("service|fallback|name"), "serversTransports": value("serversTransport"), "options": value("options")}

    def swap(p: str) -> str:
        mm = re.fullmatch(rf'(["\']?){o}(@file)?\1', p)
        return f"{mm.group(1)}{new}{mm.group(2) or ''}{mm.group(1)}" if mm else p

    def fix_content(c: str) -> str:
        if kind == "middlewares":
            if m := item.match(c):
                return f"{m.group(1)}{m.group(2)}{new}{m.group(3) or ''}{m.group(2)}"
            if m := flow.match(c):
                parts = [swap(p.strip()) for p in m.group(2).split(",")]
                return f"{m.group(1)}{', '.join(parts)}{m.group(3)}"
        elif (rx := keys.get(kind)) and (m := rx.match(c)):
            return f"{m.group(1)}{m.group(2)}{new}{m.group(3) or ''}{m.group(2)}"
        return c

    out = []
    for ln in text.split("\n"):
        if m := re.match(r"^(\s*#~ )(.*)$", ln):
            ln = m.group(1) + fix_content(m.group(2))
        elif (m := re.match(r"^(\s*#% ref (\S+) = )(.*)$", ln)) and kind in REF_KEY and m.group(2).split(".")[-1] == REF_KEY[kind]:
            try:
                val = json.loads(m.group(3))
                if isinstance(val, list):
                    val = [_sub(v, old, new) or v for v in val]
                    ln = m.group(1) + json.dumps(val, ensure_ascii=False)
            except ValueError:
                pass
        out.append(ln)
    return "\n".join(out)
