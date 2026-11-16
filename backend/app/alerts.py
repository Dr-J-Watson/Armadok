"""Alertes de l'interface : des règles prédéfinies, évaluées chaque minute, avec seuil, durée et gravité réglables.

Deux sources : Prometheus (une requête PromQL qui ne renvoie QUE ce qui dépasse le seuil) et le journal d'accès (une adresse qui insiste).
Une alerte passe « en attente » (condition vraie), puis « en cours » quand elle dure depuis la durée choisie, puis « résolue » :
chaque passage en cours / résolue est écrit dans l'historique (30 jours), signalé en direct aux navigateurs, tracé dans l'audit et
envoyé au webhook du nœud s'il y en a un. Les alertes natives de Prometheus (Alertmanager) restent visibles à part, en lecture seule."""
import ipaddress
import json
import re
import threading
import time
import urllib.error
import urllib.request

from .crypto import reveal
from . import accesslog, db, observability as obs
from .audit import audit
from .events import publish

EVERY = 60
SEVERITIES = ("info", "warning", "critical")

_EP = obs.EP
_SVC = obs.SVC

# clé -> libellé, aide, unité, seuil par défaut (None = pas de seuil), durée par défaut (s), gravité, source, expression(seuil), étiquette(s) du sujet
RULES: dict[str, dict] = {
    "error_rate": {"label": "Trop d'erreurs serveur (5xx)", "hint": "Part des réponses 5xx d'un service sur 5 minutes (ignoré s'il reçoit très peu de requêtes).", "unit": "%", "default": 5.0, "for": 300, "severity": "critical", "source": "prom", "subject": ("service",),
                   "expr": lambda t: f'(sum by (service) (rate({_SVC}{{code=~"5.."}}[5m])) / sum by (service) (rate({_SVC}[5m])) * 100 > {t}) and on (service) (sum by (service) (rate({_SVC}[5m])) > 0.02)'},
    "latency": {"label": "Service lent (p95)", "hint": "Temps de réponse dépassé par 5 % des requêtes d'un service.", "unit": "s", "default": 2.0, "for": 300, "severity": "warning", "source": "prom", "subject": ("service",),
                "expr": lambda t: f"{obs._p(0.95, 'traefik_service_request_duration_seconds', 'service')} > {t}"},
    "cert_expiry": {"label": "Certificat bientôt expiré", "hint": "Jours restants avant l'expiration d'un certificat encore valide. Les certificats DÉJÀ expirés (restes de sites supprimés) sont comptés à part dans la vue d'ensemble.", "unit": "jours", "default": 14.0, "for": 0, "severity": "critical", "source": "prom", "subject": ("cn",),
                    "expr": lambda t: f"(min by (cn) ((traefik_tls_certs_not_after - time()) / 86400 > 0)) < {t}"},
    "target_down": {"label": "Cible Prometheus injoignable", "hint": "Prometheus n'arrive plus à lire une de ses cibles (Traefik, un exporteur…).", "unit": "", "default": None, "for": 120, "severity": "critical", "source": "prom", "subject": ("job", "instance"),
                    "expr": lambda t: "up == 0"},
    "reload_failed": {"label": "Configuration Traefik refusée", "hint": "Le dernier rechargement de la configuration a échoué : Traefik garde l'ancienne.", "unit": "", "default": None, "for": 0, "severity": "critical", "source": "prom", "subject": (),
                      "expr": lambda t: "max(traefik_config_last_reload_failure) > max(traefik_config_last_reload_success)"},
    "server_down": {"label": "Serveur d'un service hors service", "hint": "Un serveur dont la vérification de santé échoue (seulement si vous avez configuré des « healthcheck »).", "unit": "", "default": None, "for": 60, "severity": "warning", "source": "prom", "subject": ("service", "url"),
                    "expr": lambda t: "traefik_service_server_up == 0"},
    "traffic_spike": {"label": "Pic de trafic", "hint": "Requêtes par seconde (tous sites) au-dessus d'un seuil : attaque, boucle, buzz…", "unit": "req/s", "default": 100.0, "for": 120, "severity": "warning", "source": "prom", "subject": (),
                      "expr": lambda t: f"sum(rate({_EP}[5m])) > {t}"},
    "leak_exposed": {"label": "Fichier sensible servi", "hint": "Un fichier qui ne devrait jamais être public (.env, .git, sauvegarde…) a répondu 200 à quelqu'un : il a peut-être été lu. Vient du journal d'accès.", "unit": "", "default": None, "for": 0, "severity": "critical", "source": "logs", "subject": ("sample",), "expr": None},
    "exploit_attempt": {"label": "Tentative d'exploitation", "hint": "Une même adresse cumule des tentatives graves en 10 minutes : injection SQL, exécution de commandes (Log4Shell…), remontée de répertoires. Vient du journal d'accès.", "unit": "tentatives / 10 min", "default": 5.0, "for": 0, "severity": "critical", "source": "logs", "subject": ("ip",), "expr": None},
    "scan_burst": {"label": "Une adresse insiste", "hint": "Une même adresse cumule trop d'erreurs 401 / 403 / 404 en 10 minutes (balayage, essai de mots de passe). Vient du journal d'accès.", "unit": "erreurs / 10 min", "default": 60.0, "for": 0, "severity": "warning", "source": "logs", "subject": ("ip",), "expr": None},
}


