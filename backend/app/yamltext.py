"""Opérations directement sur le texte YAML pour activer / désactiver des blocs sans réécrire le fichier.

Un bloc désactivé reste dans le fichier, chaque ligne préfixée de « #~ » juste après son indentation (réversible à l'identique,
commentaires compris). Les lignes « #% » sont des notes de reprise : références retirées d'un routeur, motif d'une pause.
"""
import json
import re

MARK, META = "#~ ", "#% "
_MARKED = re.compile(r"^( *)#~ ?")
_KEY = re.compile(r"""^( *)(?:"([^"]*)"|'([^']*)'|([^\s#"'][^:#]*?))\s*:(?:\s|$)""")
_BARE = re.compile(r"^[A-Za-z0-9_./@-]+$")


class Unsupported(Exception):
    """Mise en forme que l'on ne sait pas modifier proprement (YAML « en ligne » sur plusieurs lignes, par exemple)."""


def indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def is_marked(line: str) -> bool:
    return bool(_MARKED.match(line))


def uncomment(text: str) -> str:
    """Le texte comme si tout était activé. Le nombre de lignes ne change jamais : les numéros de ligne restent valables."""
    return "\n".join(_MARKED.sub(r"\1", ln) for ln in text.split("\n"))


def key_of(line: str) -> str | None:
    m = _KEY.match(line)
    if not m:
        return None
    return next(g for g in m.groups()[1:] if g is not None).strip()


def is_blank(line: str) -> bool:
    return not line.strip()


def block_end(lines: list[str], start: int, indentless: bool = False) -> int:
    """Fin (exclue) du bloc qui commence à `start` : tout ce qui est plus indenté, commentaires compris, sans les blancs finaux."""
    base, end, i = indent(lines[start]), start + 1, start + 1
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            i += 1
            continue
        if indent(ln) > base or (indentless and indent(ln) == base and (s == "-" or s.startswith("- "))):
            end = i + 1
            i += 1
            continue
        break
    return end


def child_indent(lines: list[str], start: int, end: int) -> int | None:
    for ln in lines[start + 1:end]:
        if ln.strip() and not ln.lstrip().startswith("#"):
            return indent(ln)
    return None


def find_child(lines: list[str], start: int, end: int, key: str) -> int | None:
    ci = child_indent(lines, start, end)
    if ci is None:
        return None
    for i in range(start + 1, end):
        if indent(lines[i]) == ci and not lines[i].lstrip().startswith("#") and key_of(lines[i]) == key:
            return i
    return None


def comment_range(lines: list[str], start: int, end: int) -> None:
    for i in range(start, end):
        if lines[i].strip():
            n = indent(lines[i])
            lines[i] = lines[i][:n] + MARK + lines[i][n:]


def uncomment_range(lines: list[str], start: int, end: int) -> None:
    for i in range(start, end):
        lines[i] = _MARKED.sub(r"\1", lines[i])


# ------------------------------------------------------------------ notes de reprise
_PAUSE = re.compile(r"^ *#% pause (.+)$")
_REF = re.compile(r"^ *#% ref (\S+) = (.*)$")


def pause_tag(lines: list[str], start: int) -> str | None:
    """Motif de la pause (ex. « domain:exemple.fr ») d'un bloc désactivé, d'après le texte décommenté."""
    for ln in lines[start + 1:block_end(lines, start)]:
        if m := _PAUSE.match(ln):
            return m.group(1).strip()
    return None


def add_pause_tag(lines: list[str], start: int, tag: str) -> None:
    lines.insert(start + 1, " " * (indent(lines[start]) + 2) + META + f"pause {tag}")


def drop_pause_tag(lines: list[str], start: int) -> None:
    for i in range(block_end(lines, start) - 1, start, -1):
        if _PAUSE.match(lines[i]):
            del lines[i]


def read_refs(lines: list[str], start: int) -> dict[str, object]:
    out = {}
    for ln in lines[start + 1:block_end(lines, start)]:
        if m := _REF.match(ln):
            try:
                out[m.group(1)] = json.loads(m.group(2))
            except ValueError:
                pass
    return out


def write_refs(lines: list[str], start: int, refs: dict[str, object]) -> None:
    for i in range(block_end(lines, start) - 1, start, -1):
        if _REF.match(lines[i]):
            del lines[i]
    pad = " " * (indent(lines[start]) + 2)
    for n, (path, value) in enumerate(sorted(refs.items())):
        lines.insert(start + 1 + n, f"{pad}{META}ref {path} = {json.dumps(value, ensure_ascii=False)}")


# ------------------------------------------------------------------ valeurs (listes et textes) dans un bloc
def _fmt(x: str) -> str:
    return x if _BARE.match(x) else json.dumps(x, ensure_ascii=False)


def _items_of(lines: list[str]) -> dict[str, str]:
    """valeur -> ligne d'origine, pour garder à l'identique (commentaires compris) les entrées de liste qui restent."""
    out = {}
    for ln in lines:
        s = ln.strip()
        if s == "-" or s.startswith("- "):
            val = re.sub(r"\s+#.*$", "", s[1:]).strip().strip("\"'")
            out[val] = ln
    return out


_EMPTY_MAP = re.compile(r":\s*\{\s*\}(\s*#.*)?$")


def _open_parent(lines: list[str], idx: int) -> None:
    """`tls: {}` redevient `tls:` quand on lui ajoute un enfant."""
    lines[idx] = _EMPTY_MAP.sub(lambda m: ":" + (m.group(1) or ""), lines[idx])


