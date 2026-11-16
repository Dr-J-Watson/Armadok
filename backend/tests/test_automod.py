"""Automod : conditions, observation puis blocage, pays inconnu jamais banni, adresses validées et locales épargnées, limite de sécurité, droits."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import accesslog, automod, db, geo
from app.config import settings
from app.main import app

client = TestClient(app)
PW = "Un-Mot-De-Passe-Solide-9"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    log = tmp_path_factory.mktemp("am") / "access.log"
    log.write_text("")
    old = settings.access_log
    settings.access_log = str(log)
    tok = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
    nid = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    h = {"Authorization": "Bearer " + tok, "X-Node": nid}
    client.post("/api/users", headers=h, json={"username": "am-ed", "password": PW})
    uid = db.one("SELECT id FROM users WHERE username='am-ed'")["id"]
    db.run("INSERT INTO node_members(node_id,user_id,role,created_at) VALUES(?,?,?,?)", (nid, uid, "editor", db.now()))
    ed = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"username": "am-ed", "password": PW}).json()["access_token"], "X-Node": nid}
    yield {"h": h, "ed": ed, "nid": nid}
    settings.access_log = old
    for t in ("ip_hits", "ip_seen", "ip_paths", "ip_attacks", "ip_trusted", "ip_bans", "automod_rules", "automod_log", "alert_state", "alert_history", "alert_rules"):
        db.run(f"DELETE FROM {t} WHERE node_id=?", (nid,))
    accesslog._recent.clear()
    accesslog._recent_exploit.clear()


def L(ip, path, status, ua="Mozilla/5.0"):
    return json.dumps({"ClientHost": ip, "RequestHost": "app.exemple.fr", "RequestPath": path, "DownstreamStatus": status, "RequestMethod": "GET",
                       "StartUTC": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "request_User-Agent": ua})


COUNTRY = {"46.1.1.1": "RU", "46.1.1.2": "RU", "46.1.1.3": "FR", "46.1.1.4": None, "46.1.1.5": "CN", "10.9.9.9": "RU"}


@pytest.fixture(autouse=True)
def fake_geo(monkeypatch):
    monkeypatch.setattr(geo, "status", lambda: {"available": True, "updated": 1})
    monkeypatch.setattr(geo, "lookup", lambda ip, precise=True: ({"country": COUNTRY[ip], "countryName": COUNTRY[ip]} if COUNTRY.get(ip) else None))


def rule(h, **kw):
    body = {"name": "r", "conditions": {"suspicious": True}, "mode": "observe", "hours": 24, **kw}
    return client.post("/api/obs/automod", headers=h, json=body)


def test_matching_logic_is_exact():
    row = {"hits": 30, "errors": 25, "suspicious": True, "attacks": [{"kind": "sqli"}]}
    m = automod.matches
    assert m({"suspicious": True}, row, None) and not m({"suspicious": True}, {**row, "suspicious": False}, None)
    assert m({"country": {"mode": "in", "codes": ["RU"]}}, row, "RU") and not m({"country": {"mode": "in", "codes": ["RU"]}}, row, "FR")
    assert m({"country": {"mode": "not_in", "codes": ["FR"]}}, row, "RU") and not m({"country": {"mode": "not_in", "codes": ["FR"]}}, row, "FR")
    assert not m({"country": {"mode": "not_in", "codes": ["FR"]}}, row, None) and not m({"country": {"mode": "in", "codes": ["RU"]}}, row, None)   # pays inconnu : jamais
    assert m({"attacks": ["sqli", "rce"]}, row, None) and not m({"attacks": ["xss"]}, row, None) and m({"attacks": "any"}, row, None)
    assert not m({"attacks": "any"}, {**row, "attacks": []}, None)
    assert m({"min_hits": 30}, row, None) and not m({"min_hits": 31}, row, None) and not m({"min_errors": 26}, row, None)
    assert m({"suspicious": True, "country": {"mode": "in", "codes": ["RU"]}, "min_hits": 10}, row, "RU") and not m({"suspicious": True, "country": {"mode": "in", "codes": ["RU"]}}, row, "FR")   # ET


def test_validation(world):
    h = world["h"]
    assert rule(h, conditions={}).status_code == 422                                                       # aucune condition : bannirait tout le monde
    assert rule(h, conditions={"country": {"mode": "in", "codes": []}}).status_code == 422
    assert rule(h, conditions={"country": {"mode": "x", "codes": ["RU"]}}).status_code == 422
    assert rule(h, conditions={"attacks": ["pas-un-type"]}).status_code == 422
    assert rule(h, conditions={"min_hits": -3}).status_code == 422
    assert rule(h, mode="boom").status_code == 422
    assert rule(h, name="  ").status_code == 422
    ok = rule(h, conditions={"country": {"mode": "in", "codes": ["ru", "cn", "zz!"]}, "junk": 1})
    assert ok.status_code == 200 and ok.json()["conditions"] == {"country": {"mode": "in", "codes": ["CN", "RU"]}} and ok.json()["summary"] == ["pays dans CN, RU"]
    client.delete(f"/api/obs/automod/{ok.json()['id']}", headers=h)


def test_country_rule_needs_the_geo_database(world, monkeypatch):
    monkeypatch.setattr(geo, "status", lambda: {"available": False, "updated": None})
    r = rule(world["h"], conditions={"country": {"mode": "in", "codes": ["RU"]}})
    assert r.status_code == 422 and "base de localisation" in r.json()["detail"]


def test_observe_then_block(world):
    nid, h = world["nid"], world["h"]
    accesslog.ingest(nid, [L("46.1.1.1", f"/p{i}", 404) for i in range(20)] + [L("46.1.1.2", "/.env", 404) for _ in range(3)] + [L("46.1.1.3", "/.env", 404) for _ in range(3)]
                     + [L("46.1.1.4", "/.env", 404) for _ in range(3)] + [L("10.9.9.9", "/.env", 404) for _ in range(3)] + [L("46.1.1.5", "/ok", 200)])
    r = rule(h, name="Russie suspecte", conditions={"suspicious": True, "country": {"mode": "in", "codes": ["RU"]}}).json()
    pv = client.post("/api/obs/automod/preview", headers=h, json={"conditions": r["conditions"]}).json()
    assert sorted(x["ip"] for x in pv["rows"]) == ["46.1.1.1", "46.1.1.2"] and pv["count"] == 2                 # FR, inconnu, local et normal : épargnés
    assert client.post("/api/obs/automod/run", headers=h).json()["matched"] == 2
    log = client.get("/api/obs/automod", headers=h).json()["log"]
    assert {x["ip"] for x in log if x["action"] == "would_ban"} == {"46.1.1.1", "46.1.1.2"} and client.get("/api/obs/bans", headers=h).json()["bans"] == []   # observation : rien de banni
    assert client.post("/api/obs/automod/run", headers=h).json()["matched"] == 0                                    # pas de doublon dans la journée
    up = client.put(f"/api/obs/automod/{r['id']}", headers=h, json={"name": r["name"], "conditions": r["conditions"], "mode": "block", "hours": 2, "enabled": True})
    assert up.status_code == 200 and up.json()["mode"] == "block"
    assert client.post("/api/obs/automod/run", headers=h).json()["matched"] == 2
    bans = {b["ip"]: b for b in client.get("/api/obs/bans", headers=h).json()["bans"]}
    assert set(bans) == {"46.1.1.1", "46.1.1.2"} and bans["46.1.1.1"]["created_by"].startswith("automod:") and bans["46.1.1.1"]["reason"].startswith("automod : Russie")
    assert bans["46.1.1.1"]["expires_at"] - time.time() < 2 * 3600 + 60
    assert client.post("/api/obs/automod/run", headers=h).json()["matched"] == 0                                    # déjà bannies
    assert client.get("/api/obs/automod", headers=h).json()["rules"][0]["hits"] == 2
    for b in bans.values():
        client.delete(f"/api/obs/bans/{b['id']}", headers=h)
    client.delete(f"/api/obs/automod/{r['id']}", headers=h)


def test_trusted_addresses_are_spared_and_limit_suspends_the_rule(world, monkeypatch):
    nid, h = world["nid"], world["h"]
    client.post("/api/obs/trusted", headers=h, json={"ip": "46.1.1.2", "note": "moi"})
    r = rule(h, name="Tous les RU", mode="block", conditions={"country": {"mode": "in", "codes": ["RU"]}}).json()
    assert client.post("/api/obs/automod/run", headers=h).json()["matched"] == 1                                    # 46.1.1.1 seulement : la 2e est validée
    assert "46.1.1.2" not in {b["ip"] for b in client.get("/api/obs/bans", headers=h).json()["bans"]}
    for b in client.get("/api/obs/bans", headers=h).json()["bans"]:
        client.delete(f"/api/obs/bans/{b['id']}", headers=h)
    monkeypatch.setattr(automod, "MAX_PER_RUN", 0)                                                                  # garde-fou : trop d'adresses d'un coup
    client.post("/api/obs/automod/run", headers=h)
    got = client.get("/api/obs/automod", headers=h).json()
    assert got["rules"][0]["enabled"] is False and any(x["action"] == "limit" for x in got["log"]) and client.get("/api/obs/bans", headers=h).json()["bans"] == []
    client.delete(f"/api/obs/automod/{r['id']}", headers=h)


def test_rights(world):
    ed = world["ed"]
    assert client.get("/api/obs/automod", headers=ed).status_code == 403                                            # l'éditeur par défaut ne voit pas l'activité des adresses
    assert client.post("/api/obs/automod", headers=ed, json={"name": "x", "conditions": {"suspicious": True}}).status_code == 403
    assert client.post("/api/obs/automod/run", headers=ed).status_code == 403
    assert "security.automod" in __import__("app.caps", fromlist=["CATALOG"]).CATALOG


def test_an_address_carrying_most_of_the_traffic_is_never_banned(world, monkeypatch):
    """Derrière un proxy ou un CDN, une seule adresse porte presque tout le trafic : l'automod l'écarte au lieu de couper tous les visiteurs."""
    nid, h = world["nid"], world["h"]
    for t in ("ip_hits", "ip_seen", "ip_paths", "ip_attacks", "automod_log", "automod_rules", "ip_bans"):
        db.run(f"DELETE FROM {t} WHERE node_id=?", (nid,))
    accesslog.ingest(nid, [L("46.7.7.7", f"/p{i}", 404) for i in range(300)] + [L("46.8.8.8", "/.env", 404) for _ in range(3)] + [L("46.9.9.9", "/", 200) for _ in range(5)])
    COUNTRY.update({"46.7.7.7": "RU", "46.8.8.8": "RU", "46.9.9.9": "FR"})
    r = rule(h, name="RU suspectes", mode="block", conditions={"suspicious": True, "country": {"mode": "in", "codes": ["RU"]}}).json()
    assert client.post("/api/obs/automod/run", headers=h).json()["matched"] == 1                                    # 46.8.8.8 seulement
    banned = {b["ip"] for b in client.get("/api/obs/bans", headers=h).json()["bans"]}
    assert banned == {"46.8.8.8"} and "46.7.7.7" not in banned
    skip = [x for x in client.get("/api/obs/automod", headers=h).json()["log"] if x["action"] == "skip"]
    assert skip and skip[0]["ip"] == "46.7.7.7" and "proxy" in skip[0]["detail"]
    for b in client.get("/api/obs/bans", headers=h).json()["bans"]:
        client.delete(f"/api/obs/bans/{b['id']}", headers=h)
    client.delete(f"/api/obs/automod/{r['id']}", headers=h)
