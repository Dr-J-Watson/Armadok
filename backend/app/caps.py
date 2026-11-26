"""Droits fins d'un membre sur un nœud.

Un RÔLE (propriétaire, administrateur, éditeur, lecteur, domaines) est un modèle : un ensemble de droits de départ. On peut ensuite,
par membre, ACCORDER un droit en plus (éventuellement pour une durée limitée) ou en RETIRER un. Les droits réellement appliqués
(« effectifs ») = modèle du rôle + accordés − retirés, en tenant compte des dépendances (« requires »).

Les droits stockés pour un membre : {"grant": {droit: expiration|null}, "deny": {droit: expiration|null}}.
Les droits propres au propriétaire (transférer, supprimer le nœud) ne se délèguent jamais."""
import json
import time

# (clé, libellé, aide, dépendances)
_G = []   # [(id, libellé, [(clé, libellé, aide, requires)])]


def _group(gid: str, label: str, caps: list[tuple]) -> None:
    _G.append((gid, label, caps))


_group("scope", "Portée sur Traefik", [
    ("traefik.all", "Voir et agir sur tout Traefik", "Sans ce droit, seuls les sites des domaines de la personne sont visibles et modifiables (avec les restrictions de cibles).", ()),
])
_group("sites", "Sites (routeur + service)", [
    ("sites.view", "Voir les sites", "Liste des sites, leur état et leurs protections.", ()),
    ("sites.create", "Créer des sites", "Ajouter un site (routeur et service).", ("sites.view",)),
    ("sites.edit", "Modifier des sites", "Changer l'adresse, les protections, la destination.", ("sites.view",)),
    ("sites.delete", "Supprimer des sites", "Retirer définitivement un site.", ("sites.view",)),
    ("sites.toggle", "Mettre en pause / réactiver des sites", "Couper un site sans le supprimer, ou tous les sites d'un domaine.", ("sites.view",)),
])
for _id, _label, _what in (("middlewares", "Middlewares (protections)", "middlewares"), ("services", "Services", "services"),
                           ("transports", "Transports serveur", "transports"), ("tls", "Options et stores TLS", "réglages TLS")):
    _group(_id, _label, [
        (f"{_id}.view", f"Voir les {_what}", f"Lister les {_what} et leur configuration.", ("traefik.all",)),
        (f"{_id}.create", f"Créer des {_what}", f"Ajouter de nouveaux {_what}.", (f"{_id}.view",)),
        (f"{_id}.edit", f"Modifier des {_what}", f"Changer la configuration (et renommer : les références suivent).", (f"{_id}.view",)),
        (f"{_id}.delete", f"Supprimer des {_what}", "Suppression définitive.", (f"{_id}.view",)),
        (f"{_id}.toggle", f"Désactiver / réactiver des {_what}", "Mise en pause réversible (les éléments qui en dépendent suivent).", (f"{_id}.view",)),
    ])
