"""Bannir des adresses IP : une règle Traefik, écrite dans un fichier dédié du dossier dynamique (rien à installer en plus).

Traefik n'a pas de « liste noire » : on crée un routeur de très haute priorité dont la règle est ClientIP(`a`) || ClientIP(`b`) …, qui
envoie vers un service interne vide, derrière un middleware qui répond 403 (ipAllowList sur une adresse qui ne correspond à personne).
Une adresse bannie reçoit donc un 403 sur TOUS les sites de ce nœud, HTTP comme HTTPS. La règle voit l'adresse telle que Traefik la reçoit :
derrière un autre proxy (Cloudflare…), c'est l'adresse du proxy ; configurez alors les en-têtes de confiance de Traefik.

Garde-fous : jamais d'adresse privée, locale ou réservée, jamais l'adresse de celui qui bannit, réseaux pas plus larges que /16 (IPv4) ou /48 (IPv6),
nombre de bannissements limité (2000 par défaut, réglable par nœud). Les bannissements temporaires expirent seuls."""
import ipaddress
import threading
import time

from . import db
from .events import publish
from .traefik_store import StoreError

FILE = "zz-webui-bans.yml"
MW = "webui-ban-403"
ROUTERS = ("webui-ban-tls", "webui-ban-plain")
PRIORITY = 2000000000
DEFAULT_LIMIT = 2000
LIMIT_RANGE = (10, 1000000)
OVERFLOW_DEFAULT = {"mode": "refuse", "by": "remaining", "permanent": False}


def limit(node_id: str) -> int:
    """Nombre maximal de bannissements de ce nœud : réglable, 0 = illimité, sinon la valeur par défaut."""
    r = db.one("SELECT ban_limit FROM nodes WHERE id=?", (node_id,))
    if not r or r["ban_limit"] is None:
        return DEFAULT_LIMIT
    return int(r["ban_limit"])


def overflow(node_id: str) -> dict:
    """Que faire quand la limite est atteinte : « refuse » (ne pas bannir la nouvelle adresse) ou « evict » (la bannir et lever d'anciens
    bannissements d'AUTOMOD, les moins longs à courir (« remaining ») ou les plus anciens (« oldest ») ; « permanent » : les définitifs peuvent aussi partir)."""
    import json
    r = db.one("SELECT ban_overflow FROM nodes WHERE id=?", (node_id,))
    try:
        d = json.loads(r["ban_overflow"]) if r and r["ban_overflow"] else {}
    except ValueError:
        d = {}
    return {"mode": d.get("mode") if d.get("mode") in ("refuse", "evict") else "refuse",
            "by": d.get("by") if d.get("by") in ("remaining", "oldest") else "remaining",
            "permanent": bool(d.get("permanent"))}


def set_limit(node_id: str, value: int | None, ov: dict | None = None) -> int:
    """Change la limite (None = valeur par défaut, 0 = illimité) et, si fourni, le comportement au débordement. Peut descendre sous le nombre actuel :
    les bannissements existants restent, seuls les ajouts sont concernés."""
    import json
    if value is not None and value != 0 and not LIMIT_RANGE[0] <= value <= LIMIT_RANGE[1]:
        raise BanError(f"Limite invalide : entre {LIMIT_RANGE[0]} et {LIMIT_RANGE[1]}, ou illimitée")
    db.run("UPDATE nodes SET ban_limit=? WHERE id=?", (value, node_id))
    if ov is not None:
        cur = {**overflow(node_id), **{k: v for k, v in ov.items() if k in OVERFLOW_DEFAULT and v is not None}}
        if cur["mode"] not in ("refuse", "evict") or cur["by"] not in ("remaining", "oldest"):
            raise BanError("Comportement au débordement invalide")
        db.run("UPDATE nodes SET ban_overflow=? WHERE id=?", (json.dumps({**cur, "permanent": bool(cur["permanent"])}), node_id))
    return limit(node_id)


