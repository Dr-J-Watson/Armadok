"""Planning d'une vérification récurrente : « toutes les 6 heures », « chaque mardi à 16 h », « le 10 de chaque mois à 9 h ».

Un planning est un petit dictionnaire :
  {"kind": "every",   "every": N, "unit": "hours" | "days"}
  {"kind": "weekly",  "days": [0..6 (0 = lundi)], "time": "HH:MM"}
  {"kind": "monthly", "day": 1..31, "time": "HH:MM"}   (un mois plus court : son dernier jour)
Les heures sont celles du serveur. Fonctions pures."""
import calendar
import re
from datetime import datetime, timedelta

DEFAULT = {"kind": "every", "every": 24, "unit": "hours"}
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def validate(spec) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("Planning invalide")
    kind = spec.get("kind")
    if kind == "every":
        unit = spec.get("unit")
        try:
            n = int(spec.get("every"))
        except (TypeError, ValueError):
            raise ValueError("Durée invalide") from None
        if unit not in ("hours", "days") or not 1 <= n <= (24 * 90 if unit == "hours" else 90):
            raise ValueError("Durée invalide : de 1 heure à 90 jours")
        return {"kind": "every", "every": n, "unit": unit}
    t = str(spec.get("time") or "")
    if kind in ("weekly", "monthly") and not _TIME.match(t):
        raise ValueError("Heure invalide (HH:MM)")
    if kind == "weekly":
        days = sorted({int(d) for d in spec.get("days") or [] if str(d).isdigit() and 0 <= int(d) <= 6})
        if not days:
            raise ValueError("Choisissez au moins un jour de la semaine")
        return {"kind": "weekly", "days": days, "time": t}
    if kind == "monthly":
        try:
            day = int(spec.get("day"))
        except (TypeError, ValueError):
            raise ValueError("Jour du mois invalide") from None
        if not 1 <= day <= 31:
            raise ValueError("Jour du mois invalide (1 à 31)")
        return {"kind": "monthly", "day": day, "time": t}
    raise ValueError("Planning invalide")


def next_run(spec: dict, after: float) -> float:
    """Le premier instant STRICTEMENT après `after` (timestamp) où la vérification doit avoir lieu."""
    if spec["kind"] == "every":
        return after + spec["every"] * (3600 if spec["unit"] == "hours" else 86400)
    hh, mm = (int(x) for x in spec["time"].split(":"))
    start = datetime.fromtimestamp(after)
    if spec["kind"] == "weekly":
        for add in range(0, 9):
            d = (start + timedelta(days=add)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if d.weekday() in spec["days"] and d.timestamp() > after:
                return d.timestamp()
    y, m = start.year, start.month
    for _ in range(14):
        d = datetime(y, m, min(spec["day"], calendar.monthrange(y, m)[1]), hh, mm)
        if d.timestamp() > after:
            return d.timestamp()
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    raise ValueError("Planning sans prochaine échéance")


def describe(spec: dict) -> str:
    if spec["kind"] == "every":
        n, u = spec["every"], spec["unit"]
        return "Chaque heure" if (n, u) == (1, "hours") else "Chaque jour" if (n, u) == (1, "days") else f"Toutes les {n} " + ("heures" if u == "hours" else "jours")
    h = spec["time"].replace(":", " h ") if not spec["time"].endswith(":00") else spec["time"][:2] + " h"
    if spec["kind"] == "weekly":
        return "Chaque " + ", ".join(DAYS[d] for d in spec["days"]) + f" à {h}"
    return f"Le {spec['day']} de chaque mois à {h}"
