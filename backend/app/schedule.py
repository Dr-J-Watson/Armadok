"""Plannings récurrents : une LISTE de règles, la prochaine échéance est la plus proche de toutes. Précision à la seconde, heures du serveur.

Une règle est un dictionnaire :
  {"kind": "every",   "days": 0, "hours": 6, "minutes": 30, "seconds": 0}        toutes les 6 h 30 (au moins 1 minute, au plus 90 jours)
  {"kind": "daily",   "every": 1, "time": "16:00:00"}                            chaque jour (ou tous les N jours après la dernière vérification) à l'heure dite
  {"kind": "weekly",  "days": [0..6 (0 = lundi)], "time": "16:00:00"}
  {"kind": "monthly", "days": [1..31, "last"], "months": [1..12] (vide = tous), "time": "…"}   (un mois plus court : son dernier jour)
  {"kind": "nth",     "nth": 1..4 | -1, "weekday": 0..6, "months": [...], "time": "…"}          le premier lundi, le dernier vendredi du mois
  {"kind": "once",    "at": "2026-12-25T09:00:00"}                               une seule fois
  {"kind": "cron",    "expr": "0 */15 8-18 * * 1-5"}                             5 champs (min h jour mois jour-semaine) ou 6 (secondes en tête) ; « L » = dernier jour du mois
Fonctions pures."""
import calendar
import re
from datetime import date, datetime, timedelta

MIN_GAP = 60   # deux échéances d'une même règle sont toujours espacées d'au moins une minute (un registre n'est pas fait pour être interrogé en boucle)
MAX_RULES = 10
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
NTH = {1: "premier", 2: "deuxième", 3: "troisième", 4: "quatrième", -1: "dernier"}
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?$")
HORIZON = 366 * 6   # jours explorés au plus pour trouver une échéance


def default_rules(hours: int = 24) -> list[dict]:
    return [{"kind": "every", "days": 0, "hours": max(1, int(hours or 24)), "minutes": 0, "seconds": 0}]


def _time(s) -> tuple[int, int, int]:
    m = _TIME.match(str(s or ""))
    if not m:
        raise ValueError("Heure invalide (HH:MM ou HH:MM:SS)")
    return int(m[1]), int(m[2]), int(m[3] or 0)


def _fmt_time(t: tuple[int, int, int]) -> str:
    return "%02d:%02d:%02d" % t


def _human_time(t: tuple[int, int, int]) -> str:
    h, m, s = t
    return f"{h} h" if not m and not s else f"{h} h {m:02d}" if not s else f"{h} h {m:02d} min {s:02d} s"


def _ints(v, lo: int, hi: int, what: str) -> list[int]:
    out = set()
    for x in v if isinstance(v, list) else []:
        try:
            n = int(x)
        except (TypeError, ValueError):
            raise ValueError(f"{what} invalide") from None
        if not lo <= n <= hi:
            raise ValueError(f"{what} invalide ({lo} à {hi})")
        out.add(n)
    return sorted(out)


# ------------------------------------------------------------------ cron
def _cron_field(text: str, lo: int, hi: int, name: str, allow_l: bool = False) -> tuple[set[int], bool, bool]:
    """(valeurs, « * » seul, contient L)."""
    vals, last = set(), False
    star = text in ("*", "?")
    for part in text.split(","):
        if allow_l and part.upper() == "L":
            last = True
            continue
        base, _, step = part.partition("/")
        try:
            st = int(step) if step else 1
            if st < 1:
                raise ValueError
            if base in ("*", "?"):
                a, b = lo, hi
            elif "-" in base:
                x, y = base.split("-", 1)
                a, b = int(x), int(y)
            else:
                a = int(base)
                b = hi if step else a
        except ValueError:
            raise ValueError(f"Expression invalide dans le champ « {name} »") from None
        if a < lo or b > hi or a > b:
            raise ValueError(f"Champ « {name} » : valeurs de {lo} à {hi}")
        vals.update(range(a, b + 1, st))
    return vals, star, last


def _cron(expr: str) -> dict:
    f = str(expr or "").split()
    if len(f) == 5:
        f = ["0", *f]
    if len(f) != 6:
        raise ValueError("Expression : 5 champs (minute heure jour mois jour-semaine) ou 6 avec les secondes en premier")
    sec, _, _ = _cron_field(f[0], 0, 59, "secondes")
    mi, _, _ = _cron_field(f[1], 0, 59, "minutes")
    hr, _, _ = _cron_field(f[2], 0, 23, "heures")
    dom, dom_star, dom_l = _cron_field(f[3], 1, 31, "jour du mois", allow_l=True)
    mon, _, _ = _cron_field(f[4], 1, 12, "mois")
    dow, dow_star, _ = _cron_field(f[5], 0, 7, "jour de la semaine")
    return {"sec": sorted(sec), "min": sorted(mi), "hour": sorted(hr), "dom": dom, "dom_star": dom_star, "dom_l": dom_l, "mon": mon, "dow": {d % 7 for d in dow}, "dow_star": dow_star}


