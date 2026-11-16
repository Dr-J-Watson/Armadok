"""Fournisseurs DNS : chaque compte (credential) a un fournisseur, et l'interface s'adapte à ce qu'il sait faire.

 - ovh      : zone complète (enregistrements créés, modifiés, supprimés) — voir ovh_client.OvhService
 - duckdns  : une seule adresse (IPv4 / IPv6) par nom `xxx.duckdns.org` + un TXT ; tous les sous-domaines suivent cette adresse
 - local    : aucun fournisseur (DNS interne : Pi-hole, AdGuard, routeur…). Le domaine sert de nom aux sites et aux droits ;
              rien n'est publié nulle part. Les certificats restent l'affaire de Traefik.
"""
import base64
import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from .config import settings
from .ovh_client import OvhError

PROVIDERS: dict[str, dict] = {
    "ovh": {"label": "OVH", "needsAccount": True, "records": True, "singleIp": False,
            "help": "Zone DNS complète : enregistrements créés, modifiés et supprimés depuis ici."},
    "duckdns": {"label": "DuckDNS", "needsAccount": True, "records": False, "singleIp": True,
                "help": "Une adresse IP (et un TXT) par nom xxx.duckdns.org ; ses sous-domaines suivent la même adresse."},
    "adguard": {"label": "AdGuard Home", "needsAccount": True, "records": True, "singleIp": False,
                "help": "Votre serveur DNS local AdGuard Home : les noms de vos sites locaux y sont créés (réécritures DNS) vers l'adresse de votre choix, sans rien publier."},
    "pihole": {"label": "Pi-hole", "needsAccount": True, "records": True, "singleIp": False,
               "help": "Votre Pi-hole (version 6) : les noms de vos sites locaux y sont créés (DNS local et CNAME), sans rien publier. Pas de jokers."},
    "local": {"label": "DNS local", "needsAccount": False, "records": False, "singleIp": False,
              "help": "Domaine résolu par votre propre DNS (Pi-hole, AdGuard, routeur…) : rien n'est créé chez un fournisseur."},
}

DUCK_SUFFIX = ".duckdns.org"
_DUCK_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_HOSTNAME = re.compile(r"^(?=.{3,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")


ADGUARD_TYPES = ["A", "AAAA", "CNAME"]


def capabilities(provider: str) -> dict:
    """Ce que l'interface peut proposer pour ce fournisseur. types : None = tous ; ttl / publish / zoneTools : fonctions propres à OVH."""
    p = PROVIDERS.get(provider, PROVIDERS["ovh"])
    local_server = provider in LOCAL_SERVERS
    return {"records": p["records"], "singleIp": p["singleIp"], "txt": provider == "duckdns",
            "types": ADGUARD_TYPES if local_server else None, "wildcards": provider != "pihole", "ttl": not local_server, "publish": not local_server, "zoneTools": not local_server}


def clean_hostname(name: str) -> str | None:
    n = (name or "").strip().lower().rstrip(".")
    return n if _HOSTNAME.match(n) else None


def clean_duck_name(name: str) -> str | None:
    """« monsite » ou « monsite.duckdns.org » -> « monsite.duckdns.org » ; None si le nom est invalide."""
    n = (name or "").strip().lower().rstrip(".")
    label = n[: -len(DUCK_SUFFIX)] if n.endswith(DUCK_SUFFIX) else n
    return label + DUCK_SUFFIX if _DUCK_LABEL.match(label) else None


_SUB_OK = re.compile(r"^(\*|[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?)(\.[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?)*$")


LOCAL_SERVERS = ("adguard", "pihole")   # serveurs DNS locaux pilotés par leur API : mêmes enregistrements (A / AAAA / CNAME), mêmes limites
_API_PATH = {"adguard": r"/control.*$", "pihole": r"/(api|admin).*$"}   # chemin collé depuis le navigateur ou la documentation : on garde la base


def _forbidden(ip) -> bool:
    return ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved or (ip.is_loopback and not settings.allow_loopback_dns)


