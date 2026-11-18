"""Surveillance : métriques Prometheus, alertes, adresses IP et bannissements. Chaque route vérifie son droit fin ; les adresses IP
sont masquées côté serveur pour qui n'a pas « security.ips.reveal » (le navigateur ne reçoit jamais l'adresse en clair)."""
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import accesslog, alerts, attacks as atk, automod, bans, db, geo, obs_scope, observability as obs, trusted
from .nodes import NodeCtx, node_cap, node_ctx
from .traefik_store import StoreError

router = APIRouter(prefix="/api/obs")


def _prom(ctx: NodeCtx) -> str:
    row = db.one("SELECT prometheus_url FROM nodes WHERE id=?", (ctx.id,))
    if ctx.node["kind"] != "local" or not row or not row["prometheus_url"]:
        raise HTTPException(404, "Prometheus n'est pas branché sur ce nœud (voir l'aide, « Fonctionnalités facultatives »)")
    return row["prometheus_url"]


_cache: dict[tuple, tuple[float, object]] = {}
_cache_lock = threading.Lock()
CACHE_MAX = 300


def _cached(key: tuple, ttl: float, fn, *a, **k):
    """Mémorise un résultat `ttl` secondes : dix personnes (ou un script) qui rechargent la page ne font pas dix fois le même travail côté Prometheus.
    Les erreurs ne sont pas mémorisées. La clé contient le nœud et tout ce qui change la réponse (adresses en clair ou masquées…)."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = fn(*a, **k)
    with _cache_lock:
        if len(_cache) >= CACHE_MAX:
            for k_ in [k_ for k_, (exp, _) in _cache.items() if exp <= now] or list(_cache)[: CACHE_MAX // 2]:
                _cache.pop(k_, None)
        _cache[key] = (now + ttl, value)
    return value


def _guard(fn, *a, **k):
    try:
        return fn(*a, **k)
    except obs.PromError as e:
        raise HTTPException(e.status, str(e))


def _row(ctx: NodeCtx) -> dict:
    return dict(db.one("SELECT * FROM nodes WHERE id=?", (ctx.id,)))


# ------------------------------------------------------------------ état et réglages
@router.get("/features")
def features(ctx: NodeCtx = Depends(node_ctx)):
    certs = False
    if ctx.can("certs.view") and ctx.p.traefik != "none":
        try:
            certs = bool(ctx.store.certs().get("configured"))
        except Exception:  # noqa: BLE001 - worker absent : on ne montre pas
            certs = False
    f = obs.features(ctx.node, certs)
    # ce que cette personne a le droit de voir, en plus de ce qui est branché
    f["metrics"]["allowed"] = ctx.can("metrics.view")
    f["metrics"]["explore"] = ctx.can("metrics.explore")
    f["alerts"]["allowed"] = ctx.can("alerts.view")
    f["alerts"]["manage"] = ctx.can("alerts.manage")
    f["ips"]["allowed"] = ctx.can("security.view")
    f["ips"]["reveal"] = ctx.can("security.ips.reveal")
    f["ips"]["automod"] = ctx.can("security.automod")
    f["bans"]["allowed"] = ctx.can("security.view")
    f["bans"]["manage"] = ctx.can("security.ban")
    f["firing"] = sum(1 for s in alerts.state(ctx.id) if s["state"] == "firing") if ctx.can("alerts.view") else 0
    return f


@router.get("/config")
def config(ctx: NodeCtx = Depends(node_cap("node.settings"))):
    row = _row(ctx)
    return {"prometheusUrl": row["prometheus_url"], "webhookSet": bool(row["webhook_url"]), "canEditUrls": bool(ctx.p.is_admin),
            "accessLog": accesslog.status(ctx.id), "kind": ctx.node["kind"]}


@router.post("/test")
def test(ctx: NodeCtx = Depends(node_cap("node.settings"))):
    base = _prom(ctx)
    t = _guard(obs.targets, base)
    return {"ok": True, "targets": len(t), "up": sum(1 for x in t if x["health"] == "up")}


# ------------------------------------------------------------------ métriques
@router.get("/overview")
def overview(ctx: NodeCtx = Depends(node_cap("metrics.view"))):
    sc = obs_scope.of(ctx)
    return _guard(_cached, ("overview", ctx.id, sc and sc["sel"]), 10, obs.overview, _prom(ctx), sc["sel"] if sc else "")


@router.get("/series")
def series(name: str, range: str = "1h", ctx: NodeCtx = Depends(node_cap("metrics.view"))):
    sc = obs_scope.of(ctx)
    return _guard(_cached, ("series", ctx.id, name, range, sc and sc["sel"]), 15, obs.series, _prom(ctx), name, range, sc["sel"] if sc else "")


@router.get("/breakdown")
def breakdown(range: str = "24h", ctx: NodeCtx = Depends(node_cap("metrics.view"))):
    try:
        seconds = obs.parse_range(range)
    except obs.PromError as e:
        raise HTTPException(e.status, str(e))
    sc = obs_scope.of(ctx)
    return _guard(_cached, ("breakdown", ctx.id, seconds, sc and sc["sel"]), 30, obs.breakdown, _prom(ctx), seconds, sc["sel"] if sc else "")


@router.get("/site")
def site_detail(service: str, range: str = "6h", ctx: NodeCtx = Depends(node_cap("metrics.view"))):
    try:
        seconds = obs.parse_range(range)
    except obs.PromError as e:
        raise HTTPException(e.status, str(e))
    sc = obs_scope.of(ctx)
    if sc and service.split("@")[0] not in sc["services"]:
        raise HTTPException(404, "Ce site n'est pas à ce nœud")
    return _guard(_cached, ("site", ctx.id, service, seconds), 15, obs.site_stats, _prom(ctx), service, seconds)


@router.get("/services")
def services(ctx: NodeCtx = Depends(node_cap("metrics.view"))):
    sc = obs_scope.of(ctx)
    return _guard(_cached, ("services", ctx.id, sc and tuple(sorted(sc["services"]))), 10, obs.services, _prom(ctx), sc["services"] if sc else None)


_METRIC = r"[a-zA-Z_:][a-zA-Z0-9_:]*"


@router.get("/metrics")
def metric_list(search: str = "", ctx: NodeCtx = Depends(node_cap("metrics.explore"))):
    return {"metrics": _guard(_cached, ("metrics", ctx.id, search.strip()[:80]), 60, obs.metrics, _prom(ctx), search.strip()[:80])}


@router.get("/labels")
def metric_labels(metric: str, ctx: NodeCtx = Depends(node_cap("metrics.explore"))):
    import re
    if not re.fullmatch(_METRIC, metric):
        raise HTTPException(422, "Nom de métrique invalide")
    return {"labels": _guard(_cached, ("labels", ctx.id, metric), 60, obs.labels_of, _prom(ctx), metric)}


@router.get("/query")
def explore(expr: str, range: str = "1h", step: int | None = None, ctx: NodeCtx = Depends(node_cap("metrics.explore"))):
    if not expr.strip() or len(expr) > 2000:
        raise HTTPException(422, "Expression vide ou trop longue (2000 caractères au plus)")
    try:
        seconds = obs.parse_range(range)
    except obs.PromError as e:
        raise HTTPException(e.status, str(e))
    if step is not None and not (1 <= step <= 86400):
        raise HTTPException(422, "Pas invalide")
    series_ = _guard(_cached, ("query", ctx.id, expr.strip(), seconds, step), 10, obs.query_range, _prom(ctx), expr.strip(), seconds, step)
    if sum(len(s["points"]) for s in series_) > 60000:
        raise HTTPException(422, "Trop de points : choisissez une durée plus courte ou un pas plus grand")
    return {"series": series_[:40], "truncated": len(series_) > 40}


# ------------------------------------------------------------------ alertes
def _mask_alerts(ctx: NodeCtx, items: list[dict]) -> list[dict]:
    reveal = ctx.can("security.ips.reveal")
    out = []
    for i in items:
        i = dict(i)
        if i.get("key") in ("scan_burst", "exploit_attempt") and i.get("subject"):
            i["ref"] = accesslog.ref_of(i["subject"])   # référence opaque : permet d'ouvrir l'adresse sans jamais en révéler le texte
            if not reveal:
                i["subject"] = accesslog.mask_ip(i["subject"])
        if not reveal and "text" in i:
            i["text"] = alerts.mask_text(i["text"])
        out.append(i)
    return out


@router.get("/alerts")
def get_alerts(ctx: NodeCtx = Depends(node_cap("alerts.view"))):
    cfg = alerts.rule_settings(ctx.id)
    f = obs.features(ctx.node, False)
    rules = [{"key": k, "label": r["label"], "hint": r["hint"], "unit": r["unit"], "source": r["source"], "hasThreshold": r["default"] is not None,
              "available": (f["metrics"]["enabled"] if r["source"] == "prom" else f["ips"]["enabled"]), "source": r["source"], **cfg[k]} for k, r in alerts.RULES.items()]
    native, native_error = [], None
    row = _row(ctx)
    sc = obs_scope.of(ctx)
    if row["prometheus_url"] and ctx.node["kind"] == "local" and not sc:
        try:
            native = obs.native_alerts(row["prometheus_url"])
        except obs.PromError as e:
            native_error = str(e)
    state, history = alerts.state(ctx.id), alerts.history(ctx.id)
    if sc:   # vue filtrée : seulement les alertes qui visent ses sites
        state = [a for a in state if obs_scope.alert_visible(ctx.id, sc, a["key"], a["subject"])]
        history = [a for a in history if obs_scope.alert_visible(ctx.id, sc, a["key"], a["subject"])]
    return {"rules": rules, "state": _mask_alerts(ctx, state), "history": _mask_alerts(ctx, history),
            "native": native, "nativeError": native_error, "webhookSet": bool(row["webhook_url"]), "canManage": ctx.can("alerts.manage")}


def _local_only(ctx: NodeCtx) -> None:
    if ctx.node["kind"] != "local":
        raise HTTPException(400, "L'automod lit le journal d'accès : pour l'instant seulement sur le nœud principal")


class RuleBody(BaseModel):
    enabled: bool | None = None
    threshold: float | None = None
    forSeconds: int | None = Field(default=None, ge=0, le=86400)
    severity: str | None = None


@router.put("/alerts/rules/{key}")
def put_rule(key: str, body: RuleBody, ctx: NodeCtx = Depends(node_cap("alerts.manage"))):
    try:
        cur = alerts.set_rule(ctx.id, key, body.enabled, body.threshold, body.forSeconds, body.severity)
    except ValueError as e:
        raise HTTPException(422, str(e))
    ctx.audit("alert.rule", f"{key} {'activée' if cur['enabled'] else 'désactivée'} seuil={cur['threshold']} durée={cur['for_s']}s")
    return cur


def _snooze_subject(ctx: NodeCtx, subject: str, ref: str | None) -> str:
    """Le sujet d'une alerte sur une adresse est masqué sans le droit de la voir : la référence opaque retrouve le vrai."""
    if ref:
        ip = accesslog.resolve_ref(ctx.id, ref)
        if not ip:
            raise HTTPException(404, "Adresse inconnue (peut-être purgée après 7 jours)")
        return ip
    return subject


