"""Activer / désactiver : réversibilité à l'octet près, dépendances, fichiers, pauses, droits."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.traefik_store import Store, StoreError

client = TestClient(app)

FILES = {
    "apps/mealie.yml": """# mealie
http:
  routers:
    mealie:
      rule: "Host(`mealie.example.com`)"
      entryPoints: ["websecure"]   # flux public
      service: mealie-svc
      tls:
        certResolver: le
        options: modern
      middlewares:
        - chain-public
        - headers-sec

  services:
    mealie-svc:
      loadBalancer:
        servers:
          - url: "http://mealie:9000"
""",
    "apps/two.yml": """http:
  routers:
    a:
      rule: Host(`a.example.com`)
      service: a-svc
    b:
      rule: Host(`b.example.com`)
      service: a-svc
      middlewares: [headers-sec, "errors-pages"]
  services:
    a-svc:
      loadBalancer:
        servers:
          - url: http://a:80
""",
    "middlewares/chain-public.yml": """http:
  middlewares:
    chain-public:
      chain:
        middlewares:
          - errors-pages
          - headers-sec
""",
    "middlewares/errors-pages.yml": "http:\n  middlewares:\n    errors-pages:\n      errors:\n        status: [\"500-599\"]\n        service: mealie-svc\n        query: /{status}.html\n",
    "middlewares/headers-sec.yml": "# sécurité\nhttp:\n  middlewares:\n    headers-sec:\n      headers:\n        frameDeny: true\n",
    "tls.yml": "tls:\n  options:\n    modern:\n      minVersion: VersionTLS13\n",
}


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    for path, content in FILES.items():
        s.write(path, content, create=True)
    return s


def snapshot(s):
    return {f["path"]: (s.root / f["path"]).read_text() for f in s.list_files()}


def spec(s, id_):
    return next(i for i in s.items()[0] if i["id"] == id_)


def live(s, id_):
    """Ce que Traefik verrait : seulement les documents actifs."""
    docs, _ = s.load_docs()
    return {i["id"] for i in s.items()[0] if not i["disabled"]} if docs else set()


def test_router_off_and_on_is_byte_identical(store):
    before = snapshot(store)
    r = store.toggle(["http/routers/a"], [], False)
    assert [b["id"] for b in r["blocks"]] == ["http/routers/a"]
    assert spec(store, "http/routers/a")["disabled"]
    assert "http/routers/a" not in {i["id"] for i in store.items()[0] if not i["disabled"]}
    docs, _ = store.load_docs()
    assert "a" not in (docs["apps/two.yml"]["http"]["routers"] or {})            # invisible pour Traefik
    assert "    #~ a:" in store.read("apps/two.yml")
    store.toggle(["http/routers/a"], [], True)
    assert snapshot(store) == before


def test_middleware_off_removes_every_reference_and_restores(store):
    before = snapshot(store)
    r = store.toggle(["http/middlewares/headers-sec"], [], False)
    assert {x["id"] for x in r["refs"]} == {"http/routers/mealie", "http/routers/b", "http/middlewares/chain-public"}
    assert spec(store, "http/routers/mealie")["spec"]["middlewares"] == ["chain-public"]
    assert spec(store, "http/routers/b")["spec"]["middlewares"] == ["errors-pages"]                  # liste « en ligne » respectée
    assert spec(store, "http/middlewares/chain-public")["spec"]["chain"]["middlewares"] == ["errors-pages"]
    assert "#% ref middlewares" in store.read("apps/mealie.yml")
    store.toggle(["http/middlewares/headers-sec"], [], True)
    assert spec(store, "http/routers/mealie")["spec"]["middlewares"] == ["chain-public", "headers-sec"]
    assert spec(store, "http/routers/b")["spec"]["middlewares"] == ["headers-sec", "errors-pages"]
    assert "#%" not in "".join(snapshot(store).values())                                            # plus aucune note
    assert snapshot(store)["apps/mealie.yml"] == before["apps/mealie.yml"]


def test_two_middlewares_off_then_on_one_by_one(store):
    store.toggle(["http/middlewares/headers-sec"], [], False)
    store.toggle(["http/middlewares/errors-pages"], [], False)
    assert "middlewares" not in spec(store, "http/routers/b")["spec"]                                # liste vide : clé retirée
    store.toggle(["http/middlewares/headers-sec"], [], True)
    assert spec(store, "http/routers/b")["spec"]["middlewares"] == ["headers-sec"]                   # l'autre reste coupé
    store.toggle(["http/middlewares/errors-pages"], [], True)
    assert spec(store, "http/routers/b")["spec"]["middlewares"] == ["headers-sec", "errors-pages"]
    assert "#%" not in "".join(snapshot(store).values())


def test_service_off_takes_its_routers_and_wakes_them_back(store):
    before = snapshot(store)
    r = store.toggle(["http/services/a-svc"], [], False)
    assert {b["id"] for b in r["cascade"]} == {"http/routers/a", "http/routers/b"}
    assert all(spec(store, i)["disabled"] for i in ("http/services/a-svc", "http/routers/a", "http/routers/b"))
    # l'erreur-page « errors-pages » pointe sur mealie-svc, pas touchée
    assert not spec(store, "http/services/mealie-svc")["disabled"]
    r = store.toggle(["http/routers/a"], [], True)                                                   # un routeur redemande son service
    assert [b["id"] for b in r["cascade"]] == ["http/services/a-svc"]
    store.toggle(["http/routers/b"], [], True)
    assert snapshot(store) == before


def test_chain_of_services_and_errors_middleware(store):
    store.toggle(["http/services/mealie-svc"], [], False)
    off = {i["id"] for i in store.items()[0] if i["disabled"]}
    assert {"http/routers/mealie", "http/middlewares/errors-pages"} <= off                           # le middleware errors utilise ce service
    assert "http/middlewares/chain-public" not in off                                                # la chaîne perd juste une entrée
    assert spec(store, "http/middlewares/chain-public")["spec"]["chain"]["middlewares"] == ["headers-sec"]


def test_tls_option_off(store):
    before = snapshot(store)
    store.toggle(["tls/options/modern"], [], False)
    assert "options" not in (spec(store, "http/routers/mealie")["spec"].get("tls") or {})
    assert spec(store, "http/routers/mealie")["spec"]["tls"]["certResolver"] == "le"
    store.toggle(["tls/options/modern"], [], True)
    assert snapshot(store)["apps/mealie.yml"] == before["apps/mealie.yml"]


def test_whole_file_off_and_on(store):
    before = snapshot(store)
    r = store.toggle([], ["middlewares/headers-sec.yml"], False)
    assert r["files"] == [{"path": "middlewares/headers-sec.yml", "to": "middlewares/headers-sec.yml.off"}]
    assert (store.root / "middlewares/headers-sec.yml.off").exists() and not (store.root / "middlewares/headers-sec.yml").exists()
    assert spec(store, "http/middlewares/headers-sec")["disabled"]
    assert spec(store, "http/routers/mealie")["spec"]["middlewares"] == ["chain-public"]
    docs, _ = store.load_docs()
    assert "middlewares/headers-sec.yml.off" not in docs
    store.toggle([], ["middlewares/headers-sec.yml.off"], True)
    now = snapshot(store)
    assert {k: v for k, v in now.items() if k != "apps/two.yml"} == {k: v for k, v in before.items() if k != "apps/two.yml"}
    assert spec(store, "http/routers/b")["spec"]["middlewares"] == ["headers-sec", "errors-pages"]   # liste en ligne : même contenu, réécrite en JSON


def test_enabling_a_file_needs_its_services(store):
    store.toggle([], ["apps/two.yml"], False)
    store.toggle(["http/services/mealie-svc"], [], False)
    with pytest.raises(StoreError):
        store.toggle(["http/routers/a"], [], True)                                                   # fichier encore désactivé


def test_pause_tag_and_selective_resume(store):
    store.toggle(["http/routers/a"], [], False)                                                      # pause manuelle
    store.toggle(["http/routers/b"], [], False, tag="domain:example.com")
    assert spec(store, "http/routers/b")["pausedBy"] == "domain:example.com" and spec(store, "http/routers/a")["pausedBy"] is None
    store.toggle(["http/routers/b"], [], True)
    assert not spec(store, "http/routers/b")["disabled"] and spec(store, "http/routers/a")["disabled"]
    assert "pause" not in store.read("apps/two.yml")


def test_other_edits_keep_disabled_blocks(store):
    store.toggle(["http/routers/a"], [], False)
    s = spec(store, "http/routers/b")["spec"]
    store.upsert("http", "routers", "b", {**s, "priority": 5})
    assert spec(store, "http/routers/a")["disabled"] and spec(store, "http/routers/b")["spec"]["priority"] == 5
    store.toggle(["http/routers/a"], [], True)
    assert not spec(store, "http/routers/a")["disabled"]
    with pytest.raises(StoreError) as e:                                                             # un nom désactivé reste réservé
        store.toggle(["http/routers/a"], [], False)
        store.upsert("http", "routers", "a", {"rule": "Host(`x.example.com`)", "service": "a-svc"}, file="apps/new.yml")
    assert e.value.status == 409


def test_delete_a_disabled_item_forgets_it(store):
    store.toggle(["http/middlewares/headers-sec"], [], False)
    store.delete_item("http", "middlewares", "headers-sec")
    assert not any(i["id"] == "http/middlewares/headers-sec" for i in store.items()[0])
    store.toggle(["http/routers/a"], [], False)
    store.toggle(["http/routers/a"], [], True)                                                       # une autre bascule ne ressuscite pas la référence
    assert spec(store, "http/routers/mealie")["spec"]["middlewares"] == ["chain-public"]
    assert "#%" not in store.read("apps/mealie.yml")


def test_unsupported_formats_are_refused_not_corrupted(store):
    store.write("flow.yml", "http: {routers: {x: {rule: 'Host(`x.example.com`)', service: a-svc}}}\n", create=True)
    before = snapshot(store)
    with pytest.raises(StoreError):
        store.toggle(["http/routers/x"], [], False)
    assert snapshot(store) == before


def test_dry_run_changes_nothing(store):
    before = snapshot(store)
    r = store.toggle(["http/services/a-svc"], [], False, dry=True)
    assert not r["applied"] and len(r["cascade"]) == 2
    assert snapshot(store) == before


def test_lint_ignores_disabled_and_flags_dangling_refs(store):
    from app.traefik_lint import lint, used_by
    store.toggle(["http/routers/a"], [], False)
    items, errors = store.items()
    assert "http/routers/a" not in used_by(items).get("http/services/a-svc", [])
    assert not [i for i in lint(items, errors, [], []) if i["level"] == "error"]


# ------------------------------------------------------------------ API
def _root():
    r = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_api_site_and_file_toggle():
    h = _root()
    client.put("/api/traefik/file", headers=h, json={"path": "tg.yml", "create": True, "content": "http:\n  routers:\n    tg:\n      rule: Host(`tg.example.com`)\n      service: noop@internal\n"})
    p = client.post("/api/traefik/toggle", headers=h, json={"ids": ["http/routers/tg"], "enable": False, "dry": True}).json()
    assert [b["id"] for b in p["blocks"]] == ["http/routers/tg"] and not p["applied"]
    assert client.post("/api/traefik/toggle", headers=h, json={"ids": ["http/routers/tg"], "enable": False}).status_code == 200
    ov = client.get("/api/traefik/overview", headers=h).json()
    assert next(i for i in ov["items"] if i["id"] == "http/routers/tg")["disabled"] is True
    assert client.post("/api/traefik/toggle", headers=h, json={"ids": [], "files": ["tg.yml"], "enable": False}).status_code == 200
    ov = client.get("/api/traefik/overview", headers=h).json()
    assert next(f for f in ov["files"] if f["path"] == "tg.yml.off")["disabled"] is True
    assert client.post("/api/traefik/toggle", headers=h, json={"ids": [], "files": ["tg.yml.off"], "enable": True}).status_code == 200
    assert client.post("/api/traefik/toggle", headers=h, json={"ids": ["http/routers/nope"], "enable": False}).status_code == 404
    assert client.post("/api/traefik/toggle", headers=h, json={"enable": False}).status_code == 422
    client.post("/api/traefik/toggle", headers=h, json={"ids": ["http/routers/tg"], "enable": True})
    client.delete("/api/traefik/file", headers=h, params={"path": "tg.yml"})


def _login(name, pw="un-mot-de-passe-solide"):
    r = client.post("/api/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_editor_rights_and_domain_pause():
    from app import db
    root = _root()
    zone = db.one("SELECT d.name FROM domains d JOIN credentials c ON c.id=d.credential_id WHERE c.owner_id IS NULL LIMIT 1")["name"]
    local = db.one("SELECT id FROM nodes WHERE kind='local'")["id"]
    for name in ("eve", "mallory"):
        assert client.post("/api/users", headers=root, json={"username": name, "password": "un-mot-de-passe-solide"}).status_code == 200
        client.post(f"/api/nodes/{local}/members", headers=root, json={"username": name, "role": "editor"})
    eve_id = db.one("SELECT id FROM users WHERE username='eve'")["id"]
    client.put(f"/api/domains/{zone}/access", headers=root, json={"grants": [{"userId": eve_id, "level": "edit"}]})
    body = ("http:\n  routers:\n    eve-site:\n      rule: Host(`s1.%s`)\n      service: eve-svc\n    foreign:\n      rule: Host(`x.nowhere.invalid`)\n      service: eve-svc\n"
            "  services:\n    eve-svc:\n      loadBalancer:\n        servers:\n          - url: http://x:1\n") % zone
    client.put("/api/traefik/file", headers=root, json={"path": "eve.yml", "create": True, "content": body})
    eve, mallory = _login("eve"), _login("mallory")
    t = lambda h, **kw: client.post("/api/traefik/toggle", headers=h, json=kw)  # noqa: E731
    assert t(eve, ids=["http/routers/eve-site"], enable=False).status_code == 200                     # son propre site
    assert t(eve, ids=["http/routers/foreign"], enable=False).status_code == 403                       # pas le sien
    assert t(eve, ids=["http/services/eve-svc"], enable=False).status_code == 403                      # un service : réservé aux admins
    assert t(eve, files=["eve.yml"], enable=False).status_code == 403
    assert t(mallory, ids=["http/routers/eve-site"], enable=True).status_code == 403
    assert t(eve, ids=["http/routers/eve-site"], enable=True).status_code == 200
    # pause d'un domaine : seuls les sites dont tous les hôtes sont sur ce domaine
    pause = client.post("/api/traefik/toggle/domain", headers=root, json={"domain": zone, "paused": True}).json()
    assert [b["id"] for b in pause["blocks"]] == ["http/routers/eve-site"]
    state = next(d for d in client.get("/api/traefik/toggle/domains", headers=root).json() if d["domain"] == zone)
    assert state["paused"] == 1
    # une pause manuelle n'est pas levée par la reprise du domaine
    assert client.post("/api/traefik/toggle/domain", headers=eve, json={"domain": zone, "paused": False}).json()["blocks"][0]["id"] == "http/routers/eve-site"
    assert client.post("/api/traefik/toggle/domain", headers=mallory, json={"domain": zone, "paused": True}).status_code == 404
    client.delete("/api/traefik/file", headers=root, params={"path": "eve.yml"})


def test_worker_toggle_op(store):
    from app import worker
    r = worker.handle(store, "toggle", {"ids": ["http/routers/a"], "files": [], "enable": False})
    assert r["blocks"][0]["id"] == "http/routers/a"
    with pytest.raises(StoreError):
        worker.handle(store, "toggle", {"ids": "nope", "files": [], "enable": False})


def test_file_never_keeps_an_empty_section(store):
    """Un `http:` sans contenu ferait rejeter tout le dossier par Traefik : les sections vides sont mises en commentaire avec leurs blocs."""
    store.toggle(["http/middlewares/headers-sec"], [], False)
    text = store.read("middlewares/headers-sec.yml")
    assert "#~ http:" in text and "#~   middlewares:" in text.replace("  #~ middlewares:", "#~   middlewares:")
    assert store.parse_text(text) is not None and not any(k for k in store.parse_text(text))          # le document actif est vide
    store.toggle(["http/middlewares/headers-sec"], [], True)
    assert store.read("middlewares/headers-sec.yml") == FILES["middlewares/headers-sec.yml"]


def test_tls_stays_enabled_when_only_option_is_removed(store):
    store.write("tlsonly.yml", "http:\n  routers:\n    t:\n      rule: Host(`t.example.com`)\n      service: a-svc\n      tls:\n        options: modern\n", create=True)
    store.toggle(["tls/options/modern"], [], False)
    assert spec(store, "http/routers/t")["spec"]["tls"] == {}                                            # `tls: {}` : le TLS reste actif
    assert "tls: {}" in store.read("tlsonly.yml")
    store.toggle(["tls/options/modern"], [], True)
    assert store.read("tlsonly.yml").endswith("      tls:\n        options: modern\n")


def test_purging_the_last_block_leaves_a_valid_file(store):
    store.toggle(["http/middlewares/headers-sec"], [], False)
    store.delete_item("http", "middlewares", "headers-sec")
    assert store.parse_text(store.read("middlewares/headers-sec.yml")) is not None
    assert "http:" not in store.read("middlewares/headers-sec.yml").replace("# sécurité", "")


def test_group_with_no_active_member_steps_aside_and_returns(store):
    """Un `chain` vide fait rejeter tout le dossier par Traefik : il se met de côté et revient avec son premier membre."""
    before = snapshot(store)
    r = store.toggle(["http/middlewares/headers-sec"], [], False)
    assert not r["cascade"]                                                                          # errors-pages tient encore la chaîne
    r = store.toggle(["http/middlewares/errors-pages"], [], False)
    assert [b["id"] for b in r["cascade"]] == ["http/middlewares/chain-public"]
    assert spec(store, "http/middlewares/chain-public")["pausedBy"] == "auto:chain"
    assert "middlewares" not in spec(store, "http/routers/mealie")["spec"]                           # plus aucune protection, mais un routeur valide
    with pytest.raises(StoreError):                                                                  # le groupe seul n'a rien à regrouper
        store.toggle(["http/middlewares/chain-public"], [], True)
    store.toggle(["http/middlewares/headers-sec"], [], True)                                         # un membre revient : le groupe aussi
    assert not spec(store, "http/middlewares/chain-public")["disabled"]
    assert spec(store, "http/middlewares/chain-public")["spec"]["chain"]["middlewares"] == ["headers-sec"]
    assert spec(store, "http/routers/mealie")["spec"]["middlewares"] == ["chain-public", "headers-sec"]
    store.toggle(["http/middlewares/errors-pages"], [], True)
    assert snapshot(store)["apps/mealie.yml"] == before["apps/mealie.yml"]
    assert snapshot(store)["middlewares/chain-public.yml"] == before["middlewares/chain-public.yml"]


def _state(store):
    return {i["id"]: bool(i["disabled"]) for i in store.items()[0]}


def test_reactiver_un_service_remet_les_routeurs_tombes_avec_lui(store):
    """Désactiver « a-svc » coupe les routeurs a et b ; le réactiver doit les rétablir (et pas ceux coupés à la main)."""
    store.toggle(["http/routers/mealie"], [], False)                      # coupé à la main : ne doit PAS revenir
    before = _state(store)
    r = store.toggle(["http/services/a-svc"], [], False)
    assert {b["name"] for b in r["cascade"]} == {"a", "b"}
    assert _state(store)["http/routers/a"] and _state(store)["http/routers/b"]
    r = store.toggle(["http/services/a-svc"], [], True)
    assert {b["name"] for b in r["cascade"]} == {"a", "b"}                # ils reviennent avec lui
    assert r["dependents"] == []
    assert _state(store) == before                                        # mealie reste coupé, tout le reste est comme avant
    assert not store.items()[1]


def test_composite_et_routeur_reviennent_en_cascade(tmp_path):
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    s.write("f.yml", "http:\n  routers:\n    r:\n      rule: Host(`r.example.com`)\n      service: wrr\n  services:\n    wrr:\n      weighted:\n        services:\n          - name: base\n            weight: 1\n    base:\n      loadBalancer:\n        servers:\n          - url: http://x:80\n", create=True)
    before = _state(s)
    s.toggle(["http/services/base"], [], False)
    assert all(_state(s).values())                                        # base, puis wrr, puis r : tout est tombé
    s.toggle(["http/services/base"], [], True)
    assert _state(s) == before


def test_elements_coupes_sans_etiquette_sont_proposes_a_la_reactivation(tmp_path):
    """Cas d'avant ce correctif (ou coupés à la main) : pas d'étiquette, donc on le propose au lieu de le deviner."""
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    s.write("f.yml", "http:\n  routers:\n    r1:\n      rule: Host(`a.example.com`)\n      service: svc\n    r2:\n      rule: Host(`b.example.com`)\n      service: svc\n  services:\n    svc:\n      loadBalancer:\n        servers:\n          - url: http://x:80\n", create=True)
    s.toggle(["http/routers/r1", "http/routers/r2"], [], False)           # coupés à la main (sans étiquette)
    s.toggle(["http/services/svc"], [], False)
    r = s.toggle(["http/services/svc"], [], True)
    assert {d["name"] for d in r["dependents"]} == {"r1", "r2"}           # proposés, pas réactivés d'office
    assert _state(s)["http/routers/r1"] and _state(s)["http/routers/r2"]
    s.toggle([d["id"] for d in r["dependents"]], [], True)                # l'utilisateur accepte
    assert not any(_state(s).values())