_group("files", "Fichiers YAML bruts", [
    ("files.view", "Voir et télécharger les fichiers", "Lire le contenu des fichiers de Traefik.", ("traefik.all",)),
    ("files.edit", "Modifier des fichiers", "Enregistrer le contenu d'un fichier (peut tout changer : à réserver aux administrateurs).", ("files.view",)),
    ("files.create", "Créer et importer des fichiers", "Nouveau fichier ou import depuis l'ordinateur.", ("files.view",)),
    ("files.delete", "Supprimer des fichiers et dossiers", "Suppression d'un fichier (une copie reste dans l'historique).", ("files.view",)),
    ("files.move", "Déplacer / renommer", "Fichiers et dossiers.", ("files.view",)),
    ("files.toggle", "Désactiver / réactiver un fichier", "Traefik ne lit plus le fichier désactivé.", ("files.view",)),
    ("files.reorganize", "Ranger automatiquement", "Assistant de rangement : il déplace et réécrit plusieurs fichiers d'un coup.", ("files.edit", "files.create", "files.delete", "files.move")),
    ("history.view", "Voir l'historique des fichiers", "Versions précédentes et comparaison.", ("files.view",)),
])
_group("certs", "Certificats et état", [
    ("certs.view", "Voir les certificats", "Échéances et sites sans certificat (limité aux domaines de la personne si elle ne voit pas tout Traefik).", ()),
    ("certs.manage", "Supprimer, renouveler et redemander des certificats", "Retirer un certificat d'acme.json (Traefik est arrêté puis relancé quelques secondes), le faire renouveler, ou redemander celui d'un site qui n'en a pas. L'interface ne lit jamais les clés.", ("certs.view", "traefik.all")),
])
_group("obs", "Surveillance (métriques, alertes, adresses IP)", [
    ("metrics.view", "Voir les métriques (Prometheus)", "Trafic, erreurs, lenteur, connexions, par site : lecture seule.", ("traefik.all",)),
    ("metrics.explore", "Interroger Prometheus librement (PromQL)", "Explorateur de requêtes : montre tout ce que Prometheus connaît, pas seulement Traefik.", ("metrics.view",)),
    ("alerts.view", "Voir les alertes", "Alertes en cours et historique.", ("traefik.all",)),
    ("alerts.manage", "Régler les alertes", "Activer, changer les seuils et la durée, choisir où prévenir (webhook).", ("alerts.view",)),
    ("security.view", "Voir l'activité des adresses IP", "Qui se connecte et qui insiste (erreurs, chemins testés). Les adresses sont masquées sans le droit ci-dessous.", ("traefik.all",)),
    ("security.ips.reveal", "Voir les adresses IP en clair", "Sans ce droit, les adresses sont masquées (203.0.xxx.xxx).", ("security.view",)),
    ("security.ban", "Bannir, débannir et valider des adresses IP", "Bloque une adresse (ou un réseau) sur tous les sites de ce nœud, ou la « valide » : une adresse reconnue n'est plus marquée suspecte ni bannissable.", ("security.view",)),
    ("security.automod", "Gérer l'automod (blocage automatique)", "Créer des règles qui bannissent toutes seules les adresses qui remplissent des conditions (pays, suspecte, type d'attaque…). Une règle trop large peut bloquer de vrais visiteurs : à réserver aux personnes de confiance.", ("security.ban",)),
])
_group("dns", "DNS (sur les domaines auxquels la personne a accès)", [
    ("dns.view", "Voir les domaines et enregistrements", "Liste des zones, enregistrements, SOA, état DNSSEC.", ()),
    ("dns.records.create", "Créer des enregistrements", "", ("dns.view",)),
    ("dns.records.edit", "Modifier des enregistrements", "", ("dns.view",)),
    ("dns.records.delete", "Supprimer des enregistrements", "", ("dns.view",)),
    ("dns.publish", "Publier la zone (« Appliquer »)", "Rafraîchir la zone chez le fournisseur : rend les changements visibles.", ("dns.view",)),
    ("dns.provider", "Mettre à jour l'adresse (DuckDNS)", "Adresse IP et texte TXT d'un domaine DuckDNS.", ("dns.view",)),
    ("dns.export", "Exporter une zone", "Télécharger le texte de la zone.", ("dns.view",)),
    ("dns.import", "Importer une zone", "Remplace des enregistrements depuis un texte de zone.", ("dns.view",)),
    ("dns.history.view", "Voir l'historique de la zone", "", ("dns.view",)),
    ("dns.history.restore", "Restaurer une version de la zone", "", ("dns.history.view",)),
    ("dns.dnssec.view", "Voir DNSSEC", "", ("dns.view",)),
    ("dns.dnssec.edit", "Activer / désactiver DNSSEC", "", ("dns.dnssec.view",)),
    ("dns.share", "Partager l'accès à un domaine", "Donner ou retirer l'accès d'autres personnes à un domaine dont on est gestionnaire.", ("dns.view",)),
])
_group("node", "Administration du nœud", [
    ("node.settings", "Modifier les réglages", "Nom, description, points d'entrée, résolveurs, cible DNS.", ()),
    ("node.members.view", "Voir les membres", "", ()),
    ("node.members.add", "Inviter des membres", "Dans la limite des droits qu'on a soi-même.", ("node.members.view",)),
    ("node.members.edit", "Modifier les droits des membres", "Rôle, restrictions, droits fins : jamais plus que les siens.", ("node.members.view",)),
    ("node.members.remove", "Retirer des membres", "", ("node.members.view",)),
    ("node.connection", "Gérer la connexion du worker", "Jeton d'enrôlement, reconnexion, révocation.", ()),
    ("node.audit", "Voir l'activité du nœud", "Journal des actions faites sur ce nœud.", ()),
    ("node.domains.view", "Voir les domaines du nœud", "", ()),
    ("node.domains.link", "Rattacher / détacher des domaines", "", ("node.domains.view",)),
    ("node.backup", "Sauvegarder le nœud", "Créer, télécharger et téléverser des sauvegardes des fichiers du nœud (configuration Traefik, historiques).", ()),
    ("node.restore", "Restaurer une sauvegarde du nœud", "Remplace des fichiers du nœud par ceux d'une sauvegarde : à réserver au propriétaire.", ("node.backup",)),
    ("node.transfer", "Transférer le nœud", "Réservé au propriétaire.", ()),
    ("node.delete", "Supprimer le nœud", "Réservé au propriétaire.", ()),
])