def defaults(key: str) -> dict:
    r = RULES[key]
    return {"enabled": key != "traffic_spike", "threshold": r["default"], "for_s": r["for"], "severity": r["severity"]}


def rule_settings(node_id: str) -> dict:
    over = {r["key"]: r for r in db.rows("SELECT * FROM alert_rules WHERE node_id=?", (node_id,))}
    out = {}
    for key in RULES:
        d = defaults(key)
        if key in over:
            o = over[key]
            d = {"enabled": bool(o["enabled"]), "threshold": o["threshold"] if o["threshold"] is not None else d["threshold"], "for_s": o["for_s"], "severity": o["severity"]}
        out[key] = d
    return out


def set_rule(node_id: str, key: str, enabled: bool | None, threshold: float | None, for_s: int | None, severity: str | None) -> dict:
    if key not in RULES:
        raise ValueError("Règle inconnue")
    cur = rule_settings(node_id)[key]
    if enabled is not None:
        cur["enabled"] = enabled
    if threshold is not None and RULES[key]["default"] is not None:
        if not (0 <= threshold <= 1e9):
            raise ValueError("Seuil invalide")
        cur["threshold"] = float(threshold)
    if for_s is not None:
        if not (0 <= for_s <= 86400):
            raise ValueError("Durée invalide (0 à 24 h)")
        cur["for_s"] = int(for_s)
    if severity is not None:
        if severity not in SEVERITIES:
            raise ValueError("Gravité inconnue")
        cur["severity"] = severity
    db.run("INSERT INTO alert_rules(node_id,key,enabled,threshold,for_s,severity) VALUES(?,?,?,?,?,?) ON CONFLICT(node_id,key) DO UPDATE SET "
           "enabled=excluded.enabled, threshold=excluded.threshold, for_s=excluded.for_s, severity=excluded.severity",
           (node_id, key, 1 if cur["enabled"] else 0, cur["threshold"], cur["for_s"], cur["severity"]))
    return cur


def _subject(rule: dict, labels: dict) -> str:
    return " · ".join(str(labels.get(k, "")) for k in rule["subject"] if labels.get(k, "")) or ""


def _text(key: str, subject: str, value: float | None, cfg: dict) -> str:
    r = RULES[key]
    bits = [r["label"]]
    if subject:
        bits.append(subject)
    if value is not None and r["unit"]:
        bits.append(f"{value:.3g} {r['unit']}" + (f" (seuil {cfg['threshold']:g})" if cfg["threshold"] is not None else ""))
    return " — ".join(bits)


_IP_RE = re.compile(r"(?<![\w:.])(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]*:[0-9a-fA-F:]+)(?![\w:.])")


def mask_text(text: str) -> str:
    """Masque les adresses IP d'un texte (pour qui n'a pas le droit de les voir)."""
    def sub(m):
        try:
            ipaddress.ip_address(m.group(0))
        except ValueError:
            return m.group(0)
        return accesslog.mask_ip(m.group(0))
    return _IP_RE.sub(sub, text)


