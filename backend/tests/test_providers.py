"""Fournisseurs DNS : OVH (inchangé), DNS local (sans compte) et DuckDNS (une adresse)."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.dns_providers import capabilities, clean_duck_name, clean_hostname
from app.main import app

client = TestClient(app)
PW = "un-mot-de-passe-solide"


def login(name, pw=PW):
    r = client.post("/api/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture(scope="module")
def world():
    root = login("root", "admin-password-1")
    for name, can in (("pat", True), ("quinn", False), ("rae", False)):
        assert client.post("/api/users", headers=root, json={"username": name, "password": PW, "canAddDomains": can}).status_code == 200
    local = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    # quinn administre le nœud local sans pouvoir ajouter de comptes ; rae n'est qu'éditeur
    client.post(f"/api/nodes/{local}/members", headers=root, json={"username": "quinn", "role": "admin"})
    client.post(f"/api/nodes/{local}/members", headers=root, json={"username": "rae", "role": "editor"})
    client.post(f"/api/nodes/{local}/members", headers=root, json={"username": "pat", "role": "editor"})   # « pat » a le droit global, et un nœud où déposer ses domaines
    return {"root": root, "pat": login("pat"), "quinn": login("quinn"), "rae": login("rae"), "local": local}


def test_name_validation():
    assert clean_hostname("Maison.Exemple.fr.") == "maison.exemple.fr" and clean_hostname("home.lan") == "home.lan"
    for bad in ("lan", "a b.fr", "-x.fr", "x..fr", "", "http://x.fr", "x.fr/y"):
        assert clean_hostname(bad) is None
    assert clean_duck_name("monsite") == "monsite.duckdns.org" and clean_duck_name("MonSite.DuckDNS.org") == "monsite.duckdns.org"
    for bad in ("a.b.duckdns.org", "-x", "x y", "", "x.evil.com"):
        assert clean_duck_name(bad) is None
    assert capabilities("ovh")["records"] and not capabilities("local")["records"] and capabilities("duckdns")["singleIp"]


def test_existing_accounts_stay_ovh(world):
    creds = client.get("/api/credentials", headers=world["root"]).json()
    assert all(c["provider"] == "ovh" for c in creds if c["system"])
    assert [p["id"] for p in client.get("/api/dns-providers", headers=world["pat"]).json()] == ["ovh", "duckdns", "adguard", "pihole", "local"]


def test_local_domain_needs_no_account_and_publishes_nothing(world):
    pat = world["pat"]
    r = client.post("/api/domains/local", headers=pat, json={"names": ["maison.exemple.fr", "home.lan", "pas un nom", "home.lan"]})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == ["maison.exemple.fr", "home.lan"] and len(r.json()["skipped"]) == 2
    doms = {d["name"]: d for d in client.get("/api/domains", headers=pat).json()}
    assert doms["home.lan"]["provider"] == "local" and doms["home.lan"]["level"] == "manage" and not doms["home.lan"]["capabilities"]["records"]
    creds = client.get("/api/credentials", headers=pat).json()
    assert [c["provider"] for c in creds] == ["local"] and creds[0]["domains"] == ["home.lan", "maison.exemple.fr"]
    # pas de clé, rien chez un fournisseur : toutes les opérations d'enregistrements sont refusées proprement
    assert client.get("/api/dns/zones/home.lan/records", headers=pat).status_code == 400
    assert client.post("/api/dns/zones/home.lan/records", headers=pat, json={"fieldType": "A", "subDomain": "x", "target": "1.2.3.4"}).status_code == 400
    assert client.get("/api/dns/zones/home.lan/export", headers=pat).status_code == 400
    info = client.get("/api/dns/zones/home.lan/provider", headers=pat).json()
    assert info["provider"] == "local" and not info["capabilities"]["records"]
    zone = next(z for z in client.get("/api/dns/zones", headers=pat).json() if z["name"] == "home.lan")
    assert zone["provider"] == "local"
    # un nom déjà pris ne peut pas l'être deux fois
    assert client.post("/api/domains/local", headers=world["root"], json={"names": ["home.lan"]}).status_code == 422


def test_who_may_add_a_local_domain(world):
    assert client.post("/api/domains/local", headers=world["rae"], json={"names": ["x.lan"]}).status_code == 403   # simple éditeur
    r = client.post("/api/domains/local", headers=world["quinn"], json={"names": ["quinn.lan"]})                    # administrateur du nœud
    assert r.status_code == 200 and r.json()["added"] == ["quinn.lan"]
    assert client.post("/api/credentials/duckdns", headers=world["rae"], json={"label": "d", "token": "abcdefgh-1234", "domains": ["q1"], "ip": "1.1.1.1"}).status_code == 403


def test_local_domain_is_a_domain_for_sites_and_sync(world):
    """Les sites d'un domaine local n'ont rien à créer chez un fournisseur : jamais « DNS manquant »."""
    root, pat = world["root"], world["pat"]
    rows = client.post("/api/sync/check", headers=pat, json={"hosts": ["app.home.lan", "photos.maison.exemple.fr", "autre.example.org"]}).json()
    by = {r["host"]: r for r in rows}
    assert by["app.home.lan"]["status"] == "local" and by["photos.maison.exemple.fr"]["status"] == "local"
    assert by["autre.example.org"]["status"] == "external"
    assert by["app.home.lan"]["canEdit"] is True


def test_share_and_import_a_local_domain_on_another_node(world):
    pat, root = world["pat"], world["root"]
    uid = db.one("SELECT id FROM users WHERE username='rae'")["id"]
    assert client.put("/api/domains/home.lan/access", headers=pat, json={"grants": [{"userId": uid, "level": "edit"}]}).status_code == 200
    doms = {d["name"]: d for d in client.get("/api/domains", headers=world["rae"]).json()}
    assert doms["home.lan"]["level"] == "edit" and doms["home.lan"]["provider"] == "local"
    assert client.delete("/api/domains/home.lan", headers=pat).status_code == 200


def test_duckdns_add_update_and_secrets(world):
    pat = world["pat"]
    bad = client.post("/api/credentials/duckdns", headers=pat, json={"label": "x", "token": "bad-token-1", "domains": ["nope"], "ip": "1.2.3.4"})
    assert bad.status_code in (403, 422)                                                                            # jeton refusé
    assert client.post("/api/credentials/duckdns", headers=pat, json={"label": "x", "token": "abcdefgh-1234", "domains": ["a.b.c"]}).status_code == 422
    assert client.post("/api/credentials/duckdns", headers=pat, json={"label": "x", "token": "abcdefgh-1234", "domains": ["monsite"]}).status_code == 422   # pas d'adresse
    r = client.post("/api/credentials/duckdns", headers=pat, json={"label": "Duck", "token": "abcdefgh-1234", "domains": ["monsite", "autre.duckdns.org"], "ip": "192.168.1.20"})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == ["monsite.duckdns.org", "autre.duckdns.org"] and r.json()["provider"] == "duckdns"
    assert "abcdefgh" not in r.text and "abcdefgh" not in client.get("/api/credentials", headers=pat).text            # le jeton ne revient jamais
    row = db.one("SELECT secret FROM credentials WHERE provider='duckdns'")
    assert row["secret"] and "abcdefgh" not in row["secret"]                                                         # chiffré en base
    info = client.get("/api/dns/zones/monsite.duckdns.org/provider", headers=pat).json()
    assert info["provider"] == "duckdns" and info["ip4"] == ["192.168.1.20"] and info["capabilities"]["singleIp"]
    up = client.put("/api/dns/zones/monsite.duckdns.org/provider/ip", headers=pat, json={"ip": "10.0.0.5", "txt": "acme-token"})
    assert up.status_code == 200, up.text
    info = client.get("/api/dns/zones/monsite.duckdns.org/provider", headers=pat).json()
    assert info["ip4"] == ["10.0.0.5"] and info["txt"] == "acme-token"
    assert client.put("/api/dns/zones/monsite.duckdns.org/provider/ip", headers=pat, json={"ip": "pas-une-ip"}).status_code == 422
    assert client.put("/api/dns/zones/monsite.duckdns.org/provider/ip", headers=pat, json={}).status_code == 422
    assert client.put("/api/dns/zones/monsite.duckdns.org/provider/ip", headers=pat, json={"clearTxt": True}).status_code == 200
    assert client.get("/api/dns/zones/monsite.duckdns.org/records", headers=pat).status_code == 400                   # pas d'enregistrements libres
    st = client.post("/api/sync/check", headers=pat, json={"hosts": ["app.monsite.duckdns.org"]}).json()[0]
    assert st["status"] == "managed" and st["ip"] == "10.0.0.5"
    # un autre utilisateur ne peut ni voir ni modifier ce domaine
    assert client.put("/api/dns/zones/monsite.duckdns.org/provider/ip", headers=world["rae"], json={"ip": "1.1.1.1"}).status_code == 404
    # le même nom ne peut pas être ajouté deux fois
    assert client.post("/api/credentials/duckdns", headers=pat, json={"label": "d2", "token": "abcdefgh-1234", "domains": ["monsite"], "ip": "1.1.1.1"}).status_code == 409


def test_duckdns_token_can_be_replaced_and_account_deleted(world):
    pat = world["pat"]
    cid = next(c["id"] for c in client.get("/api/credentials", headers=pat).json() if c["provider"] == "duckdns")
    assert client.put(f"/api/credentials/{cid}", headers=pat, json={"token": "nouveau-jeton-9999"}).status_code == 200
    assert client.get(f"/api/credentials/{cid}/zones", headers=pat).status_code == 400                                # import : OVH seulement
    assert client.post(f"/api/credentials/{cid}/import", headers=pat, json={"zones": ["x"]}).status_code == 400
    assert client.delete(f"/api/credentials/{cid}", headers=pat).status_code == 200
    assert "monsite.duckdns.org" not in [d["name"] for d in client.get("/api/domains", headers=pat).json()]


def test_meta_tells_when_any_dns_is_usable(world):
    assert client.get("/api/meta", headers=world["pat"]).json()["dnsEnabled"] is True


def test_more_duckdns_names_reuse_the_stored_token(world):
    pat = world["pat"]
    r = client.post("/api/credentials/duckdns", headers=pat, json={"label": "Duck2", "token": "abcdefgh-5678", "domains": ["premier"], "ip": "10.1.1.1"})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    r = client.post(f"/api/credentials/{cid}/duckdns-names", headers=pat, json={"domains": ["second"], "ip": "10.1.1.2"})
    assert r.status_code == 200 and r.json()["added"] == ["second.duckdns.org"]
    assert client.post(f"/api/credentials/{cid}/duckdns-names", headers=world["rae"], json={"domains": ["x"], "ip": "1.1.1.1"}).status_code == 403
    assert client.post(f"/api/credentials/{cid}/duckdns-names", headers=world["quinn"], json={"domains": ["x"], "ip": "1.1.1.1"}).status_code == 404   # pas son compte
    client.delete(f"/api/credentials/{cid}", headers=pat)


def test_node_owner_adds_dns_for_their_own_node_without_global_right(world):
    """Propriétaire d'un nœud = instance à lui : il branche ses comptes DNS pour ce nœud, sans le droit global."""
    root = world["root"]
    r = client.post("/api/users", headers=root, json={"username": "sam", "password": PW, "maxNodes": 1})
    assert r.status_code == 200, r.text
    sam = login("sam")
    node = client.post("/api/nodes", headers=sam, json={"name": "chez-sam"}).json()
    hn = {**sam, "X-Node": node["id"]}
    cred = client.post("/api/credentials", headers=hn, json={"label": "Perso", "endpoint": "ovh-eu", "applicationKey": "samkey", "applicationSecret": "s", "consumerKey": "c"})
    assert cred.status_code == 200, cred.text
    zone = next(z["name"] for z in cred.json()["zones"] if z["available"])
    imp = client.post(f"/api/credentials/{cred.json()['id']}/import", headers=hn, json={"zones": [zone]})
    assert imp.status_code == 200 and imp.json()["added"] == [zone]
    assert zone in [d["name"] for d in client.get("/api/domains", headers=hn).json()]                 # visible sur SON nœud
    local = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    assert db.one("SELECT 1 FROM node_domains WHERE node_id=? AND domain_id=(SELECT id FROM domains WHERE name=?)", (node["id"], zone))
    assert not db.one("SELECT 1 FROM node_domains WHERE node_id=? AND domain_id=(SELECT id FROM domains WHERE name=?)", (local, zone))   # pas sur le nœud principal
    assert client.post("/api/domains/local", headers=hn, json={"names": ["sam.lan"]}).status_code == 200
    client.delete(f"/api/nodes/{node['id']}", headers=sam)


