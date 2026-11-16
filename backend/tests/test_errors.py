"""Erreurs lisibles : clés OVH refusées (4xx en français, jamais 502) et nœud sans worker (l'interface reste utilisable)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ovh_client import explain

client = TestClient(app)


def _e(name, msg=""):
    return type(name, (Exception,), {})(msg)


@pytest.mark.parametrize("exc,status,fragment", [
    (_e("APIError", "This application key is invalid\nOVH-Query-ID: EU.ext-4.x"), 422, "Application key"),
    (_e("APIError", "Invalid signature OVH-Query-ID"), 422, "Application secret"),
    (_e("InvalidCredential", "x"), 422, "Consumer key"),
    (_e("APIError", "Invalid credential"), 422, "Consumer key"),
    (_e("NotGrantedCall", "This call has not been granted"), 422, "/domain/zone"),
    (_e("InvalidRegion", "Unknown endpoint"), 422, "Région"),
    (_e("ResourceNotFoundError", "The requested object (zone) does not exist"), 404, "introuvable"),
    (_e("NetworkError", "boom"), 502, "injoignable"),
    (_e("APIError", "Quelque chose d'inattendu"), 502, "OVH :"),
])
def test_ovh_errors_are_explained(exc, status, fragment):
    msg, code = explain(exc)
    assert code == status and fragment in msg and "OVH-Query-ID" not in msg


def test_bad_keys_are_a_user_error_not_a_server_error(monkeypatch):
    from app import routes_access
    from app.ovh_client import OvhError

    class Boom:
        def __init__(self, *_): pass
        def zone_names(self): raise OvhError(*explain(_e("APIError", "This application key is invalid")))
    monkeypatch.setattr(routes_access, "OvhService", Boom)
    r = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"})
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    r = client.post("/api/credentials", headers=h, json={"label": "x", "endpoint": "ovh-eu", "applicationKey": "a", "applicationSecret": "b", "consumerKey": "c"})
    assert r.status_code == 422 and "Application key" in r.json()["detail"]


def test_node_without_worker_does_not_break_meta():
    r = client.post("/api/auth/login", json={"username": "root", "password": "admin-password-1"})
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    node = client.post("/api/nodes", headers=h, json={"name": "toto"}).json()
    hn = {**h, "X-Node": node["id"]}
    m = client.get("/api/meta", headers=hn)
    assert m.status_code == 200 and m.json()["nodeOffline"] is True and m.json()["user"]["username"] == "root"
    assert client.get("/api/traefik/overview", headers=hn).status_code == 503          # attendu : pas de worker
    client.delete(f"/api/nodes/{node['id']}", headers=h)


def test_erreur_imprevue_repond_en_json_lisible():
    """Une exception non prévue ne doit jamais donner du texte brut (le navigateur n'aurait rien à afficher)."""
    from fastapi.routing import APIRoute

    def boom():
        raise RuntimeError("secret interne")
    route = APIRoute("/api/_boom", boom, methods=["GET"])
    app.router.routes.insert(0, route)
    try:
        r = TestClient(app, raise_server_exceptions=False).get("/api/_boom")
    finally:
        app.router.routes.remove(route)
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/json")
    assert "Erreur interne" in r.json()["detail"] and "secret interne" not in r.json()["detail"]
