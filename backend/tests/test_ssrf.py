"""Serveurs DNS locaux : jamais la machine elle-même, jamais les métadonnées cloud ; les adresses privées restent permises ; le corps des réponses n'est pas recopié."""
import socket

import pytest

from app import dns_providers as dp
from app.config import settings
from app.ovh_client import OvhError


@pytest.fixture(autouse=True)
def strict(monkeypatch):
    monkeypatch.setattr(settings, "allow_loopback_dns", False)   # les autres tests en ont besoin (faux serveurs sur 127.0.0.1) ; ici, le vrai comportement


@pytest.mark.parametrize("url", ["http://127.0.0.1:3000", "http://localhost:3000", "http://[::1]:3000", "http://0.0.0.0", "http://169.254.169.254/latest",
                                 "http://127.1.2.3", "http://foo.localhost", "http://240.0.0.1"])
def test_forbidden_destinations(url):
    with pytest.raises(OvhError):
        dp.clean_server_url(url)


@pytest.mark.parametrize("url", ["192.168.1.5:3000", "http://10.0.0.2", "http://172.20.0.9:8080", "https://192.168.0.1"])
def test_private_addresses_remain_allowed(url):
    assert dp.clean_server_url(url).startswith("http")


def test_a_name_resolving_to_loopback_or_metadata_is_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))])
    with pytest.raises(OvhError):
        dp.clean_server_url("http://pi.hole.exemple")
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: [(2, 1, 6, "", ("192.168.1.9", 0)), (2, 1, 6, "", ("169.254.169.254", 0))])
    with pytest.raises(OvhError):                                                                      # une seule mauvaise adresse suffit
        dp.clean_server_url("http://double.exemple")
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: [(2, 1, 6, "", ("192.168.1.9", 0))])
    assert dp.clean_server_url("http://adguard:3000") == "http://adguard:3000"


def test_checked_again_at_every_call(monkeypatch):
    """Un nom accepté à l'enregistrement puis redirigé vers 127.0.0.1 (DNS rebinding) est refusé à l'appel."""
    svc = dp.new_service({"id": 0, "provider": "adguard", "endpoint": "http://adguard.exemple:3000", "app_key": "", "secret": "", "options": None})
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))])
    with pytest.raises(OvhError):
        svc._http("GET", "/control/status")
