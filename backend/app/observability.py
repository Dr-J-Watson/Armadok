"""Surveillance : client Prometheus (lecture seule), requêtes prédéfinies sur les métriques de Traefik, et drapeaux de fonctionnalités.

Prometheus n'est jamais modifié : on l'interroge (/api/v1/query, query_range, targets, alerts). Les métriques par adresse IP n'existent pas
chez Prometheus (une étiquette par visiteur ferait exploser sa mémoire) : elles viennent du journal d'accès de Traefik (voir accesslog.py)."""
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import settings


class PromError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _get(base: str, path: str, params: dict | None = None, timeout: float = 10):
    url = base.rstrip("/") + path + ("?" + urllib.parse.urlencode(params) if params else "")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(urllib.request.Request(url, headers={"User-Agent": "armadok", "Accept": "application/json"}), timeout=timeout) as r:
            raw = r.read(8_000_000)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = (json.loads(e.read(2000) or b"{}").get("error") or "")[:200]
        except ValueError:
            pass
        if e.code == 400:
            raise PromError(f"Requête refusée par Prometheus : {detail or 'expression invalide'}", 422)
        if e.code in (401, 403):
            raise PromError("Prometheus demande une authentification : non pris en charge ici (placez-le sur un réseau interne)", 422)
        if e.code == 404:
            raise PromError("Ce n'est pas l'API de Prometheus (adresse ou port incorrect ?)", 422)
        if 300 <= e.code < 400:
            raise PromError("Cette adresse redirige ailleurs : indiquez l'adresse finale de Prometheus", 422)
        raise PromError(f"Prometheus a répondu {e.code}{' : ' + detail if detail else ''}", 502)
    except (urllib.error.URLError, OSError):
        raise PromError("Prometheus est injoignable depuis ce serveur (adresse, port ou réseau Docker)", 502)
    try:
        data = json.loads(raw)
    except ValueError:
        raise PromError("Réponse inattendue : ce n'est pas l'API de Prometheus", 422)
    if not isinstance(data, dict) or data.get("status") != "success":
        raise PromError(f"Prometheus : {(data or {}).get('error', 'erreur')}"[:200] if isinstance(data, dict) else "Réponse inattendue", 422)
    return data["data"]


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def query(base: str, expr: str) -> list[dict]:
    """Requête instantanée -> [{labels, value}]. Les valeurs non finies (NaN, Inf) sont écartées."""
    d = _get(base, "/api/v1/query", {"query": expr})
    out = []
    for r in d.get("result", []):
        v = _num((r.get("value") or [0, None])[1])
        if v is not None:
            out.append({"labels": r.get("metric", {}), "value": v})
    return out