class SnoozeBody(BaseModel):
    key: str
    subject: str = ""
    ref: str | None = None
    hours: float = Field(default=1, gt=0, le=720)


@router.post("/alerts/snooze")
def snooze_alert(body: SnoozeBody, ctx: NodeCtx = Depends(node_cap("alerts.manage"))):
    subject = _snooze_subject(ctx, body.subject, body.ref)
    try:
        until = alerts.snooze(ctx.id, body.key, subject, body.hours)
    except ValueError as e:
        raise HTTPException(422, str(e))
    shown = accesslog.mask_ip(subject) if body.ref and not ctx.can("security.ips.reveal") else subject
    ctx.audit("alert.snooze", f"{body.key} {shown} {body.hours:g} h")
    return {"until": until}


@router.delete("/alerts/snooze")
def unsnooze_alert(key: str, subject: str = "", ref: str = "", ctx: NodeCtx = Depends(node_cap("alerts.manage"))):
    subject = _snooze_subject(ctx, subject, ref or None)
    alerts.unsnooze(ctx.id, key, subject)
    ctx.audit("alert.snooze", f"{key} {accesslog.mask_ip(subject) if ref and not ctx.can('security.ips.reveal') else subject} levée")
    return {"ok": True}


@router.post("/alerts/test-webhook")
def test_webhook(ctx: NodeCtx = Depends(node_cap("alerts.manage"))):
    from .crypto import reveal
    url = reveal(_row(ctx)["webhook_url"])
    if not url:
        raise HTTPException(404, "Aucun webhook n'est défini pour ce nœud (« Ce nœud », réglages d'observabilité)")
    err = alerts.send_webhook(url, {"text": f"✅ Test des alertes · {ctx.node['name']}", "content": f"✅ Test des alertes · {ctx.node['name']}", "state": "test"})
    if err:
        raise HTTPException(502, err)
    return {"ok": True}