def check_host(host: str) -> None:
    """Refuse ce qui n'est jamais un serveur DNS de votre réseau : cette machine elle-même (127.0.0.1, localhost), les adresses de métadonnées cloud
    (169.254.x), les adresses non routables. Les adresses privées (192.168.x, 10.x, 172.16-31.x) restent permises : c'est le cas normal.
    Un NOM est résolu et chaque adresse obtenue est contrôlée (un nom qui mène à 127.0.0.1 est refusé aussi)."""
    h = (host or "").strip("[]").lower()
    if not h or h == "localhost" or h.endswith(".localhost"):
        if not settings.allow_loopback_dns:
            raise OvhError("Cette adresse désigne l'interface elle-même, pas un serveur DNS", 422)
    try:
        ips = [ipaddress.ip_address(h)]
    except ValueError:
        try:
            ips = [ipaddress.ip_address(a[4][0].split("%")[0]) for a in socket.getaddrinfo(h, None)]
        except (socket.gaierror, OSError):
            return   # nom inconnu d'ici : l'erreur de connexion le dira ; rien à contrôler
    if any(_forbidden(ip) for ip in ips):
        raise OvhError("Cette adresse n'est pas celle d'un serveur DNS de votre réseau (boucle locale, métadonnées cloud ou adresse réservée)", 422)


def clean_server_url(raw: str, provider: str = "adguard") -> str:
    """Adresse d'un serveur DNS local : « 192.168.1.5:3000 » -> « http://192.168.1.5:3000 ». Lève OvhError si inutilisable.
    Les adresses privées sont VOULUES (c'est un serveur de votre réseau) ; seules les adresses de métadonnées cloud sont refusées."""
    u = (raw or "").strip()
    if not u:
        raise OvhError("Indiquez l'adresse du serveur (ex. http://192.168.1.5:3000)", 422)
    if "://" not in u:
        u = "http://" + u
    parts = urllib.parse.urlsplit(u)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise OvhError("Adresse invalide : elle doit commencer par http:// ou https://", 422)
    check_host(parts.hostname)
    path = re.sub(_API_PATH.get(provider, _API_PATH["adguard"]), "", parts.path).rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):   # on ne suit jamais une redirection : les identifiants ne partent pas ailleurs
        return None


def _rid(domain: str, answer: str) -> int:
    return zlib.crc32(f"{domain}|{answer}".encode())


def _answer_type(answer: str) -> str | None:
    try:
        return "A" if ipaddress.ip_address(answer).version == 4 else "AAAA"
    except ValueError:
        pass
    if answer.upper() in ("A", "AAAA"):   # mots-clés d'exception d'AdGuard (« ne pas répondre en A / AAAA ») : hors de notre périmètre
        return None
    return "CNAME" if clean_hostname(answer) else None


