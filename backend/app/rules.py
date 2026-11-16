"""Parseur de règles Traefik (Host(`a`) && !PathPrefix(`/x`) || …) pour contrôler les droits côté serveur."""
import re

TOKEN = re.compile(r"\s*(&&|\|\||!|\(|\)|,|`[^`]*`|\"[^\"]*\"|'[^']*'|[A-Za-z_][A-Za-z0-9_]*)")


def tokenize(rule: str) -> list[str]:
    pos, out = 0, []
    rule = rule.strip()
    while pos < len(rule):
        m = TOKEN.match(rule, pos)
        if not m:
            raise ValueError(f"Règle invalide près de « {rule[pos:pos + 12]} »")
        out.append(m.group(1))
        pos = m.end()
        while pos < len(rule) and rule[pos].isspace():
            pos += 1
    return out


def parse(rule: str):
    toks = tokenize(rule)
    i = 0

    def peek():
        return toks[i] if i < len(toks) else None

    def take(expected=None):
        nonlocal i
        t = peek()
        if t is None or (expected and t != expected):
            raise ValueError("Règle invalide")
        i += 1
        return t

    def p_or():
        nodes = [p_and()]
        while peek() == "||":
            take()
            nodes.append(p_and())
        return nodes[0] if len(nodes) == 1 else ("or", nodes)

    def p_and():
        nodes = [p_not()]
        while peek() == "&&":
            take()
            nodes.append(p_not())
        return nodes[0] if len(nodes) == 1 else ("and", nodes)

    def p_not():
        if peek() == "!":
            take()
            return ("not", p_not())
        return p_atom()

    def p_atom():
        if peek() == "(":
            take()
            n = p_or()
            take(")")
            return n
        name = take()
        if not re.match(r"^[A-Za-z_]", name):
            raise ValueError("Règle invalide")
        take("(")
        args = []
        while peek() != ")":
            args.append(take()[1:-1] if peek()[0] in "`\"'" else take())
            if peek() == ",":
                take()
        take(")")
        return ("fn", name, args)

    if not toks:
        raise ValueError("Règle vide")
    node = p_or()
    if i != len(toks):
        raise ValueError("Règle invalide")
    return node


def host_constraint(node) -> set[str] | None:
    """Ensemble d'hôtes auquel la règle est limitée, ou None si elle peut correspondre à n'importe quel hôte."""
    kind = node[0]
    if kind == "fn":
        if node[1] == "Host":
            return {a.lower() for a in node[2]}
        return None
    if kind == "not":
        return None
    parts = [host_constraint(n) for n in node[1]]
    if kind == "and":
        known = [p for p in parts if p is not None]
        return set().union(*known) if known else None  # conservateur : tous les hôtes cités doivent être autorisés
    if any(p is None for p in parts):
        return None
    return set().union(*parts)
