"""Adresses « validées » : des adresses (ou réseaux) que vous reconnaissez — vous, un collègue, un service de supervision. Elles ne sont plus
marquées comme suspectes ni comme attaques, ne déclenchent plus d'alerte, et ne peuvent pas être bannies. Les adresses du réseau local
(privées, loopback, lien local) sont validées d'office, sans rien enregistrer.

Une adresse validée reste visible dans la liste (avec ses requêtes) : on la dispense seulement du soupçon."""
import ipaddress
import time

from . import db
from .events import publish

MAX_TRUSTED = 500


class TrustError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def normalize(raw: str) -> str:
    raw = (raw or "").strip()
    try:
        net = ipaddress.ip_network(raw, strict=False)
    except ValueError:
        raise TrustError("Adresse ou réseau invalide (ex. 203.0.113.7 ou 203.0.113.0/24)")
    if net.version == 4 and net.prefixlen < 16 or net.version == 6 and net.prefixlen < 48:
        raise TrustError("Réseau trop large : /16 au plus en IPv4 (/48 en IPv6) — valider tout Internet reviendrait à tout ignorer")
    return str(net.network_address) if net.num_addresses == 1 else str(net)


def list_trusted(node_id: str) -> list[dict]:
    return [dict(r) for r in db.rows("SELECT id, ip, note, created_by, created_at FROM ip_trusted WHERE node_id=? ORDER BY created_at DESC", (node_id,))]


def networks(node_id: str) -> list:
    out = []
    for r in db.rows("SELECT ip FROM ip_trusted WHERE node_id=?", (node_id,)):
        try:
            out.append(ipaddress.ip_network(r["ip"], strict=False))
        except ValueError:
            pass
    return out


def is_trusted(node_id: str, ip: str, nets: list | None = None) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in (nets if nets is not None else networks(node_id)) if n.version == a.version)


def add(node_id: str, ip: str, note: str, by: str) -> dict:
    ip = normalize(ip)
    if db.one("SELECT COUNT(*) n FROM ip_trusted WHERE node_id=?", (node_id,))["n"] >= MAX_TRUSTED:
        raise TrustError(f"{MAX_TRUSTED} adresses validées au plus", 409)
    if db.one("SELECT 1 FROM ip_trusted WHERE node_id=? AND ip=?", (node_id, ip)):
        raise TrustError("Cette adresse est déjà validée", 409)
    now = time.time()
    db.run("INSERT INTO ip_trusted(node_id, ip, note, created_by, created_at) VALUES(?,?,?,?,?)", (node_id, ip, (note or "")[:200], by, now))
    publish("security", node_id)
    return {"ip": ip, "note": (note or "")[:200], "created_by": by, "created_at": now}


def remove(node_id: str, tid: int) -> str | None:
    r = db.one("SELECT ip FROM ip_trusted WHERE node_id=? AND id=?", (node_id, tid))
    if not r:
        return None
    db.run("DELETE FROM ip_trusted WHERE node_id=? AND id=?", (node_id, tid))
    publish("security", node_id)
    return r["ip"]
