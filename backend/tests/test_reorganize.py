"""Assistant de rangement : propositions, application, sécurité."""
import pytest
from fastapi.testclient import TestClient

from app import worker
from app.main import app
from app.reorganize import build_plan
from app.traefik_store import Store, StoreError

client = TestClient(app)


def router(name, svc, host=None):
    return f"    {name}:\n      rule: Host(`{host or name + '.example.com'}`)\n      service: {svc}\n"


def service(name):
    return f"    {name}:\n      loadBalancer:\n        servers:\n          - url: http://{name}:80\n"


FILES = {
    "blog.yml": "# mon blog\nhttp:\n  routers:\n" + router("blog", "blog-svc") + "  services:\n" + service("blog-svc"),
    "auth.yml": "http:\n  middlewares:\n    auth:\n      basicAuth:\n        users: [\"a:b\"]\n    gzip:\n      compress: {}\n",
    "mix.yml": "http:\n  routers:\n" + router("x", "x-svc") + "  services:\n" + service("x-svc") + "  middlewares:\n    mw-x:\n      compress: {}\n",
    "old-svc.yml": "http:\n  services:\n" + service("old-svc"),
    "apps/old.yml": "http:\n  routers:\n" + router("old", "old-svc"),
    "infra/dash.yml": "http:\n  routers:\n" + router("dash", "api@internal"),
    "infra/stray-mw.yml": "http:\n  middlewares:\n    stray:\n      compress: {}\n",
    "infra/dash2.yml": "http:\n  routers:\n" + router("dash2", "api@internal"),
}


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    for path, content in FILES.items():
        s.write(path, content, create=True)
    return s


def plan(store, scope="root"):
    items, errors = store.items()
    return build_plan(items, store.list_dirs(), errors, scope, layout="keep")


def moves(p):
    return {(s["kind"], s["from"], s["to"], tuple(s["ids"]) if s["kind"] == "blocks" else ()) for s in p["steps"]}


def test_plan_groups_by_type_and_never_touches_organised_apps(store):
    p = plan(store)
    m = moves(p)
    assert ("file", "blog.yml", "apps/blog.yml", ()) in m                      # application complète : le fichier part intact
    assert ("file", "auth.yml", "middlewares/auth.yml", ()) in m               # fichier de middlewares
    assert ("blocks", "mix.yml", "middlewares/mw-x.yml", ("http/middlewares/mw-x",)) in m   # middleware extrait d'un fichier mixte
    assert ("file", "mix.yml", "apps/mix.yml", ()) in m                         # le reste (routeur + service) rejoint apps/
    assert ("blocks", "old-svc.yml", "apps/old.yml", ("http/services/old-svc",)) in m   # service rejoint son routeur
    assert not any(s["from"].startswith(("infra/", "apps/")) for s in p["steps"])      # déjà rangé : intact (scope racine)


def test_scope_all_also_reclassifies_stray_middlewares(store):
    m = moves(plan(store, "all"))
    assert ("file", "infra/stray-mw.yml", "middlewares/stray-mw.yml", ()) in m
    assert not any(s[1] == "infra/dash.yml" for s in m)                         # routeurs rangés : jamais déplacés


def test_apply_keeps_content_and_is_idempotent(store):
    before = {i["id"]: i["spec"] for i in store.items()[0]}
    p = plan(store)
    done = store.reorganize([{k: s[k] for k in ("kind", "from", "to", "ids")} for s in p["steps"]], clean=False)
    assert done["files"] >= 2 and done["blocks"] >= 2
    items, errors = store.items()
    assert errors == {} and {i["id"]: i["spec"] for i in items} == before      # rien n'a changé dans les blocs
    assert store.read("apps/blog.yml").startswith("# mon blog")                 # commentaires conservés (clean=False)
    assert "old-svc" in store.read("apps/old.yml") and not (store.root / "old-svc.yml").exists()
    assert not [f for f in store.list_files() if "/" not in f["path"]]          # plus rien à la racine
    assert plan(store)["steps"] == []                                           # rien d'autre à proposer
    assert list((store.backup_root / "_reorg").iterdir())                       # copie de sécurité avant application


