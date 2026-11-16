"""Règles Traefik : ce que l'éditeur de conditions (ET / OU / groupes) peut écrire reste borné aux domaines autorisés."""
import pytest

from app.rules import host_constraint, parse


def hosts(rule):
    return host_constraint(parse(rule))


@pytest.mark.parametrize("rule,expected", [
    ("Host(`a.fr`)", {"a.fr"}),
    ("Host(`a.fr`) && PathPrefix(`/x`)", {"a.fr"}),
    ("Host(`a.fr`) && (PathPrefix(`/a`) || PathPrefix(`/b`))", {"a.fr"}),                 # adresse ET (chemin A OU chemin B)
    ("(Host(`a.fr`) || Host(`b.fr`)) && !PathPrefix(`/x`)", {"a.fr", "b.fr"}),
    ("(Host(`a.fr`) && PathPrefix(`/a`)) || (Host(`b.fr`) && PathPrefix(`/b`))", {"a.fr", "b.fr"}),
    ("Host(`a.fr`, `b.fr`) && Method(`GET`)", {"a.fr", "b.fr"}),
    ("Host(`a.fr`) && (PathPrefix(`/a`) || (Host(`b.fr`) || Method(`GET`)))", {"a.fr"}),   # toujours lié à a.fr par le ET
])
def test_regles_limitees_a_des_hotes(rule, expected):
    assert hosts(rule) == expected


@pytest.mark.parametrize("rule", [
    "Host(`a.fr`) || PathPrefix(`/x`)",                  # le OU sans adresse capterait le chemin de TOUS les domaines
    "PathPrefix(`/x`)",
    "!Host(`a.fr`)",
    "(Host(`a.fr`) && PathPrefix(`/a`)) || PathPrefix(`/b`)",
    "(Host(`a.fr`) && PathPrefix(`/a`)) || (Host(`b.fr`) || Method(`GET`))",
])
def test_regles_qui_echappent_aux_domaines_sont_refusees(rule):
    assert hosts(rule) is None
