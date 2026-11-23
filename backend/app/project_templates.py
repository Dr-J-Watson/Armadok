"""Modèles de projet : un dossier de départ (docker-compose.yml, .env, autres petits fichiers) qu'on peut recréer en quelques clics.

Un modèle = des fichiers texte. Ses VARIABLES sont les clés de son `.env` : à la création, la personne les remplit (valeur de départ proposée, champ
masqué et bouton « générer » pour les secrets) ; le compose y renvoie déjà par ${NOM}. Dans les fichiers, `{{name}}` est remplacé par le nom du dossier
du projet (pour un container_name, un label…). Les modèles enregistrés sont chiffrés en base (un .env peut contenir des secrets) ; quelques modèles
intégrés sont fournis. Fonctions pures, sans accès à la base."""
import re

MAX_FILES = 60
MAX_FILE = 256 * 1024
MAX_TOTAL = 1024 * 1024
SECRET_KEY = re.compile(r"(PASS|SECRET|TOKEN|KEY|PWD|CREDENTIAL|DSN|CONNECTION_?STRING)", re.I)
URL_SECRET = re.compile(r"://[^/\s:@]*:[^/\s@]+@")   # postgres://app:motdepasse@db/app : un identifiant dans l'URL, quel que soit le nom de la clé


def is_secret(key: str, value: str = "") -> bool:
    return bool(SECRET_KEY.search(key) or URL_SECRET.search(value))
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")

BUILTIN = [
    {
        "id": "builtin:base", "name": "Modèle de base", "description": "Un service qui lit ses réglages dans le .env (image, nom, port).", "builtin": True,
        "files": {
            "docker-compose.yml": "# Modèle de base : un service qui lit ses réglages dans le .env.\nservices:\n  app:\n    image: ${APP_IMAGE}\n    container_name: ${APP_NAME}\n    restart: unless-stopped\n    env_file: .env\n    ports:\n      - \"${APP_PORT}:80\"\n    # Pour le publier avec Traefik (réseau partagé « proxy »), décommentez :\n    # networks: [proxy]\n    # labels:\n    #   - traefik.enable=true\n    #   - traefik.http.routers.{{name}}.rule=Host(`{{name}}.exemple.fr`)\n    #   - traefik.http.services.{{name}}.loadbalancer.server.port=80\n\n# networks:\n#   proxy:\n#     external: true\n",
            ".env": "# Image à lancer\nAPP_IMAGE=nginx:alpine\n# Nom du conteneur\nAPP_NAME={{name}}\n# Port publié sur la machine\nAPP_PORT=8080\n",
        },
    },
    {
        "id": "builtin:minecraft", "name": "Serveur Minecraft (exemple)", "description": "Un serveur Minecraft Java (image itzg/minecraft-server) : version, mémoire, port, mot de passe d'administration.", "builtin": True,
        "files": {
            "docker-compose.yml": "# Serveur Minecraft Java. Le monde est gardé dans ./data (à sauvegarder).\nservices:\n  mc:\n    image: itzg/minecraft-server:latest\n    container_name: ${MC_NAME}\n    restart: unless-stopped\n    tty: true\n    stdin_open: true\n    ports:\n      - \"${MC_PORT}:25565\"\n    environment:\n      EULA: \"${MC_EULA}\"\n      TYPE: ${MC_TYPE}\n      VERSION: ${MC_VERSION}\n      MEMORY: ${MC_MEMORY}\n      MOTD: ${MC_MOTD}\n      MAX_PLAYERS: ${MC_PLAYERS}\n      ENABLE_RCON: \"true\"\n      RCON_PASSWORD: ${MC_RCON_PASSWORD}\n    volumes:\n      - ./data:/data\n",
            ".env": "# J'accepte le contrat de licence de Minecraft (https://aka.ms/MinecraftEULA) : TRUE ou FALSE\nMC_EULA=FALSE\n# Nom du conteneur\nMC_NAME={{name}}\n# Port publié (celui de vos joueurs)\nMC_PORT=25565\n# Type de serveur : VANILLA, PAPER, FABRIC, FORGE…\nMC_TYPE=PAPER\n# Version de Minecraft (LATEST ou un numéro, ex. 1.21.4)\nMC_VERSION=LATEST\n# Mémoire du serveur\nMC_MEMORY=2G\n# Message affiché dans la liste des serveurs\nMC_MOTD=Un serveur Armadok\n# Nombre maximum de joueurs\nMC_PLAYERS=20\n# Mot de passe d'administration à distance (RCON)\nMC_RCON_PASSWORD=\n",
        },
    },
]