def _current(node_id: str, prom: str | None, cfg: dict, now: float | None = None) -> tuple[dict, list[str]]:
    """{(clé, sujet): (valeur, texte)} des conditions vraies maintenant, et les erreurs de lecture."""
    now_true, errors = {}, []
    for key, rule in RULES.items():
        c = cfg[key]
        if not c["enabled"]:
            continue
        try:
            if rule["source"] == "prom":
                if not prom:
                    continue
                for r in obs.query(prom, rule["expr"](c["threshold"])):
                    sub = _subject(rule, r["labels"])
                    now_true[(key, sub)] = (r["value"], _text(key, sub, r["value"], c))
            elif key == "leak_exposed":
                for lk in accesslog.recent_leaks(node_id, (now or time.time()) - 3600):   # une fuite reste « en cours » une heure après la dernière lecture
                    if not accesslog.exempt(node_id, lk["ip"]):
                        now_true[(key, lk["sample"])] = (float(lk["hits"]), f"{RULES[key]['label']} — {lk['sample']} (200, lu par {lk['ip']})")
            elif key == "exploit_attempt":
                for ip, n in accesslog.exploit_burst(now=now, node_id=node_id).items():
                    if n >= (c["threshold"] or 5) and not accesslog.exempt(node_id, ip):
                        now_true[(key, ip)] = (float(n), _text(key, ip, float(n), c))
            elif accesslog.available(node_id) or accesslog._recent:
                for ip, n in accesslog.burst(now=now, node_id=node_id).items():
                    if n >= (c["threshold"] or 60) and not accesslog.exempt(node_id, ip):
                        now_true[(key, ip)] = (float(n), _text(key, ip, float(n), c))
        except obs.PromError as e:
            errors.append(f"{key}: {e}")
    return now_true, errors


def evaluate(node: dict, now: float | None = None, notify=None) -> dict:
    """Une passe d'évaluation pour un nœud. `notify(rec)` est appelé à chaque passage en cours / résolue."""
    now = now or time.time()
    nid = node["id"]
    cfg = rule_settings(nid)
    true_now, errors = _current(nid, node.get("prometheus_url") or None, cfg, now)
    states = {(r["key"], r["subject"]): dict(r) for r in db.rows("SELECT * FROM alert_state WHERE node_id=?", (nid,))}
    events = []
    for (key, sub), (val, text) in true_now.items():
        st = states.get((key, sub))
        if st is None:
            st = {"state": "pending", "since": now}
            db.run("INSERT INTO alert_state(node_id,key,subject,state,since,value) VALUES(?,?,?,?,?,?)", (nid, key, sub, "pending", now, val))
        if st["state"] == "pending" and now - st["since"] >= cfg[key]["for_s"]:
            db.run("UPDATE alert_state SET state='firing', value=? WHERE node_id=? AND key=? AND subject=?", (val, nid, key, sub))
            events.append({"state": "firing", "key": key, "subject": sub, "value": val, "text": text, "severity": cfg[key]["severity"]})
        elif st["state"] == "firing":
            db.run("UPDATE alert_state SET value=? WHERE node_id=? AND key=? AND subject=?", (val, nid, key, sub))
    for (key, sub), st in states.items():
        if (key, sub) in true_now:
            continue
        db.run("DELETE FROM alert_state WHERE node_id=? AND key=? AND subject=?", (nid, key, sub))
        if st["state"] == "firing":
            events.append({"state": "resolved", "key": key, "subject": sub, "value": st["value"], "text": _text(key, sub, st["value"], cfg[key]) if key in cfg else key,
                           "severity": cfg.get(key, defaults("error_rate"))["severity"]})
    for ev in events:
        ev["snoozed"] = is_snoozed(nid, ev["key"], ev["subject"], now)
        db.run("INSERT INTO alert_history(node_id,key,subject,state,at,value,text) VALUES(?,?,?,?,?,?,?)", (nid, ev["key"], ev["subject"], ev["state"], now, ev["value"], ev["text"]))
        if notify and not ev["snoozed"]:
            notify(ev)
    return {"events": events, "errors": errors}


MAX_SNOOZE_H = 24 * 30