def test_conflicts_and_bad_plans_are_refused(store):
    with pytest.raises(StoreError):
        store.reorganize([{"kind": "file", "from": "blog.yml", "to": "apps/old.yml", "ids": []}])   # destination existante
    with pytest.raises(StoreError):
        store.reorganize([{"kind": "file", "from": "blog.yml", "to": "../evil.yml", "ids": []}])
    with pytest.raises(StoreError):
        store.reorganize([{"kind": "blocks", "from": "mix.yml", "to": "x.yml", "ids": ["http/middlewares/nope"]}])
    assert (store.root / "blog.yml").exists() and not (store.root / "x.yml").exists()
    with pytest.raises(StoreError):
        worker.handle(store, "reorganize", {"steps": [{"kind": "rm", "from": "a.yml", "to": "b.yml"}]})


def test_name_collision_gets_a_new_file_name(store):
    store.write("middlewares/auth.yml", "http:\n  middlewares:\n    other:\n      compress: {}\n", create=True)
    m = moves(plan(store))
    assert ("file", "auth.yml", "middlewares/auth-2.yml", ()) in m


def test_yaml_error_files_are_left_alone(store):
    (store.root / "broken.yml").write_text("http: [oops\n")
    assert not any(s["from"] == "broken.yml" for s in plan(store)["steps"])


def test_api_plan_and_apply():
    r = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"})
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    client.put("/api/traefik/file", headers=h, json={"path": "wild.yml", "create": True, "content": "http:\n  middlewares:\n    wild-mw:\n      compress: {}\n"})
    p = client.get("/api/traefik/reorganize", headers=h).json()
    step = next(s for s in p["steps"] if s["from"] == "wild.yml")
    assert step["to"] == "middlewares/wild.yml"
    ok = client.post("/api/traefik/reorganize", headers=h, json={"steps": [{"kind": step["kind"], "from": step["from"], "to": step["to"], "ids": step["ids"]}]})
    assert ok.status_code == 200 and ok.json()["files"] == 1
    assert client.post("/api/traefik/reorganize", headers=h, json={"steps": [{"kind": "file", "from": "../../etc/passwd", "to": "x.yml"}]}).status_code == 400
    assert client.post("/api/traefik/reorganize", headers=h, json={"steps": []}).status_code == 422
    client.delete("/api/traefik/file", headers=h, params={"path": "middlewares/wild.yml"})
    client.delete("/api/traefik/dir", headers=h, params={"path": "middlewares"})


# ---- gros fichier mélangé : découpage par site, par routeur/service, sous-dossiers ----
BIG = ("http:\n  routers:\n" + router("grafana", "grafana-svc", "grafana.example.com") + router("books", "books-svc", "books.example.org")
       + router("shared-a", "shared", "a.example.com") + router("shared-b", "shared", "b.example.com")
       + "  services:\n" + service("grafana-svc") + service("books-svc") + service("shared") + service("orphan")
       + "  middlewares:\n    auth-x:\n      basicAuth:\n        users: [\"a:b\"]\n    sec:\n      headers: {}\n")


@pytest.fixture()
def big(tmp_path):
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    s.write("gros.yml", BIG, create=True)
    return s


def bplan(store, layout, sub=False, scope="root"):
    items, errors = store.items()
    return build_plan(items, store.list_dirs(), errors, scope, layout, sub)


def sites(p):
    out = {}
    for s in p["steps"]:
        if s["kind"] == "blocks" and s["group"] == "sites":
            out.setdefault(s["to"], set()).update(i.split("/")[-1] for i in s["ids"])
    return out


def test_un_fichier_par_site_regroupe_routeurs_et_service(big):
    p = bplan(big, "sites")
    assert sites(p) == {"apps/grafana-svc.yml": {"grafana", "grafana-svc"}, "apps/books-svc.yml": {"books", "books-svc"},
                        "apps/shared.yml": {"shared", "shared-a", "shared-b"}, "apps/orphan.yml": {"orphan"}}