def parse_env(text: str) -> list[dict]:
    """Variables d'un .env, dans l'ordre : [{key, value, help, secret}]. L'aide est le commentaire placé juste au-dessus."""
    out, prev = [], ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            prev = s.lstrip("#").strip()
            continue
        m = _ENV_LINE.match(line)
        if m:
            k, v = m.group(1), m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out.append({"key": k, "value": v, "help": prev[:200], "secret": is_secret(k, v)})
        prev = ""
    return out


def env_quote(v: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@%+=,-]*", v):
        return v
    if "'" not in v and "\n" not in v and "\r" not in v:
        return f"'{v}'"
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "") + '"'


def fill_env(text: str, values: dict[str, str]) -> str:
    """Réécrit le .env du modèle avec les valeurs saisies (commentaires et ordre gardés). Une clé absente de `values` garde sa valeur de départ."""
    out = []
    for line in text.splitlines():
        m = _ENV_LINE.match(line)
        if m and m.group(1) in values:
            out.append(f"{m.group(1)}={env_quote(str(values[m.group(1)]))}")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def blank_secrets(text: str, keys: set[str] | None = None, secrets_too: bool = True) -> str:
    """Vide la valeur des clés demandées (et, par défaut, de toutes celles qui ressemblent à un secret) : elles seront à saisir à la création."""
    out = []
    for line in text.splitlines():
        m = _ENV_LINE.match(line)
        if m and ((keys and m.group(1) in keys) or (secrets_too and is_secret(m.group(1), m.group(2).strip()))):
            out.append(f"{m.group(1)}=")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def validate_files(files: dict) -> dict[str, str]:
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise ValueError(f"Un modèle contient entre 1 et {MAX_FILES} fichiers")
    clean, total = {}, 0
    for rel, content in files.items():
        parts = str(rel).split("/")
        if not rel or str(rel).startswith("/") or "\\" in rel or len(rel) > 300 or len(parts) > 8 or any(p in ("", ".", "..", ".git") for p in parts):
            raise ValueError(f"Chemin invalide : {str(rel)[:80]}")
        if not isinstance(content, str) or len(content.encode()) > MAX_FILE:
            raise ValueError(f"« {rel} » : contenu invalide ou trop gros ({MAX_FILE // 1024} Ko au plus)")
        total += len(content.encode())
        clean[str(rel)] = content
    if total > MAX_TOTAL:
        raise ValueError("Ce modèle est trop gros (1 Mo au plus)")
    if not any(re.fullmatch(r"(docker-)?compose([._-][A-Za-z0-9._-]*)?\.ya?ml", p) for p in clean):
        raise ValueError("Un modèle doit contenir au moins un fichier compose (docker-compose.yml)")
    return clean


def variables(files: dict[str, str]) -> list[dict]:
    """Les champs à remplir à la création : les clés du .env. Le défaut d'un secret n'est jamais renvoyé s'il est vide ; `gen` propose d'en générer un."""
    env = files.get(".env", "")
    return [{"key": v["key"], "default": v["value"], "help": v["help"], "secret": v["secret"]} for v in parse_env(env)]


def render(files: dict[str, str], name: str, values: dict[str, str]) -> dict[str, str]:
    """Les fichiers du nouveau projet : `{{name}}` remplacé partout, le .env rempli avec les valeurs saisies."""
    out = {}
    for rel, content in files.items():
        txt = content.replace("{{name}}", name)
        if rel == ".env":
            txt = fill_env(txt, {k: v for k, v in values.items() if isinstance(v, str)})
        out[rel] = txt
    return out
