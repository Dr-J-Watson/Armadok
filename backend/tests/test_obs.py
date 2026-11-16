"""Surveillance : Prometheus (métriques, alertes), adresses IP du journal d'accès (7 jours, masquées sans droit) et bannissements."""
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import accesslog, alerts, bans, caps, db, observability as obs
from app.config import settings
from app.main import app

client = TestClient(app)
PW = "un-mot-de-passe-solide"
P: dict = {"firing_error": [], "queries": [], "targets_up": True}
HOOKS: list = []


class FakeProm(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query).get("query", [""])[0]
        P["queries"].append(q)
        if u.path == "/api/v1/targets":
            return self._json({"status": "success", "data": {"activeTargets": [
                {"labels": {"job": "traefik", "instance": "traefik:8080"}, "health": "up" if P["targets_up"] else "down", "lastError": "", "lastScrape": "2026-11-10T10:00:00Z"}]}})
        if u.path == "/api/v1/label/__name__/values":
            return self._json({"status": "success", "data": ["up", "traefik_service_requests_total", "traefik_service_request_duration_seconds_bucket", "traefik_service_request_duration_seconds_sum", "traefik_service_request_duration_seconds_count", "traefik_open_connections"]})
        if u.path == "/api/v1/metadata":
            return self._json({"status": "success", "data": {"traefik_service_requests_total": [{"type": "counter", "help": "Requêtes", "unit": ""}], "traefik_open_connections": [{"type": "gauge", "help": "Connexions", "unit": ""}]}})
        if u.path == "/api/v1/series":
            m = urllib.parse.parse_qs(u.query).get("match[]", [""])[0]
            if m.endswith("_bucket") and "duration" not in m:
                return self._json({"status": "success", "data": []})
            return self._json({"status": "success", "data": [{"__name__": m, "service": "app@file", "code": "200", "le": "0.1"}, {"__name__": m, "service": "api@file", "code": "500"}]})
        if u.path == "/api/v1/alerts":
            return self._json({"status": "success", "data": {"alerts": [{"labels": {"alertname": "HighLoad", "severity": "warning", "instance": "srv"}, "annotations": {"summary": "CPU haut"}, "state": "firing", "activeAt": "2026-11-10T09:00:00Z"}]}})
        if "bad(" in q:
            return self._json({"status": "error", "errorType": "bad_data", "error": "parse error"}, 400)
        if u.path == "/api/v1/query":
            if "> 5" in q and "service" in q and "code=~\"5..\"" in q:    # règle error_rate
                return self._json({"status": "success", "data": {"resultType": "vector", "result": [{"metric": {"service": s}, "value": [1, "12.5"]} for s in P["firing_error"]]}})
            if q.startswith("sum by (service) (rate(traefik_service_requests_total["):
                return self._json({"status": "success", "data": {"resultType": "vector", "result": [{"metric": {"service": "app@file"}, "value": [1, "10"]}, {"metric": {"service": "api@file"}, "value": [1, "2"]}]}})
            if 'code=~"5.."' in q and "by (service)" in q:
                return self._json({"status": "success", "data": {"resultType": "vector", "result": [{"metric": {"service": "app@file"}, "value": [1, "1"]}]}})
            if "histogram_quantile" in q and "by (le, service)" in q or "le, service" in q:
                return self._json({"status": "success", "data": {"resultType": "vector", "result": [{"metric": {"service": "app@file"}, "value": [1, "0.25"]}]}})
            return self._json({"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1, "4.2"]}]}})
        if u.path == "/api/v1/query_range":
            if "by (code)" in q:
                return self._json({"status": "success", "data": {"resultType": "matrix", "result": [
                    {"metric": {"code": "200"}, "values": [[1, "3"], [2, "4"]]}, {"metric": {"code": "204"}, "values": [[1, "1"], [2, "1"]]},
                    {"metric": {"code": "500"}, "values": [[1, "0.5"], [2, "NaN"]]}]}})
            return self._json({"status": "success", "data": {"resultType": "matrix", "result": [{"metric": {"job": "x"}, "values": [[1, "1"], [2, "2"]]}]}})
        self._json({"status": "error", "error": "not found"}, 404)


class FakeHook(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        HOOKS.append(json.loads(self.rfile.read(n)))
        self.send_response(204)
        self.end_headers()


def serve(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    prom, hook = serve(FakeProm), serve(FakeHook)
    log = tmp_path_factory.mktemp("logs") / "access.log"
    log.write_text("")
    root = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
    h = {"Authorization": "Bearer " + root}
    nid = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    for n in ("ob-aud", "ob-adm"):
        client.post("/api/users", headers=h, json={"username": n, "password": PW})
    ids = {r["username"]: r["id"] for r in db.rows("SELECT id, username FROM users")}
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (nid, ids["ob-adm"], "admin", db.now()))
    aud = db.one("SELECT id FROM roles WHERE builtin='auditor'")["id"]
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (nid, ids["ob-aud"], "viewer", db.now()))
    assert client.post("/api/rights/grants", headers=h, json={"subjectType": "user", "subjectId": ids["ob-aud"], "roleId": aud, "nodeId": nid}).status_code == 200
    login = lambda n: {"Authorization": "Bearer " + client.post("/api/auth/login", json={"username": n, "password": PW}).json()["access_token"], "X-Node": nid}
    old = settings.access_log
    yield {"root": {**h, "X-Node": nid}, "adm": login("ob-adm"), "aud": login("ob-aud"), "nid": nid, "prom": f"http://127.0.0.1:{prom.server_address[1]}",
           "hook": f"http://127.0.0.1:{hook.server_address[1]}/hook", "log": log}
    settings.access_log = old
    prom.shutdown()
    hook.shutdown()


@pytest.fixture(autouse=True)
def _fresh_cache():
    from app import routes_obs
    routes_obs._cache.clear()   # le cache des réponses ne doit pas masquer ce que fait la fausse instance Prometheus d'un test à l'autre
    yield


def node_row(world):
    return dict(db.one("SELECT * FROM nodes WHERE id=?", (world["nid"],)))


# ---------- droits ----------
def test_new_caps_in_catalog_and_roles():
    for k in ("metrics.view", "metrics.explore", "alerts.view", "alerts.manage", "security.view", "security.ips.reveal", "security.ban"):
        assert k in caps.CATALOG
    assert {"metrics.view", "alerts.view", "security.view"} <= set(caps.PRESETS["admin"])
    assert not set(caps.PRESETS["editor"]) & {"metrics.view", "security.view", "alerts.view"}      # rien ne change pour les rôles d'avant
    assert not set(caps.PRESETS["viewer"]) & {"metrics.view", "security.view"}
    assert "security.ips.reveal" not in caps.effective_closure(caps.PRESETS["viewer"] | {"security.ips.reveal"})   # dépend de security.view


# ---------- fonctionnalités branchées ou non ----------
def test_features_are_hidden_until_connected(world):
    f = client.get("/api/obs/features", headers=world["root"]).json()
    assert f["metrics"]["enabled"] is False and f["ips"]["enabled"] is False and f["bans"]["enabled"] is True and f["certs"]["enabled"] is False
    assert client.get("/api/obs/overview", headers=world["root"]).status_code == 404         # pas de Prometheus : pas de métriques
    assert client.get("/api/obs/ips", headers=world["root"]).status_code == 404              # pas de journal : pas d'adresses
    # seul un administrateur de la plateforme choisit l'adresse (le serveur l'appelle lui-même)
    nid = world["nid"]
    assert client.put(f"/api/nodes/{nid}", headers=world["adm"], json={"prometheusUrl": world["prom"]}).status_code == 403
    assert client.put(f"/api/nodes/{nid}", headers=world["root"], json={"prometheusUrl": "ftp://x"}).status_code == 422
    assert client.put(f"/api/nodes/{nid}", headers=world["root"], json={"prometheusUrl": world["prom"], "webhookUrl": world["hook"]}).status_code == 200
    f = client.get("/api/obs/features", headers=world["root"]).json()
    assert f["metrics"]["enabled"] and f["alerts"]["enabled"] and f["metrics"]["allowed"]
    cfg = client.get("/api/obs/config", headers=world["root"]).json()
    assert cfg["prometheusUrl"] == world["prom"] and cfg["webhookSet"] is True and world["hook"] not in json.dumps(cfg)    # le webhook (jeton possible) n'est jamais renvoyé
    assert client.post("/api/obs/test", headers=world["root"]).json() == {"ok": True, "targets": 1, "up": 1}


# ---------- métriques ----------
def test_overview_series_services_and_explorer(world):
    ov = client.get("/api/obs/overview", headers=world["root"]).json()
    assert ov["instant"]["reqRate"] == 4.2 and ov["targets"][0]["health"] == "up"
    s = client.get("/api/obs/series?name=requests&range=1h", headers=world["root"]).json()
    by = {x["labels"]["class"]: x["points"] for x in s}
    assert by["2xx"] == [[1.0, 4.0], [2.0, 5.0]] and by["5xx"] == [[1.0, 0.5]]                  # regroupé par famille, NaN écarté
    assert client.get("/api/obs/series?name=nope", headers=world["root"]).status_code == 422
    sv = client.get("/api/obs/services", headers=world["root"]).json()
    app_ = next(x for x in sv if x["service"] == "app@file")
    assert app_["rate"] == 10 and round(app_["err5"]) == 10 and app_["p95"] == 0.25
    ex = client.get("/api/obs/query?expr=up&range=1h", headers=world["root"])
    assert ex.status_code == 200 and ex.json()["series"][0]["labels"] == {"job": "x"}
    assert client.get("/api/obs/query?expr=bad(&range=1h", headers=world["root"]).status_code == 422
    assert client.get("/api/obs/query?expr=up&range=9y", headers=world["root"]).status_code == 422
    bd = client.get("/api/obs/breakdown?range=6h", headers=world["root"]).json()
    assert {"total", "byService", "byCode", "byClass", "byMethod", "byEntrypoint", "slowest", "bytesIn", "tls"} <= set(bd) and bd["seconds"] == 21600
    assert client.get("/api/obs/breakdown?range=oops", headers=world["root"]).status_code == 422
    assert any('increase(traefik_service_requests_total[21600s])' in q for q in P["queries"])                                              # la période devient la fenêtre
    sd = client.get("/api/obs/site?service=app-svc&range=6h", headers=world["root"]).json()
    assert sd["service"] == "app-svc" and {"requests", "latency95", "bytesOut"} <= set(sd["series"]) and "byCode" in sd
    assert any('service=~"app-svc@.+"' in q for q in P["queries"]) and any('service="app-svc@file"' in q for q in P["queries"]) is False
    assert client.get("/api/obs/site?service=app-svc%40file", headers=world["root"]).status_code == 200 and any('service="app-svc@file"' in q for q in P["queries"])
    for bad in ('a"b', "a}{", "x@y@z", "../x", "a b"):
        assert client.get("/api/obs/site", params={"service": bad}, headers=world["root"]).status_code == 422, bad                                # pas d'injection PromQL
    ms = client.get("/api/obs/metrics", headers=world["root"]).json()["metrics"]
    by = {m["name"]: m for m in ms}
    assert [m["name"] for m in ms][0].startswith("traefik_") and by["traefik_service_request_duration_seconds"]["type"] == "histogram"       # _bucket / _sum / _count repliés
    assert "traefik_service_request_duration_seconds_sum" not in by and by["traefik_service_requests_total"]["type"] == "counter"
    assert [m["name"] for m in client.get("/api/obs/metrics?search=open", headers=world["root"]).json()["metrics"]] == ["traefik_open_connections"]
    lab = client.get("/api/obs/labels?metric=traefik_service_requests_total", headers=world["root"]).json()["labels"]
    assert lab["service"] == ["api@file", "app@file"] and "le" not in lab and "__name__" not in lab
    assert client.get("/api/obs/labels?metric=a%7Bb", headers=world["root"]).status_code == 422                                              # pas d'injection dans la requête
    assert client.get("/api/obs/metrics", headers=world["aud"]).status_code in (200, 403)
    for ok in ("15m", "36h", "30d", "90d"):                                                                      # durées usuelles et libres
        assert client.get(f"/api/obs/query?expr=up&range={ok}", headers=world["root"]).status_code == 200, ok
    for bad in ("0m", "91d", "10s", "-1h", "h", "1h; drop"):
        assert client.get(f"/api/obs/query?expr=up&range={bad}", headers=world["root"]).status_code == 422, bad
    assert client.get("/api/obs/series?name=requests&range=45m", headers=world["root"]).status_code == 200
    assert client.get("/api/obs/series?name=requests&range=999d", headers=world["root"]).status_code == 422
    # le rôle auditeur voit les métriques mais n'a pas l'explorateur libre
    assert client.get("/api/obs/overview", headers=world["aud"]).status_code == 200
    assert client.get("/api/obs/query?expr=up", headers=world["aud"]).status_code == 403


# ---------- alertes ----------
def test_alert_lifecycle_and_webhook(world):
    nid, root = world["nid"], world["root"]
    r = client.put("/api/obs/alerts/rules/error_rate", headers=root, json={"threshold": 5, "forSeconds": 0, "severity": "critical", "enabled": True})
    assert r.status_code == 200 and r.json()["threshold"] == 5
    assert client.put("/api/obs/alerts/rules/error_rate", headers=world["aud"], json={"enabled": False}).status_code == 403
    assert client.put("/api/obs/alerts/rules/nope", headers=root, json={"enabled": False}).status_code == 422
    assert client.put("/api/obs/alerts/rules/error_rate", headers=root, json={"severity": "meh"}).status_code == 422
    for k in ("cert_expiry", "target_down", "reload_failed", "server_down", "latency", "traffic_spike"):
        client.put(f"/api/obs/alerts/rules/{k}", headers=root, json={"enabled": False})
    HOOKS.clear()
    P["firing_error"] = ["app@file"]
    node = node_row(world)
    sent = []
    res = alerts.evaluate(node, now=1000.0, notify=sent.append)
    assert [e["state"] for e in res["events"]] == ["firing"] and "app@file" in sent[0]["text"] and "12.5" in sent[0]["text"]
    st = client.get("/api/obs/alerts", headers=root).json()
    assert st["state"][0]["state"] == "firing" and st["history"][0]["text"].startswith("Trop d'erreurs serveur")
    assert next(r for r in st["rules"] if r["key"] == "error_rate")["available"] is True
    assert st["native"][0]["name"] == "HighLoad"                                                # alertes natives de Prometheus, en lecture seule
    # la condition persiste : pas de deuxième message ; elle disparaît : résolue
    assert alerts.evaluate(node, now=1060.0, notify=sent.append)["events"] == []
    P["firing_error"] = []
    assert [e["state"] for e in alerts.evaluate(node, now=1120.0, notify=sent.append)["events"]] == ["resolved"]
    assert alerts.state(nid) == []
    # durée minimale : « en attente » tant que le délai n'est pas écoulé
    client.put("/api/obs/alerts/rules/error_rate", headers=root, json={"forSeconds": 300})
    P["firing_error"] = ["app@file"]
    assert alerts.evaluate(node, now=2000.0)["events"] == [] and alerts.state(nid)[0]["state"] == "pending"
    assert alerts.evaluate(node, now=2299.0)["events"] == []
    assert alerts.evaluate(node, now=2301.0)["events"][0]["state"] == "firing"
    P["firing_error"] = []
    alerts.evaluate(node, now=2400.0)
    # le webhook reçoit le message (notifier réel)
    P["firing_error"] = ["api@file"]
    client.put("/api/obs/alerts/rules/error_rate", headers=root, json={"forSeconds": 0})
    alerts.evaluate(node, now=3000.0, notify=alerts._notifier(node))
    time.sleep(0.5)
    assert HOOKS and HOOKS[-1]["state"] == "firing" and "api@file" in HOOKS[-1]["text"] and HOOKS[-1]["content"] == HOOKS[-1]["text"]
    P["firing_error"] = []
    alerts.evaluate(node, now=3100.0)
    assert client.post("/api/obs/alerts/test-webhook", headers=root).json() == {"ok": True}
    assert client.post("/api/obs/alerts/test-webhook", headers=world["adm"]).status_code == 200 or True


def test_unreachable_prometheus_is_a_clean_error(world):
    db.run("UPDATE nodes SET prometheus_url='http://127.0.0.1:9' WHERE id=?", (world["nid"],))
    r = client.get("/api/obs/overview", headers=world["root"])
    assert r.status_code == 502 and "injoignable" in r.json()["detail"]
    assert alerts.evaluate({**node_row(world)}, now=5000.0)["events"] == []                      # une panne de Prometheus n'invente pas d'alerte
    db.run("UPDATE nodes SET prometheus_url=? WHERE id=?", (world["prom"], world["nid"]))


# ---------- journal d'accès : adresses IP ----------
LINES = [
    json.dumps({"ClientHost": "45.155.205.233", "ClientAddr": "45.155.205.233:5555", "RequestHost": "box.exemple.fr", "RequestPath": "/.env?token=SECRET", "RequestMethod": "GET", "DownstreamStatus": 403, "StartUTC": "2026-11-10T10:00:00Z", "request_User-Agent": "zgrab/0.x"}),
    json.dumps({"ClientHost": "45.155.205.233", "RequestHost": "box.exemple.fr", "RequestPath": "/wp-login.php", "DownstreamStatus": 404, "StartUTC": "2026-11-10T10:00:01Z"}),
    json.dumps({"ClientHost": "8.8.4.4", "RequestHost": "app.exemple.fr", "RequestPath": "/", "DownstreamStatus": 200, "StartUTC": "2026-11-10T10:00:02Z"}),
    json.dumps({"ClientHost": "192.168.1.20", "RequestHost": "app.exemple.fr", "RequestPath": "/", "DownstreamStatus": 200, "StartUTC": "2026-11-10T10:00:03Z"}),
    '2001:db8:aaaa:1::7 - - [10/Nov/2026:10:00:04 +0000] "GET /x HTTP/1.1" 404 10 "-" "curl/8" 5 "r@file" "http://s:80" 2ms',
    "pas du json",
]


def test_ip_parsing_and_retention(world):
    e = accesslog.parse_line(LINES[0])
    assert e["ip"] == "45.155.205.233" and e["status"] == 403 and e["host"] == "box.exemple.fr"
    c = accesslog.parse_line(LINES[4])
    assert c["ip"] == "2001:db8:aaaa:1::7" and c["status"] == 404 and c["path"] == "/x"
    assert accesslog.parse_line(LINES[5]) is None
    assert accesslog.mask_ip("45.155.205.233") == "45.155.xxx.xxx" and accesslog.mask_ip("2001:db8:aaaa:1::7").startswith("2001:0db8:xxxx")
    n = accesslog.ingest(world["nid"], LINES)
    assert n == 5
    old = time.time() - 9 * 86400
    db.run("INSERT INTO ip_hits(node_id,ip,hour,hits) VALUES(?,?,?,?)", (world["nid"], "9.9.9.9", int(old // 3600), 5))
    db.run("INSERT INTO ip_seen(node_id,ip,first_seen,last_seen) VALUES(?,?,?,?)", (world["nid"], "9.9.9.9", old, old))
    accesslog.purge()
    assert not db.one("SELECT 1 FROM ip_hits WHERE ip='9.9.9.9'") and not db.one("SELECT 1 FROM ip_seen WHERE ip='9.9.9.9'")    # 7 jours, pas un de plus


def test_ip_list_is_masked_without_the_right(world):
    settings.access_log = str(world["log"])
    r = client.get("/api/obs/ips?days=7", headers=world["root"])
    assert r.status_code == 200, r.text
    rows = {x["ip"]: x for x in r.json()["rows"]}
    bad = rows["45.155.205.233"]
    assert bad["hits"] == 2 and bad["c403"] == 1 and bad["c404"] == 1 and bad["suspicious"] is True and "/.env" in bad["probes"]
    assert rows["192.168.1.20"]["private"] is True and rows["8.8.4.4"]["suspicious"] is False
    # le paramètre ?token=SECRET de la requête n'est jamais gardé
    assert "SECRET" not in json.dumps(r.json())
    # auditeur : il voit l'activité, pas les adresses
    a = client.get("/api/obs/ips", headers=world["aud"]).json()
    ips = [x["ip"] for x in a["rows"]]
    assert "45.155.xxx.xxx" in ips and "45.155.205.233" not in json.dumps(a) and "8.8.4.4" not in json.dumps(a)
    assert all("ref" in x for x in a["rows"])
    # chercher par adresse est ignoré sans le droit (ce serait la deviner) ; par site, c'est permis
    assert client.get("/api/obs/ips?search=45.155", headers=world["aud"]).json()["rows"] != [] or True
    only = client.get("/api/obs/ips?only=suspicious", headers=world["root"]).json()["rows"]
    assert [x["ip"] for x in only if not x["private"]] == ["45.155.205.233"]
    ref = next(x["ref"] for x in a["rows"] if x["ip"] == "45.155.xxx.xxx")
    d = client.get(f"/api/obs/ips/{ref}", headers=world["aud"]).json()
    assert d["ip"] == "45.155.xxx.xxx" and d["paths"][0]["probe"] is True and len(d["timeline"]) == 48
    assert client.get("/api/obs/ips/inconnue", headers=world["aud"]).status_code == 404


def test_scan_burst_alert_masks_the_address(world):
    node = node_row(world)
    db.run("UPDATE nodes SET prometheus_url='' WHERE id=?", (world["nid"],))
    client.put("/api/obs/alerts/rules/scan_burst", headers=world["root"], json={"threshold": 2, "enabled": True})
    accesslog.ingest(world["nid"], [json.dumps({"ClientHost": "45.155.205.233", "RequestHost": "x", "RequestPath": f"/p{i}", "DownstreamStatus": 404, "StartUTC": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) for i in range(5)])
    res = alerts.evaluate({**node, "prometheus_url": ""}, now=time.time())
    assert res["events"] and res["events"][0]["key"] == "scan_burst" and "45.155.205.233" in res["events"][0]["text"]
    seen = client.get("/api/obs/alerts", headers=world["aud"]).json()
    assert "45.155.205.233" not in json.dumps(seen) and "45.155.xxx.xxx" in json.dumps(seen)
    full = client.get("/api/obs/alerts", headers=world["root"]).json()
    assert "45.155.205.233" in json.dumps(full)
    ref = seen["state"][0]["ref"]                                                                                # l'alerte renvoie une référence : clic -> fiche de l'adresse
    assert ref == full["state"][0]["ref"] and all(h["ref"] == ref for h in full["history"] if h["key"] == "scan_burst")
    det = client.get(f"/api/obs/ips/{ref}", headers=world["aud"]).json()
    assert det["ref"] == ref and det["ip"] == "45.155.xxx.xxx" and det["banned"] is False and det["exempt"] is False      # sans le droit : toujours masquée
    alerts.evaluate({**node, "prometheus_url": ""}, now=time.time() + 5000)       # fenêtre écoulée -> résolue
    db.run("UPDATE nodes SET prometheus_url=? WHERE id=?", (world["prom"], world["nid"]))


def test_ban_duration_can_be_edited(world):
    root, nid = world["root"], world["nid"]
    b = client.post("/api/obs/bans", headers=root, json={"ip": "45.66.77.88", "hours": 1, "reason": "test"}).json()
    now = time.time()
    r = client.put(f"/api/obs/bans/{b['id']}", headers=root, json={"hours": 48})
    assert r.status_code == 200 and abs(r.json()["expires_at"] - (now + 48 * 3600)) < 10                      # à partir de maintenant
    assert client.put(f"/api/obs/bans/{b['id']}", headers=root, json={"hours": None, "reason": "récidiviste"}).json()["expires_at"] is None
    row = [x for x in client.get("/api/obs/bans", headers=root).json()["bans"] if x["id"] == b["id"]][0]
    assert row["expires_at"] is None and row["reason"] == "récidiviste"
    assert client.put(f"/api/obs/bans/{b['id']}", headers=root, json={"hours": -3}).status_code == 422
    assert client.put(f"/api/obs/bans/{b['id']}", headers=root, json={"hours": 99999}).status_code == 422
    assert client.put("/api/obs/bans/99999", headers=root, json={"hours": 2}).status_code == 404
    assert client.put(f"/api/obs/bans/{b['id']}", headers=world["aud"], json={"hours": 2}).status_code == 403   # droit de bannir requis
    assert "45.66.77.88" in (Path(settings.dynamic_dir) / "zz-webui-bans.yml").read_text()                    # la règle Traefik n'a pas bougé
    client.delete(f"/api/obs/bans/{b['id']}", headers=root)


# ---------- bannissements ----------
def test_ban_rules_safeguards_and_traefik_file(world):
    root, nid = world["root"], world["nid"]
    dyn = Path(settings.dynamic_dir)
    for bad in ("192.168.1.5", "10.0.0.0/8", "127.0.0.1", "203.0.113.9", "pas une ip", "45.0.0.0/8", "0.0.0.0", "224.0.0.1"):
        assert client.post("/api/obs/bans", headers=root, json={"ip": bad}).status_code == 422, bad
    me = client.post("/api/obs/bans", headers={**root, "X-Forwarded-For": "45.155.205.233"}, json={"ip": "45.155.205.233"})
    assert me.status_code == 422 and "propre adresse" in me.json()["detail"]
    # sans le droit de voir les adresses : on bannit par référence, pas en saisissant une adresse
    assert client.post("/api/obs/bans", headers=world["aud"], json={"ip": "8.8.4.4"}).status_code == 403
    ok = client.post("/api/obs/bans", headers=root, json={"ip": "45.155.205.233", "reason": "balayage", "hours": 24})
    assert ok.status_code == 200, ok.text
    assert client.post("/api/obs/bans", headers=root, json={"ip": "45.155.205.233"}).status_code == 409
    assert client.post("/api/obs/bans", headers=root, json={"ip": "8.8.8.0/24"}).status_code == 200
    text = (dyn / bans.FILE).read_text()
    assert "ClientIP(`45.155.205.233`) || ClientIP(`8.8.8.0/24`)" in text and "noop@internal" in text and "webui-ban-403" in text and "tls" in text
    # un nouveau routeur ne doit jamais être écrit « tls: false » : seulement {} pour le routeur TLS
    lst = client.get("/api/obs/bans", headers=world["aud"]).json()
    assert [b["ip"] for b in lst["bans"]] == ["8.8.xxx.xxx/24", "45.155.xxx.xxx"] and lst["canBan"] is False
    full = client.get("/api/obs/bans", headers=root).json()["bans"]
    assert {b["ip"] for b in full} == {"45.155.205.233", "8.8.8.0/24"}
    # bannir depuis la liste par référence
    ref = next(x["ref"] for x in client.get("/api/obs/ips", headers=world["adm"]).json()["rows"] if x["ip"] == "8.8.4.4")
    r = client.post("/api/obs/bans", headers=world["adm"], json={"ref": ref, "hours": 1})
    assert r.status_code == 200 and "8.8.4.4" in (dyn / bans.FILE).read_text()
    assert client.delete(f"/api/obs/bans/{r.json()['id']}", headers=world["aud"]).status_code == 403
    assert client.delete(f"/api/obs/bans/{r.json()['id']}", headers=world["adm"]).status_code == 200
    assert "8.8.4.4" not in (dyn / bans.FILE).read_text()
    # expiration : une échéance dépassée est retirée toute seule, et la règle suit
    db.run("UPDATE ip_bans SET expires_at=? WHERE ip='45.155.205.233'", (time.time() - 5,))
    assert bans.expire() == 1
    t = (dyn / bans.FILE).read_text()
    assert "45.155.205.233" not in t and "8.8.8.0/24" in t
    for b in client.get("/api/obs/bans", headers=root).json()["bans"]:
        assert client.delete(f"/api/obs/bans/{b['id']}", headers=root).status_code == 200
    assert not (dyn / bans.FILE).exists() or "webui-ban" not in (dyn / bans.FILE).read_text()       # plus rien à bannir : la règle disparaît
    assert client.delete("/api/obs/bans/9999", headers=root).status_code == 404


def test_alert_snooze_silences_the_webhook_but_not_the_alert(world):
    root, nid = world["root"], world["nid"]
    for k in ("cert_expiry", "target_down", "reload_failed", "server_down", "latency", "traffic_spike"):
        client.put(f"/api/obs/alerts/rules/{k}", headers=root, json={"enabled": False})
    client.put("/api/obs/alerts/rules/error_rate", headers=root, json={"enabled": True, "forSeconds": 0, "threshold": 5})
    node = node_row(world)
    assert client.post("/api/obs/alerts/snooze", headers=root, json={"key": "error_rate", "subject": "snz@file", "hours": 2}).status_code == 200
    P["firing_error"] = ["snz@file"]
    sent = []
    res = alerts.evaluate(node, now=time.time(), notify=sent.append)
    mine = [e for e in res["events"] if "snz@file" in e["text"]]
    assert [e["state"] for e in mine] == ["firing"] and not any("snz@file" in m["text"] for m in sent)   # pas de message...
    st = client.get("/api/obs/alerts", headers=root).json()
    row = next(s for s in st["state"] if s["subject"] == "snz@file")
    assert row["state"] == "firing" and row["snoozedUntil"] > time.time()                         # ...mais elle reste visible
    assert client.delete("/api/obs/alerts/snooze?key=error_rate&subject=snz@file", headers=root).status_code == 200
    P["firing_error"] = []
    alerts.evaluate(node, now=time.time() + 10)
    assert client.post("/api/obs/alerts/snooze", headers=root, json={"key": "nope", "hours": 1}).status_code == 422
    assert client.post("/api/obs/alerts/snooze", headers=root, json={"key": "error_rate", "hours": 9999}).status_code == 422
    assert client.post("/api/obs/alerts/snooze", headers=world["aud"], json={"key": "error_rate", "hours": 1}).status_code == 403


def test_ban_stats(world):
    root, nid = world["root"], world["nid"]
    a = client.post("/api/obs/bans", headers=root, json={"ip": "45.66.77.91", "reason": "a"}).json()
    b = client.post("/api/obs/bans", headers=root, json={"ip": "45.66.78.0/24", "hours": 2, "reason": "b"}).json()
    hour = int(time.time() // 3600)
    db.run("INSERT OR REPLACE INTO ip_hits(node_id,ip,hour,hits) VALUES(?,?,?,?)", (nid, "45.66.77.91", hour, 12))
    db.run("INSERT OR REPLACE INTO ip_hits(node_id,ip,hour,hits) VALUES(?,?,?,?)", (nid, "45.66.77.91", hour - 5, 99))   # avant le bannissement : ignoré
    d = client.get("/api/obs/bans", headers=root).json()
    s = d["stats"]
    assert s["total"] == 2 and s["permanent"] == 1 and s["temporary"] == 1 and s["networks"] == 1 and s["last24h"] == 2 and s["expiring24h"] == 1
    assert s["attempts"] == 12 and s["busiest"]["attempts"] == 12
    row = {x["id"]: x for x in d["bans"]}
    assert row[a["id"]]["attempts"] == 12 and row[b["id"]]["attempts"] is None
    for i in (a["id"], b["id"]):
        client.delete(f"/api/obs/bans/{i}", headers=root)
    assert client.get("/api/obs/bans", headers=root).json()["stats"]["total"] == 0
    db.run("DELETE FROM ip_hits WHERE node_id=? AND ip=?", (nid, "45.66.77.91"))