def test_sous_dossiers_par_domaine_et_type_de_middleware(big):
    p = bplan(big, "sites", sub=True)
    assert set(sites(p)) == {"apps/example.com/grafana-svc.yml", "apps/example.org/books-svc.yml", "apps/example.com/shared.yml", "apps/orphan.yml"}
    mw = {s["to"] for s in p["steps"] if s["group"] == "mw"}
    assert mw == {"middlewares/auth/auth-x.yml", "middlewares/headers/sec.yml"}


def test_un_fichier_par_routeur_et_par_service(big):
    p = bplan(big, "split")
    to = {s["to"] for s in p["steps"] if s["group"] in ("routers", "services")}
    assert "routers/grafana.yml" in to and "services/shared.yml" in to and len(to) == 8


@pytest.mark.parametrize("layout,sub", [("sites", False), ("sites", True), ("split", False)])
def test_le_decoupage_s_applique_sans_rien_perdre(big, layout, sub):
    before = {(i["proto"], i["kind"], i["name"]) for i in big.items()[0]}
    steps = [{"kind": s["kind"], "from": s["from"], "to": s["to"], "ids": s["ids"]} for s in bplan(big, layout, sub)["steps"]]
    big.reorganize(steps)
    after = {(i["proto"], i["kind"], i["name"]) for i in big.items()[0]}
    assert after == before
    assert not big.safe("gros.yml").exists()                       # vidé : le fichier d'origine disparaît
    assert not [f for f in big.list_files() if f["path"] == "gros.yml"]
    assert not big.items()[1]                                       # aucun fichier en erreur


def test_fichier_qui_ne_contient_qu_un_site_part_intact(tmp_path):
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    s.write("blog.yml", FILES["blog.yml"], create=True)
    p = bplan(s, "sites")
    assert [(x["kind"], x["to"]) for x in p["steps"]] == [("file", "apps/blog.yml")]


def test_bloc_desactive_suit_son_groupe_et_reste_desactive(tmp_path):
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    s.write("f.yml", "http:\n  routers:\n" + router("a", "a-svc") + "    #~ b:\n      #~ rule: Host(`b.example.com`)\n      #~ service: b-svc\n"
            + "  services:\n" + service("a-svc") + service("b-svc") + "  serversTransports:\n    t:\n      insecureSkipVerify: true\n", create=True)
    state = lambda: sorted((i["id"], bool(i["disabled"]), i["file"]) for i in s.items()[0])  # noqa: E731
    before = {(i, d) for i, d, _ in state()}
    p = bplan(s, "sites")
    s.reorganize([{"kind": x["kind"], "from": x["from"], "to": x["to"], "ids": x["ids"]} for x in p["steps"]])
    after = state()
    assert {(i, d) for i, d, _ in after} == before                       # rien perdu, et « b » est toujours désactivé
    where = {i: f for i, _, f in after}
    assert where["http/routers/b"] == where["http/services/b-svc"] == "apps/b-svc.yml"
    assert not s.items()[1]


def test_transports_serveur_ont_leur_dossier(big):
    big.write("t.yml", "http:\n  serversTransports:\n    insecure:\n      insecureSkipVerify: true\n", create=True)
    assert ("file", "t.yml", "transports/t.yml") in {(s["kind"], s["from"], s["to"]) for s in bplan(big, "sites")["steps"]}


# ---- nettoyage des commentaires, fichiers vides, groupes de routeurs, sous-dossiers ----
def apply(store, p, clean=True):
    return store.reorganize([{k: s[k] for k in ("kind", "from", "to", "ids")} for s in p["steps"]], clean)


def full(store):
    items, errors = store.items()
    assert not errors
    return {i["id"]: (i["spec"], bool(i["disabled"])) for i in items}


COMMENTED = """# --- Mes sites ---
http:
  routers:
    # --- Grafana ---
    grafana:
      rule: Host(`grafana.example.com`)  # accès public
      service: grafana
    # --- Wiki ---
    wiki:
      rule: Host(`wiki.example.com`)
      service: grafana
    # --- Blog (autre service) ---
    blog:
      rule: Host(`blog.example.org`)
      service: blog
  # --- Services ---
  services:
    grafana:
      loadBalancer:
        servers:
          - url: http://grafana:3000
    blog:
      loadBalancer:
        servers:
          - url: http://blog:80
"""