# ------------------------------------------------------------------ validation
def validate_rule(spec) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("Règle invalide")
    kind = spec.get("kind")
    if kind == "every":
        if "unit" in spec and "hours" not in spec:   # ancien format : {every, unit}
            try:
                n = int(spec.get("every"))
            except (TypeError, ValueError):
                raise ValueError("Durée invalide") from None
            spec = {"days": n if spec.get("unit") == "days" else 0, "hours": n if spec.get("unit") == "hours" else 0, "minutes": 0, "seconds": 0}
        parts = {}
        for k in ("days", "hours", "minutes", "seconds"):
            try:
                parts[k] = int(spec.get(k) or 0)
            except (TypeError, ValueError):
                raise ValueError("Durée invalide") from None
            if parts[k] < 0:
                raise ValueError("Durée invalide")
        total = parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]
        if not MIN_GAP <= total <= 90 * 86400:
            raise ValueError("Durée invalide : de 1 minute à 90 jours")
        return {"kind": "every", **parts}
    if kind == "once":
        try:
            at = datetime.fromisoformat(str(spec.get("at") or ""))
        except ValueError:
            raise ValueError("Date invalide") from None
        return {"kind": "once", "at": at.replace(tzinfo=None, microsecond=0).isoformat()}
    if kind == "cron":
        expr = " ".join(str(spec.get("expr") or "").split())
        _cron(expr)
        rule = {"kind": "cron", "expr": expr}
        a = next_rule(rule, datetime(2026, 1, 1).timestamp())
        b = next_rule(rule, a) if a else None
        if a is None:
            raise ValueError("Cette expression ne tombe jamais")
        if b is not None and b - a < MIN_GAP:
            raise ValueError("Cette expression revient plus d'une fois par minute : 1 minute au plus rapproché")
        return rule
    t = _fmt_time(_time(spec.get("time")))
    if kind == "daily":
        try:
            n = int(spec.get("every") if spec.get("every") not in (None, "") else 1)
        except (TypeError, ValueError):
            raise ValueError("Nombre de jours invalide") from None
        if not 1 <= n <= 365:
            raise ValueError("Nombre de jours invalide (1 à 365)")
        return {"kind": "daily", "every": n, "time": t}
    if kind == "weekly":
        days = _ints(spec.get("days"), 0, 6, "Jour de la semaine")
        if not days:
            raise ValueError("Choisissez au moins un jour de la semaine")
        return {"kind": "weekly", "days": days, "time": t}
    if kind == "monthly":
        raw = spec.get("days") if isinstance(spec.get("days"), list) else []
        days = _ints([x for x in raw if x != "last"], 1, 31, "Jour du mois")
        if not days and "last" not in raw:
            raise ValueError("Choisissez au moins un jour du mois")
        return {"kind": "monthly", "days": [*days, *(["last"] if "last" in raw else [])], "months": _ints(spec.get("months"), 1, 12, "Mois"), "time": t}
    if kind == "nth":
        try:
            nth, wd = int(spec.get("nth")), int(spec.get("weekday"))
        except (TypeError, ValueError):
            raise ValueError("Règle invalide") from None
        if nth not in NTH or not 0 <= wd <= 6:
            raise ValueError("Règle invalide")
        return {"kind": "nth", "nth": nth, "weekday": wd, "months": _ints(spec.get("months"), 1, 12, "Mois"), "time": t}
    raise ValueError("Type de règle inconnu")


def validate(spec) -> list[dict]:
    """Une liste de règles (ou, ancien format, une règle seule / {"rules": [...]}) → liste de règles propres."""
    if isinstance(spec, dict):
        spec = spec["rules"] if isinstance(spec.get("rules"), list) else [spec]
    if not isinstance(spec, list) or not 1 <= len(spec) <= MAX_RULES:
        raise ValueError(f"Un planning contient de 1 à {MAX_RULES} règles")
    return [validate_rule(r) for r in spec]