def is_snoozed(node_id: str, key: str, subject: str, now: float | None = None) -> bool:
    r = db.rows("SELECT until FROM alert_snooze WHERE node_id=? AND key=? AND subject=?", (node_id, key, subject))
    return bool(r and r[0]["until"] > (now or time.time()))


def snooze(node_id: str, key: str, subject: str, hours: float) -> float:
    """Met une alerte en sourdine : elle reste visible mais n'envoie plus de message au webhook. Renvoie l'heure de fin."""
    if key not in RULES:
        raise ValueError("Règle inconnue")
    if not (0 < hours <= MAX_SNOOZE_H):
        raise ValueError("Durée invalide (jusqu'à 30 jours)")
    until = time.time() + hours * 3600
    db.run("INSERT INTO alert_snooze(node_id,key,subject,until) VALUES(?,?,?,?) ON CONFLICT(node_id,key,subject) DO UPDATE SET until=excluded.until", (node_id, key, subject, until))
    return until


def unsnooze(node_id: str, key: str, subject: str) -> None:
    db.run("DELETE FROM alert_snooze WHERE node_id=? AND key=? AND subject=?", (node_id, key, subject))


def state(node_id: str) -> list[dict]:
    db.run("DELETE FROM alert_snooze WHERE until<?", (time.time(),))
    snz = {(r["key"], r["subject"]): r["until"] for r in db.rows("SELECT key, subject, until FROM alert_snooze WHERE node_id=?", (node_id,))}
    out = []
    for r in db.rows("SELECT key, subject, state, since, value FROM alert_state WHERE node_id=? ORDER BY since", (node_id,)):
        d = dict(r)
        d["snoozedUntil"] = snz.get((d["key"], d["subject"]))
        out.append(d)
    return out


def history(node_id: str, limit: int = 100) -> list[dict]:
    return [dict(r) for r in db.rows("SELECT key, subject, state, at, value, text FROM alert_history WHERE node_id=? ORDER BY at DESC, id DESC LIMIT ?", (node_id, limit))]


# ------------------------------------------------------------------ prévenir
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def send_webhook(url: str, payload: dict) -> str | None:
    """POST JSON ; renvoie un message d'erreur ou None. Ne suit jamais de redirection ; ne cite jamais l'adresse (elle peut contenir un jeton)."""
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "armadok"})
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=6) as r:
            r.read(1000)
        return None
    except urllib.error.HTTPError as e:
        return f"Le webhook a répondu {e.code}"
    except (urllib.error.URLError, OSError):
        return "Le webhook est injoignable depuis ce serveur"


def _notifier(node: dict):
    def notify(ev: dict) -> None:
        mark = "🔴" if ev["state"] == "firing" and ev["severity"] == "critical" else "🟠" if ev["state"] == "firing" else "🟢"
        msg = f"{mark} {'ALERTE' if ev['state'] == 'firing' else 'Résolue'} · {node['name']} · {ev['text']}"
        audit("système", f"alert.{ev['state']}", ev["text"], node=node["id"])
        publish("alerts", node["id"])
        url = reveal(node.get("webhook_url"))
        if url:
            payload = {"text": msg, "content": msg, "state": ev["state"], "rule": ev["key"], "subject": ev["subject"], "value": ev["value"],
                       "severity": ev["severity"], "node": node["name"], "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            threading.Thread(target=send_webhook, args=(url, payload), daemon=True).start()
    return notify


def _loop() -> None:
    time.sleep(15)
    while True:
        try:
            for node in db.rows("SELECT * FROM nodes WHERE status='active' AND deleted_at IS NULL"):
                n = dict(node)
                try:
                    if (n["kind"] == "local" and n.get("prometheus_url")) or accesslog.available(n["id"]):   # un nœud distant : seulement s'il envoie son journal d'accès
                        evaluate(n, notify=_notifier(n))
                except Exception:  # noqa: BLE001 - un nœud en panne ne doit pas empêcher les autres
                    pass
        except Exception:  # noqa: BLE001 - la surveillance ne doit jamais s'arrêter
            pass
        time.sleep(EVERY)


_started = False


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="alerts", daemon=True).start()