@pytest.fixture()
def commented(tmp_path):
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    s.write("tout.yml", COMMENTED, create=True)
    s.write("vide.yml", "# plus rien ici\n", create=True)
    return s


def test_commentaires_supprimes_fichiers_vides_supprimes_routeurs_du_meme_service_ensemble(commented):
    before = full(commented)
    items, errors = commented.items()
    p = build_plan(items, commented.list_dirs(), errors, "root", "sites", True, existing=["tout.yml", "vide.yml"], empty=["vide.yml"])
    grp = {s["to"]: {i.split("/")[-1] for i in s["ids"]} for s in p["steps"] if s["group"] == "sites"}
    assert grp == {"apps/example.com/grafana.yml": {"grafana", "wiki", "grafana"}, "apps/example.org/blog.yml": {"blog", "blog"}}
    done = apply(commented, p)
    assert done["deleted"] == 1
    assert full(commented) == before                                  # aucun bloc perdu ni modifié
    left = {f["path"] for f in commented.list_files()}
    assert left == {"apps/example.com/grafana.yml", "apps/example.org/blog.yml"}   # tout.yml (vidé) et vide.yml ont disparu
    for f in left:
        assert "#" not in commented.read(f)                           # plus aucun commentaire
    g = commented.read("apps/example.com/grafana.yml")
    assert "grafana:" in g and "wiki:" in g and g.count("services:") == 1


def test_commentaires_gardes_si_demande(commented):
    items, errors = commented.items()
    p = build_plan(items, commented.list_dirs(), errors, "root", "sites", False)
    apply(commented, p, clean=False)
    assert any("#" in commented.read(f["path"]) for f in commented.list_files())


def test_reste_du_site_rejoint_la_partie_deja_rangee(tmp_path):
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    s.write("apps/wiki.yml", "http:\n  routers:\n" + router("wiki", "shared"), create=True)
    s.write("racine.yml", "http:\n  routers:\n" + router("wiki2", "shared") + "  services:\n" + service("shared"), create=True)
    items, errors = s.items()
    p = build_plan(items, s.list_dirs(), errors, "root", "sites")
    assert [(x["kind"], x["from"], x["to"]) for x in p["steps"]] == [("blocks", "racine.yml", "apps/wiki.yml")]
    apply(s, p)
    assert {f["path"] for f in s.list_files()} == {"apps/wiki.yml"}
    assert set(full(s)) == {"http/routers/wiki", "http/routers/wiki2", "http/services/shared"}


def test_bloc_desactive_avec_middleware_desactive_garde_son_etat(tmp_path):
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    s.write("f.yml", "http:\n  routers:\n" + router("a", "a-svc") + "    b:\n      rule: Host(`b.example.com`)\n      service: a-svc\n      middlewares: [m1, m2]\n"
            + "  services:\n" + service("a-svc") + "  middlewares:\n    m1:\n      compress: {}\n    m2:\n      compress: {}\n", create=True)
    s.toggle(["http/middlewares/m2"], [], False)                  # m2 en pause : la liste du routeur « b » ne le cite plus (note de rétablissement)
    s.toggle(["http/routers/a"], [], False, "domain:example.com")  # « a » en pause avec une étiquette
    before = full(s)
    items, errors = s.items()
    p = build_plan(items, s.list_dirs(), errors, "root", "sites", True)
    apply(s, p)
    assert full(s) == before                                      # mêmes blocs, mêmes états, mêmes listes
    assert next(i for i in s.items()[0] if i["id"] == "http/routers/a")["pausedBy"] == "domain:example.com"
    s.toggle(["http/middlewares/m2"], [], True)                   # la réactivation retrouve bien m2 dans la liste de « b »
    assert next(i for i in s.items()[0] if i["id"] == "http/routers/b")["spec"]["middlewares"] == ["m1", "m2"]


