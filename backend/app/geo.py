"""Localisation approximative d'une adresse IP, SANS rien envoyer à l'extérieur : on lit une base locale (DB-IP « City Lite », format MMDB,
licence CC BY 4.0). Elle se télécharge d'un clic (une fois par mois suffit) ; sans elle, l'interface fonctionne comme avant.
La précision est celle d'un fournisseur d'accès : le pays est fiable, la ville approximative."""
import gzip
import ipaddress
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from .config import settings

_lock = threading.Lock()
_reader = None
_loaded_mtime = 0.0


def path() -> Path:
    return Path(os.environ.get("GEOIP_DB") or settings.data_dir / "geoip" / "dbip-city-lite.mmdb")


def _open():
    """Lecteur MMDB (rechargé si le fichier a changé), ou None s'il n'y a pas de base ou de bibliothèque."""
    global _reader, _loaded_mtime
    p = path()
    try:
        m = p.stat().st_mtime
    except OSError:
        _reader = None
        return None
    if _reader is not None and m == _loaded_mtime:
        return _reader
    with _lock:
        try:
            import maxminddb
            _reader = maxminddb.open_database(str(p))
            _loaded_mtime = m
        except Exception:
            _reader = None
    return _reader


def status() -> dict:
    p = path()
    try:
        st = p.stat()
    except OSError:
        return {"available": False, "updated": None}
    return {"available": _open() is not None, "updated": st.st_mtime, "size": st.st_size}


def lookup(ip: str, precise: bool = True) -> dict | None:
    """{country, countryName, city, lat, lon} ou None. Sans `precise` : le pays seulement (pour qui ne voit pas les adresses en clair)."""
    r = _open()
    if r is None:
        return None
    try:
        a = ipaddress.ip_address(ip)
        if not a.is_global:
            return None
        d = r.get(str(a))
    except (ValueError, OSError):
        return None
    if not isinstance(d, dict):
        return None
    c = d.get("country") or {}
    code = c.get("iso_code")
    if not code:
        return None
    out = {"country": code, "countryName": (c.get("names") or {}).get("fr") or (c.get("names") or {}).get("en") or code}
    if precise:
        loc = d.get("location") or {}
        city = ((d.get("city") or {}).get("names") or {})
        out["city"] = city.get("fr") or city.get("en") or ""
        if isinstance(loc.get("latitude"), (int, float)) and isinstance(loc.get("longitude"), (int, float)):
            out["lat"] = round(loc["latitude"], 2)
            out["lon"] = round(loc["longitude"], 2)
    return out


def download(now: float | None = None) -> dict:
    """Télécharge la base du mois (ou du mois précédent, si la nouvelle n'est pas encore publiée), vérifie qu'elle s'ouvre, puis la remplace d'un coup."""
    import maxminddb
    d = date.fromtimestamp(now or time.time())
    months = [(d.year, d.month), (d.year - (d.month == 1), 12 if d.month == 1 else d.month - 1)]
    last = "introuvable"
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    for y, m in months:
        url = f"https://download.db-ip.com/free/dbip-city-lite-{y}-{m:02d}.mmdb.gz"
        tmp = p.with_suffix(".part")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "armadok"}), timeout=60) as r, open(tmp, "wb") as out:
                raw = gzip.GzipFile(fileobj=r)
                n = 0
                while chunk := raw.read(1 << 20):
                    n += len(chunk)
                    if n > 400_000_000:
                        raise ValueError("fichier trop gros")
                    out.write(chunk)
            maxminddb.open_database(str(tmp)).close()
            os.chmod(tmp, 0o600)
            os.replace(tmp, p)
            return {"month": f"{y}-{m:02d}", "size": p.stat().st_size}
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except (urllib.error.URLError, OSError, ValueError, EOFError) as e:
            last = str(getattr(e, "reason", e))[:120]
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
    raise RuntimeError(f"Téléchargement impossible ({last})")
