"""Modules d'un nœud (traefik, docker) et liaison conteneur ⇄ Traefik (fonctions pures, sur des données fictives)."""
import stat

from app import linkage, nodes, worker_proto as proto
from app.config import settings


def test_parse_modules():
    assert proto.parse_modules("") == ("traefik",) and proto.parse_modules(None) == ("traefik",)
    assert proto.parse_modules("docker") == ("docker",)
    assert proto.parse_modules(" Docker ; TRAEFIK,docker ") == ("traefik", "docker")   # casse, séparateurs, doublons, ordre canonique
    assert proto.parse_modules("rm -rf,nope") == ("traefik",)                           # inconnus ignorés
    assert proto.parse_modules(["docker", 3, None]) == ("docker",)
    assert proto.parse_modules("", default=()) == ()


def test_ops_follow_modules():
    t, d = proto.ops_for("traefik"), proto.ops_for("docker")
    assert "items" in t and "write" in t and "d_info" not in t           # un nœud « traefik » seul ne reçoit aucune opération Docker…
    assert "d_info" in d and "items" not in d                            # …et un nœud « docker » seul aucun ordre Traefik
    assert proto.ops_for("traefik,docker") == t | d
    assert proto.ops_for("") == frozenset()
    assert set(proto.OPS) == set(proto.OPS_BY_MODULE["traefik"]) | set(proto.OPS_BY_MODULE["docker"])


def test_local_node_docker_module_needs_explicit_opt_in(monkeypatch, tmp_path):
    sock = tmp_path / "docker.sock"
    import socket
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(sock))
    assert stat.S_ISSOCK(sock.stat().st_mode)
    try:
        monkeypatch.setattr(settings, "docker_socket", str(sock))
        monkeypatch.setattr(settings, "docker_manage", False)
        assert nodes.local_modules() == ("traefik",)                      # socket présent mais non demandé : pas de Docker
        monkeypatch.setattr(settings, "docker_manage", True)
        assert nodes.local_modules() == ("traefik", "docker")
        monkeypatch.setattr(settings, "docker_socket", str(tmp_path / "absent.sock"))
        assert nodes.local_modules() == ("traefik",)                      # demandé mais pas de socket
        f = tmp_path / "pas-un-socket"
        f.write_text("x")
        monkeypatch.setattr(settings, "docker_socket", str(f))
        assert nodes.local_modules() == ("traefik",)
    finally:
        srv.close()


# ------------------------------------------------------------------ liaison
def C(name, **kw):
    return {"name": name, "labels": kw.pop("labels", {}), "ips": kw.pop("ips", []), "ports": kw.pop("ports", []), "service": kw.pop("service", ""), "project": kw.pop("project", ""), **kw}


def R(name, rule, service):
    return {"id": f"http/routers/{name}", "proto": "http", "kind": "routers", "name": name, "spec": {"rule": rule, "service": service}}


def S(name, *urls):
    return {"id": f"http/services/{name}", "proto": "http", "kind": "services", "name": name, "spec": {"loadBalancer": {"servers": [{"url": u} for u in urls]}}}


def test_parse_labels():
    d = linkage.parse_labels({"traefik.http.routers.web.rule": "Host(`a.fr`)", "traefik.http.routers.web.tls.certresolver": "le",
                              "traefik.http.services.web.loadbalancer.server.port": "8080", "traefik.tcp.routers.x.rule": "HostSNI(`*`)", "autre": "1"})
    assert d["routers"] == {"web": {"rule": "Host(`a.fr`)", "tls.certresolver": "le"}}
    assert d["services"] == {"web": {"loadbalancer.server.port": "8080"}}