def test_fichier_avec_autre_contenu_n_est_pas_vide(tmp_path):
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    s.write("certs.yml", "tls:\n  certificates:\n    - certFile: /a.crt\n      keyFile: /a.key\n", create=True)
    s.write("vide.yml", "# rien\n", create=True)
    from app.routes_traefik import _empty_files
    assert _empty_files(s, *s.items()) == ["vide.yml"]


MIGRATION = """# Migration
http:
  routers:
    # AdGuard
    adguard:
      rule: "Host(`guard.example.fr`)"
      service: adguard
      middlewares: [local-only, secure-headers]
    adguard-doh:
      rule: "Host(`guard.example.fr`) && PathPrefix(`/dns-query`)"
      priority: 100
      service: adguard
    #~ harbor:
      #~ rule: "Host(`harbor.example.fr`)"
      #~ service: harbor
    vaultwarden:
      rule: "Host(`vault.example.fr`)"
      service: vaultwarden
      middlewares: [vaultwarden-body-limit, secure-headers]
    vaultwarden-admin:
      rule: "Host(`vault.example.fr`) && PathPrefix(`/admin`)"
      service: vaultwarden
    vaultwarden-hub:
      rule: "Host(`vault.example.fr`) && PathPrefix(`/notifications/hub`)"
      service: vaultwarden
    proxmox:
      rule: "Host(`proxmox.example.fr`)"
      service: proxmox
  services:
    adguard:
      loadBalancer:
        servers: [{url: "http://192.168.50.39:80"}]
    harbor:
      loadBalancer:
        servers: [{url: "http://192.168.50.122:80"}]
    vaultwarden:
      loadBalancer:
        servers: [{url: "http://vaultwarden"}]
    proxmox:
      loadBalancer:
        serversTransport: insecure-transport
        servers: [{url: "https://192.168.50.120:8006"}]
  middlewares:
    local-only:
      ipAllowList:
        sourceRange: ["192.168.1.0/24"]
    secure-headers:
      headers:
        stsSeconds: 63072000
    vaultwarden-body-limit:
      buffering:
        maxRequestBodyBytes: 134217728
  serversTransports:
    insecure-transport:
      insecureSkipVerify: true
"""


def test_gros_fichier_de_migration(tmp_path):
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    s.write("migration.yml", MIGRATION, create=True)
    before = full(s)
    items, errors = s.items()
    p = build_plan(items, s.list_dirs(), errors, "root", "sites", True, existing=["migration.yml"])
    apply(s, p)
    assert full(s) == before
    files = {f["path"] for f in s.list_files()}
    assert files == {"apps/example.fr/adguard.yml", "apps/example.fr/harbor.yml", "apps/example.fr/vaultwarden.yml", "apps/example.fr/proxmox.yml",
                     "middlewares/ip/local-only.yml", "middlewares/headers/secure-headers.yml", "middlewares/limites/vaultwarden-body-limit.yml",
                     "transports/insecure-transport.yml"}
    where = {i["id"]: i["file"] for i in s.items()[0]}
    assert {where[f"http/routers/{n}"] for n in ("adguard", "adguard-doh")} == {where["http/services/adguard"]}
    assert {where[f"http/routers/{n}"] for n in ("vaultwarden", "vaultwarden-admin", "vaultwarden-hub")} == {where["http/services/vaultwarden"]}
    assert not any("#" in s.read(f) for f in files if "harbor" not in f)


# ---- « tout ranger » : même les sites déjà rangés dans des dossiers par catégorie ----
@pytest.fixture()
def rangé(tmp_path):
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    s.write("infra/outils.yml", "http:\n  routers:\n" + router("grafana", "grafana-svc", "grafana.example.com") + router("portainer", "portainer-svc", "portainer.example.com")
            + "  services:\n" + service("grafana-svc") + service("portainer-svc"), create=True)
    s.write("media/film.yml", "http:\n  routers:\n" + router("film", "film-svc", "film.example.org") + router("film-api", "film-svc", "api.film.example.org")
            + "  services:\n" + service("film-svc"), create=True)
    return s


def tout(store, layout, sub=True):
    items, errors = store.items()
    return build_plan(items, store.list_dirs(), errors, "all", layout, sub, existing=[f["path"] for f in store.list_files()])


