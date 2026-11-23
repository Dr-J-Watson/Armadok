"""Automod : des règles qui bannissent automatiquement les adresses qui répondent à des CONDITIONS (pays, suspecte, type d'attaque, volume…).

Une règle = des conditions (toutes doivent être vraies) + une action (bannir pour X heures, ou définitivement) + un mode :
 - « observation » : la règle ne bannit rien, elle journalise ce qu'elle AURAIT banni (à utiliser d'abord, pour vérifier) ;
 - « blocage » : elle bannit réellement, via le mécanisme de bannissement habituel (règle Traefik, 403 partout).
Garde-fous : jamais une adresse du réseau local ni une adresse validée, jamais une adresse déjà bannie, jamais plus de MAX_PER_RUN bannissements par
passe et par règle (une règle trop large s'arrête d'elle-même et le dit), mêmes limites que les bannissements manuels. Une adresse dont le pays est
inconnu n'est JAMAIS bannie par une condition de pays. Les bannissements automatiques sont reconnaissables (« automod : nom de la règle »)."""
import json
import re
import threading
import time

from . import accesslog, bans, db, geo
from .events import publish

MAX_RULES = 30
MAX_PER_RUN = 25
EVERY = 30   # secondes entre deux passes
WINDOW_DAYS = 1
KEEP_LOG_DAYS = 30
ATTACK_KINDS = ("leak", "sensitive_file", "cms_probe", "traversal", "sqli", "xss", "rce", "scanner_ua", "bruteforce", "enumeration", "flood")


class AutomodError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def clean_conditions(raw) -> dict:
    """Conditions saisies -> forme canonique, ou AutomodError. Rien d'inattendu n'est conservé."""
    if not isinstance(raw, dict):
        raise AutomodError("Conditions invalides")
    out: dict = {}
    c = raw.get("country")
    if c:
        mode = c.get("mode") if isinstance(c, dict) else None
        codes = sorted({str(x).upper() for x in (c.get("codes") or []) if isinstance(x, str) and re.fullmatch(r"[A-Za-z]{2}", x)}) if isinstance(c, dict) else []
        if mode not in ("in", "not_in") or not codes:
            raise AutomodError("Condition de pays : choisissez « est dans » ou « n'est pas dans » et au moins un pays")
        if len(codes) > 250:
            raise AutomodError("Trop de pays")
        out["country"] = {"mode": mode, "codes": codes}
    if raw.get("suspicious"):
        out["suspicious"] = True
    kinds = raw.get("attacks")
    if kinds:
        if kinds == "any" or kinds == ["any"]:
            out["attacks"] = "any"
        elif isinstance(kinds, list) and all(isinstance(k, str) and k in ATTACK_KINDS for k in kinds) and kinds:
            out["attacks"] = sorted(set(kinds))
        else:
            raise AutomodError("Type d'attaque inconnu")
    for k, lo, hi in (("min_hits", 1, 1_000_000), ("min_errors", 1, 1_000_000)):
        v = raw.get(k)
        if v not in (None, "", 0):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise AutomodError("Nombre invalide")
            if not lo <= v <= hi:
                raise AutomodError("Nombre hors limites")
            out[k] = v
    if not out:
        raise AutomodError("Ajoutez au moins une condition : une règle sans condition bannirait tout le monde")
    return out


def describe(cond: dict) -> list[str]:
    parts = []
    if "country" in cond:
        parts.append(f"pays {'dans' if cond['country']['mode'] == 'in' else 'hors de'} {', '.join(cond['country']['codes'])}")
    if cond.get("suspicious"):
        parts.append("détectée comme suspecte")
    if "attacks" in cond:
        parts.append("attaque détectée" if cond["attacks"] == "any" else f"attaque : {', '.join(cond['attacks'])}")
    if "min_hits" in cond:
        parts.append(f"≥ {cond['min_hits']} requêtes")
    if "min_errors" in cond:
        parts.append(f"≥ {cond['min_errors']} erreurs")
    return parts


def matches(cond: dict, row: dict, country: str | None) -> bool:
    """Toutes les conditions doivent être vraies. Une condition de pays ne se vérifie que si le pays est connu."""
    c = cond.get("country")
    if c:
        if not country:
            return False
        if (country in c["codes"]) != (c["mode"] == "in"):
            return False
    if cond.get("suspicious") and not row.get("suspicious"):
        return False
    a = cond.get("attacks")
    if a:
        kinds = {x["kind"] for x in row.get("attacks") or []}
        if not kinds if a == "any" else not (kinds & set(a)):
            return False
    if row.get("hits", 0) < cond.get("min_hits", 0) or row.get("errors", 0) < cond.get("min_errors", 0):
        return False
    return True


