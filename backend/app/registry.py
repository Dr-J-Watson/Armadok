"""Suivi des images : une nouvelle version existe-t-elle au registre ? On compare l'empreinte (digest) du manifeste, sans rien télécharger.

Seuls les registres publics sont interrogés, sans identifiants : un registre privé, un nom inconnu ou une panne donnent « inconnu » (jamais une fausse alerte).
Garde-fou : on n'appelle jamais une adresse privée ou locale (un fichier compose ne doit pas pouvoir faire sonder le réseau interne par le serveur)."""
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

ACCEPT = ", ".join(("application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"))
HUB = {"docker.io", "index.docker.io", "registry-1.docker.io", "registry.hub.docker.com"}
TIMEOUT = 8
_REF = re.compile(r"^(?:(?P<host>[A-Za-z0-9.-]+(?::\d+)?)/)?(?P<repo>[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*)(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?(?:@(?P<digest>sha256:[0-9a-f]{64}))?$")


def parse_ref(image: str) -> dict | None:
    """« nginx », « user/app:1.2 », « ghcr.io/org/app:tag », « app@sha256:… » → {registry, repo, tag, digest, key}. None si le nom n'est pas un nom d'image
    (variable ${…} non résolue, par exemple)."""
    s = str(image or "").strip()
    if not s or "$" in s:
        return None
    m = _REF.match(s)
    if not m:
        return None
    host, repo = m.group("host"), m.group("repo")
    if host and "." not in host and ":" not in host and host != "localhost":   # « user/app » : c'est un dépôt Docker Hub, pas un hôte
        repo, host = f"{host}/{repo}", None
    registry = "docker.io" if (not host or host in HUB) else host
    if registry == "docker.io" and "/" not in repo:
        repo = "library/" + repo
    tag = m.group("tag") or ("latest" if not m.group("digest") else "")
    return {"registry": registry, "repo": repo, "tag": tag, "digest": m.group("digest") or "", "key": f"{registry}/{repo}:{tag or m.group('digest')}"}


def public_host(host: str) -> bool:
    """Le nom pointe-t-il uniquement vers des adresses publiques ?"""
    h = host.rsplit(":", 1)[0] if re.search(r":\d+$", host) else host
    try:
        infos = socket.getaddrinfo(h, None)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _http(method: str, url: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
    """Un appel HTTPS court. Ne suit pas les redirections. Renvoie (statut, en-têtes en minuscules, corps)."""
    req = urllib.request.Request(url, method=method, headers={"User-Agent": "armadok", **(headers or {})})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=TIMEOUT) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read(200_000)
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read(20_000)


def _token(challenge: str, repo: str) -> str | None:
    p = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    realm = p.get("realm", "")
    u = urllib.parse.urlparse(realm)
    if u.scheme != "https" or not u.hostname or not public_host(u.netloc):
        return None
    q = {"service": p.get("service", ""), "scope": p.get("scope") or f"repository:{repo}:pull"}
    st, _, body = _http("GET", realm + ("&" if "?" in realm else "?") + urllib.parse.urlencode(q))
    if st != 200:
        return None
    try:
        d = json.loads(body)
    except ValueError:
        return None
    return d.get("token") or d.get("access_token")


def remote_digest(ref: dict) -> tuple[str | None, str]:
    """(empreinte au registre, message d'erreur). Exactement l'un des deux est vide."""
    host = "registry-1.docker.io" if ref["registry"] == "docker.io" else ref["registry"]
    if not public_host(host):
        return None, "Registre privé ou injoignable depuis ce serveur : non vérifié"
    url = f"https://{host}/v2/{ref['repo']}/manifests/{ref['tag'] or 'latest'}"
    try:
        st, h, _ = _http("HEAD", url, {"Accept": ACCEPT})
        if st == 401 and "bearer" in h.get("www-authenticate", "").lower():
            tok = _token(h["www-authenticate"], ref["repo"])
            if not tok:
                return None, "Authentification au registre impossible (image privée ?)"
            st, h, _ = _http("HEAD", url, {"Accept": ACCEPT, "Authorization": f"Bearer {tok}"})
    except (OSError, ValueError):
        return None, "Registre injoignable"
    if st == 200 and h.get("docker-content-digest"):
        return h["docker-content-digest"], ""
    if st in (401, 403):
        return None, "Image privée : non vérifiée"
    if st == 404:
        return None, "Image ou version introuvable au registre"
    return None, f"Le registre a répondu {st}"


def local_info(ref: dict, images: list[dict]) -> dict | None:
    """L'image téléchargée localement qui porte ce nom : {id, digests: {empreintes du dépôt}} ; None si elle n'est pas là."""
    for img in images:
        for tag in img["tags"]:
            t = parse_ref(tag)
            if t and t["registry"] == ref["registry"] and t["repo"] == ref["repo"] and t["tag"] == ref["tag"]:
                ds = set()
                for d in img.get("digests") or []:
                    r, _, dig = d.partition("@")
                    x = parse_ref(r)
                    if x and x["registry"] == ref["registry"] and x["repo"] == ref["repo"]:
                        ds.add(dig)
                return {"id": img["id"], "digests": ds}
    return None


def status_of(ref: dict, local: dict | None, remote: str | None, error: str) -> str:
    """pinned | absent | latest (à jour) | update | unknown"""
    if ref["digest"]:
        return "pinned"
    if not local:
        return "absent"
    if remote and remote in local["digests"]:
        return "latest"
    if remote and local["digests"]:
        return "update"
    return "unknown"