def _close_parent(lines: list[str], idx: int) -> None:
    """Un `tls:` vidé doit rester `tls: {}` : sans valeur, YAML le lit comme « rien » et Traefik désactiverait le TLS du routeur."""
    if child_indent(lines, idx, block_end(lines, idx)) is None and re.search(r":\s*(#.*)?$", lines[idx]):
        lines[idx] = re.sub(r":(\s*#.*)?$", lambda m: ": {}" + (m.group(1) or ""), lines[idx])


def set_value(lines: list[str], start: int, path: tuple[str, ...], value) -> bool:
    """Pose (liste ou texte) ou retire (None) la clé `path` dans le bloc commençant à `start`. Retourne True si le texte a changé."""
    parent = start
    chain: list[int] = []
    for part in path[:-1]:
        end = block_end(lines, parent)
        idx = find_child(lines, parent, end, part)
        if idx is None:
            if value is None:
                return False
            ci = child_indent(lines, parent, end) or indent(lines[parent]) + 2
            lines.insert(end, " " * ci + f"{part}:")
            idx = end
        elif _EMPTY_MAP.search(lines[idx]):
            if value is None:
                return False
            _open_parent(lines, idx)
        elif re.search(r":\s*[{\[]", lines[idx]):
            raise Unsupported("mise en forme en ligne")
        parent = idx
        chain.append(idx)
    leaf = path[-1]
    end = block_end(lines, parent)
    ci = child_indent(lines, parent, end) or indent(lines[parent]) + 2
    pad = " " * ci
    idx = find_child(lines, parent, end, leaf)
    old: list[str] = []
    flow = False
    offset = 2
    if idx is not None:
        stop = block_end(lines, idx, indentless=True)
        old = lines[idx:stop]
        after = old[0].split(":", 1)[1].strip()
        flow = after.startswith("[")
        if flow and (not after.endswith("]") or len(old) > 1):
            raise Unsupported("liste sur plusieurs lignes")
        dashes = [ln for ln in old[1:] if ln.strip().startswith("-")]
        offset = indent(dashes[0]) - indent(old[0]) if dashes else 2
    if value is None:
        new: list[str] = []
    elif isinstance(value, list):
        if not value:
            new = []
        elif flow:
            new = [f"{pad}{leaf}: {json.dumps(value, ensure_ascii=False)}"]
        else:
            kept = _items_of(old)
            new = [f"{pad}{leaf}:"] + [kept.get(x) or f"{pad}{' ' * offset}- {_fmt(x)}" for x in value]
    else:
        new = [f"{pad}{leaf}: {_fmt(str(value))}"]
    if idx is None:
        if not new:
            return False
        lines[end:end] = new
        return True
    if new == old:
        return False
    lines[idx:idx + len(old)] = new
    if not new and chain:
        _close_parent(lines, chain[-1])
    return True


# ------------------------------------------------------------------ sections vides
def sync_sections(text: str, sections: dict[str, tuple[str, ...]], doc) -> str:
    """Traefik refuse tout le dossier si un fichier contient `http:` sans rien dedans : quand tous les blocs d'une section sont en
    commentaire, la section l'est aussi (et elle redevient active dès qu'un bloc l'est). `doc` : le texte décommenté, déjà analysé."""
    lines = text.split("\n")
    for top, subs in sections.items():
        sec = doc.get(top)
        if not isinstance(sec, dict) or getattr(sec.fa, "flow_style", lambda: False)():
            continue
        sub_marks = {}
        for sub in subs:
            block = sec.get(sub)
            if not isinstance(block, dict) or not block or getattr(block.fa, "flow_style", lambda: False)():
                continue
            kids = [is_marked(lines[block.lc.key(k)[0]]) for k in block]
            sub_marks[sub] = (all(kids), doc[top].lc.key(sub)[0])
        for sub, (want, ln) in sub_marks.items():
            lines[ln] = _set_mark(lines[ln], want)
        others = [k for k in sec if k not in subs]
        want_top = bool(sub_marks) and not others and all(w for w, _ in sub_marks.values()) and len(sub_marks) == len(sec)
        lines[doc.lc.key(top)[0]] = _set_mark(lines[doc.lc.key(top)[0]], want_top)
    return "\n".join(lines)


def _set_mark(line: str, marked: bool) -> str:
    if marked == is_marked(line):
        return line
    n = indent(line)
    return line[:n] + MARK + line[n:] if marked else _MARKED.sub(r"\1", line)


def drop_empty_sections(text: str, sections: dict[str, tuple[str, ...]], doc) -> str:
    """Retire les `routers:` / `http:` laissés vides (sans enfant du tout) après une suppression."""
    lines = text.split("\n")
    gone: list[int] = []
    for top, subs in sections.items():
        if top not in doc:
            continue
        sec = doc[top]
        if isinstance(sec, dict):
            for sub in subs:
                if sub in sec and sec[sub] is None and not is_marked(lines[sec.lc.key(sub)[0]]):
                    gone.append(sec.lc.key(sub)[0])
            if all(k in subs and sec[k] is None for k in sec) and sec:
                gone.append(doc.lc.key(top)[0])
        elif sec is None:
            gone.append(doc.lc.key(top)[0])
    for i in sorted(set(gone), reverse=True):
        del lines[i]
    return "\n".join(lines)