def query_range(base: str, expr: str, seconds: int, step: int | None = None) -> list[dict]:
    """Série sur les dernières `seconds` secondes -> [{labels, points: [[t, v], …]}]."""
    end = int(time.time())
    step = step or max(15, seconds // 120)
    d = _get(base, "/api/v1/query_range", {"query": expr, "start": end - seconds, "end": end, "step": step}, timeout=20)
    out = []
    for r in d.get("result", []):
        pts = [[float(t), v] for t, raw in r.get("values", []) if (v := _num(raw)) is not None]
        if pts:
            out.append({"labels": r.get("metric", {}), "points": pts})
    return out


def targets(base: str) -> list[dict]:
    d = _get(base, "/api/v1/targets", {"state": "active"})
    return [{"job": t["labels"].get("job", ""), "instance": t["labels"].get("instance", ""), "health": t.get("health", "unknown"),
             "lastError": (t.get("lastError") or "")[:200], "lastScrape": t.get("lastScrape", "")} for t in d.get("activeTargets", [])]


def native_alerts(base: str) -> list[dict]:
    d = _get(base, "/api/v1/alerts")
    return [{"name": a.get("labels", {}).get("alertname", ""), "state": a.get("state", ""), "severity": a.get("labels", {}).get("severity", ""),
             "summary": (a.get("annotations", {}).get("summary") or a.get("annotations", {}).get("description") or "")[:300],
             "since": a.get("activeAt", ""), "labels": {k: v for k, v in a.get("labels", {}).items() if k not in ("alertname", "severity")}} for a in d.get("alerts", [])]


# ------------------------------------------------------------------ requêtes prédéfinies (métriques standard de Traefik v2/v3)
EP = "traefik_entrypoint_requests_total"
SVC = "traefik_service_requests_total"


def _p(q: float, metric: str, by: str = "") -> str:
    sel = f"le{', ' + by if by else ''}"
    return f"histogram_quantile({q}, sum by ({sel}) (rate({metric}_bucket[5m])))"


INSTANT = {
    "reqRate": f"sum(rate({EP}[5m]))",
    "err4": f'sum(rate({EP}{{code=~"4.."}}[5m]))',
    "err5": f'sum(rate({EP}{{code=~"5.."}}[5m]))',
    "p95": _p(0.95, "traefik_entrypoint_request_duration_seconds"),
    "openConn": "sum(traefik_open_connections{entrypoint!='traefik'})",
    "bytesIn": "sum(rate(traefik_entrypoint_requests_bytes_total[5m]))",
    "bytesOut": "sum(rate(traefik_entrypoint_responses_bytes_total[5m]))",
    "certDays": "min((traefik_tls_certs_not_after - time()) / 86400 > 0)",       # le plus proche de l'expiration parmi les certificats ENCORE valides
    "certExpired": "count(traefik_tls_certs_not_after < time())",                  # certificats déjà expirés que Traefik garde (souvent : restes de sites supprimés)
    "reloadOk": "max(traefik_config_last_reload_success)",
    "reloadFail": "max(traefik_config_last_reload_failure)",
}
SERIES = {
    "requests": f"sum by (code) (rate({EP}[5m]))",
    "latency50": _p(0.5, "traefik_entrypoint_request_duration_seconds"),
    "latency95": _p(0.95, "traefik_entrypoint_request_duration_seconds"),
    "latency99": _p(0.99, "traefik_entrypoint_request_duration_seconds"),
    "connections": "sum(traefik_open_connections{entrypoint!='traefik'})",
    "bytesIn": "sum(rate(traefik_entrypoint_requests_bytes_total[5m]))",
    "bytesOut": "sum(rate(traefik_entrypoint_responses_bytes_total[5m]))",
}
_UNITS = {"m": 60, "h": 3600, "d": 86400}
MIN_RANGE, MAX_RANGE = 60, 90 * 86400


def parse_range(spec: str) -> int:
    """« 15m », « 6h », « 7d », « 36h »… -> secondes (1 minute à 90 jours). Les durées courantes comme les durées libres passent par ici."""
    import re
    m = re.fullmatch(r"(\d{1,5})([mhd])", (spec or "").strip().lower())
    if not m:
        raise PromError("Durée invalide (exemples : 30m, 6h, 14d)", 422)
    sec = int(m.group(1)) * _UNITS[m.group(2)]
    if not MIN_RANGE <= sec <= MAX_RANGE:
        raise PromError("Durée hors limites : de 1 minute à 90 jours", 422)
    return sec


def overview(base: str) -> dict:
    out: dict = {"instant": {}, "errors": {}}
    for k, expr in INSTANT.items():
        try:
            r = query(base, expr)
            out["instant"][k] = r[0]["value"] if r else None
        except PromError as e:
            out["errors"][k] = str(e)
            if e.status == 502:   # injoignable : inutile d'insister
                raise
    try:
        out["targets"] = targets(base)
    except PromError as e:
        out["targets"] = []
        out["errors"]["targets"] = str(e)
    return out


def series(base: str, name: str, rng: str) -> list[dict]:
    if name not in SERIES:
        raise PromError("Série inconnue", 422)
    res = query_range(base, SERIES[name], parse_range(rng))
    if name == "requests":   # regroupe par classe (2xx, 3xx, 4xx, 5xx) plutôt que par code exact
        merged: dict[str, dict[float, float]] = {}
        for s in res:
            cls = (s["labels"].get("code") or "?")[:1] + "xx"
            m = merged.setdefault(cls, {})
            for t, v in s["points"]:
                m[t] = m.get(t, 0) + v
        return [{"labels": {"class": c}, "points": sorted([t, v] for t, v in pts.items())} for c, pts in sorted(merged.items())]
    return res


def services(base: str) -> list[dict]:
    """Par service : requêtes/s, % d'erreurs serveur (5xx), % d'erreurs client (4xx), latence p95."""
    rate = {r["labels"].get("service", ""): r["value"] for r in query(base, f"sum by (service) (rate({SVC}[5m]))")}
    e5 = {r["labels"].get("service", ""): r["value"] for r in query(base, f'sum by (service) (rate({SVC}{{code=~"5.."}}[5m]))')}
    e4 = {r["labels"].get("service", ""): r["value"] for r in query(base, f'sum by (service) (rate({SVC}{{code=~"4.."}}[5m]))')}
    try:
        p95 = {r["labels"].get("service", ""): r["value"] for r in query(base, _p(0.95, "traefik_service_request_duration_seconds", "service"))}
    except PromError:
        p95 = {}
    out = []
    for name, r in rate.items():
        out.append({"service": name, "rate": r, "err5": (e5.get(name, 0) / r * 100) if r else 0, "err4": (e4.get(name, 0) / r * 100) if r else 0, "p95": p95.get(name)})
    return sorted(out, key=lambda x: -x["rate"])


# ------------------------------------------------------------------ répartitions sur une période (vue d'ensemble)
def _group(base: str, expr: str, label: str, top: int = 8) -> list[dict]:
    rows = [{"name": r["labels"].get(label, "") or "—", "value": r["value"]} for r in query(base, expr)]
    return sorted(rows, key=lambda r: -r["value"])[:top]


def breakdown(base: str, seconds: int) -> dict:
    """Ce qui s'est passé sur la période : totaux et répartitions (sites, codes, méthodes, points d'entrée, protocole, TLS, lenteur). Chaque
    morceau est indépendant : un morceau qui échoue (métrique absente) est simplement omis."""
    w = f"{max(60, int(seconds))}s"
    out: dict = {"seconds": int(seconds)}
    qs = {
        "total": f"sum(increase({EP}[{w}]))",
        "tls": f"sum(increase(traefik_entrypoint_requests_tls_total[{w}]))",
        "bytesIn": f"sum(increase(traefik_entrypoint_requests_bytes_total[{w}]))",
        "bytesOut": f"sum(increase(traefik_entrypoint_responses_bytes_total[{w}]))",
        "reloads": f"sum(increase(traefik_config_reloads_total[{w}]))",
    }
    for k, expr in qs.items():
        try:
            r = query(base, expr)
            out[k] = r[0]["value"] if r else None
        except PromError as e:
            if e.status == 502:
                raise
            out[k] = None
    groups = {
        "byService": (f"sum by (service) (increase({SVC}[{w}]))", "service", 10),
        "byCode": (f"sum by (code) (increase({EP}[{w}]))", "code", 30),
        "byMethod": (f"sum by (method) (increase({SVC}[{w}]))", "method", 8),
        "byEntrypoint": (f"sum by (entrypoint) (increase({EP}[{w}]))", "entrypoint", 8),
        "byProtocol": (f"sum by (protocol) (increase({SVC}[{w}]))", "protocol", 5),
        "errorsByService": (f'sum by (service) (increase({SVC}{{code=~"5.."}}[{w}]))', "service", 8),
        "slowest": (_p(0.95, "traefik_service_request_duration_seconds", "service").replace("[5m]", f"[{w}]"), "service", 6),
    }
    for k, (expr, label, top) in groups.items():
        try:
            out[k] = _group(base, expr, label, top)
        except PromError as e:
            if e.status == 502:
                raise
            out[k] = []
    classes: dict[str, float] = {}
    for r in out["byCode"]:
        c = (r["name"][:1] or "?") + "xx"
        classes[c] = classes.get(c, 0) + r["value"]
    out["byClass"] = [{"name": k, "value": v} for k, v in sorted(classes.items())]
    return out


# ------------------------------------------------------------------ un site (service Traefik) en détail
def _svc_sel(service: str) -> str:
    """Sélecteur d'étiquette pour un service : nom complet (« app-svc@file ») ou nom seul (n'importe quel fournisseur). Le nom est validé : pas d'injection."""
    import re
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,120}(@[A-Za-z0-9_.\-]{1,40})?", service or ""):
        raise PromError("Nom de service invalide", 422)
    if "@" in service:
        return f'service="{service}"'
    return f'service=~"{service.replace(".", "[.]")}@.+"'