def _victims(node_id: str, need: int, now: float) -> list | None:
    """Les « need » bannissements d'automod à lever pour faire de la place, ou None s'il n'y en a pas assez (les bannissements posés à la main ne sont jamais touchés)."""
    cfg = overflow(node_id)
    cand = [dict(r) for r in db.rows("SELECT * FROM ip_bans WHERE node_id=? AND created_by LIKE 'automod:%' AND (expires_at IS NULL OR expires_at>?)", (node_id, now))]
    if not cfg["permanent"]:
        cand = [c for c in cand if c["expires_at"]]
    if cfg["by"] == "oldest":
        cand.sort(key=lambda c: c["created_at"])
    else:   # le moins de temps restant d'abord ; les définitifs (temps infini) en dernier
        cand.sort(key=lambda c: (c["expires_at"] is None, c["expires_at"] or 0, c["created_at"]))
    return cand[:need] if len(cand) >= need else None


_NOBODY = "192.0.2.1/32"   # adresse de documentation (RFC 5737) : personne n'en vient, donc l'ipAllowList refuse tout le monde


class BanError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def normalize(raw: str, requesters: list[str] | str | None = None) -> str:
    """Adresse ou réseau autorisé au bannissement, sous sa forme canonique. Lève BanError sinon."""
    raw = (raw or "").strip()
    try:
        net = ipaddress.ip_network(raw, strict=False)
    except ValueError:
        raise BanError("Adresse ou réseau invalide (ex. 203.0.113.7 ou 203.0.113.0/24)")
    if net.version == 4 and net.prefixlen < 16 or net.version == 6 and net.prefixlen < 48:
        raise BanError("Réseau trop large : /16 au plus en IPv4 (/48 en IPv6)")
    probe = net.network_address
    for special in (probe.is_private, probe.is_loopback, probe.is_link_local, probe.is_unspecified, probe.is_multicast, probe.is_reserved):
        if special:
            raise BanError("Cette adresse est privée ou réservée : la bannir couperait votre propre réseau")
    for who in ([requesters] if isinstance(requesters, str) else requesters or []):
        try:
            mine = ipaddress.ip_address(who) in net
        except ValueError:
            continue
        if mine:   # (hors du try : BanError est une ValueError)
            raise BanError("C'est votre propre adresse : vous vous bloqueriez vous-même")
    return str(net.network_address) if net.num_addresses == 1 else str(net)


def _rule(ips: list[str]) -> str:
    return " || ".join(f"ClientIP(`{i}`)" for i in ips)


def sync(node: dict, store) -> None:
    """Réécrit les routeurs de bannissement d'après la base (et les retire s'il n'y a plus rien à bannir)."""
    ips = [r["ip"] for r in db.rows("SELECT ip FROM ip_bans WHERE node_id=? ORDER BY created_at", (node["id"],))]
    items, _ = store.items()
    exists = {(i["kind"], i["name"]) for i in items if i["proto"] == "http"}
    if not ips:
        for name in ROUTERS:
            if ("routers", name) in exists:
                store.delete_item("http", "routers", name)
        if ("middlewares", MW) in exists:
            store.delete_item("http", "middlewares", MW)
        try:
            if store.read(FILE).strip() in ("", "{}"):   # plus rien dans le fichier : on ne laisse pas un fichier vide
                store.delete(FILE)
        except StoreError:
            pass
        return
    eps = sorted({e for i in items if i["proto"] == "http" and i["kind"] == "routers" and i["name"] not in ROUTERS
                  for e in ((i["spec"] or {}).get("entryPoints") or [])}) or list(node.get("entrypoints") or ["websecure"])
    store.upsert("http", "middlewares", MW, {"ipAllowList": {"sourceRange": [_NOBODY]}}, file=FILE)
    for name, tls in (("webui-ban-tls", {}), ("webui-ban-plain", None)):
        spec = {"rule": _rule(ips), "entryPoints": eps, "service": "noop@internal", "middlewares": [MW], "priority": PRIORITY}
        if tls is not None:
            spec["tls"] = tls
        store.upsert("http", "routers", name, spec, file=FILE)


