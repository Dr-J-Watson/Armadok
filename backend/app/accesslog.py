"""Adresses IP qui se connectent : lues dans le journal d'accès de Traefik (JSON ou format commun), conservées 7 jours.

Prometheus ne sait pas compter par visiteur ; le journal d'accès, si. Un fil lit le fichier au fil de l'eau (rotation et troncature gérées),
agrège par adresse et par heure, et purge ce qui dépasse 7 jours. Ne sont gardés que : adresse, heure, compteurs par famille de statut,
chemin SANS paramètres (ils peuvent contenir des jetons), site demandé, navigateur. Les adresses sont masquées pour qui n'a pas le droit de les voir."""
import collections
import hashlib
import hmac
import ipaddress
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import attacks as atk, db, trusted
from .config import env, settings

RETENTION_DAYS = 7
FLUSH_EVERY = 3
MAX_PATHS_PER_IP = 30
MAX_IPS = 100_000   # adresses suivies par nœud (les plus récentes) : borne la taille de la base en cas d'attaque distribuée
BURST_WINDOW = 600          # secondes : fenêtre de l'alerte « une adresse insiste »
_PROBE = re.compile(r"(\.env|\.git|\.aws|\.ssh|wp-login|wp-admin|xmlrpc|phpmyadmin|\.php|cgi-bin|etc/passwd|\.bak|\.sql|/old\b|boaform|actuator|\.DS_Store|/vendor/|/admin(/|$))", re.I)
_CLF = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d{3}) \S+ "[^"]*" "([^"]*)"')

_lock = threading.Lock()
_recent: dict[str, collections.deque] = {}     # adresse -> instants des réponses d'erreur (401/403/404) de la dernière fenêtre
_recent_exploit: dict[str, collections.deque] = {}   # adresse -> instants des tentatives graves (injection, exécution, remontée de répertoires, fuite)
_started = False
_status: dict = {"running": False, "lines": 0, "lastLine": None, "error": None}
# Nœuds DISTANTS : le worker lit son propre journal et envoie les lignes ; l'état « récent » (rafales) est tenu séparément par nœud
_remote: dict[str, dict] = {}                                  # nœud -> {lines, lastLine}
_remote_recent: dict[str, dict[str, collections.deque]] = {}
_remote_exploit: dict[str, dict[str, collections.deque]] = {}
MAX_REMOTE_LINES = 2000        # lignes acceptées par message d'un worker
MAX_LINE = 8192


def _is_local(node_id: str | None) -> bool:
    return node_id is None or node_id == _local_node()


def available(node_id: str | None = None) -> bool:
    """Le journal d'accès est-il lu pour ce nœud ? (sans argument ou nœud du hub : le fichier local ; nœud distant : son worker l'envoie ou l'a déjà envoyé)"""
    if _is_local(node_id):
        p = settings.access_log
        return bool(p) and os.path.isfile(p) and os.access(p, os.R_OK)
    return node_id in _remote or bool(db.one("SELECT 1 FROM ip_seen WHERE node_id=? LIMIT 1", (node_id,)))


def status(node_id: str | None = None) -> dict:
    if _is_local(node_id):
        return {**_status, "path": settings.access_log or "", "available": available()}
    r = _remote.get(node_id) or {}
    return {"running": node_id in _remote, "lines": r.get("lines", 0), "lastLine": r.get("lastLine"), "error": None, "path": "", "available": available(node_id), "remote": True}


def remote_hello(node_id: str, on: bool) -> None:
    """Le worker annonce (ou non) qu'il lit son journal d'accès."""
    if on:
        _remote.setdefault(node_id, {"lines": 0, "lastLine": None})
    else:
        _remote.pop(node_id, None)


# ------------------------------------------------------------------ masquage
def _key() -> bytes:
    return hashlib.sha256(("ovh-webui-ipref:" + (env("ENCRYPTION_KEY") or settings.jwt_secret)).encode()).digest()


def mask_ip(ip: str) -> str:
    """203.0.113.7 -> 203.0.xxx.xxx ; 2001:db8:1:2::5 -> 2001:db8:xxxx:… : assez pour reconnaître un réseau, pas un visiteur."""
    if "/" in ip:   # réseau : on masque l'adresse et on garde la taille
        base, _, prefix = ip.partition("/")
        return f"{mask_ip(base)}/{prefix}"
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "xxx"
    if a.version == 4:
        p = str(a).split(".")
        return f"{p[0]}.{p[1]}.xxx.xxx"
    h = a.exploded.split(":")
    return f"{h[0]}:{h[1]}:xxxx:xxxx:xxxx:xxxx:xxxx:xxxx"


def ref_of(ip: str) -> str:
    """Référence opaque et stable d'une adresse : permet d'agir dessus (bannir) sans la connaître."""
    return hmac.new(_key(), ip.encode(), hashlib.sha256).hexdigest()[:16]


def resolve_ref(node_id: str, ref: str) -> str | None:
    for r in db.rows("SELECT ip FROM ip_seen WHERE node_id=?", (node_id,)):
        if ref_of(r["ip"]) == ref:
            return r["ip"]
    return None


# ------------------------------------------------------------------ lecture des lignes
def _clean_ip(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("["):                       # [::1]:1234
        raw = raw[1:].split("]")[0]
    elif raw.count(":") == 1:                     # 1.2.3.4:5678
        raw = raw.split(":")[0]
    try:
        return ipaddress.ip_address(raw).compressed
    except ValueError:
        return None


def _ts(raw) -> float:
    if isinstance(raw, str) and raw:
        for fmt in ("%d/%b/%Y:%H:%M:%S %z",):
            try:
                return datetime.strptime(raw, fmt).timestamp()
            except ValueError:
                pass
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return time.time()


INTERNAL_ENTRYPOINT = "traefik"   # l'entrypoint interne de Traefik (:8080) : jamais du trafic de visiteurs


def parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    if line.startswith("{"):
        try:
            d = json.loads(line)
        except ValueError:
            return None
        ip = _clean_ip(d.get("ClientHost") or d.get("ClientAddr"))
        if d.get("entryPointName") == INTERNAL_ENTRYPOINT:   # API, métriques, ping de Traefik : ce sont nos propres outils (Prometheus, cette interface), pas des visiteurs
            return None
        status = d.get("DownstreamStatus") or d.get("OriginStatus")
        if not ip or not isinstance(status, int):
            return None
        path = str(d.get("RequestPath") or "/")
        return {"ip": ip, "host": str(d.get("RequestHost") or "").lower()[:253], "path": path, "raw": path + " " + str(d.get("RequestLine") or ""), "status": status,
                "ts": _ts(d.get("StartUTC") or d.get("time")), "ua": str(d.get("request_User-Agent") or "")[:150], "method": str(d.get("RequestMethod") or "")}
    m = _CLF.match(line)
    if not m:
        return None
    ip = _clean_ip(m.group(1))
    if not ip:
        return None
    return {"ip": ip, "host": "", "path": m.group(4), "raw": m.group(4), "status": int(m.group(5)), "ts": _ts(m.group(2)), "ua": m.group(6)[:150] if m.group(6) != "-" else "", "method": m.group(3)}


def _clean_path(p: str) -> str:
    return (p.split("?", 1)[0] or "/")[:200]


# ------------------------------------------------------------------ agrégation et écriture
class _Batch:
    def __init__(self, node_id: str | None = None) -> None:
        self.node_id = node_id                                # None : nœud du hub ; sinon nœud distant (état « récent » séparé)
        self.hits: dict[tuple[str, int], list[int]] = {}      # (ip, heure) -> [hits, c2, c3, c401, c403, c404, c4(autres 4xx), c5]
        self.seen: dict[str, dict] = {}
        self.paths: dict[tuple[str, str, str], list] = {}
        self.attacks: dict[tuple[str, str], list] = {}        # (ip, type) -> [compteur, dernier instant, exemple]

    def add(self, e: dict) -> None:
        ip, st, ts = e["ip"], e["status"], e["ts"]
        c = self.hits.setdefault((ip, int(ts // 3600)), [0] * 8)
        c[0] += 1
        if 200 <= st < 300:
            c[1] += 1
        elif 300 <= st < 400:
            c[2] += 1
        elif st == 401:
            c[3] += 1
        elif st == 403:
            c[4] += 1
        elif st == 404:
            c[5] += 1
        elif 400 <= st < 500:
            c[6] += 1
        elif st >= 500:
            c[7] += 1
        s = self.seen.setdefault(ip, {"first": ts, "last": 0.0})
        s["first"] = min(s["first"], ts)
        if ts >= s["last"]:
            s.update(last=ts, host=e["host"], path=_clean_path(e["path"]), status=st, ua=e["ua"])
        k = (ip, _clean_path(e["path"]), e["host"])
        pp = self.paths.setdefault(k, [0, 0, 0.0])
        pp[0] += 1
        pp[1] += 1 if st >= 400 else 0
        pp[2] = max(pp[2], ts)
        recent, exploit = (_recent, _recent_exploit) if self.node_id is None else (_remote_recent.setdefault(self.node_id, {}), _remote_exploit.setdefault(self.node_id, {}))
        if st in (401, 403, 404):
            with _lock:
                dq = recent.setdefault(ip, collections.deque(maxlen=500))
                dq.append(ts)
        for kind in atk.classify(e.get("raw", e["path"]), e["ua"], st, e["path"]):
            a = self.attacks.setdefault((ip, kind), [0, 0.0, ""])
            a[0] += 1
            if ts >= a[1]:
                a[1], a[2] = ts, (e["ua"][:80] if kind == "scanner_ua" else atk.sample_of(e["host"], e["path"]))
            if kind in atk.SEVERE:
                with _lock:
                    exploit.setdefault(ip, collections.deque(maxlen=200)).append(ts)

    def flush(self, node_id: str) -> None:
        if not self.hits:
            return
        c = db.conn()
        with c:
            for (ip, hour), v in self.hits.items():
                c.execute("INSERT INTO ip_hits(node_id,ip,hour,hits,c2,c3,c401,c403,c404,c4,c5) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                          "ON CONFLICT(node_id,ip,hour) DO UPDATE SET hits=hits+excluded.hits, c2=c2+excluded.c2, c3=c3+excluded.c3, c401=c401+excluded.c401, "
                          "c403=c403+excluded.c403, c404=c404+excluded.c404, c4=c4+excluded.c4, c5=c5+excluded.c5", (node_id, ip, hour, *v))
            for ip, s in self.seen.items():
                c.execute("INSERT INTO ip_seen(node_id,ip,first_seen,last_seen,last_host,last_path,last_status,last_ua) VALUES(?,?,?,?,?,?,?,?) "
                          "ON CONFLICT(node_id,ip) DO UPDATE SET first_seen=MIN(first_seen,excluded.first_seen), "
                          "last_host=CASE WHEN excluded.last_seen>=last_seen THEN excluded.last_host ELSE last_host END, "
                          "last_path=CASE WHEN excluded.last_seen>=last_seen THEN excluded.last_path ELSE last_path END, "
                          "last_status=CASE WHEN excluded.last_seen>=last_seen THEN excluded.last_status ELSE last_status END, "
                          "last_ua=CASE WHEN excluded.last_seen>=last_seen THEN excluded.last_ua ELSE last_ua END, "
                          "last_seen=MAX(last_seen,excluded.last_seen)", (node_id, ip, s["first"], s["last"], s.get("host", ""), s.get("path", ""), s.get("status", 0), s.get("ua", "")))
            for (ip, path, host), (n, errs, last) in self.paths.items():
                c.execute("INSERT INTO ip_paths(node_id,ip,path,host,hits,errors,last_seen) VALUES(?,?,?,?,?,?,?) "
                          "ON CONFLICT(node_id,ip,path,host) DO UPDATE SET hits=hits+excluded.hits, errors=errors+excluded.errors, last_seen=MAX(last_seen,excluded.last_seen)",
                          (node_id, ip, path, host, n, errs, last))
            for (ip, kind), (n, last, sample) in self.attacks.items():
                c.execute("INSERT INTO ip_attacks(node_id,ip,kind,hits,last_seen,sample) VALUES(?,?,?,?,?,?) "
                          "ON CONFLICT(node_id,ip,kind) DO UPDATE SET hits=hits+excluded.hits, "
                          "sample=CASE WHEN excluded.last_seen>=last_seen THEN excluded.sample ELSE sample END, last_seen=MAX(last_seen,excluded.last_seen)",
                          (node_id, ip, kind, n, last, sample))
            for ip in self.seen:   # on ne garde que les chemins les plus demandés de chaque adresse
                c.execute("DELETE FROM ip_paths WHERE node_id=? AND ip=? AND rowid NOT IN (SELECT rowid FROM ip_paths WHERE node_id=? AND ip=? ORDER BY hits DESC, last_seen DESC LIMIT ?)",
                          (node_id, ip, node_id, ip, MAX_PATHS_PER_IP))
        self.__init__(self.node_id)


def purge(now: float | None = None) -> None:
    now = now or time.time()
    cut = now - RETENTION_DAYS * 86400
    c = db.conn()
    with c:
        c.execute("DELETE FROM ip_hits WHERE hour<?", (int(cut // 3600),))
        c.execute("DELETE FROM ip_seen WHERE last_seen<?", (cut,))
        c.execute("DELETE FROM ip_paths WHERE last_seen<?", (cut,))
        c.execute("DELETE FROM ip_attacks WHERE last_seen<?", (cut,))
        c.execute("DELETE FROM alert_history WHERE at<?", (now - 30 * 86400,))
        # Une attaque répartie sur des millions d'adresses ne fait pas grossir la base sans fin : on ne garde que les MAX_IPS plus récentes par nœud.
        for n in c.execute("SELECT node_id FROM ip_seen GROUP BY node_id HAVING COUNT(*) > ?", (MAX_IPS,)).fetchall():
            old = c.execute("SELECT ip FROM ip_seen WHERE node_id=? ORDER BY last_seen DESC LIMIT -1 OFFSET ?", (n["node_id"], MAX_IPS)).fetchall()
            for chunk in (old[i:i + 500] for i in range(0, len(old), 500)):
                marks = ",".join("?" * len(chunk))
                ips = [r["ip"] for r in chunk]
                for table in ("ip_seen", "ip_paths", "ip_attacks", "ip_hits"):
                    c.execute(f"DELETE FROM {table} WHERE node_id=? AND ip IN ({marks})", (n["node_id"], *ips))
    with _lock:
        for ip in [i for i, dq in _recent.items() if not dq or dq[-1] < now - BURST_WINDOW]:
            _recent.pop(ip, None)
        for ip in [i for i, dq in _recent_exploit.items() if not dq or dq[-1] < now - BURST_WINDOW]:
            _recent_exploit.pop(ip, None)
        for table in (_remote_recent, _remote_exploit):
            for per in table.values():
                for ip in [i for i, dq in per.items() if not dq or dq[-1] < now - BURST_WINDOW]:
                    per.pop(ip, None)


def _head(f) -> str:
    """Empreinte du début du fichier : un fichier tronqué puis réécrit garde son identifiant et peut redevenir plus grand, mais son début change."""
    pos = f.tell()
    try:
        f.seek(0)
        b = f.read(200)
    finally:
        f.seek(pos)
    return hashlib.sha1(b).hexdigest() if len(b) >= 200 else ""


def _local_node() -> str | None:
    r = db.one("SELECT id FROM nodes WHERE kind='local' AND deleted_at IS NULL ORDER BY created_at LIMIT 1")
    return r["id"] if r else None


def ingest(node_id: str, lines, batch: _Batch | None = None) -> int:
    """Lit des lignes de journal (testable sans fichier). Renvoie le nombre de lignes retenues."""
    b = batch or _Batch(None if _is_local(node_id) else node_id)
    n = 0
    for line in lines:
        e = parse_line(line)
        if e:
            b.add(e)
            n += 1
    if batch is None:
        b.flush(node_id)
    return n


def ingest_remote(node_id: str, lines) -> int:
    """Lignes de journal envoyées par le worker d'un nœud distant. Bornées (nombre et taille) : le worker est authentifié, mais rien n'est pris sur parole."""
    if not isinstance(lines, list):
        return 0
    clean = [ln for ln in lines[:MAX_REMOTE_LINES] if isinstance(ln, str) and len(ln) <= MAX_LINE]
    n = ingest(node_id, clean)
    st = _remote.setdefault(node_id, {"lines": 0, "lastLine": None})
    st["lines"] += n
    if n:
        st["lastLine"] = time.time()
    return n


def _tail() -> None:
    last_purge = 0.0
    while True:
        try:
            path, node_id = settings.access_log, _local_node()
            if not path or not node_id or not os.path.isfile(path):
                _status.update(running=False, error=None if not path else "fichier introuvable")
                time.sleep(10)
                continue
            with open(path, "rb") as f:
                st = os.fstat(f.fileno())
                saved = db.one("SELECT inode, off, head FROM log_offsets WHERE node_id=?", (node_id,))
                head = _head(f)
                if saved and saved["inode"] == st.st_ino and saved["off"] <= st.st_size and (not saved["head"] or not head or saved["head"] == head):
                    f.seek(saved["off"])
                elif saved and saved["inode"] == st.st_ino and head and saved["head"] and saved["head"] != head:
                    f.seek(0)                                      # même fichier, autre contenu : il a été remplacé, on relit depuis le début
                else:                                             # première lecture : les dernières lignes seulement
                    f.seek(max(0, st.st_size - 2_000_000))
                    if f.tell():
                        f.readline()
                _status.update(running=True, error=None)
                batch, last_flush = _Batch(), time.time()
                while True:
                    pos = f.tell()
                    raw = f.readline()
                    if raw and raw.endswith(b"\n"):
                        e = parse_line(raw.decode("utf-8", "ignore"))
                        if e:
                            batch.add(e)
                            _status["lines"] += 1
                            _status["lastLine"] = time.time()
                        continue
                    if raw:                                       # ligne en cours d'écriture : on la reprendra
                        f.seek(pos)
                    now = time.time()
                    if now - last_flush >= FLUSH_EVERY:
                        batch.flush(node_id)
                        head = head or _head(f)
                        db.run("INSERT INTO log_offsets(node_id,inode,off,head) VALUES(?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET inode=excluded.inode, off=excluded.off, head=excluded.head", (node_id, st.st_ino, f.tell(), head))
                        last_flush = now
                        if now - last_purge > 3600:
                            purge(now)
                            last_purge = now
                    time.sleep(1)
                    try:
                        cur = os.stat(path)
                    except OSError:
                        break
                    if cur.st_ino != st.st_ino or cur.st_size < f.tell() or (head and _head(f) != head):   # rotation, troncature ou remplacement : on rouvre
                        batch.flush(node_id)
                        db.run("DELETE FROM log_offsets WHERE node_id=?", (node_id,))
                        break
        except Exception as e:  # noqa: BLE001 - la lecture ne doit jamais s'arrêter
            _status.update(running=False, error=str(e)[:200])
            time.sleep(10)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_tail, name="accesslog-tail", daemon=True).start()


# ------------------------------------------------------------------ questions posées aux données
def burst(window: int = BURST_WINDOW, now: float | None = None, node_id: str | None = None) -> dict[str, int]:
    """Erreurs 401/403/404 par adresse sur la fenêtre récente (en mémoire). `node_id` : un nœud distant ; sans lui, celui du hub."""
    cut = (now or time.time()) - window
    with _lock:
        src = _recent if _is_local(node_id) else _remote_recent.get(node_id, {})
        return {ip: n for ip, dq in src.items() if (n := sum(1 for t in dq if t >= cut))}


def exploit_burst(window: int = BURST_WINDOW, now: float | None = None, node_id: str | None = None) -> dict[str, int]:
    """Tentatives GRAVES (injection, exécution de commandes, remontée de répertoires, fuite) par adresse sur la fenêtre récente."""
    cut = (now or time.time()) - window
    with _lock:
        src = _recent_exploit if _is_local(node_id) else _remote_exploit.get(node_id, {})
        return {ip: n for ip, dq in src.items() if (n := sum(1 for t in dq if t >= cut))}


def recent_leaks(node_id: str, since: float) -> list[dict]:
    """Fichiers sensibles qui ont répondu 200 depuis `since`."""
    return [dict(r) for r in db.rows("SELECT ip, hits, last_seen, sample FROM ip_attacks WHERE node_id=? AND kind='leak' AND last_seen>=? ORDER BY last_seen DESC", (node_id, since))]


def is_suspicious(row: dict, probing: bool) -> bool:
    errs = row["c401"] + row["c403"] + row["c404"]
    return probing or (errs >= 15 and errs >= 0.5 * max(1, row["hits"]))


def _attack_rows(node_id: str, cut: float, hits_by_ip: dict[str, int]) -> dict[str, list[dict]]:
    """{adresse: [types d'attaque, du plus grave au moins grave]} : types relevés à l'écriture + types déduits des compteurs."""
    per: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    for r in db.rows("SELECT ip, kind, SUM(hits) hits, MAX(last_seen) last, MAX(sample) sample FROM ip_attacks WHERE node_id=? AND last_seen>=? GROUP BY ip, kind", (node_id, cut)):
        per[r["ip"]][r["kind"]] = {"hits": r["hits"], "last": r["last"], "sample": r["sample"]}
    err_paths = {r["ip"]: r["n"] for r in db.rows("SELECT ip, COUNT(*) n FROM ip_paths WHERE node_id=? AND last_seen>=? AND errors>0 GROUP BY ip", (node_id, cut))}
    max_hour = {r["ip"]: r["m"] for r in db.rows("SELECT ip, MAX(hits) m FROM ip_hits WHERE node_id=? AND hour>=? GROUP BY ip", (node_id, int(cut // 3600)))}
    out = {}
    for ip in set(per) | set(err_paths) | set(max_hour):
        counts = {k: v["hits"] for k, v in per.get(ip, {}).items()}
        found = {k: {"hits": v["hits"], "sample": v["sample"], "last": v["last"]} for k, v in per.get(ip, {}).items() if k != "login_fail"}
        for k, n in atk.derive(counts, err_paths.get(ip, 0), max_hour.get(ip, 0)).items():
            found[k] = {"hits": n, "sample": "", "last": per.get(ip, {}).get("login_fail", {}).get("last", 0)}
        rows = [{**atk.describe(k), **v} for k, v in found.items()]
        rows.sort(key=lambda r: (atk.SEVERITY_ORDER[r["severity"]], -r["hits"]))
        if rows:
            out[ip] = rows
    return out


def _only_hosts(node_id: str, rows: list[dict], hosts: set, cut: float) -> list[dict]:
    """Vue filtrée : les adresses vues sur ces noms de domaine, avec les requêtes et erreurs comptées sur ces domaines seulement."""
    hs = sorted(hosts)
    if not hs:
        return []
    seen = {r["ip"]: r for r in db.rows(f"SELECT ip, SUM(hits) hits, SUM(errors) errors FROM ip_paths WHERE node_id=? AND last_seen>=? AND host IN ({','.join('?' for _ in hs)}) GROUP BY ip", (node_id, cut, *hs))}
    out = []
    for r in rows:
        if r["ip"] in seen:
            out.append({**r, "hits": seen[r["ip"]]["hits"], "scopedErrors": seen[r["ip"]]["errors"]})
    return out


def listing(node_id: str, days: int = RETENTION_DAYS, sort: str = "hits", only: str = "", search: str = "", limit: int = 200, kind: str = "", hosts: set | None = None) -> dict:
    now = time.time()
    since = int((now - min(days, RETENTION_DAYS) * 86400) // 3600)
    rows = [dict(r) for r in db.rows(
        "SELECT h.ip, SUM(hits) hits, SUM(c2) c2, SUM(c3) c3, SUM(c401) c401, SUM(c403) c403, SUM(c404) c404, SUM(c4) c4, SUM(c5) c5, "
        "s.first_seen, s.last_seen, s.last_host, s.last_path, s.last_status, s.last_ua FROM ip_hits h LEFT JOIN ip_seen s ON s.node_id=h.node_id AND s.ip=h.ip "
        "WHERE h.node_id=? AND h.hour>=? GROUP BY h.ip", (node_id, since))]
    if hosts is not None:
        rows = _only_hosts(node_id, rows, hosts, now - min(days, RETENTION_DAYS) * 86400)
    paths = collections.defaultdict(list)
    for r in db.rows("SELECT ip, path FROM ip_paths WHERE node_id=? AND last_seen>=?", (node_id, now - days * 86400)):
        paths[r["ip"]].append(r["path"])
    bans = {b["ip"]: b for b in db.rows("SELECT ip, expires_at FROM ip_bans WHERE node_id=?", (node_id,))}
    nets = trusted.networks(node_id)
    attacks_by_ip = _attack_rows(node_id, now - min(days, RETENTION_DAYS) * 86400, {r["ip"]: r["hits"] for r in rows})
    out = []
    for r in rows:
        r["attacks"] = attacks_by_ip.get(r["ip"], [])
        probes = [p for p in paths.get(r["ip"], []) if _PROBE.search(p)]
        r["errors"] = r["scopedErrors"] if "scopedErrors" in r else r["c401"] + r["c403"] + r["c404"] + r["c4"]
        r["suspicious"] = is_suspicious(r, bool(probes)) or bool(r["attacks"])
        r["leak"] = any(a["kind"] == "leak" for a in r["attacks"])
        r["probes"] = probes[:3]
        r["private"] = _is_private(r["ip"])
        r["trusted"] = not r["private"] and trusted.is_trusted(node_id, r["ip"], nets)
        r["exempt"] = r["private"] or r["trusted"]   # réseau local ou adresse validée : jamais marquée
        if r["trusted"]:
            r["attacks"], r["probes"], r["suspicious"], r["leak"] = [], [], False, False
        r["banned"] = r["ip"] in bans
        out.append(r)
    total = {"ips": len(out), "hits": sum(r["hits"] for r in out), "suspicious": sum(1 for r in out if r["suspicious"] and not r["exempt"]), "trusted": sum(1 for r in out if r["trusted"]),
             "banned": len(bans), "leaks": sum(1 for r in out if r["leak"])}
    kinds: dict[str, dict] = {}
    for r in out:
        if r["exempt"]:
            continue
        for a in r["attacks"]:
            k = kinds.setdefault(a["kind"], {**atk.describe(a["kind"]), "ips": 0, "hits": 0})
            k["ips"] += 1
            k["hits"] += a["hits"]
    kinds_list = sorted(kinds.values(), key=lambda k: (atk.SEVERITY_ORDER[k["severity"]], -k["ips"]))
    if only == "attacks":
        out = [r for r in out if r["attacks"] and not r["exempt"]]
    if kind:
        out = [r for r in out if any(a["kind"] == kind for a in r["attacks"])]
    if only == "suspicious":
        out = [r for r in out if r["suspicious"] and not r["exempt"]]
    elif only == "trusted":
        out = [r for r in out if r["trusted"]]
    elif only == "banned":
        out = [r for r in out if r["banned"]]
    elif only == "public":
        out = [r for r in out if not r["private"]]
    if search:
        q = search.strip().lower()
        out = [r for r in out if q in r["ip"] or q in (r["last_host"] or "").lower() or q in (r["last_path"] or "").lower()]
    keys = {"hits": "hits", "errors": "errors", "recent": "last_seen", "c5": "c5"}
    out.sort(key=lambda r: -(r.get(keys.get(sort, "hits")) or 0))
    return {"rows": out[:limit], "total": total, "kinds": kinds_list, "days": min(days, RETENTION_DAYS)}


def stats(node_id: str, days: int = 1, hosts: set | None = None) -> dict:
    """Vue d'ensemble du trafic d'après le journal d'accès : courbe par heure, familles de réponses, sites et chemins les plus demandés."""
    now = time.time()
    days = max(1, min(days, RETENTION_DAYS))
    since = int((now - days * 86400) // 3600)
    cur = int(now // 3600)
    by_hour = {r["hour"]: dict(r) for r in db.rows(
        "SELECT hour, SUM(hits) hits, SUM(c401+c403+c404+c4) err4, SUM(c5) err5, COUNT(DISTINCT ip) ips FROM ip_hits WHERE node_id=? AND hour>=? GROUP BY hour", (node_id, since))}
    timeline = [{"t": h * 3600, "hits": by_hour.get(h, {}).get("hits", 0), "errors": (by_hour.get(h, {}).get("err4", 0) or 0) + (by_hour.get(h, {}).get("err5", 0) or 0),
                 "ips": by_hour.get(h, {}).get("ips", 0)} for h in range(max(since, cur - days * 24 + 1), cur + 1)]
    c = dict(db.one("SELECT SUM(hits) hits, SUM(c2) c2, SUM(c3) c3, SUM(c401) c401, SUM(c403) c403, SUM(c404) c404, SUM(c4) c4, SUM(c5) c5 FROM ip_hits WHERE node_id=? AND hour>=?", (node_id, since)) or {})
    classes = [{"name": n, "value": int(c.get(k) or 0)} for n, k in (("2xx", "c2"), ("3xx", "c3"), ("401", "c401"), ("403", "c403"), ("404", "c404"), ("autres 4xx", "c4"), ("5xx", "c5"))]
    cut = now - days * 86400
    only = ""
    args: tuple = ()
    if hosts is not None:   # vue filtrée : le journal ne garde l'heure et la classe de réponse que pour l'ensemble, pas par domaine
        args = tuple(sorted(hosts))
        only = f" AND host IN ({','.join('?' for _ in args)})" if args else " AND 0"
        timeline, classes = [], []
        tot = db.one(f"SELECT SUM(hits) hits FROM ip_paths WHERE node_id=? AND last_seen>=?{only}", (node_id, cut, *args))
        c = {"hits": (tot["hits"] if tot else 0) or 0}
    hs = [dict(r) for r in db.rows(f"SELECT host name, SUM(hits) hits, SUM(errors) errors, COUNT(DISTINCT ip) ips FROM ip_paths WHERE node_id=? AND last_seen>=? AND host<>''{only} GROUP BY host ORDER BY hits DESC LIMIT 10", (node_id, cut, *args))]
    paths = [dict(r) for r in db.rows(f"SELECT host, path, SUM(hits) hits, SUM(errors) errors FROM ip_paths WHERE node_id=? AND last_seen>=?{only} GROUP BY host, path ORDER BY hits DESC LIMIT 10", (node_id, cut, *args))]
    return {"days": days, "timeline": timeline, "classes": classes, "hosts": hs, "paths": paths, "hits": int(c.get("hits") or 0), "scoped": hosts is not None}


def exempt(node_id: str, ip: str) -> bool:
    """Adresse qu'on ne marque jamais : réseau local, ou validée par un administrateur."""
    return _is_private(ip) or trusted.is_trusted(node_id, ip)


def _is_private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback or a.is_link_local
    except ValueError:
        return False


def detail(node_id: str, ip: str) -> dict | None:
    s = db.one("SELECT * FROM ip_seen WHERE node_id=? AND ip=?", (node_id, ip))
    if not s:
        return None
    now = time.time()
    since = int((now - RETENTION_DAYS * 86400) // 3600)
    hours = {r["hour"]: dict(r) for r in db.rows("SELECT hour, hits, c401+c403+c404+c4 AS err, c5 FROM ip_hits WHERE node_id=? AND ip=? AND hour>=?", (node_id, ip, since))}
    cur = int(now // 3600)
    timeline = [{"t": h * 3600, "hits": hours.get(h, {}).get("hits", 0), "errors": hours.get(h, {}).get("err", 0) + hours.get(h, {}).get("c5", 0)} for h in range(cur - 47, cur + 1)]
    paths = [dict(r) for r in db.rows("SELECT path, host, hits, errors, last_seen FROM ip_paths WHERE node_id=? AND ip=? ORDER BY errors DESC, hits DESC LIMIT 15", (node_id, ip))]
    for p in paths:
        p["probe"] = bool(_PROBE.search(p["path"]))
    cut = now - RETENTION_DAYS * 86400
    return {"ip": ip, "firstSeen": s["first_seen"], "lastSeen": s["last_seen"], "lastUa": s["last_ua"], "paths": paths, "timeline": timeline,
            "private": _is_private(ip), "trusted": (tr := not _is_private(ip) and trusted.is_trusted(node_id, ip)),
            "attacks": [] if tr or _is_private(ip) else _attack_rows(node_id, cut, {}).get(ip, [])}