def test_labels_link_and_disabled():
    c = C("blog", labels={"traefik.http.routers.blog.rule": "Host(`b.fr`) || Host(`www.b.fr`)", "traefik.http.services.blog.loadbalancer.server.port": "2368"})
    (lk,) = linkage.links_for(c, [c], [])
    assert lk["router"] == "blog@docker" and lk["provider"] == "docker" and lk["sure"] and lk["port"] == 2368
    assert lk["hosts"] == ["b.fr", "www.b.fr"] and lk["via"] == "labels" and lk["item"] is None
    off = C("blog", labels={**c["labels"], "traefik.enable": "false"})
    assert linkage.links_for(off, [off], []) == []


def test_file_router_reaches_container_by_name_url_or_alias():
    web = C("web-1", ips=["172.20.0.5"], ports=[80], service="web", project="site")
    other = C("autre", ips=["172.20.0.9"], ports=[3000], service="api")
    items = [R("r-name", "Host(`a.fr`)", "s-name"), S("s-name", "http://web-1:80"),
             R("r-ip", "Host(`b.fr`)", "s-ip@file"), S("s-ip", "http://172.20.0.5:80"),
             R("r-alias", "Host(`c.fr`)", "s-alias"), S("s-alias", "http://web:80"),
             R("r-bad-port", "Host(`d.fr`)", "s-port"), S("s-port", "http://web-1:9999"),
             R("r-other", "Host(`e.fr`)", "s-other"), S("s-other", "http://autre:3000"),
             R("r-docker", "Host(`f.fr`)", "svc@docker")]
    got = {lk["router"]: lk for lk in linkage.links_for(web, [web, other], items)}
    assert set(got) == {"r-name", "r-ip", "r-alias", "r-bad-port"}
    assert got["r-name"]["sure"] and got["r-ip"]["sure"] and got["r-alias"]["sure"]       # nom, adresse, alias unique : sûrs
    assert not got["r-bad-port"]["sure"]                                                   # port que le conteneur n'expose pas : probable seulement
    assert got["r-name"]["via"] == "url" and got["r-name"]["item"] == "http/routers/r-name" and got["r-name"]["hosts"] == ["a.fr"]
    assert [lk["router"] for lk in linkage.links_for(other, [web, other], items)] == ["r-other"]


def test_ambiguous_compose_service_name_is_only_probable():
    a, b = C("p1-web-1", service="web", project="p1"), C("p2-web-1", service="web", project="p2")
    items = [R("r", "Host(`x.fr`)", "s"), S("s", "http://web:80")]
    assert [(lk["router"], lk["sure"]) for lk in linkage.links_for(a, [a, b], items)] == [("r", False)]
    assert [(lk["router"], lk["sure"]) for lk in linkage.links_for(b, [a, b], items)] == [("r", False)]


def test_link_all_reverse_and_hosts():
    web = C("web-1", ports=[80], service="web")
    blog = C("blog", labels={"traefik.http.routers.blog.rule": "Host(`b.fr`)"})
    lone = C("db", service="db")
    items = [R("r", "Host(`a.fr`)", "s"), S("s", "http://web-1:80")]
    linked = linkage.link_all([web, blog, lone], items)
    assert set(linked) == {"web-1", "blog"}                                                # un conteneur sans règle n'apparaît pas
    assert linkage.by_router(linked) == {"r": [{"container": "web-1", "sure": True}], "blog@docker": [{"container": "blog", "sure": True}]}
    assert linkage.hosts_of(linked["web-1"]) == ["a.fr"] and linkage.hosts_of([]) == []


def test_features_are_hidden_without_the_traefik_module():
    from app import observability as obs
    base = {"kind": "local", "metricsEnabled": True}
    on = obs.features({**base, "modules": ["traefik", "docker"]}, True)
    assert on["bans"]["enabled"] and on["certs"]["enabled"] and on["metrics"]["enabled"]
    off = obs.features({**base, "modules": ["docker"]}, True)
    assert not any(off[k]["enabled"] for k in ("certs", "metrics", "alerts", "ips", "bans"))
    assert obs.features({"kind": "local", "metricsEnabled": False}, False)["bans"]["enabled"]    # sans « modules » : comme avant (traefik)
