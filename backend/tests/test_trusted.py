"""Adresses validées : plus marquées, plus d'alerte, non bannissables ; réseau local validé d'office ; droits et masquage."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import accesslog, alerts, db
from app.config import settings
from app.main import app

client = TestClient(app)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    log = tmp_path_factory.mktemp("trust") / "access.log"
    log.write_text("")
    old = settings.access_log
    settings.access_log = str(log)
    tok = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
    nid = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    yield {"h": {"Authorization": "Bearer " + tok, "X-Node": nid}, "nid": nid}
    settings.access_log = old
    for t in ("ip_hits", "ip_seen", "ip_paths", "ip_attacks", "ip_trusted", "ip_bans", "alert_state", "alert_history", "alert_rules"):
        db.run(f"DELETE FROM {t} WHERE node_id=?", (nid,))
    accesslog._recent.clear()
    accesslog._recent_exploit.clear()


def L(ip, path, status):
    return json.dumps({"ClientHost": ip, "RequestHost": "app.exemple.fr", "RequestPath": path, "DownstreamStatus": status, "RequestMethod": "GET",
                       "StartUTC": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "request_User-Agent": "Mozilla/5.0"})


def test_trusted_address_is_not_marked_and_cannot_be_banned(world):
    nid, h = world["nid"], world["h"]
    accesslog.ingest(nid, [L("45.9.9.1", "/.env", 404) for _ in range(3)] + [L("45.9.9.2", "/.env", 404) for _ in range(3)] + [L("45.9.8.7", "/.git/config", 404)])
    rows = {r["ip"]: r for r in client.get("/api/obs/ips?only=attacks", headers=h).json()["rows"]}
    assert {"45.9.9.1", "45.9.9.2", "45.9.8.7"} <= set(rows)
    ref = rows["45.9.9.1"]["ref"]
    assert client.post("/api/obs/trusted", headers=h, json={"ref": ref, "note": "mon bureau"}).status_code == 200
    assert client.post("/api/obs/trusted", headers=h, json={"ref": ref}).status_code == 409                       # déjà validée
    assert client.post("/api/obs/trusted", headers=h, json={"ip": "45.9.8.0/24", "note": "supervision"}).status_code == 200   # un réseau
    after = {r["ip"]: r for r in client.get("/api/obs/ips?only=attacks", headers=h).json()["rows"]}
    assert "45.9.9.1" not in after and "45.9.8.7" not in after and "45.9.9.2" in after                             # plus marquées ; les autres restent
    allrows = {r["ip"]: r for r in client.get("/api/obs/ips", headers=h).json()["rows"]}
    assert allrows["45.9.9.1"]["trusted"] is True and allrows["45.9.9.1"]["attacks"] == [] and allrows["45.9.9.1"]["suspicious"] is False
    assert allrows["45.9.8.7"]["trusted"] is True                                                                   # couverte par le réseau validé
    assert {r["ip"] for r in client.get("/api/obs/ips?only=trusted", headers=h).json()["rows"]} == {"45.9.9.1", "45.9.8.7"}
    assert client.get("/api/obs/ips/" + ref, headers=h).json()["attacks"] == []
    assert client.get("/api/obs/ips", headers=h).json()["total"]["trusted"] == 2
    b = client.post("/api/obs/bans", headers=h, json={"ref": ref, "hours": 1})                                      # une adresse validée ne se bannit pas
    assert b.status_code == 409 and "validée" in b.json()["detail"]
    assert client.post("/api/obs/bans", headers=h, json={"ip": "45.9.8.0/24", "hours": 1}).status_code == 409        # ni un réseau qui la contient
    lst = client.get("/api/obs/trusted", headers=h).json()
    assert {t["ip"] for t in lst["trusted"]} == {"45.9.9.1", "45.9.8.0/24"} and lst["canManage"]
    tid = next(t["id"] for t in lst["trusted"] if t["ip"] == "45.9.9.1")
    assert client.delete(f"/api/obs/trusted/{tid}", headers=h).status_code == 200
    assert client.delete(f"/api/obs/trusted/{tid}", headers=h).status_code == 404
    assert "45.9.9.1" in {r["ip"] for r in client.get("/api/obs/ips?only=attacks", headers=h).json()["rows"]}      # validation retirée : de nouveau marquée


def test_rules_of_the_list(world):
    h = world["h"]
    assert client.post("/api/obs/trusted", headers=h, json={"ip": "192.168.1.5"}).status_code == 409                # réseau local : déjà validé d'office
    assert client.post("/api/obs/trusted", headers=h, json={"ip": "0.0.0.0/0"}).status_code == 422                  # tout Internet : refusé
    assert client.post("/api/obs/trusted", headers=h, json={"ip": "10.0.0.0/8"}).status_code in (409, 422)
    assert client.post("/api/obs/trusted", headers=h, json={"ip": "pas une adresse"}).status_code == 422
    assert client.post("/api/obs/trusted", headers=h, json={}).status_code == 422


def test_trusted_address_raises_no_alert(world):
    nid = world["nid"]
    ip = "45.7.7.7"
    accesslog.ingest(nid, [L(ip, f"/p{i}", 404) for i in range(80)])
    assert accesslog.exempt(nid, ip) is False
    client.post("/api/obs/trusted", headers=world["h"], json={"ip": ip})
    assert accesslog.exempt(nid, ip) is True and accesslog.exempt(nid, "10.1.2.3") is True


def test_traffic_stats(world):
    nid, h = world["nid"], world["h"]
    accesslog.ingest(nid, [L("45.5.5.1", "/a", 200) for _ in range(4)] + [L("45.5.5.2", "/.env", 404) for _ in range(2)] + [L("10.1.1.1", "/", 200)])
    d = client.get("/api/obs/stats?days=1", headers=h).json()
    assert d["hits"] >= 7 and sum(c["value"] for c in d["classes"]) == d["hits"]
    assert len(d["timeline"]) in (24, 25) and sum(t["hits"] for t in d["timeline"]) == d["hits"]
    assert d["hosts"][0]["name"] == "app.exemple.fr" and any(p["path"] == "/.env" for p in d["paths"])
    assert {t["ip"] for t in d["topIps"]} >= {"45.5.5.1"} and "10.1.1.1" not in {t["ip"] for t in d["topIps"]}      # le réseau local n'est pas dans les « publiques »
    assert client.get("/api/obs/stats?days=99", headers=h).json()["days"] == accesslog.RETENTION_DAYS


def test_internal_entrypoint_traffic_is_not_visitor_traffic(world):
    """Les appels de Prometheus ou de cette interface vers l'entrypoint interne de Traefik (:8080) ne sont pas des visiteurs."""
    nid = world["nid"]
    internal = json.dumps({"ClientHost": "172.21.0.7", "RequestHost": "traefik", "RequestPath": "/api/version", "DownstreamStatus": 404, "RequestMethod": "GET",
                           "entryPointName": "traefik", "StartUTC": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "request_User-Agent": "Python-urllib/3.12"})
    public = json.dumps({"ClientHost": "172.21.0.7", "RequestHost": "app.exemple.fr", "RequestPath": "/", "DownstreamStatus": 200, "RequestMethod": "GET",
                         "entryPointName": "websecure", "StartUTC": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    assert accesslog.ingest(nid, [internal] * 50) == 0
    assert accesslog.ingest(nid, [public]) == 1                                                                      # le même client sur un vrai point d'entrée compte
    ips = {r["ip"]: r for r in client.get("/api/obs/ips", headers=world["h"]).json()["rows"]}
    assert ips["172.21.0.7"]["hits"] == 1 and ips["172.21.0.7"]["private"] is True
