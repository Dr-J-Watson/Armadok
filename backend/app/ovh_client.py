"""Client OVH (zone DNS) avec cache court et mode mock pour les tests."""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .config import settings

RECORD_TYPES = ["A", "AAAA", "CNAME", "MX", "TXT", "SRV", "CAA", "NS", "TLSA", "SSHFP", "NAPTR", "DNAME", "LOC",
                "SPF", "DKIM", "DMARC", "HTTPS", "SVCB", "PTR"]


class OvhError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class MockClient:
    """Mini-OVH en mémoire (OVH_MOCK=1)."""

    def __init__(self, seed_zone: str | None = None) -> None:
        self.zones = {
            "example.com": [
                (1, "A", "", "203.0.113.10", 3600), (2, "A", "grafana", "203.0.113.10", 3600),
                (3, "CNAME", "books", "example.com.", 3600), (4, "A", "mealie", "203.0.113.10", 3600),
                (5, "MX", "", "10 mx1.mail.ovh.net.", 3600), (6, "TXT", "", "v=spf1 include:mx.ovh.com ~all", 3600),
                (7, "A", "vpn", "203.0.113.10", 600),
            ],
            "example.org": [(100, "A", "", "198.51.100.7", 3600)],
        }
        if seed_zone:
            self.zones = {seed_zone: [(1, "A", "", "198.51.100.20", 3600), (2, "A", "www", "198.51.100.20", 3600)]}
        self.next_id = 1000
        self.dnssec = set()

    def _rec(self, z, r):
        return {"id": r[0], "zone": z, "fieldType": r[1], "subDomain": r[2], "target": r[3], "ttl": r[4]}

    def request(self, method, path, **kw):
        m = re.match(r"^/domain/zone(?:/([^/]+)(?:/(.*))?)?$", path)
        if not m:
            raise OvhError("mock: route inconnue " + path)
        zone, rest = m.group(1), m.group(2) or ""
        if zone is None:
            return list(self.zones)
        if zone not in self.zones:
            raise OvhError("zone inconnue", 404)
        recs = self.zones[zone]
        if rest == "":
            return {"name": zone, "dnssecSupported": True, "hasDnsAnycast": False,
                    "lastUpdate": "2026-10-20T10:00:00+02:00", "nameServers": ["dns200.anycast.me", "ns200.anycast.me"]}
        if rest == "soa":
            return {"email": "tech.ovh.net", "refresh": 86400, "retry": 3600, "expire": 3600000, "nxDomainTtl": 60, "ttl": 3600, "serial": 2026102001, "server": "dns200.anycast.me"}
        if rest == "refresh":
            return None
        if rest == "export":
            return "\n".join(f"{r[2] or '@'} {r[4]} IN {r[1]} {r[3]}" for r in recs)
        if rest == "import":
            return None
        if rest == "dnssec":
            if method == "GET":
                return {"status": "enabled" if zone in self.dnssec else "disabled"}
            (self.dnssec.add if method == "POST" else self.dnssec.discard)(zone)
            return None
        if rest == "history":
            return ["2026-10-19T22:00:00+02:00", "2026-10-18T09:12:00+02:00"]
        if rest.startswith("history/"):
            return None
        if rest == "record":
            q = kw
            return [r[0] for r in recs if (not q.get("fieldType") or r[1] == q["fieldType"])
                    and (q.get("subDomain") is None or r[2] == q["subDomain"])]
        rm = re.match(r"^record/(\d+)$", rest)
        if rest == "record" or rm:
            rid = int(rm.group(1))
            idx = next((i for i, r in enumerate(recs) if r[0] == rid), None)
            if idx is None:
                raise OvhError("record inconnu", 404)
            if method == "GET":
                return self._rec(zone, recs[idx])
            if method == "DELETE":
                recs.pop(idx)
                return None
            r = recs[idx]
            recs[idx] = (r[0], r[1], kw.get("subDomain", r[2]), kw.get("target", r[3]), kw.get("ttl", r[4]))
            return None
        raise OvhError("mock: route inconnue " + path)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

    def put(self, path, **kw):
        return self.request("PUT", path, **kw)

    def post(self, path, **kw):
        if path.endswith("/record"):
            zone = path.split("/")[3]
            self.next_id += 1
            r = (self.next_id, kw["fieldType"], kw.get("subDomain") or "", kw["target"], kw.get("ttl") or 3600)
            self.zones[zone].append(r)
            return self._rec(zone, r)
        return self.request("POST", path, **kw)


def explain(e: Exception) -> tuple[str, int]:
    """Message clair et code HTTP adapté : une clé refusée est une erreur de saisie (4xx), pas une panne du serveur (502)."""
    name, txt = type(e).__name__, str(e)
    low = txt.lower()
    if name == "ResourceNotFoundError":
        return f"OVH : introuvable ({txt.splitlines()[0][:120]})", 404
    if name == "InvalidRegion":
        return "Région OVH inconnue : choisissez la région où votre compte a été créé.", 422
    if "application key is invalid" in low or name == "InvalidKey":
        return "OVH refuse l'Application key : elle est invalide, ou créée sur une autre région d'OVH.", 422
    if "invalid signature" in low or "application secret" in low:
        return "L'Application secret ne correspond pas à cette Application key.", 422
    if "consumer" in low or "credential" in low or name in ("InvalidCredential", "NotCredential"):
        return "La Consumer key est invalide ou a expiré : générez-en une nouvelle (même région, mêmes droits).", 422
    if name == "NotGrantedCall" or "not been granted" in low or "not granted" in low:
        # 422 et pas 403 : c'est OVH qui refuse les droits de CETTE clé, pas cette interface qui refuse l'utilisateur (les deux se confondaient)
        return "OVH accepte la clé mais elle n'a pas les droits nécessaires : elle doit autoriser GET, POST, PUT et DELETE sur /domain/zone et /domain/zone/* (à recréer sur api.ovh.com/createToken).", 422
    if name in ("NetworkError", "ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout"):
        return "OVH est injoignable depuis ce serveur (réseau, DNS ou pare-feu).", 502
    return f"OVH : {txt.splitlines()[0][:200] if txt else name}", 502