_group("docker", "Docker (conteneurs, images, réseaux, volumes) : n'existe que sur un nœud qui gère Docker", [
    ("docker.view", "Voir les conteneurs, images, réseaux et volumes", "Liste et détail (les valeurs des variables d'environnement ne sont jamais montrées).", ()),
    ("docker.logs", "Lire les journaux des conteneurs", "Les journaux peuvent contenir des informations sensibles.", ("docker.view",)),
    ("docker.control", "Démarrer, arrêter, redémarrer des conteneurs", "Coupe ou relance des services : à réserver aux personnes de confiance.", ("docker.view",)),
    ("docker.remove", "Supprimer et nettoyer (conteneurs, images, réseaux, volumes)", "Suppression définitive, y compris le nettoyage des éléments inutilisés. Supprimer un volume détruit ses données.", ("docker.view",)),
    ("docker.exec", "Ouvrir un terminal dans un conteneur", "Équivaut à un accès administrateur au conteneur : le droit le plus sensible. Jamais donné par défaut, même aux administrateurs.", ("docker.logs",)),
])
_group("projects", "Projets Compose", [
    ("projects.view", "Voir les projets", "Liste des projets, leurs services, leur état et leurs statistiques.", ("docker.view",)),
    ("projects.import", "Importer, ignorer et retirer des projets", "Choisir les dossiers que l'interface gère (rien n'est déplacé ni modifié sur le disque).", ("projects.view",)),
    ("projects.deploy", "Déployer (up, down, redémarrer, pull, build)", "Lance docker compose sur le projet.", ("projects.view", "docker.control")),
    ("projects.files", "Voir les fichiers du projet", "docker-compose.yml, Dockerfiles et fichiers du dossier (le .env demande son propre droit).", ("projects.view",)),
    ("projects.edit", "Modifier les fichiers du projet", "Peut tout changer dans le déploiement : à réserver aux administrateurs.", ("projects.files",)),
    ("projects.nolimit", "Transferts et suppressions sans limite de taille", "Sans ce droit : 500 fichiers par import, 100 Mo par fichier ou par téléchargement, suppression d'un dossier jusqu'à 300 fichiers ou 5 Mo. Pour un gestionnaire du nœud.", ("projects.files",)),
    ("projects.env", "Voir et modifier le .env du projet", "Le .env contient souvent des mots de passe et des clés.", ("projects.files",)),
    ("projects.git", "Gérer Git et le suivi des images", "Lier un dépôt, récupérer les changements, surveiller les nouvelles versions d'images.", ("projects.view",)),
    ("projects.rename", "Renommer un projet (nom affiché)", "Le nom montré dans l'interface. Rien ne change sur le disque ni pour Compose.", ("projects.view",)),
    ("projects.folder", "Renommer le dossier du projet sur le disque", "Déplace le dossier au même endroit sous un autre nom ; le nom du projet pour Compose peut en dépendre (ses volumes ne seraient plus repris).", ("projects.rename", "projects.deploy")),
    ("projects.delete", "Supprimer un projet et ses fichiers", "Efface le dossier du projet sur le disque : irréversible.", ("projects.deploy", "projects.files")),
])

