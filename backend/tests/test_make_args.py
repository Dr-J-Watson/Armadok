"""Cibles de Makefile qui attendent des arguments (ENV=prod…) : détection, saisie, et transmission comme simples données."""
import pytest

from app import project_ops
from app.traefik_store import StoreError
from test_projects import pj  # noqa: F401  (fixture : un dossier de projets autorisé)

MAKEFILE_ARGS = """ifndef ENV
$(error ENV est obligatoire)
endif
TAG ?= latest
VERSION := 1
.PHONY: deploy build release
deploy: build ## Déploie
\t@echo $(ENV) $(TAG) $(VERSION) $(HOME) $$loop
build:
\tdocker build -t app:$(TAG) $(EXTRA)
release:
\ttest -n "$${TOKEN:?le jeton est obligatoire}" ; echo $${REGION:-eu}
"""


def test_make_targets_list_the_arguments_they_expect():
    parsed = project_ops.parse_make(MAKEFILE_ARGS)
    t = {x["name"]: {v["name"]: v for v in x["vars"]} for x in parsed}
    assert set(t["deploy"]) == {"ENV", "TAG", "EXTRA"}                       # VERSION (constante) et HOME (environnement) ne sont pas des arguments ; build est une dépendance
    assert t["deploy"]["ENV"]["required"] is True and t["deploy"]["TAG"] == {"name": "TAG", "required": False, "default": "latest"}
    assert t["deploy"]["EXTRA"]["required"] is False and set(t["build"]) == {"TAG", "EXTRA"}
    assert t["release"]["TOKEN"]["required"] is True and t["release"]["REGION"]["default"] == "eu"
    assert [v["name"] for v in next(x for x in parsed if x["name"] == "deploy")["vars"]][0] == "ENV"   # les obligatoires d'abord


def test_make_run_passes_only_known_arguments_as_data(pj, monkeypatch):
    d = pj["base"] / "args-site"
    d.mkdir(exist_ok=True)
    (d / "Makefile").write_text(MAKEFILE_ARGS)
    seen = {}
    monkeypatch.setattr(project_ops, "_spawn", lambda dd, argv, label, action, **k: seen.update(argv=argv, label=label) or {"job": "x", "label": label})
    monkeypatch.setattr(project_ops.shutil, "which", lambda n: "/usr/bin/make")
    project_ops.p_exec_make_run({"dir": str(d), "target": "deploy", "vars": {"ENV": "prod", "TAG": "v 1; id", "EXTRA": ""}})
    assert seen["argv"] == ["make", "-f", "Makefile", "--no-print-directory", "ENV=prod", "TAG=v 1; id", "--", "deploy"]   # chaque valeur est UN argument : aucun shell, rien à échapper
    assert seen["label"] == "make deploy (ENV, TAG)" and "v 1" not in seen["label"]                                         # le libellé n'affiche pas les valeurs (secrets)
    cases = (({}, "obligatoire"), ({"ENV": ""}, "obligatoire"), ({"ENV": "x", "SHELL": "/bin/sh"}, "inconnu"), ({"ENV": "x", "PATH": "/tmp"}, "inconnu"),
             ({"ENV": "x", "NOPE": "1"}, "inconnu"), ({"ENV": "a\nb"}, "invalide"), ({"ENV": "x" * 1001}, "invalide"), ({"ENV": 5}, "invalide"), ({"-f": "x"}, "inconnu"))
    for bad, why in cases:
        with pytest.raises(StoreError, match=why):
            project_ops.p_exec_make_run({"dir": str(d), "target": "deploy", "vars": bad})
    with pytest.raises(StoreError, match="Arguments invalides"):
        project_ops.p_exec_make_run({"dir": str(d), "target": "deploy", "vars": ["ENV=x"]})
    project_ops.p_exec_make_run({"dir": str(d), "target": "release", "vars": {"TOKEN": "s3cret"}})
    assert seen["argv"][4:] == ["TOKEN=s3cret", "--", "release"]
