"""Renommer un élément renomme aussi ses références (blocs actifs, en pause, fichiers .off, notes #% ref)."""
import pytest

from app.traefik_store import Store

MAIN = """http:
  routers:
    site:
      rule: Host(`a.example.com`)
      service: app
      middlewares:
        - auth
        - gzip@file
      tls:
        options: strict
    other:
      rule: Host(`b.example.com`)
      service: app@file
      middlewares: [auth, keep]
    #~ paused:
      #~ rule: Host(`c.example.com`)
      #~ service: app
      #~ middlewares:
        #~ - auth
        #~ - "gzip@file"
  services:
    app:
      loadBalancer:
        serversTransport: tr
        servers:
          - url: http://app:80
    split:
      weighted:
        services:
          - name: app
            weight: 1
    fb:
      failover:
        service: app
        fallback: split
  middlewares:
    auth:
      basicAuth:
        users: ["u:h"]
    gzip:
      compress: {}
    keep:
      compress: {}
    chained:
      chain:
        middlewares: [auth, keep]
    err:
      errors:
        status: ["500"]
        service: app
  serversTransports:
    tr:
      insecureSkipVerify: true
tls:
  options:
    strict:
      minVersion: VersionTLS12
"""
OFF = "http:\n  routers:\n    off-r:\n      rule: Host(`o.example.com`)\n      service: app\n      middlewares:\n        - auth\n"


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "dyn", tmp_path / "bak")
    s.root.mkdir()
    s.write("main.yml", MAIN, create=True)
    (s.root / "extra.yml.off").write_text(OFF)
    return s


def text(s, f="main.yml"):
    return (s.root / f).read_text()


def rename(s, proto, kind, old, new):
    s.upsert(proto, kind, new, s.items()[0] and next(i for i in s.items()[0] if i["id"] == f"{proto}/{kind}/{old}")["spec"], old_name=old)


def test_middleware_renomme_partout(store):
    rename(store, "http", "middlewares", "auth", "login")
    t = text(store)
    assert "        - login\n        - gzip@file" in t            # liste en bloc
    assert "middlewares: [login, keep]" in t                      # liste en ligne, et chain (2 occurrences)
    assert t.count("[login, keep]") == 2
    assert "#~ - login" in t                                      # bloc en pause
    assert "auth" not in t.replace("basicAuth", "")
    assert "- login" in text(store, "extra.yml.off")              # fichier désactivé
    items = {i["id"]: i for i in store.items()[0]}
    assert "http/middlewares/login" in items and "http/middlewares/auth" not in items
    assert items["http/routers/paused"]["disabled"] and "login" in items["http/routers/paused"]["spec"]["middlewares"]


def test_suffixe_file_conserve(store):
    rename(store, "http", "middlewares", "gzip", "compression")
    t = text(store)
    assert "- compression@file" in t and '#~ - "compression@file"' in t and "gzip" not in t.replace("compression", "")


def test_service_renomme_partout(store):
    rename(store, "http", "services", "app", "web")
    t = text(store)
    assert t.count("service: web") >= 4        # routeurs (dont @file), errors, failover
    assert "service: web@file" in t and "#~ service: web" in t and "- name: web" in t
    assert "service: app" not in t and "name: app" not in t
    assert "service: web" in text(store, "extra.yml.off")


def test_transport_et_option_tls_renommes(store):
    rename(store, "http", "serversTransports", "tr", "insecure")
    assert "serversTransport: insecure" in text(store)
    rename(store, "tls", "options", "strict", "tls12")
    assert "options: tls12" in text(store)


def test_aucun_element_orphelin_apres_renommage(store):
    from app.traefik_lint import used_by
    rename(store, "http", "middlewares", "auth", "login")
    rename(store, "http", "services", "app", "web")
    items, errors = store.items()
    assert not errors
    by = used_by(items)
    assert by.get("http/middlewares/login") and by.get("http/services/web")


def test_nom_identique_ne_touche_rien(store):
    before = text(store)
    store.upsert("http", "middlewares", "auth", {"basicAuth": {"users": ["u:h"]}})
    assert text(store) == before.replace("[\"u:h\"]", "[\"u:h\"]")


def test_notes_de_reactivation_suivent_le_renommage(store):
    store.toggle(["http/middlewares/keep"], [], False)        # « keep » désactivé : les listes qui le citent le retirent et gardent une note
    assert '#% ref' in text(store) and '"auth"' in text(store)
    rename(store, "http", "middlewares", "auth", "login")
    assert '"auth"' not in text(store) and '"login"' in text(store)
    store.toggle(["http/middlewares/keep"], [], True)         # la réactivation rétablit les listes avec le nouveau nom
    t = text(store)
    assert t.count('["login", "keep"]') == 2                  # le routeur « other » et la chaîne
    assert not store.items()[1]
