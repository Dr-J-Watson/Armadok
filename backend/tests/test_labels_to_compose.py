"""Site de Traefik → labels dans un compose (en gardant sa mise en forme), retrait des labels, suggestion du service."""
import pytest

from app import labels_yaml as ly

COMPOSE_LIST = """# mon projet
services:
  web:
    image: nginx:1   # le front
    container_name: monsite
    labels:
      - "com.example=1"
      - "traefik.enable=false"
  db:
    image: postgres
"""
COMPOSE_MAP = "services:\n  web:\n    image: nginx\n    labels:\n      a: b\n"
LABELS = {"traefik.enable": "true", "traefik.http.routers.monsite.rule": "Host(`a.example.com`)", "traefik.http.services.monsite.loadbalancer.server.port": "80"}


def test_labels_are_added_keeping_comments_and_style():
    new, added, conflicts = ly.set_service_labels(COMPOSE_LIST, "web", LABELS)
    assert "# mon projet" in new and "# le front" in new
    assert "traefik.http.routers.monsite.rule=Host(`a.example.com`)" in new and "com.example=1" in new
    assert conflicts == ["traefik.enable"] and "traefik.enable" not in added             # existe avec une AUTRE valeur : on ne l'écrase pas
    assert "traefik.enable=false" in new
    new2, added2, conflicts2 = ly.set_service_labels(COMPOSE_LIST, "web", LABELS, overwrite=True)
    assert "traefik.enable=true" in new2 and "traefik.enable=false" not in new2 and not conflicts2 and "traefik.enable" in added2


def test_dict_style_and_missing_labels():
    new, added, _ = ly.set_service_labels(COMPOSE_MAP, "web", LABELS)
    assert "a: b" in new and "traefik.http.routers.monsite.rule" in new and len(added) == 3
    new, added, _ = ly.set_service_labels(COMPOSE_LIST, "db", LABELS)                  # pas de labels : une liste est créée
    assert "- traefik.enable=true" in new or "- 'traefik.enable=true'" in new or '- "traefik.enable=true"' in new
    again, added, conflicts = ly.set_service_labels(new, "db", LABELS)                  # idempotent
    assert again == new and added == [] and conflicts == []
    with pytest.raises(ValueError):
        ly.set_service_labels(COMPOSE_LIST, "absent", LABELS)


def test_remove_traefik_labels_keeps_the_others():
    new, removed = ly.remove_traefik_labels(COMPOSE_LIST, "web")
    assert removed == ["traefik.enable"] and "com.example=1" in new and "traefik." not in new
    new2, removed2 = ly.remove_traefik_labels(new, "web")
    assert removed2 == []
    only, removed3 = ly.remove_traefik_labels("services:\n  w:\n    image: x\n    labels:\n      - traefik.enable=true\n", "w")
    assert removed3 == ["traefik.enable"] and "labels" not in only                      # plus aucun label : la clé disparaît


SERVICES = [
    {"file": "docker-compose.yml", "service": "web", "containerName": "monsite", "image": "nginx:1", "ports": ["8080:80"]},
    {"file": "docker-compose.yml", "service": "blog", "containerName": "", "image": "ghost", "ports": []},
    {"file": "docker-compose.prod.yml", "service": "cache", "containerName": "", "image": "redis", "ports": []},
]


def test_suggest_service_ranks_by_target():
    r = ly.suggest_service(["http://monsite:80"], SERVICES)
    assert r[0]["service"] == "web" and r[0]["score"] >= 100 and "nom de ce conteneur" in r[0]["reason"]
    r = ly.suggest_service(["http://blog:2368"], SERVICES)
    assert r[0]["service"] == "blog" and r[0]["score"] == 90
    r = ly.suggest_service(["http://192.168.1.5:9000"], SERVICES)
    assert all(x["score"] == 0 for x in r)                                              # une IP ne ressemble à aucun nom : pas de suggestion
    assert ly.suggest_service([], SERVICES)[0]["score"] == 0