def test_a_la_racine_seule_rien_a_proposer_pour_des_sites_deja_en_dossiers(rangé):
    items, errors = rangé.items()
    p = build_plan(items, rangé.list_dirs(), errors, "root", "split", True)
    assert p["steps"] == [] and p["stats"]["sitesInFolders"] == 7


@pytest.mark.parametrize("layout", ["sites", "split"])
def test_tout_ranger_liste_tous_les_blocs_et_est_idempotent(rangé, layout):
    before = full(rangé)
    p = tout(rangé, layout)
    assert p["stats"]["moved"] == 7                                    # les 7 blocs sont placés
    apply(rangé, p)
    assert full(rangé) == before
    assert tout(rangé, layout)["steps"] == []                          # rien d'autre à proposer ensuite
    files = {f["path"] for f in rangé.list_files()}
    if layout == "sites":
        assert files == {"apps/example.com/grafana-svc.yml", "apps/example.com/portainer-svc.yml", "apps/example.org/film.yml"}   # fichier à lui seul : il garde son nom
        where = {i["id"]: i["file"] for i in rangé.items()[0]}
        assert where["http/routers/film"] == where["http/routers/film-api"] == where["http/services/film-svc"]
    else:
        assert all(f.startswith(("routers/", "services/")) for f in files) and len(files) == 7


def test_lignes_vides_entre_blocs_et_dossiers_vides_supprimes(rangé):
    (rangé.root / "projets" / "ancien").mkdir(parents=True)            # dossier déjà vide
    items, errors = rangé.items()
    from app.routes_traefik import _empty_dirs
    assert _empty_dirs(rangé) == ["projets"]
    p = build_plan(items, rangé.list_dirs(), errors, "all", "sites", True, existing=[f["path"] for f in rangé.list_files()], empty_dirs=_empty_dirs(rangé))
    assert any(x["kind"] == "rmdir" and x["from"] == "projets" for x in p["steps"])
    done = apply(rangé, p)
    dirs = set(rangé.list_dirs())
    assert "infra" not in dirs and "media" not in dirs and "projets" not in dirs      # emptied by the move / requested
    assert done["dirs"] >= 3
    t = rangé.read("apps/example.org/film.yml")
    assert "\n\n    film-api:" in t or "\n\n    film:" in t                            # ligne vide entre deux routeurs
    g = rangé.read("apps/example.com/grafana-svc.yml")
    assert "\n\n  services:" in g                                                    # et entre les sections


def test_files_starting_with_zz_are_managed_by_the_interface_and_left_alone(store):
    """zz-webui-bans.yml & co : jamais déplacés, jamais réécrits, jamais supprimés par le rangement (même vides, même en « tout ranger »)."""
    store.write("zz-webui-bans.yml", "http:\n  routers:\n" + router("ban", "noop@internal") + "  middlewares:\n    webui-ban-403:\n      compress: {}\n", create=True)
    store.write("zz-webui-nudge.yml", "http:\n  middlewares:\n    webui-nudge:\n      compress: {}\n", create=True)
    for scope in ("root", "all"):
        items, errors = store.items()
        p = build_plan(items, store.list_dirs(), errors, scope, layout="sites", sub=True, empty=["zz-webui-empty.yml", "vide.yml"])
        touched = {s["from"] for s in p["steps"]} | {s["to"] for s in p["steps"]}
        assert not any("zz-" in t for t in touched), touched
        assert any(s["from"] == "vide.yml" and s["kind"] == "delete" for s in p["steps"])                         # un fichier vide ordinaire reste proposé
    assert any(s["from"] == "blog.yml" for s in build_plan(*store.items()[:1], store.list_dirs(), {}, "root", layout="keep")["steps"])
    admin = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"}).json()["access_token"]
    r = client.post("/api/traefik/reorganize", headers={"Authorization": "Bearer " + admin}, json={"steps": [{"kind": "file", "from": "zz-webui-bans.yml", "to": "apps/x.yml"}]})
    assert r.status_code == 422 and "zz" in r.json()["detail"]