def site_stats(base: str, service: str, seconds: int) -> dict:
    sel = _svc_sel(service)
    w = f"{max(60, int(seconds))}s"
    t = f"traefik_service_requests_total{{{sel}}}"
    series: dict[str, list] = {}

    def rng(name: str, expr: str) -> None:
        try:
            series[name] = query_range(base, expr, seconds)
        except PromError as e:
            if e.status == 502:
                raise
            series[name] = []

    rng("requests", f"sum by (code) (rate({t}[5m]))")
    for q_, n in ((0.5, "latency50"), (0.95, "latency95"), (0.99, "latency99")):
        rng(n, f"histogram_quantile({q_}, sum by (le) (rate(traefik_service_request_duration_seconds_bucket{{{sel}}}[5m])))")
    rng("bytesIn", f"sum(rate(traefik_service_requests_bytes_total{{{sel}}}[5m]))")
    rng("bytesOut", f"sum(rate(traefik_service_responses_bytes_total{{{sel}}}[5m]))")
    merged: dict[str, dict[float, float]] = {}
    for r in series["requests"]:
        cls = (r["labels"].get("code") or "?")[:1] + "xx"
        m = merged.setdefault(cls, {})
        for ts, v in r["points"]:
            m[ts] = m.get(ts, 0) + v
    series["requests"] = [{"labels": {"class": c}, "points": sorted([ts, v] for ts, v in pts.items())} for c, pts in sorted(merged.items())]
    out: dict = {"service": service, "seconds": int(seconds), "series": series}
    for k, expr in (("total", f"sum(increase({t}[{w}]))"), ("err5", f'sum(increase(traefik_service_requests_total{{{sel},code=~"5.."}}[{w}]))'),
                    ("err4", f'sum(increase(traefik_service_requests_total{{{sel},code=~"4.."}}[{w}]))'),
                    ("p95", f"histogram_quantile(0.95, sum by (le) (rate(traefik_service_request_duration_seconds_bucket{{{sel}}}[{w}])))"),
                    ("bytesOut", f"sum(increase(traefik_service_responses_bytes_total{{{sel}}}[{w}]))"),
                    ("bytesIn", f"sum(increase(traefik_service_requests_bytes_total{{{sel}}}[{w}]))")):
        try:
            r = query(base, expr)
            out[k] = r[0]["value"] if r else None
        except PromError as e:
            if e.status == 502:
                raise
            out[k] = None
    for k, label in (("byCode", "code"), ("byMethod", "method")):
        try:
            out[k] = _group(base, f"sum by ({label}) (increase({t}[{w}]))", label, 12)
        except PromError:
            out[k] = []
    try:
        out["backends"] = sorted({r["labels"].get("service", "") for r in query(base, f"count by (service) ({t})")})
    except PromError:
        out["backends"] = []
    return out


