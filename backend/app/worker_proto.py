"""Protocole hub ↔ worker, partagé par les deux côtés.

Le worker ouvre la connexion (WebSocket sortant) ; le hub envoie des requêtes {id, op, args} et reçoit des réponses.
  worker → hub : {type: hello, nodeId, version, protocol}
  hub → worker : {type: challenge, nonce}
  worker → hub : {type: auth, signature}      signature Ed25519 de auth_message(nodeId, nonce)
  hub → worker : {type: ready}
  ensuite      : hub → {type: request, id, op, args} ; worker → {type: response, id, ok, result | error} ; worker → {type: ping}
"""
PROTOCOL = 1
MAX_MESSAGE = 8 * 1024 * 1024   # taille maximale d'un message (octets)
MAX_FILE = 1_000_000            # taille maximale d'un fichier YAML écrit par le hub
MAX_FILES = 500                 # fichiers maximum dans le dossier dynamique d'un nœud
REQUEST_TIMEOUT = 25            # secondes
PING_EVERY = 20                 # le worker signale sa présence
IDLE_LIMIT = 75                 # le hub coupe un worker silencieux plus longtemps que ça
TOKEN_TTL = 15 * 60             # validité d'un jeton d'enrôlement
CLOSE_REPLACED, CLOSE_REVOKED, CLOSE_UNAUTHORIZED, CLOSE_TOO_MANY, CLOSE_SUSPENDED = 4000, 4403, 4401, 4429, 4430

# Un worker porte des MODULES : « traefik » (fichiers dynamiques, statut, certificats) et/ou « docker » (conteneurs, projets Compose…).
# Seules les opérations NOMMÉES de ses modules sont exécutées : le hub ne peut rien lui demander d'autre, jamais de commande libre.
MODULES = ("traefik", "docker")
OPS_BY_MODULE = {
    "traefik": ("items", "list_files", "list_dirs", "read", "write", "delete", "move", "history", "history_content",
                "upsert", "delete_item", "live", "mkdir", "rmdir", "move_dir", "reorganize", "toggle", "certs"),
    "docker": ("d_info", "d_containers", "d_container", "d_action", "d_remove_container", "d_logs", "d_stats", "d_images", "d_networks", "d_volumes",
               "d_remove_image", "d_remove_network", "d_remove_volume", "d_prune",
               "p_create", "p_make_targets", "p_exec_make_run", "p_info", "p_discover", "p_project", "p_tree", "p_read", "p_write", "p_delete_file", "p_mkdir", "p_move", "p_delete_dir", "p_history", "p_history_content", "p_validate", "p_compose", "p_job", "p_destroy",
               "p_git_status", "p_git_log", "p_git_run", "p_git_clone",
               "d_image", "d_network", "d_network_create", "d_network_connect", "d_network_disconnect", "d_volume", "d_exec_volls", "d_exec_volread",
               "d_exec_open", "d_exec_read", "d_exec_write", "d_exec_resize", "d_exec_close"),   # terminal : session tenue par le worker, refusé sans WORKER_ALLOW_EXEC   # exécutées par host_ops.handle (docker_ops + project_ops : même liste, vérifiée par un test)
}
OPS = tuple(op for ops in OPS_BY_MODULE.values() for op in ops)


def parse_modules(raw, default: tuple[str, ...] = ("traefik",)) -> tuple[str, ...]:
    """Modules d'une liste « traefik,docker » (ou d'une liste JSON) : connus seulement, sans doublon, dans l'ordre canonique.
    Une valeur vide ou sans aucun module connu donne `default`."""
    items = raw if isinstance(raw, (list, tuple)) else str(raw or "").replace(";", ",").split(",")
    wanted = {str(m).strip().lower() for m in items}
    found = tuple(m for m in MODULES if m in wanted)
    return found or tuple(default)


def ops_for(modules) -> frozenset[str]:
    """Opérations permises à un nœud qui porte ces modules."""
    return frozenset(op for m in parse_modules(modules, default=()) for op in OPS_BY_MODULE.get(m, ()))


def auth_message(node_id: str, nonce: str) -> bytes:
    return f"ovh-webui-worker-v{PROTOCOL}|{node_id}|{nonce}".encode()