GROUPS = [(gid, label, [c[0] for c in caps]) for gid, label, caps in _G]
CATALOG: dict[str, dict] = {c[0]: {"label": c[1], "hint": c[2], "requires": list(c[3]), "group": gid}
                            for gid, _l, caps in _G for c in caps}
ALL = frozenset(CATALOG)
OWNER_ONLY = frozenset({"node.transfer", "node.delete"})   # jamais délégués
NOT_BY_DEFAULT = frozenset({"docker.exec", "projects.delete", "node.restore"})   # pas même pour un administrateur : seul le propriétaire les a d'office, un autre doit se les voir accorder

# Un invité d'un hub lié n'a JAMAIS ces droits, quoi qu'on lui attribue : tout ce qui touche à la propriété du nœud, à ses membres, à sa connexion, à ses secrets,
# à ses sauvegardes et à l'exécution de commandes reste réservé aux comptes de CE hub. (Zéro confiance : l'autre hub n'est pas là pour répondre de ce qui se passe ici.)
GUEST_FORBIDDEN = frozenset(OWNER_ONLY | NOT_BY_DEFAULT | {
    "node.settings", "node.connection", "node.members.view", "node.members.add", "node.members.edit", "node.members.remove", "node.domains.link",
    "node.audit", "node.backup", "node.restore", "docker.exec", "projects.delete", "projects.env", "projects.rename", "projects.folder", "dns.provider", "dns.share", "dns.import", "alerts.manage",
})

_SITES = {k for k in ALL if k.startswith("sites.")}
_DNS_ALL = {k for k in ALL if k.startswith("dns.")}
_DNS_READ = {"dns.view", "dns.export", "dns.history.view", "dns.dnssec.view"}

# Modèles de rôle : exactement les droits qu'avaient les rôles avant les droits fins (rien ne change à la mise à jour).
PRESETS: dict[str, frozenset] = {
    "owner": ALL,
    "admin": ALL - OWNER_ONLY - NOT_BY_DEFAULT,
    "editor": frozenset(_SITES | {"certs.view"} | _DNS_ALL - {"dns.share"} | {"dns.share"}),
    "viewer": frozenset({"sites.view", "certs.view"} | _DNS_READ),
    "dns": frozenset(_DNS_ALL),
}
# L'éditeur d'avant n'avait pas accès à l'administration du nœud, ni aux fichiers : c'est déjà le cas ci-dessus.


# Droits dont l'abus fait le plus de dégâts : proposés d'un clic quand on exige la double authentification (A2F) sur un nœud.
MFA_SENSITIVE = frozenset({
    "certs.manage",                                                              # retirer / renouveler des certificats (arrête Traefik)
    "security.ban", "security.automod",                                          # bloquer des adresses, règles automatiques
    "files.edit", "files.create", "files.delete", "files.move", "files.reorganize",   # fichiers YAML bruts : tout Traefik
    "node.settings", "node.connection", "node.members.add", "node.members.edit", "node.members.remove",
    "node.domains.link", "node.transfer", "node.delete", "node.backup", "node.restore",
    "dns.provider", "dns.dnssec.edit", "dns.share", "dns.import",
    "alerts.manage",
    "docker.control", "docker.remove", "docker.exec",                           # couper des services, supprimer, terminal
    "projects.deploy", "projects.edit", "projects.env", "projects.delete", "projects.git", "projects.folder",
})