def list_bans(node_id: str) -> list[dict]:
    now = time.time()
    return [dict(r) for r in db.rows("SELECT id, ip, reason, created_by, created_at, expires_at FROM ip_bans WHERE node_id=? AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC", (node_id, now))]


def attempts(node_id: str, rows: list[dict]) -> None:
    """Ajoute à chaque bannissement d'une adresse seule « attempts » : les requêtes revues depuis l'heure du bannissement (elles reçoivent un 403 ; comptées par heure, donc à peu près)."""
    for r in rows:
        r["is_net"] = "/" in r["ip"]
        if r["is_net"]:
            r["attempts"] = None   # un réseau : on ne sait pas lesquelles de ses adresses sont comptées
            continue
        x = db.one("SELECT COALESCE(SUM(hits),0) n, MAX(hour) last FROM ip_hits WHERE node_id=? AND ip=? AND hour>=?", (node_id, r["ip"], int(r["created_at"] // 3600)))
        r["attempts"] = x["n"]
        r["last_attempt"] = x["last"] * 3600 if x["last"] else None


def stats(rows: list[dict], now: float | None = None) -> dict:
    """Chiffres de la liste (rows : lignes déjà passées par attempts() puis enrichies de « geo »)."""
    now = now or time.time()
    by_country: dict[str, dict] = {}
    for r in rows:
        g = r.get("geo")
        if g:
            c = by_country.setdefault(g["country"], {"code": g["country"], "name": g["countryName"], "n": 0})
            c["n"] += 1
    who: dict[str, int] = {}
    for r in rows:
        k = "automod" if r["created_by"].startswith("automod:") else r["created_by"]
        who[k] = who.get(k, 0) + 1
    top_who = max(who.items(), key=lambda kv: kv[1]) if who else None
    busiest = max((r for r in rows if r.get("attempts")), key=lambda r: r["attempts"], default=None)
    return {
        "total": len(rows),
        "permanent": sum(1 for r in rows if not r["expires_at"]),
        "temporary": sum(1 for r in rows if r["expires_at"]),
        "automod": sum(1 for r in rows if r["created_by"].startswith("automod:")),
        "networks": sum(1 for r in rows if r.get("is_net")),
        "last24h": sum(1 for r in rows if r["created_at"] > now - 86400),
        "expiring24h": sum(1 for r in rows if r["expires_at"] and r["expires_at"] < now + 86400),
        "attempts": sum(r.get("attempts") or 0 for r in rows),
        "countries": sorted(by_country.values(), key=lambda c: -c["n"])[:5],
        "countryCount": len(by_country),
        "topAuthor": {"name": top_who[0], "n": top_who[1]} if top_who else None,
        "busiest": {"ref": busiest["ref"], "attempts": busiest["attempts"]} if busiest else None,
        "oldest": min((r["created_at"] for r in rows), default=None),
    }


def add(node: dict, store, ip: str, reason: str, hours: float | None, by: str, requesters: list[str] | str | None = None) -> dict:
    ip = normalize(ip, requesters)
    from . import trusted
    net = ipaddress.ip_network(ip, strict=False)
    if trusted.is_trusted(node["id"], str(net.network_address)) or any(net.version == t.version and (t.subnet_of(net)) for t in trusted.networks(node["id"])):
        raise BanError(f"{ip} est validée (ou contient une adresse validée) : retirez d'abord la validation si vous voulez la bannir", 409)
    if db.one("SELECT 1 FROM ip_bans WHERE node_id=? AND ip=?", (node["id"], ip)):
        raise BanError(f"{ip} est déjà bannie", 409)
    now = time.time()
    cap = limit(node["id"])
    used = db.one("SELECT COUNT(*) c FROM ip_bans WHERE node_id=? AND (expires_at IS NULL OR expires_at>?)", (node["id"], now))["c"]
    victims: list[dict] = []
    if cap and used >= cap:
        if overflow(node["id"])["mode"] == "evict":
            victims = _victims(node["id"], used - cap + 1, now) or []
        if not victims:
            raise BanError(f"Limite de {cap} bannissements atteinte : retirez-en ou relevez la limite (onglet Bannies)"
                           + (" — aucun bannissement d'automod ne peut être levé pour faire de la place" if overflow(node["id"])["mode"] == "evict" else ""))
        for v in victims:
            db.run("DELETE FROM ip_bans WHERE id=?", (v["id"],))
    exp = now + hours * 3600 if hours else None
    bid = db.run("INSERT INTO ip_bans(node_id,ip,reason,created_by,created_at,expires_at) VALUES(?,?,?,?,?,?)", (node["id"], ip, (reason or "").strip()[:200], by, now, exp))
    try:
        sync(node, store)
    except StoreError:
        db.run("DELETE FROM ip_bans WHERE id=?", (bid,))   # pas écrit dans Traefik : on ne prétend pas avoir banni, et on rend ce qu'on avait levé
        for v in victims:
            db.run("INSERT INTO ip_bans(node_id,ip,reason,created_by,created_at,expires_at) VALUES(?,?,?,?,?,?)", (v["node_id"], v["ip"], v["reason"], v["created_by"], v["created_at"], v["expires_at"]))
        raise
    publish("security", node["id"])
    return {"id": bid, "ip": ip, "expires_at": exp, "evicted": [v["ip"] for v in victims]}


def update(node_id: str, bid: int, hours: float | None, reason: str | None = None, now: float | None = None) -> dict | None:
    """Change la durée d'un bannissement : « hours » heures À PARTIR DE MAINTENANT, ou définitif (None). La règle Traefik ne change pas (mêmes adresses),
    seule l'échéance bouge. Renvoie None si le bannissement n'existe pas."""
    r = db.one("SELECT ip FROM ip_bans WHERE id=? AND node_id=?", (bid, node_id))
    if not r:
        return None
    if hours is not None and not 0 < hours <= 24 * 365:
        raise BanError("Durée invalide (de quelques minutes à un an, ou définitif)")
    exp = (now or time.time()) + hours * 3600 if hours else None
    if reason is None:
        db.run("UPDATE ip_bans SET expires_at=? WHERE id=?", (exp, bid))
    else:
        db.run("UPDATE ip_bans SET expires_at=?, reason=? WHERE id=?", (exp, reason.strip()[:200], bid))
    publish("security", node_id)
    return {"id": bid, "ip": r["ip"], "expires_at": exp}


def remove(node: dict, store, bid: int) -> str | None:
    r = db.one("SELECT ip FROM ip_bans WHERE id=? AND node_id=?", (bid, node["id"]))
    if not r:
        return None
    db.run("DELETE FROM ip_bans WHERE id=?", (bid,))
    sync(node, store)
    publish("security", node["id"])
    return r["ip"]


def expire() -> int:
    """Retire les bannissements arrivés à échéance, sur chaque nœud concerné. Renvoie le nombre retiré."""
    from .nodes import node_out, store_for
    now = time.time()
    due = db.rows("SELECT DISTINCT node_id FROM ip_bans WHERE expires_at IS NOT NULL AND expires_at<=?", (now,))
    n = 0
    for d in due:
        row = db.one("SELECT * FROM nodes WHERE id=? AND deleted_at IS NULL", (d["node_id"],))
        cnt = db.one("SELECT COUNT(*) c FROM ip_bans WHERE node_id=? AND expires_at IS NOT NULL AND expires_at<=?", (d["node_id"], now))["c"]
        db.run("DELETE FROM ip_bans WHERE node_id=? AND expires_at IS NOT NULL AND expires_at<=?", (d["node_id"], now))
        n += cnt
        if row:
            try:
                sync(node_out(row), store_for(node_out(row)))
            except Exception:  # noqa: BLE001 - nœud injoignable : repris à la prochaine passe
                pass
            publish("security", d["node_id"])
    return n


_started = False


def start() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        while True:
            time.sleep(60)
            try:
                expire()
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=loop, name="bans-expire", daemon=True).start()