def test_a_dns_account_does_not_need_a_node(world):
    """Un compte DNS appartient à l'utilisateur. Sans nœud, ses domaines existent mais attendent d'être rattachés à un nœud."""
    root = world["root"]
    assert client.post("/api/users", headers=root, json={"username": "tim", "password": PW, "canAddDomains": True}).status_code == 200
    tim = login("tim")
    assert client.post("/api/domains/local", headers=tim, json={"names": ["tim.lan"]}).json()["added"] == ["tim.lan"]
    assert client.post("/api/credentials/duckdns", headers=tim, json={"label": "d", "token": "abcdefgh-1234", "domains": ["timduck"], "ip": "1.1.1.1"}).status_code == 200
    cred = client.post("/api/credentials", headers=tim, json={"label": "o", "endpoint": "ovh-eu", "applicationKey": "tk", "applicationSecret": "s", "consumerKey": "c"})
    assert cred.status_code == 200, cred.text
    zone = next(z["name"] for z in cred.json()["zones"] if z["available"])
    assert client.post(f"/api/credentials/{cred.json()['id']}/import", headers=tim, json={"zones": [zone]}).json()["added"] == [zone]
    creds = {c["provider"]: c for c in client.get("/api/credentials", headers=tim).json()}
    assert set(creds) == {"local", "duckdns", "ovh"} and creds["local"]["nodeless"] == ["tim.lan"] and creds["duckdns"]["nodeless"] == ["timduck.duckdns.org"]
    assert db.one("SELECT 1 FROM node_domains nd JOIN domains d ON d.id=nd.domain_id WHERE d.name='tim.lan'") is None      # sur aucun nœud
    # plus tard : un nœud à lui (quota accordé), et il y rattache ses domaines sans rien recréer
    uid = db.one("SELECT id FROM users WHERE username='tim'")["id"]
    client.put(f"/api/users/{uid}", headers=root, json={"maxNodes": 1})
    node = client.post("/api/nodes", headers=tim, json={"name": "chez-tim"}).json()
    assert sorted(d["name"] for d in client.get(f"/api/nodes/{node['id']}/importable", headers=tim).json()) == sorted(["tim.lan", "timduck.duckdns.org", zone])
    r = client.post(f"/api/nodes/{node['id']}/domains", headers=tim, json={"names": ["tim.lan", "timduck.duckdns.org"]})
    assert r.status_code == 200 and sorted(r.json()["added"]) == ["tim.lan", "timduck.duckdns.org"]
    assert "tim.lan" in [d["name"] for d in client.get("/api/domains", headers={**tim, "X-Node": node["id"]}).json()]
    client.delete(f"/api/nodes/{node['id']}", headers=tim)


def test_without_any_right_it_is_still_refused(world):
    rae = world["rae"]   # simple éditeur du nœud principal, sans droit global
    assert client.post("/api/domains/local", headers=rae, json={"names": ["rae.lan"]}).status_code == 403
    assert client.post("/api/credentials", headers=rae, json={"label": "o", "endpoint": "ovh-eu", "applicationKey": "rk", "applicationSecret": "s", "consumerKey": "c"}).status_code == 403