# ------------------------------------------------------------------ adresses IP
def _present(ctx: NodeCtx, rows: list[dict]) -> list[dict]:
    reveal = ctx.can("security.ips.reveal")
    out = []
    for r in rows:
        r = dict(r)
        r["ref"] = accesslog.ref_of(r["ip"])
        r["geo"] = geo.lookup(r["ip"], precise=reveal)
        if not reveal:
            r["ip"] = accesslog.mask_ip(r["ip"])
        out.append(r)
    return out


@router.get("/ips")
def list_ips(days: int = 7, sort: str = "hits", only: str = "", search: str = "", kind: str = "", ctx: NodeCtx = Depends(node_cap("security.view"))):
    if not accesslog.available(ctx.id):
        raise HTTPException(404, "Le journal d'accès de Traefik n'est pas branché (voir l'aide, « Fonctionnalités facultatives »)")
    reveal = ctx.can("security.ips.reveal")
    sc = obs_scope.of(ctx)
    res = accesslog.listing(ctx.id, max(1, min(days, accesslog.RETENTION_DAYS)), sort, only, search if reveal else "", 500, kind, sc["hosts"] if sc else None)
    if search and not reveal:   # sans le droit, on ne cherche pas par adresse (ce serait la deviner) : seulement par site ou chemin
        q = search.strip().lower()
        res["rows"] = [r for r in res["rows"] if q in (r["last_host"] or "").lower() or q in (r["last_path"] or "").lower()]
    res["rows"] = _present(ctx, res["rows"])
    res["status"] = {k: accesslog.status(ctx.id)[k] for k in ("running", "lastLine", "error")}
    res["geo"] = geo.status()
    return res


