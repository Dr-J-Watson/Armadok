"""Certificats de Traefik : lecture seule, métadonnées uniquement.

Deux sources, au même format de sortie :
 - directement acme.json (le cas normal : l'interface tourne avec le même utilisateur que Traefik) ;
 - un relevé « sans clés » écrit par `python -m app.certsync` (nœuds distants : le worker n'envoie au hub que ce relevé).
Une clé privée n'est jamais conservée, renvoyée ni journalisée : seules les parties publiques (noms, émetteur, dates) sortent d'ici.
"""
import base64
import hashlib
import json
import time
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtensionOID, NameOID

from .traefik_lint import extract_hosts

MAX_FILE = 20 * 1024 * 1024
MAX_CERTS = 500
EXPIRING_DAYS = 14   # Traefik renouvelle ~30 jours avant l'échéance : en dessous de 14, un renouvellement a échoué


def _text(v, n=253) -> str:
    return v[:n] if isinstance(v, str) else ""


def describe(b64: str) -> dict | None:
    """Parties publiques du premier certificat d'une chaîne PEM encodée en base64 (le format d'acme.json)."""
    try:
        pem = base64.b64decode(b64, validate=False)
        cert = x509.load_pem_x509_certificate(pem)
    except Exception:
        return None
    try:
        sans = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        sans = []
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    org = cert.issuer.get_attributes_for_oid(NameOID.ORGANIZATION_NAME) or cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
    return {"names": sorted({n.lower() for n in sans} | ({cn[0].value.lower()} if cn else set())),
            "issuer": _text(org[0].value, 120) if org else "", "notBefore": cert.not_valid_before_utc.timestamp(),
            "notAfter": cert.not_valid_after_utc.timestamp(), "serial": format(cert.serial_number, "x")[:40],
            "fingerprint": hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()[:16]}


def from_acme(data) -> list[dict]:
    out = []
    if not isinstance(data, dict):
        return out
    for resolver, body in data.items():
        if not isinstance(body, dict):
            continue
        for c in (body.get("Certificates") or body.get("certificates") or [])[:MAX_CERTS]:
            if not isinstance(c, dict):
                continue
            info = describe(c.get("certificate") or c.get("Certificate") or "")
            dom = c.get("domain") or c.get("Domain") or {}
            main = _text(dom.get("main") or dom.get("Main")).lower()
            sans = [_text(x).lower() for x in (dom.get("sans") or dom.get("SANs") or []) if isinstance(x, str)]
            if not info:
                continue
            names = info["names"] or sorted({n for n in [main, *sans] if n})
            out.append({"resolver": _text(resolver, 60), "main": main or (names[0] if names else ""), "names": names[:100], "issuer": info["issuer"],
                        "notBefore": info["notBefore"], "notAfter": info["notAfter"], "serial": info["serial"], "fingerprint": info["fingerprint"]})
            c.pop("key", None), c.pop("Key", None)   # la clé n'a jamais servi ; on ne la garde pas en mémoire plus longtemps que nécessaire
    return out


def clean(certs) -> list[dict]:
    """Remet en forme (et borne) des entrées reçues d'un relevé ou d'un worker : on ne fait confiance à aucun type."""
    out = []
    for c in certs if isinstance(certs, list) else []:
        if not isinstance(c, dict):
            continue
        try:
            na, nb = float(c.get("notAfter")), float(c.get("notBefore"))
        except (TypeError, ValueError):
            continue
        names = [_text(n).lower() for n in (c.get("names") or []) if isinstance(n, str)][:100]
        out.append({"resolver": _text(c.get("resolver"), 60), "main": _text(c.get("main")).lower(), "names": names, "issuer": _text(c.get("issuer"), 120),
                    "notBefore": nb, "notAfter": na, "serial": _text(c.get("serial"), 40), "fingerprint": _text(c.get("fingerprint"), 16)})
        if len(out) >= MAX_CERTS:
            break
    return out


def load(path) -> dict:
    if not path:
        return {"configured": False, "error": None, "certs": [], "updated": None}
    p = Path(path)
    try:
        if p.stat().st_size > MAX_FILE:
            return {"configured": True, "error": "Fichier de certificats trop gros", "certs": [], "updated": None}
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"configured": True, "error": "Fichier des certificats introuvable : acme.json n'existe pas encore (Traefik n'a rien obtenu ?), ou le dossier n'est pas monté (TRAEFIK_CERTS_FILE, CERTS_PATH).", "certs": [], "updated": None}
    except PermissionError:
        return {"configured": True, "error": "Droits insuffisants pour lire le fichier des certificats (acme.json est en mode 600 : l'interface doit tourner avec le même UID:GID que Traefik, voir le README).", "certs": [], "updated": None}
    except (OSError, ValueError):
        return {"configured": True, "error": "Fichier de certificats illisible", "certs": [], "updated": None}
    if isinstance(raw, dict) and raw.get("version") == 1:
        err = _text(raw.get("error"), 300) or None
        return {"configured": True, "error": err, "certs": clean(raw.get("certs")), "updated": raw.get("generated") if isinstance(raw.get("generated"), (int, float)) else None}
    return {"configured": True, "error": None, "certs": from_acme(raw), "updated": p.stat().st_mtime}


# ------------------------------------------------------------------ couverture et état des sites
def covers(cert: dict, host: str) -> bool:
    for n in cert["names"] or [cert["main"]]:
        if n == host:
            return True
        if n.startswith("*.") and host.endswith(n[1:]) and "." not in host[: -len(n) + 1]:
            return True
    return False


def days_left(cert: dict, now: float) -> int:
    return int((cert["notAfter"] - now) // 86400)


def host_status(host: str, certs: list[dict], resolver: str | None, now: float) -> dict:
    live = [c for c in certs if covers(c, host)]
    if not live:
        return {"host": host, "status": "missing" if resolver else "unmanaged"}
    best = max(live, key=lambda c: c["notAfter"])
    d = days_left(best, now)
    return {"host": host, "status": "expired" if d < 0 else "expiring" if d <= EXPIRING_DAYS else "ok", "days": d,
            "notAfter": best["notAfter"], "issuer": best["issuer"], "wildcard": host not in best["names"]}


_ORDER = {"expired": 0, "missing": 1, "expiring": 2, "ok": 3, "unmanaged": 4}


def sites(items: list[dict], certs: list[dict], now: float | None = None) -> list[dict]:
    now = now or time.time()
    out = []
    for it in items:
        if it["kind"] != "routers" or it["proto"] != "http" or it.get("disabled"):
            continue
        tls = (it["spec"] or {}).get("tls")
        if tls is None or (isinstance(tls, dict) and tls.get("passthrough")):
            continue
        resolver = tls.get("certResolver") if isinstance(tls, dict) else None
        hosts = [host_status(h, certs, resolver, now) for h in dict.fromkeys(extract_hosts((it["spec"] or {}).get("rule", "")))]
        if hosts:
            out.append({"id": it["id"], "name": it["name"], "resolver": resolver, "hosts": hosts,
                        "worst": min((h["status"] for h in hosts), key=lambda s: _ORDER[s]), "editable": bool(it.get("editable"))})
    return sorted(out, key=lambda s: (_ORDER[s["worst"]], s["name"]))