def parse_mfa(raw: str | None) -> frozenset:
    """Droits qui exigent la double authentification sur un nœud (JSON stocké) : inconnus écartés."""
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        data = []
    return frozenset(k for k in data if isinstance(k, str) and k in CATALOG) if isinstance(data, list) else frozenset()


def dump_mfa(keys) -> str:
    ks = sorted(k for k in keys if k in CATALOG)
    return json.dumps(ks) if ks else ""


def _clean(d) -> dict:
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            if k in CATALOG and (v is None or isinstance(v, (int, float))):
                out[k] = v
    return out


def parse(raw: str | None) -> dict:
    """Droits stockés d'un membre : {"grant": {...}, "deny": {...}} (valeurs : date d'expiration en secondes, ou null)."""
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return {"grant": _clean(data.get("grant")), "deny": _clean(data.get("deny"))}


def dump(perms: dict) -> str:
    g, d = _clean(perms.get("grant")), _clean(perms.get("deny"))
    return json.dumps({"grant": g, "deny": d}, sort_keys=True) if (g or d) else ""


def _live(d: dict, now: float) -> set[str]:
    return {k for k, until in d.items() if until is None or until > now}


def effective(role: str, perms: dict | None = None, now: float | None = None) -> frozenset:
    """Droits appliqués : modèle du rôle + accordés non expirés − retirés non expirés, puis dépendances."""
    now = time.time() if now is None else now
    perms = perms or {"grant": {}, "deny": {}}
    base = set(PRESETS.get(role, frozenset()))
    if role != "owner":   # le propriétaire garde tout ; les droits propres au propriétaire ne se délèguent pas
        base |= _live(perms.get("grant") or {}, now) - OWNER_ONLY
        base -= _live(perms.get("deny") or {}, now)
    changed = True
    while changed:   # un droit n'existe que si ce dont il dépend existe aussi
        changed = False
        for k in list(base):
            if any(r not in base for r in CATALOG[k]["requires"]):
                base.discard(k)
                changed = True
    return frozenset(base)


def closure(keys) -> frozenset:
    """Ensemble de droits cohérent : inconnus écartés, et chaque droit n'est gardé que si ce dont il dépend l'est aussi."""
    base = {k for k in keys if k in CATALOG}
    changed = True
    while changed:
        changed = False
        for k in list(base):
            if any(r not in base for r in CATALOG[k]["requires"]):
                base.discard(k)
                changed = True
    return frozenset(base)


effective_closure = closure


def next_expiry(perms: dict, after: float) -> float | None:
    """Prochaine échéance (après `after`) parmi les droits accordés ou retirés temporairement."""
    ts = [v for sect in ("grant", "deny") for v in (perms.get(sect) or {}).values() if v is not None and v > after]
    return min(ts) if ts else None


def catalog() -> dict:
    return {"groups": [{"id": gid, "label": label, "caps": [{"key": k, **CATALOG[k]} for k in keys]} for gid, label, keys in GROUPS],
            "presets": {r: sorted(c) for r, c in PRESETS.items()}, "ownerOnly": sorted(OWNER_ONLY), "mfaSensitive": sorted(MFA_SENSITIVE)}


def prefix_of(proto: str, kind: str) -> str:
    """Famille de droits d'un élément Traefik : « sites » (routeurs), « services », « middlewares », « transports », « tls »."""
    return {"routers": "sites", "services": "services", "middlewares": "middlewares", "serversTransports": "transports"}.get(kind, "tls")