@router.post("/geo/update")
def geo_update(ctx: NodeCtx = Depends(node_cap("node.settings"))):
    try:
        res = geo.download()
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    ctx.audit("geo.update", f"base de localisation {res['month']}")
    return {**res, **geo.status()}


@router.get("/stats")
def traffic_stats(days: int = 1, ctx: NodeCtx = Depends(node_cap("security.view"))):
    """Statistiques de trafic d'après le journal d'accès : courbe, familles de réponses, sites, chemins, pays, adresses les plus actives."""
    if not accesslog.available(ctx.id):
        raise HTTPException(404, "Le journal d'accès de Traefik n'est pas branché (voir l'aide, « Fonctionnalités facultatives »)")
    days = max(1, min(days, accesslog.RETENTION_DAYS))
    reveal = ctx.can("security.ips.reveal")
    sc = obs_scope.of(ctx)
    out = accesslog.stats(ctx.id, days, sc["hosts"] if sc else None)
    lst = accesslog.listing(ctx.id, days, "hits", "public", "", 5000, "", sc["hosts"] if sc else None)
    rows = _present(ctx, lst["rows"])
    countries: dict[str, dict] = {}
    for r in rows:
        if r.get("geo"):
            c = countries.setdefault(r["geo"]["country"], {"code": r["geo"]["country"], "name": r["geo"]["countryName"], "ips": 0, "hits": 0, "errors": 0, "attacks": 0})
            c["ips"] += 1
            c["hits"] += r["hits"]
            c["errors"] += r["errors"]
            c["attacks"] += 1 if r["attacks"] else 0
    out["countries"] = sorted(countries.values(), key=lambda c: -c["hits"])[:15]
    out["geo"] = geo.status()
    out["topIps"] = [{"ip": r["ip"], "ref": r["ref"], "hits": r["hits"], "errors": r["errors"], "geo": r.get("geo"), "attacks": [a["label"] for a in r["attacks"]], "trusted": r["trusted"], "banned": r["banned"]} for r in rows[:10]]
    out["total"] = lst["total"]
    out["kinds"] = lst["kinds"]
    out["newest"] = sum(1 for r in lst["rows"] if (r["first_seen"] or 0) >= time.time() - 86400)
    return out