class _LocalServer:
    """Serveur DNS local piloté par son API (AdGuard Home, Pi-hole) : ses entrées « nom -> adresse ou alias » servent d'enregistrements
    A / AAAA / CNAME. Pas de TTL, pas de TXT ni de MX, pas de zone à « appliquer » : une entrée est active dès qu'elle existe.
    Chaque serveur fournit seulement _pairs / _add / _remove / test ; tout le reste (types, validation, remplacement) est commun."""
    provider = ""
    label = ""
    supports_records = True
    publishes = False
    wildcards = True

    def __init__(self, cred) -> None:
        self.cred = dict(cred)
        self.dirty: set[str] = set()
        self._cache: tuple[float, list[tuple[str, str]]] | None = None

    # --- accès HTTP commun
    @property
    def _opts(self) -> dict:
        try:
            return json.loads(self.cred.get("options") or "{}")
        except ValueError:
            return {}

    def _http(self, method: str, path: str, body=None, headers: dict | None = None):
        """Appel JSON sans jamais suivre de redirection ; erreurs expliquées sans jamais citer les identifiants. Renvoie (code, JSON ou None)."""
        base = self.cred["endpoint"]
        check_host(urllib.parse.urlsplit(base).hostname or "")   # à chaque appel : un nom qui change de destination n'ouvre pas la porte à autre chose
        h = {"User-Agent": "armadok", "Accept": "application/json", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        handlers: list = [_NoRedirect()]
        if base.startswith("https") and self._opts.get("insecureTls"):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        cert_hint = f"Certificat HTTPS de {self.label} non reconnu : utilisez http://, ou cochez « certificat auto-signé »"
        try:
            with urllib.request.build_opener(*handlers).open(urllib.request.Request(base + path, data=data, method=method, headers=h), timeout=8) as r:
                code, raw = r.status, r.read(2_000_000)
        except urllib.error.HTTPError as e:
            # Le corps de la réponse n'est JAMAIS recopié vers l'utilisateur : ce serait un moyen de lire ce que répond n'importe quel service du réseau.
            if e.code in (401, 403):
                raise OvhError(f"{self.label} refuse l'identifiant ou le mot de passe" if e.code == 401 else f"{self.label} refuse cette opération (droits insuffisants)", 422)
            if e.code == 404:
                raise OvhError(f"L'API de {self.label} est introuvable à cette adresse : vérifiez l'adresse et le port de son interface", 422)
            if 300 <= e.code < 400:
                raise OvhError("Cette adresse redirige ailleurs (souvent http vers https) : indiquez l'adresse finale", 422)
            raise OvhError(f"{self.label} a répondu {e.code}", 502 if e.code >= 500 else 422)
        except ssl.SSLError:
            raise OvhError(cert_hint, 422)
        except (urllib.error.URLError, OSError) as e:
            if isinstance(getattr(e, "reason", e), ssl.SSLError):
                raise OvhError(cert_hint, 422)
            raise OvhError(f"{self.label} est injoignable à cette adresse depuis ce serveur (adresse, port, pare-feu ou réseau Docker)", 502)
        if not raw.strip():
            return code, None
        try:
            return code, json.loads(raw)
        except ValueError:
            raise OvhError(f"Réponse inattendue : ce n'est pas l'API de {self.label} (adresse ou port incorrect ?)", 422)

    # --- à fournir par chaque serveur
    def _pairs(self) -> list[tuple[str, str]]:
        raise NotImplementedError

    def _add(self, domain: str, answer: str) -> None:
        raise NotImplementedError

    def _remove(self, domain: str, answer: str) -> None:
        raise NotImplementedError

    def test(self) -> dict:
        raise NotImplementedError

    # --- l'interface « enregistrements » (commune)
    def _list(self, force: bool = False) -> list[tuple[str, str]]:
        if self._cache and not force and time.time() - self._cache[0] < 15:
            return self._cache[1]
        pairs = [(d.lower().rstrip("."), a.strip()) for d, a in self._pairs() if d and a]
        self._cache = (time.time(), pairs)
        return pairs

    def zone_info(self, name: str) -> dict:
        return {"name": name}

    def host_status(self, host: str, zone: str) -> dict:
        return {"status": "local", "provider": self.provider}

    def records(self, zone: str, force: bool = False) -> list[dict]:
        zone = zone.lower()
        out = []
        for dom, ans in self._list(force):
            if dom == zone:
                sub = ""
            elif dom.endswith("." + zone):
                sub = dom[: -len(zone) - 1]
            else:
                continue
            t = _answer_type(ans)
            if t:
                out.append({"id": _rid(dom, ans), "fieldType": t, "subDomain": sub, "target": ans, "ttl": 0})
        out.sort(key=lambda r: (r["subDomain"] != "", r["subDomain"], r["fieldType"], r["target"]))
        return out

    def invalidate(self, zone: str, dirty: bool = True) -> None:
        self._cache = None

    def refresh(self, zone: str) -> None:
        self._cache = None

    def _prepare(self, zone: str, rec: dict) -> tuple[str, str, str]:
        t = (rec.get("fieldType") or "").upper()
        if t not in ADGUARD_TYPES:
            raise OvhError(f"{self.label} ne gère que les types A, AAAA et CNAME", 422)
        sub = (rec.get("subDomain") or "").strip().lower().rstrip(".")
        if sub and not _SUB_OK.match(sub):
            raise OvhError("Sous-domaine invalide", 422)
        if not self.wildcards and "*" in sub:
            raise OvhError(f"{self.label} ne gère pas les jokers (*) : créez chaque nom séparément", 422)
        target = (rec.get("target") or "").strip()
        try:
            if t == "A":
                target = str(ipaddress.IPv4Address(target))
            elif t == "AAAA":
                target = str(ipaddress.IPv6Address(target))
            else:
                target = clean_hostname(target) or ""
                if not target:
                    raise ValueError
        except ValueError:
            raise OvhError({"A": "Adresse IPv4 invalide (ex. 192.168.1.10)", "AAAA": "Adresse IPv6 invalide", "CNAME": "Nom cible invalide (ex. cible.exemple.fr)"}[t], 422)
        return (f"{sub}.{zone}" if sub else zone).lower(), target, t

    def create(self, zone: str, rec: dict) -> dict:
        dom, target, t = self._prepare(zone, rec)
        if (dom, target) in self._list(True):
            raise OvhError(f"{dom} → {target} existe déjà", 409)
        self._add(dom, target)
        self._cache = None
        return {"id": _rid(dom, target), "fieldType": t, "subDomain": dom[: -len(zone) - 1] if dom != zone.lower() else "", "target": target, "ttl": 0}

    def _find(self, rid: int) -> tuple[str, str]:
        for dom, ans in self._list(True):
            if _rid(dom, ans) == rid:
                return dom, ans
        raise OvhError("Enregistrement introuvable (il a peut-être été modifié ailleurs)", 404)

    def update(self, zone: str, rid: int, rec: dict) -> None:
        old = self._find(rid)
        dom, target, _ = self._prepare(zone, rec)
        if (dom, target) != old and (dom, target) in self._list():
            raise OvhError(f"{dom} → {target} existe déjà", 409)
        self._remove(*old)
        try:
            self._add(dom, target)
        except OvhError:
            self._add(*old)   # on remet l'ancien : jamais de trou
            raise
        self._cache = None

    def delete(self, zone: str, rid: int) -> None:
        self._remove(*self._find(rid))
        self._cache = None


class AdGuardService(_LocalServer):
    """AdGuard Home : ses « réécritures DNS » (nom -> adresse ou alias). Authentification HTTP basique."""
    provider = "adguard"
    label = "AdGuard"

    def __init__(self, cred) -> None:
        super().__init__(cred)
        self._raw: dict[tuple[str, str], dict] = {}

    def _call(self, method: str, path: str, body=None):
        from .crypto import dec
        auth = base64.b64encode(f"{dec(self.cred['app_key'])}:{dec(self.cred['secret'])}".encode()).decode()
        return self._http(method, path, body, {"Authorization": "Basic " + auth})[1]

    def _pairs(self):
        data = self._call("GET", "/control/rewrite/list")
        if not isinstance(data, list):
            raise OvhError("Réponse inattendue : ce n'est pas l'API d'AdGuard Home (adresse ou port incorrect ?)", 422)
        self._raw = {((i.get("domain") or "").lower().rstrip("."), (i.get("answer") or "").strip()): i for i in data}
        return [(i.get("domain") or "", i.get("answer") or "") for i in data]

    def _add(self, domain, answer):
        self._call("POST", "/control/rewrite/add", {"domain": domain, "answer": answer})

    def _remove(self, domain, answer):
        self._call("POST", "/control/rewrite/delete", self._raw.get((domain, answer)) or {"domain": domain, "answer": answer})   # l'entrée telle qu'AdGuard l'a donnée

    def test(self) -> dict:
        n = len(self._list(True))
        version = None
        try:
            st = self._call("GET", "/control/status")
            version = st.get("version") if isinstance(st, dict) else None
        except OvhError:
            pass
        return {"rewrites": n, "version": version}


class PiHoleService(_LocalServer):
    """Pi-hole 6 : « DNS local » (dns.hosts : « IP nom ») et enregistrements CNAME (dns.cnameRecords : « nom,cible »).
    Connexion par mot de passe -> identifiant de session (conservé quelques minutes, renouvelé tout seul). Pas de joker."""
    provider = "pihole"
    label = "Pi-hole"
    wildcards = False

    def __init__(self, cred) -> None:
        super().__init__(cred)
        self._sid: tuple[float, str] | None = None
        self._hosts_raw: list[str] = []
        self._cnames_raw: list[str] = []

    def _login(self) -> str:
        from .crypto import dec
        _, data = self._http("POST", "/api/auth", {"password": dec(self.cred["secret"])})
        sess = (data or {}).get("session") if isinstance(data, dict) else None
        if not isinstance(sess, dict):
            raise OvhError("Réponse inattendue : ce n'est pas l'API de Pi-hole 6 (adresse ou port incorrect, ou Pi-hole 5 ?)", 422)
        if not sess.get("valid") or not sess.get("sid"):
            if sess.get("totp"):
                raise OvhError("Ce Pi-hole demande un code à deux facteurs : créez un « mot de passe d'application » dans ses réglages et utilisez-le ici", 422)
            raise OvhError("Pi-hole refuse le mot de passe", 422)
        self._sid = (time.time() + max(30, int(sess.get("validity") or 300) - 30), sess["sid"])
        return sess["sid"]

    def _call(self, method: str, path: str, body=None, _retry: bool = True):
        if not self._sid or self._sid[0] < time.time():
            self._login()
        sid = self._sid[1]
        try:
            return self._http(method, path, body, {"X-FTL-SID": sid, "sid": sid})[1]
        except OvhError as e:
            if e.status == 422 and "refuse l'identifiant" in str(e) and _retry:   # session expirée côté Pi-hole : on se reconnecte une fois
                self._sid = None
                return self._call(method, path, body, False)
            raise

    @staticmethod
    def _q(element: str) -> str:
        return urllib.parse.quote(element, safe="")

    def _pairs(self):
        hosts = (self._call("GET", "/api/config/dns/hosts") or {}).get("config", {}).get("dns", {}).get("hosts")
        cnames = (self._call("GET", "/api/config/dns/cnameRecords") or {}).get("config", {}).get("dns", {}).get("cnameRecords")
        if not isinstance(hosts, list) or not isinstance(cnames, list):
            raise OvhError("Réponse inattendue : ce n'est pas l'API de Pi-hole 6", 422)
        self._hosts_raw, self._cnames_raw = [str(h) for h in hosts], [str(c) for c in cnames]
        out = []
        for line in self._hosts_raw:
            ip, *names = line.split()
            out += [(n, ip) for n in names]
        for line in self._cnames_raw:
            name, target = (line.split(",") + ["", ""])[:2]   # « nom,cible » ou « nom,cible,ttl »
            out.append((name.strip(), target.strip()))
        return out

    def _add(self, domain, answer):
        if _answer_type(answer) == "CNAME":
            self._call("PUT", "/api/config/dns/cnameRecords/" + self._q(f"{domain},{answer}"))
        else:
            self._call("PUT", "/api/config/dns/hosts/" + self._q(f"{answer} {domain}"))

    def _remove(self, domain, answer):
        if _answer_type(answer) == "CNAME":
            line = next((c for c in self._cnames_raw if [x.strip() for x in c.split(",")][:2] == [domain, answer]), f"{domain},{answer}")
            self._call("DELETE", "/api/config/dns/cnameRecords/" + self._q(line))
            return
        line = next((h for h in self._hosts_raw if h.split()[:1] == [answer] and domain in h.split()[1:]), None)
        if line is None:
            return   # déjà absent
        names = [n for n in line.split()[1:] if n != domain]
        self._call("DELETE", "/api/config/dns/hosts/" + self._q(line))
        if names:   # la ligne portait plusieurs noms : on remet les autres
            self._call("PUT", "/api/config/dns/hosts/" + self._q(f"{answer} {' '.join(names)}"))

    def test(self) -> dict:
        self._sid = None
        n = len(self._list(True))
        return {"rewrites": n, "version": None}


class LocalService:
    provider = "local"
    supports_records = False

    def __init__(self, cred) -> None:
        self.cred = dict(cred)
        self.dirty: set[str] = set()

    def zone_info(self, name: str) -> dict:
        return {"name": name}

    def host_status(self, host: str, zone: str) -> dict:
        return {"status": "local", "provider": "local"}


_MOCK: dict[str, dict] = {}   # OVH_MOCK : DuckDNS en mémoire, pour les tests


class DuckDnsService:
    provider = "duckdns"
    supports_records = False

    def __init__(self, cred) -> None:
        self.cred = dict(cred)
        self.dirty: set[str] = set()
        self._ips: dict[str, tuple[float, dict]] = {}

    @property
    def token(self) -> str:
        from .crypto import dec
        return dec(self.cred["secret"])

    def zone_info(self, name: str) -> dict:
        return {"name": name}

    def current_ip(self, name: str) -> dict:
        """Adresses actuelles du nom, telles que ce serveur les résout (DuckDNS ne permet pas de les relire autrement)."""
        hit = self._ips.get(name)
        if hit and time.time() - hit[0] < 30:
            return hit[1]
        out = {"v4": [], "v6": []}
        if settings.ovh_mock:
            m = _MOCK.get(name) or {}
            out = {"v4": [m["ip"]] if m.get("ip") and ":" not in m["ip"] else [], "v6": [m["ip"]] if m.get("ip") and ":" in m["ip"] else []}
        else:
            try:
                for fam, a in ((socket.AF_INET, "v4"), (socket.AF_INET6, "v6")):
                    try:
                        out[a] = sorted({i[4][0] for i in socket.getaddrinfo(name, None, fam, socket.SOCK_STREAM)})
                    except socket.gaierror:
                        pass
            except OSError:
                pass
        self._ips[name] = (time.time(), out)
        return out

    def host_status(self, host: str, zone: str) -> dict:
        ips = self.current_ip(zone)
        ip = (ips["v4"] or ips["v6"] or [None])[0]
        return {"status": "managed", "provider": "duckdns", "ip": ip}

    def update(self, name: str, ip: str | None = None, txt: str | None = None, clear_txt: bool = False) -> None:
        """ip=None : DuckDNS prend l'adresse publique de ce serveur (c'est ce que veut « utiliser l'IP de ce serveur »)."""
        if not name.endswith(DUCK_SUFFIX) or not _DUCK_LABEL.match(name[: -len(DUCK_SUFFIX)]):
            raise OvhError("Nom DuckDNS invalide", 422)
        params = {"domains": name[: -len(DUCK_SUFFIX)], "token": self.token}
        if ip:
            try:
                addr = ipaddress.ip_address(ip.strip())
            except ValueError:
                raise OvhError("Adresse IP invalide", 422)
            params["ipv6" if addr.version == 6 else "ip"] = str(addr)
        if txt is not None:
            if len(txt) > 255 or any(ord(c) < 32 for c in txt):
                raise OvhError("Valeur TXT invalide", 422)
            params["txt"] = txt
        if clear_txt:
            params["clear"] = "true"
        if settings.ovh_mock:
            if self.token.startswith("bad"):
                raise OvhError("DuckDNS a refusé la requête (jeton ou nom invalide)", 403)
            m = _MOCK.setdefault(name, {})
            if ip:
                m["ip"] = str(addr)
            if txt is not None:
                m["txt"] = txt
            if clear_txt:
                m["txt"] = ""
        else:
            url = "https://www.duckdns.org/update?" + urllib.parse.urlencode(params)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "armadok"}), timeout=10) as r:
                    answer = r.read(64).decode("ascii", "ignore").strip()
            except Exception:  # le jeton est dans l'URL : on ne remonte jamais le détail de l'erreur
                raise OvhError("DuckDNS est injoignable", 502)
            if not answer.startswith("OK"):
                raise OvhError("DuckDNS a refusé la mise à jour (jeton ou nom invalide)", 403)
        self._ips.pop(name, None)

    def txt_of(self, name: str) -> str | None:
        return (_MOCK.get(name) or {}).get("txt") if settings.ovh_mock else None


def new_service(row):
    p = row["provider"] if "provider" in row.keys() else "ovh"
    if p == "duckdns":
        return DuckDnsService(row)
    if p == "adguard":
        return AdGuardService(row)
    if p == "pihole":
        return PiHoleService(row)
    if p == "local":
        return LocalService(row)
    return None  # ovh : construit par ovh_client (évite un import circulaire)