class OvhService:
    """Accès OVH pour UN compte (credential). Les identifiants viennent du .env (compte principal) ou de la base (chiffrés)."""

    provider = "ovh"
    supports_records = True

    def __init__(self, cred) -> None:
        self.cred = dict(cred)
        self._client = None
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._zones: tuple[float, list[dict]] | None = None
        self.dirty: set[str] = set()

    @property
    def client(self):
        if self._client is None:
            c = self.cred
            if c["source"] == "env":
                ep, ak, asec, ck = settings.ovh_endpoint, settings.ovh_app_key, settings.ovh_app_secret, settings.ovh_consumer_key
            else:
                from .crypto import dec
                ep, ak, asec, ck = c["endpoint"], dec(c["app_key"]), dec(c["app_secret"]), dec(c["consumer_key"])
            if settings.ovh_mock:
                self._client = MockClient(None if c["source"] == "env" else f"{ak}.test")
            else:
                import ovh
                self._client = ovh.Client(endpoint=ep, application_key=ak, application_secret=asec, consumer_key=ck)
        return self._client

    def call(self, method: str, path: str, **kw):
        try:
            return getattr(self.client, method)(path, **kw)
        except OvhError:
            raise
        except Exception as e:  # ovh.exceptions.*
            raise OvhError(*explain(e))

    def zone_names(self) -> list[str]:
        return sorted(self.call("get", "/domain/zone"))

    def zone_info(self, name: str) -> dict:
        if self._zones is None or time.time() - self._zones[0] > 300:
            self._zones = (time.time(), [])
        for z in self._zones[1]:
            if z["name"] == name:
                return z
        try:
            d = self.call("get", f"/domain/zone/{name}")
        except OvhError:
            d = {}
        z = {"name": name, "dnssecSupported": d.get("dnssecSupported"), "hasDnsAnycast": d.get("hasDnsAnycast"),
             "lastUpdate": d.get("lastUpdate"), "nameServers": d.get("nameServers", [])}
        self._zones[1].append(z)
        return z

    def records(self, zone: str, force: bool = False) -> list[dict]:
        hit = self._cache.get(zone)
        if hit and not force and time.time() - hit[0] < 60:
            return hit[1]
        ids = self.call("get", f"/domain/zone/{zone}/record")
        with ThreadPoolExecutor(10) as ex:
            recs = list(ex.map(lambda i: self.call("get", f"/domain/zone/{zone}/record/{i}"), ids))
        recs = [{"id": r["id"], "fieldType": r["fieldType"], "subDomain": r.get("subDomain") or "",
                 "target": r["target"], "ttl": r.get("ttl") or 0} for r in recs]
        recs.sort(key=lambda r: (r["subDomain"] != "", r["subDomain"], r["fieldType"], r["target"]))
        self._cache[zone] = (time.time(), recs)
        return recs

    def invalidate(self, zone: str, dirty: bool = True) -> None:
        self._cache.pop(zone, None)
        if dirty:
            self.dirty.add(zone)

    def create(self, zone, rec) -> dict:
        r = self.call("post", f"/domain/zone/{zone}/record", fieldType=rec["fieldType"],
                      subDomain=rec.get("subDomain") or "", target=rec["target"], ttl=rec.get("ttl") or 0)
        self.invalidate(zone)
        return r

    def update(self, zone, rid, rec) -> None:
        self.call("put", f"/domain/zone/{zone}/record/{rid}", subDomain=rec.get("subDomain") or "",
                  target=rec["target"], ttl=rec.get("ttl") or 0)
        self.invalidate(zone)

    def delete(self, zone, rid) -> None:
        self.call("delete", f"/domain/zone/{zone}/record/{rid}")
        self.invalidate(zone)

    def refresh(self, zone) -> None:
        self.call("post", f"/domain/zone/{zone}/refresh")
        self.dirty.discard(zone)
        self._cache.pop(zone, None)


class Registry:
    def __init__(self) -> None:
        self._svc: dict[int, OvhService] = {}

    def get(self, cred_id: int):
        from . import db
        from .dns_providers import new_service
        if cred_id not in self._svc:
            row = db.one("SELECT * FROM credentials WHERE id=?", (cred_id,))
            if not row:
                raise OvhError("Compte introuvable", 404)
            self._svc[cred_id] = new_service(row) or OvhService(row)
        return self._svc[cred_id]

    def forget(self, cred_id: int) -> None:
        self._svc.pop(cred_id, None)

    def for_domain(self, d):
        return self.get(d["credential_id"])


registry = Registry()