@router.get("/ips/{ref}")
def ip_detail(ref: str, ctx: NodeCtx = Depends(node_cap("security.view"))):
    ip = accesslog.resolve_ref(ctx.id, ref)
    sc = obs_scope.of(ctx)
    if sc and ip and ip not in obs_scope.ips_on_hosts(ctx.id, sc):
        ip = None
    d = accesslog.detail(ctx.id, ip) if ip else None
    if d and sc:
        d["paths"] = [p for p in d.get("paths", []) if (p.get("host") or "").lower() in sc["hosts"]]
    if not d:
        raise HTTPException(404, "Adresse inconnue (peut-être purgée après 7 jours)")
    d["ref"] = ref
    d["banned"] = bool(db.one("SELECT 1 FROM ip_bans WHERE node_id=? AND ip=?", (ctx.id, ip)))
    d["exempt"] = bool(d.get("private") or d.get("trusted"))
    d["geo"] = geo.lookup(ip, precise=ctx.can("security.ips.reveal"))
    if not ctx.can("security.ips.reveal"):
        d["ip"] = accesslog.mask_ip(d["ip"])
    return d


# ------------------------------------------------------------------ bannissements
class BanBody(BaseModel):
    ip: str | None = Field(default=None, max_length=60)
    ref: str | None = Field(default=None, max_length=40)
    hours: float | None = Field(default=None, gt=0, le=24 * 365)
    reason: str = Field(default="", max_length=200)


def _requesters(request: Request) -> list[str]:
    """Adresses de celui qui agit : l'adresse de la connexion ET celle annoncée par un proxy (Traefik) devant l'interface."""
    xff = request.headers.get("x-forwarded-for", "")
    return [a for a in ([x.strip() for x in xff.split(",")] + [request.client.host if request.client else ""]) if a]


@router.get("/bans")
def get_bans(ctx: NodeCtx = Depends(node_cap("security.view"))):
    raw = bans.list_bans(ctx.id)
    bans.attempts(ctx.id, raw)
    rows = _present(ctx, raw)
    return {"bans": rows, "canBan": ctx.can("security.ban"), "stats": bans.stats(rows)}


