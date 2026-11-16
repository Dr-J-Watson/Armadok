"""Test en un clic d'un site : le nom se résout-il, le certificat est-il valide, le site répond-il ?

Ne contacte que les adresses des routeurs de ce nœud (jamais une adresse saisie par l'appelant)."""
import http.client
import socket
import ssl
import time

TIMEOUT = 5.0
MAX_HOSTS = 6


def _dns(host: str) -> dict:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as e:
        return {"ok": False, "detail": "le nom ne se résout pas" if isinstance(e, socket.gaierror) else str(e)[:120]}
    ips = sorted({i[4][0] for i in infos})
    return {"ok": True, "detail": ", ".join(ips[:4]), "ips": ips[:8]}


def _tls(host: str) -> dict:
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=TIMEOUT) as raw, ctx.wrap_socket(raw, server_hostname=host) as s:
            cert = s.getpeercert()
        left = (ssl.cert_time_to_seconds(cert["notAfter"]) - time.time()) / 86400
        return {"ok": left > 0, "days": int(left), "detail": f"valable encore {int(left)} jours" if left > 0 else "certificat expiré"}
    except ssl.SSLCertVerificationError as e:
        return {"ok": False, "detail": f"certificat refusé : {getattr(e, 'verify_message', None) or 'invalide'}"}
    except (OSError, ssl.SSLError) as e:
        return {"ok": False, "detail": "connexion HTTPS impossible" if not isinstance(e, TimeoutError) else "délai dépassé"}


def _http(host: str) -> dict:
    t0 = time.time()
    try:
        c = http.client.HTTPSConnection(host, 443, timeout=TIMEOUT, context=ssl.create_default_context())
        c.request("HEAD", "/", headers={"User-Agent": "webui-sitecheck", "Connection": "close"})
        r = c.getresponse()
        ms = int((time.time() - t0) * 1000)
        c.close()
        # 401/403 = le site répond (il est protégé) ; 5xx = l'application derrière ne répond pas
        ok = r.status < 500
        return {"ok": ok, "status": r.status, "ms": ms, "detail": f"HTTP {r.status} en {ms} ms"}
    except (OSError, ssl.SSLError, http.client.HTTPException) as e:
        return {"ok": False, "detail": "délai dépassé" if isinstance(e, TimeoutError) else "pas de réponse"}


def check(hosts: list[str]) -> list[dict]:
    out = []
    for h in hosts[:MAX_HOSTS]:
        d = _dns(h)
        row = {"host": h, "dns": d}
        if d["ok"]:
            row["tls"] = _tls(h)
            row["http"] = _http(h) if row["tls"]["ok"] or "expiré" in row["tls"]["detail"] else {"ok": False, "detail": "non testé (HTTPS en échec)"}
        else:
            row["tls"] = {"ok": False, "detail": "non testé"}
            row["http"] = {"ok": False, "detail": "non testé"}
        row["ok"] = d["ok"] and row["tls"]["ok"] and row["http"]["ok"]
        out.append(row)
    return out