# ------------------------------------------------------------------ règles
def _out(r) -> dict:
    d = dict(r)
    d["conditions"] = json.loads(d["conditions"])
    d["summary"] = describe(d["conditions"])
    d["enabled"] = bool(d["enabled"])
    return d


def list_rules(node_id: str) -> list[dict]:
    return [_out(r) for r in db.rows("SELECT * FROM automod_rules WHERE node_id=? ORDER BY id", (node_id,))]


def save_rule(node_id: str, rid: int | None, name: str, conditions, mode: str, hours: float | None, enabled: bool, by: str) -> dict:
    name = (name or "").strip()[:80]
    if not name:
        raise AutomodError("Donnez un nom à la règle")
    cond = clean_conditions(conditions)
    if mode not in ("observe", "block"):
        raise AutomodError("Mode inconnu")
    if hours is not None and not 0 < hours <= 24 * 365:
        raise AutomodError("Durée invalide")
    if "country" in cond and not geo.status()["available"]:
        raise AutomodError("Une condition de pays demande la base de localisation (Surveillance → Adresses IP → Télécharger la base)")
    now = time.time()
    if rid is None:
        if db.one("SELECT COUNT(*) n FROM automod_rules WHERE node_id=?", (node_id,))["n"] >= MAX_RULES:
            raise AutomodError(f"{MAX_RULES} règles au plus", 409)
        rid = db.run("INSERT INTO automod_rules(node_id, name, enabled, mode, conditions, hours, created_by, created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (node_id, name, int(enabled), mode, json.dumps(cond), hours, by, now))
    else:
        if not db.one("SELECT 1 FROM automod_rules WHERE node_id=? AND id=?", (node_id, rid)):
            raise AutomodError("Règle introuvable", 404)
        db.run("UPDATE automod_rules SET name=?, enabled=?, mode=?, conditions=?, hours=? WHERE node_id=? AND id=?", (name, int(enabled), mode, json.dumps(cond), hours, node_id, rid))
    publish("security", node_id)
    return _out(db.one("SELECT * FROM automod_rules WHERE node_id=? AND id=?", (node_id, rid)))


def delete_rule(node_id: str, rid: int) -> str | None:
    r = db.one("SELECT name FROM automod_rules WHERE node_id=? AND id=?", (node_id, rid))
    if not r:
        return None
    db.run("DELETE FROM automod_rules WHERE node_id=? AND id=?", (node_id, rid))
    publish("security", node_id)
    return r["name"]


def recent_log(node_id: str, limit: int = 100) -> list[dict]:
    return [dict(r) for r in db.rows("SELECT id, rule_id, rule_name, ip, action, detail, at FROM automod_log WHERE node_id=? ORDER BY at DESC, id DESC LIMIT ?", (node_id, limit))]


# ------------------------------------------------------------------ évaluation
PROXY_SHARE = 0.20   # une adresse qui porte plus de 20 % de tout le trafic est probablement un proxy / CDN (tous les visiteurs derrière elle), pas un visiteur
PROXY_MIN_HITS = 200


def _candidates(node_id: str) -> list[tuple[dict, str | None]]:
    """Adresses publiques de la fenêtre, avec leur pays (si la base de localisation est là). Une adresse qui porte une part énorme du trafic est écartée :
    la bannir couperait tous les visiteurs qui passent par elle (Cloudflare, autre proxy…)."""
    lst = accesslog.listing(node_id, WINDOW_DAYS, "hits", "public", "", 5000, "")
    total = max(1, lst["total"]["hits"])
    out = []
    for r in lst["rows"]:
        if r["exempt"] or r["banned"]:
            continue
        r["proxy_like"] = r["hits"] >= PROXY_MIN_HITS and r["hits"] / total > PROXY_SHARE
        out.append((r, (geo.lookup(r["ip"], precise=False) or {}).get("country")))
    return out


def preview(node_id: str, conditions: dict, limit: int = 50) -> dict:
    """Qui serait visé MAINTENANT par ces conditions ? (aucune action)"""
    cond = clean_conditions(conditions)
    hit = [(r, c) for r, c in _candidates(node_id) if matches(cond, r, c)]
    return {"count": len(hit), "rows": [{"ip": r["ip"], "country": c, "hits": r["hits"], "errors": r["errors"], "attacks": [a["label"] for a in r["attacks"]], "suspicious": r["suspicious"]} for r, c in hit[:limit]]}


def run_rules(node: dict, store, now: float | None = None) -> dict:
    """Une passe : applique chaque règle active. Renvoie {rule_id: nombre d'adresses visées}."""
    now = now or time.time()
    rules = [r for r in list_rules(node["id"]) if r["enabled"]]
    if not rules:
        return {}
    cands = _candidates(node["id"])
    done: dict[int, int] = {}
    for rule in rules:
        n = 0       # adresses visées pendant cette passe
        banned = 0  # dont réellement bannies
        for row, country in cands:
            if not matches(rule["conditions"], row, country):
                continue
            ip = row["ip"]
            if row.get("proxy_like"):
                if not db.one("SELECT 1 FROM automod_log WHERE node_id=? AND rule_id=? AND ip=? AND action='skip' AND at>?", (node["id"], rule["id"], ip, now - 86400)):
                    _log(node["id"], rule, ip, "skip", f"écartée : elle porte {row['hits']} requêtes, une part énorme du trafic (proxy ou CDN ? la bannir couperait tous ses visiteurs)", now)
                continue
            if rule["mode"] == "observe":
                if db.one("SELECT 1 FROM automod_log WHERE node_id=? AND rule_id=? AND ip=? AND action='would_ban' AND at>?", (node["id"], rule["id"], ip, now - 86400)):
                    continue
                _log(node["id"], rule, ip, "would_ban", f"{country or '?'} · {row['hits']} req. · {row['errors']} err.", now)
                n += 1
                continue
            if n >= MAX_PER_RUN:
                _log(node["id"], rule, "", "limit", f"plus de {MAX_PER_RUN} adresses visées d'un coup : la règle est suspendue (conditions trop larges ?)", now)
                db.run("UPDATE automod_rules SET enabled=0 WHERE id=?", (rule["id"],))
                break
            try:
                bans.add(node, store, ip, f"automod : {rule['name']}", rule["hours"], f"automod:{rule['id']}", [])
            except bans.BanError as e:
                if e.status != 409:   # déjà bannie : normal ; autre refus (réseau local…) : on ne retente pas à chaque passe
                    _log(node["id"], rule, ip, "skip", str(e), now)
                continue
            except Exception as e:  # noqa: BLE001 - Traefik injoignable, fichier non inscriptible… : repris à la prochaine passe
                _log(node["id"], rule, ip, "error", str(e)[:160], now)
                break
            _log(node["id"], rule, ip, "ban", f"{country or '?'} · {row['hits']} req. · {row['errors']} err.", now)
            n += 1
            banned += 1
        if banned:
            db.run("UPDATE automod_rules SET hits=hits+?, last_hit=? WHERE id=?", (banned, now, rule["id"]))
        if n:
            done[rule["id"]] = n
    if done:
        publish("security", node["id"])
    return done


def _log(node_id: str, rule: dict, ip: str, action: str, detail: str, now: float) -> None:
    db.run("INSERT INTO automod_log(node_id, rule_id, rule_name, ip, action, detail, at) VALUES(?,?,?,?,?,?,?)", (node_id, rule["id"], rule["name"], ip, action, detail, now))


def purge_log(now: float | None = None) -> None:
    db.run("DELETE FROM automod_log WHERE at<?", ((now or time.time()) - KEEP_LOG_DAYS * 86400,))


_started = False


def start() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        from .nodes import node_out, store_for
        last_purge = 0.0
        while True:
            time.sleep(EVERY)
            try:
                for row in db.rows("SELECT * FROM nodes WHERE status='active' AND deleted_at IS NULL ORDER BY created_at"):
                    try:
                        if accesslog.available(row["id"]):
                            node = node_out(row)
                            run_rules(node, store_for(node))
                    except Exception:  # noqa: BLE001 - un nœud en panne ne doit pas empêcher les autres
                        pass
                if time.time() - last_purge > 3600:
                    purge_log()
                    last_purge = time.time()
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=loop, name="automod", daemon=True).start()