@router.post("/bans")
def add_ban(body: BanBody, request: Request, ctx: NodeCtx = Depends(node_cap("security.ban"))):
    ip = body.ip
    if body.ref:
        ip = accesslog.resolve_ref(ctx.id, body.ref)
        if not ip:
            raise HTTPException(404, "Adresse inconnue (peut-être purgée après 7 jours)")
    elif ip and not ctx.can("security.ips.reveal"):
        raise HTTPException(403, "Sans le droit de voir les adresses en clair, on bannit depuis la liste (par sa référence), pas en saisissant une adresse")
    if not ip:
        raise HTTPException(422, "Indiquez une adresse")
    row = _row(ctx)
    try:
        from .nodes import node_out
        r = bans.add(node_out(row), ctx.store, ip, body.reason, body.hours, ctx.p.username, _requesters(request))
    except bans.BanError as e:
        raise HTTPException(e.status, str(e))
    except StoreError as e:
        raise HTTPException(e.status, f"Impossible d'écrire la règle dans Traefik : {e}")
    ctx.audit("ip.ban", f"{r['ip']}{' · ' + body.reason if body.reason else ''}{' · ' + str(body.hours) + ' h' if body.hours else ' · définitif'}")
    return {**r, "ip": r["ip"] if ctx.can("security.ips.reveal") else accesslog.mask_ip(r["ip"].split("/")[0])}


# ------------------------------------------------------------------ adresses validées
class TrustBody(BaseModel):
    ip: str | None = Field(default=None, max_length=60)
    ref: str | None = Field(default=None, max_length=40)
    note: str = Field(default="", max_length=200)


@router.get("/trusted")
def get_trusted(ctx: NodeCtx = Depends(node_cap("security.view"))):
    return {"trusted": _present(ctx, trusted.list_trusted(ctx.id)), "canManage": ctx.can("security.ban")}


@router.post("/trusted")
def add_trusted(body: TrustBody, ctx: NodeCtx = Depends(node_cap("security.ban"))):
    ip = body.ip
    if body.ref:
        ip = accesslog.resolve_ref(ctx.id, body.ref)
        if not ip:
            raise HTTPException(404, "Adresse inconnue (peut-être purgée après 7 jours)")
    elif ip and not ctx.can("security.ips.reveal"):
        raise HTTPException(403, "Sans le droit de voir les adresses en clair, on valide depuis la liste (par sa référence), pas en saisissant une adresse")
    if not ip:
        raise HTTPException(422, "Indiquez une adresse")
    try:
        norm = trusted.normalize(ip)
        if accesslog._is_private(norm.split("/")[0]):
            raise HTTPException(409, "Les adresses du réseau local sont déjà validées d'office")
        r = trusted.add(ctx.id, norm, body.note, ctx.p.username)
    except trusted.TrustError as e:
        raise HTTPException(e.status, str(e))
    ctx.audit("ip.trust", f"{r['ip']}{' · ' + body.note if body.note else ''}")
    return {**r, "ip": r["ip"] if ctx.can("security.ips.reveal") else accesslog.mask_ip(r["ip"].split("/")[0])}


@router.delete("/trusted/{tid}")
def del_trusted(tid: int, ctx: NodeCtx = Depends(node_cap("security.ban"))):
    ip = trusted.remove(ctx.id, tid)
    if ip is None:
        raise HTTPException(404, "Adresse validée introuvable")
    ctx.audit("ip.untrust", ip)
    return {"ok": True}


# ------------------------------------------------------------------ automod
class RuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    conditions: dict
    mode: str = "observe"
    hours: float | None = Field(default=24, gt=0, le=24 * 365)
    enabled: bool = True


@router.get("/automod")
def get_automod(ctx: NodeCtx = Depends(node_cap("security.view"))):
    return {"rules": automod.list_rules(ctx.id), "log": _present(ctx, automod.recent_log(ctx.id)), "canManage": ctx.can("security.automod"),
            "geo": geo.status(), "attackKinds": [atk.describe(k) for k in automod.ATTACK_KINDS if k in atk.CATALOG], "max": automod.MAX_RULES, "maxPerRun": automod.MAX_PER_RUN,
            "available": accesslog.available(ctx.id)}


