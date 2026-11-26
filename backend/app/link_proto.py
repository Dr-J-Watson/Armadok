"""Protocole de liaison entre deux hubs, partagé par les deux côtés.

Principes (« zéro confiance ») :
  - chaque hub a une identité Ed25519 ; l'appairage échange les clés publiques avec un code à usage unique, puis les DEUX administrateurs comparent
    une empreinte courte et confirment (contre un homme du milieu au moment de l'appairage) ;
  - chaque connexion est authentifiée dans les deux sens (le hub appelé prouve son identité autant que l'appelant), avec des nonces des deux côtés
    et des messages signés qui nomment les deux hubs et le sens : une signature ne se rejoue ni dans l'autre sens ni sur une autre connexion ;
  - un hub ne croit jamais l'autre sur ses droits : seul l'identité et l'état de la liaison transitent ici, et chaque hub applique SES propres règles ;
  - les versions sont négociées : on parle le protocole commun le plus récent, avec les seules fonctions que les deux comprennent.

  appelant → hello {hubId, protocol, minProtocol, version, nonce}
  appelé   → challenge {hubId, nonce, signature}        signature de auth_message("srv", appelé, appelant, nonce_appelant, nonce_appelé)
  appelant → auth {signature}                           signature de auth_message("cli", appelant, appelé, nonce_appelant, nonce_appelé)
  appelé   → ready {protocol, features, state}          ou incompatible {…} / revoked {}
  appelant → state {…} ; ensuite des deux côtés : ping/pong, state, revoke
"""
import base64
import hashlib

PROTOCOL = 1            # le plus récent que ce hub parle
MIN_PROTOCOL = 1        # le plus ancien qu'il accepte encore
FEATURES = ("state", "guests")   # fonctions de ce hub : « state » (état de la liaison), « guests » (équipes partagées et comptes invités) ; « relay » viendra avec le relais
MAX_MESSAGE = 1 * 1024 * 1024
CODE_TTL = 15 * 60
PING_EVERY = 20
IDLE_LIMIT = 75
DEFAULT_GRACE_HOURS = 12
MAX_GRACE_HOURS = 24 * 30
CLOSE_UNAUTHORIZED, CLOSE_TOO_MANY, CLOSE_INCOMPATIBLE, CLOSE_REVOKED, CLOSE_REPLACED = 4401, 4429, 4426, 4403, 4000


def auth_message(role: str, first: str, second: str, nonce_client: str, nonce_server: str) -> bytes:
    """Ce que chaque côté signe : domaine fixe, rôle (srv/cli), les deux identités dans l'ordre, les deux nonces."""
    return "\n".join(("armadok-link-v1", role, first, second, nonce_client, nonce_server)).encode()


def negotiate(my_max: int, my_min: int, peer_max: int, peer_min: int) -> int | None:
    """Protocole commun le plus récent, ou None si les plages ne se recouvrent pas."""
    best = min(my_max, peer_max)
    return best if best >= max(my_min, peer_min) else None


def common_features(mine, theirs) -> list[str]:
    return sorted(set(mine) & {f for f in (theirs or []) if isinstance(f, str)})


def fingerprint(key_a_b64: str, key_b_b64: str) -> str:
    """Empreinte courte et identique des deux côtés (l'ordre des clés ne compte pas) : les administrateurs la comparent de vive voix."""
    ka, kb = sorted((key_a_b64, key_b_b64))
    h = hashlib.sha256(("armadok-link-fp\n" + ka + "\n" + kb).encode()).hexdigest().upper()[:20]
    return " ".join(h[i:i + 5] for i in range(0, 20, 5))


def valid_hub_id(v) -> bool:
    return isinstance(v, str) and 16 <= len(v) <= 64 and all(c in "0123456789abcdef" for c in v)


def b64_ok(v, size: int) -> bool:
    try:
        return len(base64.b64decode(v, validate=True)) == size
    except Exception:
        return False
