"""Configuration lue depuis l'environnement (supporte le suffixe _FILE pour les secrets Docker)."""
import base64
import binascii
import os
from pathlib import Path


def env(name: str, default: str | None = None) -> str | None:
    path = os.environ.get(f"{name}_FILE")
    if path:
        return Path(path).read_text().strip()
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _bool(name: str, default: bool) -> bool:
    v = env(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> list[str]:
    """Liste tolérante : virgules, points-virgules ou espaces ; guillemets et espaces parasites ignorés."""
    raw = (env(name, default) or "").replace(";", ",").replace(" ", ",")
    return [x.strip().strip("\"'") for x in raw.split(",") if x.strip().strip("\"'")]


class Settings:
    def __init__(self) -> None:
        self.jwt_secret = env("JWT_SECRET", "") or ""
        self.admin_user = env("ADMIN_USER", "admin") or "admin"
        self.admin_password_hash = env("ADMIN_PASSWORD_HASH")
        self._hash_error = ""
        if not self.admin_password_hash and env("ADMIN_PASSWORD_HASH_B64"):
            # Même hachage, encodé en base64 : le « $ » d'un hachage bcrypt est mal lu par certains éditeurs de variables (éditeurs de variables d'environnement…)
            try:
                self.admin_password_hash = base64.b64decode((env("ADMIN_PASSWORD_HASH_B64") or "").strip(), validate=True).decode().strip()
            except (binascii.Error, ValueError):
                self._hash_error = "ADMIN_PASSWORD_HASH_B64 n'est pas du base64 valide (générez-le avec : printf '%s' '$2b$…' | base64 -w0)."
        self.admin_password = env("ADMIN_PASSWORD")
        self.access_ttl = int(env("ACCESS_TOKEN_MINUTES", "15")) * 60
        self.refresh_ttl = int(env("REFRESH_TOKEN_DAYS", "7")) * 86400
        self.cookie_secure = _bool("COOKIE_SECURE", True)

        self.dynamic_dir = Path(env("TRAEFIK_DYNAMIC_DIR", "/traefik-dynamic") or "")
        self.data_dir = Path(env("DATA_DIR", "/data") or "")
        self.traefik_api = (env("TRAEFIK_API_URL", "") or "").rstrip("/")
        self.entrypoints = _list("TRAEFIK_ENTRYPOINTS", "web,websecure")
        self.cert_resolvers = _list("TRAEFIK_CERT_RESOLVERS")
        self.certs_file = env("TRAEFIK_CERTS_FILE")  # acme.json lu directement (c'est le cas normal), ou un relevé sans clés produit par `python -m app.certsync` (nœuds distants)
        self.docker_socket = env("DOCKER_SOCKET", "/var/run/docker.sock") or ""   # pour redémarrer Traefik quand on retire un certificat
        self.traefik_container = env("TRAEFIK_CONTAINER", "traefik") or "traefik"
        # Gestion de Docker (conteneurs, projets Compose…) : DÉSACTIVÉE par défaut. Docker donne un accès équivalent à root sur la machine :
        # il faut la demander (DOCKER_MANAGE=true) ET monter le socket. Le seul socket monté (pour les certificats) ne l'active pas.
        self.docker_manage = _bool("DOCKER_MANAGE", False)
        # Dossiers où chercher les projets Compose (chemins ABSOLUS, montés IDENTIQUES à l'hôte pour que les chemins relatifs des compose restent valables).
        # C'est aussi la limite dure de ce que l'interface lit et écrit : rien hors de ces dossiers.
        self.project_roots = _list("PROJECT_ROOTS")
        self.image_check_hours = int(env("IMAGE_CHECK_HOURS", "24") or 0)   # vérification quotidienne des images des projets suivis ; 0 = jamais
        self.git_allow_local = _bool("GIT_ALLOW_LOCAL", False)   # autorise les dépôts « locaux » (chemin, file://) : pour les essais seulement

        self.prometheus_url = (env("PROMETHEUS_URL", "") or "").rstrip("/")   # valeur de départ du nœud Principal (ensuite : « Ce nœud »)
        self.access_log = env("TRAEFIK_ACCESS_LOG")   # journal d'accès de Traefik (JSON ou format commun), monté en lecture seule : sert à lister les adresses IP

        self.ovh_endpoint = env("OVH_ENDPOINT", "ovh-eu")
        self.ovh_app_key = env("OVH_APPLICATION_KEY")
        self.ovh_app_secret = env("OVH_APPLICATION_SECRET")
        self.ovh_consumer_key = env("OVH_CONSUMER_KEY")
        self.ovh_mock = _bool("OVH_MOCK", False)
        self.allow_loopback_dns = _bool("DNS_ALLOW_LOOPBACK", False)   # développement / tests seulement : autorise 127.0.0.1 pour un serveur DNS local
        self.default_target = env("DEFAULT_DNS_TARGET")

    @property
    def ovh_configured(self) -> bool:
        return self.ovh_mock or all([self.ovh_app_key, self.ovh_app_secret, self.ovh_consumer_key])

    def validate(self) -> None:
        if len(self.jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET est requis (32 caractères minimum). Générez-en un : openssl rand -hex 32")
        key = env("ENCRYPTION_KEY")
        if key is not None and len(key) < 32:
            raise RuntimeError("ENCRYPTION_KEY est trop courte (32 caractères minimum) : elle protège les clés OVH et les secrets 2FA. Générez-en une : openssl rand -hex 32")
        if self._hash_error:
            raise RuntimeError(self._hash_error)
        if not (self.admin_password_hash or self.admin_password):
            raise RuntimeError("Définissez ADMIN_PASSWORD_HASH (recommandé) ou ADMIN_PASSWORD.")
        if not self.dynamic_dir.is_dir():
            raise RuntimeError(f"Dossier Traefik introuvable : {self.dynamic_dir}")
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        uid, gid = os.getuid(), os.getgid()
        for label, path in (("des données", self.data_dir), ("de Traefik", self.dynamic_dir)):
            if not (path.is_dir() and os.access(path, os.W_OK | os.X_OK)):
                raise RuntimeError(
                    f"Le conteneur (UID {uid}:{gid}) ne peut pas écrire dans le dossier {label} ({path}).\n"
                    f"  → Sur l'hôte, donnez-lui ce dossier : sudo chown -R {uid}:{gid} <dossier monté sur {path}>\n"
                    f"  → ou faites correspondre UID/GID dans le .env au propriétaire du dossier (id -u ; id -g).\n"
                    f"  (Un dossier créé automatiquement par Docker appartient à root.)")

    def harden_data_dir(self) -> list[str]:
        """data/ (base, journal, sauvegardes) n'est lisible que par son propriétaire. Retourne les chemins non modifiables."""
        failed = []
        paths = [self.data_dir, *self.data_dir.rglob("*")]
        for p in paths:
            try:
                if p.is_symlink():
                    continue
                p.chmod(0o700 if p.is_dir() else 0o600)
            except OSError:
                failed.append(str(p))
        return failed


settings = Settings()