# ------------------------------------------------------------------ explorateur : ce que Prometheus connaît
def metrics(base: str, search: str = "", limit: int = 300) -> list[dict]:
    """Métriques disponibles [{name, type, help}] ; celles de Traefik d'abord. Les séries `_bucket`, `_sum`, `_count` sont repliées sur leur histogramme."""
    names = _get(base, "/api/v1/label/__name__/values")
    try:
        meta = _get(base, "/api/v1/metadata", {"limit_per_metric": 1}, timeout=15)
    except PromError:
        meta = {}
    hist = {n[: -len("_bucket")] for n in names if n.endswith("_bucket")}
    out = {}
    for n in names:
        base_name, typ = n, None
        for suf in ("_bucket", "_sum", "_count"):
            if n.endswith(suf) and n[: -len(suf)] in hist:
                base_name, typ = n[: -len(suf)], "histogram"
        if base_name in out or (search and search.lower() not in base_name.lower()):
            continue
        m = (meta.get(base_name) or meta.get(n) or [{}])[0]
        out[base_name] = {"name": base_name, "type": typ or m.get("type") or ("counter" if n.endswith("_total") else "gauge"), "help": (m.get("help") or "")[:200]}
    rows = sorted(out.values(), key=lambda r: (not r["name"].startswith("traefik_"), r["name"]))
    return rows[:limit]


def labels_of(base: str, metric: str) -> dict[str, list[str]]:
    """{étiquette: [valeurs…]} d'une métrique (jusqu'à 30 valeurs par étiquette), pour proposer des regroupements et des filtres."""
    sel = metric + ("_bucket" if _is_hist(base, metric) else "")
    d = _get(base, "/api/v1/series", {"match[]": sel, "start": int(time.time()) - 3600, "end": int(time.time())})
    labs: dict[str, set] = {}
    for s in d[:2000]:
        for k, v in s.items():
            if k not in ("__name__", "le"):
                labs.setdefault(k, set()).add(v)
    return {k: sorted(v)[:30] for k, v in sorted(labs.items())}


def _is_hist(base: str, metric: str) -> bool:
    try:
        return bool(_get(base, "/api/v1/series", {"match[]": metric + "_bucket", "start": int(time.time()) - 600, "end": int(time.time())})[:1])
    except PromError:
        return False


# ------------------------------------------------------------------ fonctionnalités disponibles sur un nœud
def features(node: dict, certs_configured: bool = False) -> dict:
    """Ce que l'interface doit montrer : une fonctionnalité non branchée est CACHÉE, et l'aide dit comment l'activer."""
    from . import accesslog
    local = node["kind"] == "local"
    tf = "traefik" in (node.get("modules") or ["traefik"])   # un nœud sans le module Traefik n'a ni métriques, ni IP, ni bannissements, ni certificats
    prom = bool(node.get("metricsEnabled")) and local and tf
    logs = tf and accesslog.available(node.get("id"))   # nœud du hub : le fichier ; nœud distant : le journal que son worker envoie
    return {"certs": {"enabled": bool(certs_configured) and tf},
            "metrics": {"enabled": prom},
            "alerts": {"enabled": prom or logs},
            "ips": {"enabled": logs, "retentionDays": accesslog.RETENTION_DAYS},
            "bans": {"enabled": tf},
            "remoteNote": None if local else ("Métriques : pour l'instant seulement sur le nœud principal." if logs else "Métriques et journal d'accès : indisponibles sur ce nœud (le journal d'accès s'active en montant TRAEFIK_ACCESS_LOG dans le worker).")}