@router.post("/automod/preview")
def preview_automod(body: dict, ctx: NodeCtx = Depends(node_cap("security.automod"))):
    _local_only(ctx)
    try:
        res = automod.preview(ctx.id, body.get("conditions") or {})
    except automod.AutomodError as e:
        raise HTTPException(e.status, str(e))
    if not ctx.can("security.ips.reveal"):
        res["rows"] = [{**r, "ip": accesslog.mask_ip(r["ip"])} for r in res["rows"]]
    return res


@router.post("/automod")
def create_rule(body: RuleBody, ctx: NodeCtx = Depends(node_cap("security.automod"))):
    _local_only(ctx)
    try:
        r = automod.save_rule(ctx.id, None, body.name, body.conditions, body.mode, body.hours, body.enabled, ctx.p.username)
    except automod.AutomodError as e:
        raise HTTPException(e.status, str(e))
    ctx.audit("automod.create", f"{r['name']} · {r['mode']} · {', '.join(r['summary'])}")
    return r


@router.put("/automod/{rid}")
def update_rule(rid: int, body: RuleBody, ctx: NodeCtx = Depends(node_cap("security.automod"))):
    _local_only(ctx)
    try:
        r = automod.save_rule(ctx.id, rid, body.name, body.conditions, body.mode, body.hours, body.enabled, ctx.p.username)
    except automod.AutomodError as e:
        raise HTTPException(e.status, str(e))
    ctx.audit("automod.update", f"{r['name']} · {r['mode']}{'' if r['enabled'] else ' · désactivée'}")
    return r


@router.delete("/automod/{rid}")
def delete_rule(rid: int, ctx: NodeCtx = Depends(node_cap("security.automod"))):
    name = automod.delete_rule(ctx.id, rid)
    if name is None:
        raise HTTPException(404, "Règle introuvable")
    ctx.audit("automod.delete", name)
    return {"ok": True}


@router.post("/automod/run")
def run_automod(ctx: NodeCtx = Depends(node_cap("security.automod"))):
    _local_only(ctx)
    """Passe immédiate (sans attendre la minuterie)."""
    from .nodes import node_out
    try:
        done = automod.run_rules(node_out(_row(ctx)), ctx.store)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Passe impossible : {str(e)[:160]}")
    return {"matched": sum(done.values())}


class BanEditBody(BaseModel):
    hours: float | None = Field(default=None, gt=0, le=24 * 365)   # null = définitif
    reason: str | None = Field(default=None, max_length=200)


@router.put("/bans/{bid}")
def edit_ban(bid: int, body: BanEditBody, ctx: NodeCtx = Depends(node_cap("security.ban"))):
    """Change la durée (à partir de maintenant) et, si on le souhaite, la raison d'un bannissement."""
    try:
        r = bans.update(ctx.id, bid, body.hours, body.reason)
    except bans.BanError as e:
        raise HTTPException(e.status, str(e))
    if r is None:
        raise HTTPException(404, "Bannissement introuvable")
    ctx.audit("ip.ban.edit", f"{accesslog.mask_ip(r['ip']) if not ctx.can('security.ips.reveal') else r['ip']} · {'définitif' if body.hours is None else str(body.hours) + ' h'}")
    return {**r, "ip": r["ip"] if ctx.can("security.ips.reveal") else accesslog.mask_ip(r["ip"].split("/")[0])}


@router.delete("/bans/{bid}")
def del_ban(bid: int, ctx: NodeCtx = Depends(node_cap("security.ban"))):
    from .nodes import node_out
    try:
        ip = bans.remove(node_out(_row(ctx)), ctx.store, bid)
    except StoreError as e:
        raise HTTPException(e.status, f"Impossible d'écrire la règle dans Traefik : {e}")
    if ip is None:
        raise HTTPException(404, "Bannissement introuvable")
    ctx.audit("ip.unban", ip)
    return {"ok": True}
