"""Point d'entrée UNIQUE des opérations d'une machine (Docker et projets) : le hub l'appelle pour son nœud, un worker pour les ordres reçus.

Les noms d'opération commencent par « d_ » (Docker) ou « p_ » (projets) ; tout autre nom est refusé."""
from . import docker_ops, project_ops
from .traefik_store import StoreError


def names() -> frozenset[str]:
    return frozenset(docker_ops.OPS) | frozenset(project_ops.OPS)


def handle(op: str, a: dict):
    if op.startswith("d_"):
        return docker_ops.handle(op, a)
    if op.startswith("p_"):
        return project_ops.handle(op, a)
    raise StoreError("Opération non autorisée", 400)