# ------------------------------------------------------------------ prochaine échéance
def _last_day(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def _search(after: float, day_ok, times) -> float | None:
    start = datetime.fromtimestamp(after).date()
    for i in range(HORIZON):
        d = start + timedelta(days=i)
        if not day_ok(d):
            continue
        for h, m, s in times():
            ts = datetime(d.year, d.month, d.day, h, m, s).timestamp()
            if ts > after:
                return ts
    return None


def next_rule(rule: dict, after: float) -> float | None:
    """Le premier instant STRICTEMENT après `after` (timestamp) pour cette règle ; None si elle n'aura plus lieu."""
    kind = rule["kind"]
    if kind == "every":
        return after + rule["days"] * 86400 + rule["hours"] * 3600 + rule["minutes"] * 60 + rule["seconds"]
    if kind == "once":
        ts = datetime.fromisoformat(rule["at"]).timestamp()
        return ts if ts > after else None
    if kind == "cron":
        c = _cron(rule["expr"])

        def ok(d):
            if d.month not in c["mon"]:
                return False
            by_dom = d.day in c["dom"] or (c["dom_l"] and d.day == _last_day(d))
            by_dow = (d.isoweekday() % 7) in c["dow"]
            if not c["dom_star"] and not c["dow_star"]:
                return by_dom or by_dow
            return (by_dom or c["dom_star"]) and (by_dow or c["dow_star"])

        def times():
            for h in c["hour"]:
                for m in c["min"]:
                    for s in c["sec"]:
                        yield h, m, s
        return _search(after, ok, times)
    t = _time(rule["time"])
    if kind == "daily":
        if rule["every"] > 1:
            d = datetime.fromtimestamp(after).date() + timedelta(days=rule["every"])
            return datetime(d.year, d.month, d.day, *t).timestamp()
        return _search(after, lambda d: True, lambda: [t])
    if kind == "weekly":
        return _search(after, lambda d: d.weekday() in rule["days"], lambda: [t])
    months = set(rule.get("months") or [])
    if kind == "monthly":
        def ok(d):
            if months and d.month not in months:
                return False
            last = _last_day(d)
            return any(x == "last" and d.day == last or x != "last" and min(x, last) == d.day for x in rule["days"])
        return _search(after, ok, lambda: [t])
    if kind == "nth":
        def ok(d):
            if (months and d.month not in months) or d.weekday() != rule["weekday"]:
                return False
            return d.day + 7 > _last_day(d) if rule["nth"] == -1 else (d.day - 1) // 7 + 1 == rule["nth"]
        return _search(after, ok, lambda: [t])
    return None


def next_run(rules: list[dict], after: float) -> float | None:
    """La plus proche échéance de toutes les règles ; None si plus aucune."""
    ts = [x for x in (next_rule(r, after) for r in rules) if x is not None]
    return min(ts) if ts else None


def upcoming(rules: list[dict], after: float, n: int = 5) -> list[float]:
    out, t = [], after
    for _ in range(n):
        t = next_run(rules, t)
        if t is None:
            break
        out.append(t)
    return out


# ------------------------------------------------------------------ texte
def _months_text(months: list[int]) -> str:
    return "de chaque mois" if not months else "en " + ", ".join(MONTHS[m - 1] for m in months)


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " et " + items[-1]


def describe_rule(r: dict) -> str:
    k = r["kind"]
    if k == "every":
        bits = [f"{r[key]} {lab}" for key, lab in (("days", "j"), ("hours", "h"), ("minutes", "min"), ("seconds", "s")) if r[key]]
        return "Toutes les " + " ".join(bits)
    if k == "once":
        d = datetime.fromisoformat(r["at"])
        return f"Une fois, le {d:%d/%m/%Y} à {_human_time((d.hour, d.minute, d.second))}"
    if k == "cron":
        return f"Expression « {r['expr']} »"
    t = _human_time(_time(r["time"]))
    if k == "daily":
        return f"Chaque jour à {t}" if r["every"] == 1 else f"Tous les {r['every']} jours à {t}"
    if k == "weekly":
        return "Chaque " + _join([DAYS[d] for d in r["days"]]) + f" à {t}"
    if k == "monthly":
        days = [("dernier jour" if d == "last" else "1er" if d == 1 else str(d)) for d in r["days"]]
        return "Le " + _join(days) + f" {_months_text(r.get('months') or [])} à {t}"
    if k == "nth":
        return f"Le {NTH[r['nth']]} {DAYS[r['weekday']]} {_months_text(r.get('months') or [])} à {t}"
    return "?"


def describe(rules: list[dict]) -> list[str]:
    return [describe_rule(r) for r in rules]
