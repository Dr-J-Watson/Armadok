"""SQLite : utilisateurs, comptes OVH (clés chiffrées), domaines et droits d'accès."""
import sqlite3
import threading
import time

from .config import settings

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin','user')),
  can_add_domains INTEGER NOT NULL DEFAULT 0,
  traefik_access TEXT NOT NULL DEFAULT 'none' CHECK (traefik_access IN ('none','scoped')),
  target_hosts TEXT NOT NULL DEFAULT '',
  disabled INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'local',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS credentials (
  id INTEGER PRIMARY KEY,
  label TEXT NOT NULL,
  endpoint TEXT NOT NULL DEFAULT 'ovh-eu',
  app_key TEXT, app_secret TEXT, consumer_key TEXT,
  owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  source TEXT NOT NULL DEFAULT 'user',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS domains (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  credential_id INTEGER NOT NULL REFERENCES credentials(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS domain_access (
  domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  level TEXT NOT NULL CHECK (level IN ('view','edit')),
  PRIMARY KEY (domain_id, user_id)
);
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'local' CHECK (kind IN ('local','remote')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
  api_url TEXT NOT NULL DEFAULT '',
  entrypoints TEXT NOT NULL DEFAULT '',
  cert_resolvers TEXT NOT NULL DEFAULT '',
  dns_target TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS node_members (
  node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('owner','admin','editor','viewer')),
  target_hosts TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  PRIMARY KEY (node_id, user_id)
);
CREATE TABLE IF NOT EXISTS node_workers (
  node_id TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
  public_key TEXT NOT NULL,
  version TEXT NOT NULL DEFAULT '',
  last_seen REAL,
  enrolled_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS node_enroll_tokens (
  token_hash TEXT PRIMARY KEY,
  node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  expires_at REAL NOT NULL,
  used_at REAL,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS audit_head (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  h TEXT NOT NULL,
  n INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS emergency_sessions (
  id INTEGER PRIMARY KEY,
  node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  admin_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  reason TEXT NOT NULL,
  started_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  ended_at REAL
);
CREATE TABLE IF NOT EXISTS node_notices (
  id INTEGER PRIMARY KEY,
  node_id TEXT,
  node_name TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  session_id INTEGER,
  created_at REAL NOT NULL,
  acked_at REAL
);
CREATE TABLE IF NOT EXISTS roles (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE,
  description TEXT NOT NULL DEFAULT '',
  caps TEXT NOT NULL DEFAULT '[]',
  builtin TEXT,
  owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE,
  description TEXT NOT NULL DEFAULT '',
  owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS group_members (
  group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (group_id, user_id)
);
CREATE TABLE IF NOT EXISTS grants (
  id INTEGER PRIMARY KEY,
  subject_type TEXT NOT NULL CHECK (subject_type IN ('user','group')),
  subject_id INTEGER NOT NULL,
  role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  node_id TEXT REFERENCES nodes(id) ON DELETE CASCADE,
  granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  expires_at REAL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_rules (
  node_id TEXT NOT NULL, key TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  threshold REAL, for_s INTEGER NOT NULL DEFAULT 0, severity TEXT NOT NULL DEFAULT 'warning',
  PRIMARY KEY (node_id, key)
);
CREATE TABLE IF NOT EXISTS alert_state (
  node_id TEXT NOT NULL, key TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL, since REAL NOT NULL, value REAL, PRIMARY KEY (node_id, key, subject)
);
CREATE TABLE IF NOT EXISTS alert_history (
  id INTEGER PRIMARY KEY, node_id TEXT NOT NULL, key TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL, at REAL NOT NULL, value REAL, text TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS alert_snooze (
    node_id TEXT NOT NULL, key TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '', until REAL NOT NULL,
    PRIMARY KEY (node_id, key, subject)
);
CREATE INDEX IF NOT EXISTS alert_history_at ON alert_history(node_id, at);
CREATE TABLE IF NOT EXISTS ip_hits (
  node_id TEXT NOT NULL, ip TEXT NOT NULL, hour INTEGER NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0, c2 INTEGER NOT NULL DEFAULT 0, c3 INTEGER NOT NULL DEFAULT 0,
  c401 INTEGER NOT NULL DEFAULT 0, c403 INTEGER NOT NULL DEFAULT 0, c404 INTEGER NOT NULL DEFAULT 0,
  c4 INTEGER NOT NULL DEFAULT 0, c5 INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (node_id, ip, hour)
);
CREATE INDEX IF NOT EXISTS ip_hits_hour ON ip_hits(node_id, hour);
CREATE TABLE IF NOT EXISTS ip_seen (
  node_id TEXT NOT NULL, ip TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
  last_host TEXT NOT NULL DEFAULT '', last_path TEXT NOT NULL DEFAULT '', last_status INTEGER NOT NULL DEFAULT 0, last_ua TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (node_id, ip)
);
CREATE TABLE IF NOT EXISTS ip_paths (
  node_id TEXT NOT NULL, ip TEXT NOT NULL, path TEXT NOT NULL, host TEXT NOT NULL DEFAULT '',
  hits INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0, last_seen REAL NOT NULL,
  PRIMARY KEY (node_id, ip, path, host)
);
CREATE TABLE IF NOT EXISTS ip_attacks (
  node_id TEXT NOT NULL, ip TEXT NOT NULL, kind TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 0,
  last_seen REAL NOT NULL, sample TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (node_id, ip, kind)
);
CREATE INDEX IF NOT EXISTS ip_attacks_kind ON ip_attacks(node_id, kind, last_seen);
CREATE TABLE IF NOT EXISTS ip_bans (
  id INTEGER PRIMARY KEY, node_id TEXT NOT NULL, ip TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, expires_at REAL,
  UNIQUE (node_id, ip)
);
CREATE TABLE IF NOT EXISTS ip_trusted (
  id INTEGER PRIMARY KEY, node_id TEXT NOT NULL, ip TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
  UNIQUE (node_id, ip)
);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE, path TEXT NOT NULL, name TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('managed','ignored')), tags TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
  track_images INTEGER NOT NULL DEFAULT 0, UNIQUE (node_id, path)
);
CREATE TABLE IF NOT EXISTS project_folders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE, parent_id INTEGER, kind TEXT NOT NULL DEFAULT 'projects',
  name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', color TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS site_folder_items (
  node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE, router TEXT NOT NULL, folder_id INTEGER NOT NULL, PRIMARY KEY (node_id, router)
);
CREATE TABLE IF NOT EXISTS image_checks (
  node_id TEXT NOT NULL, ref TEXT NOT NULL, remote_digest TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', checked_at REAL NOT NULL, notified TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (node_id, ref)
);
CREATE TABLE IF NOT EXISTS project_git (
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE, username TEXT NOT NULL DEFAULT '', token TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS project_ssh (
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE, private TEXT NOT NULL, public TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS node_git_key (
  node_id TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE, private TEXT NOT NULL, public TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS project_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', data TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS project_actions (
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, action TEXT NOT NULL, steps TEXT NOT NULL,
  updated_by TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL, PRIMARY KEY (project_id, action)
);
CREATE TABLE IF NOT EXISTS project_hook (
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE, secret TEXT NOT NULL, deploy INTEGER NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, last_at REAL, last_result TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS automod_rules (
  id INTEGER PRIMARY KEY, node_id TEXT NOT NULL, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, mode TEXT NOT NULL DEFAULT 'observe',
  conditions TEXT NOT NULL, hours REAL, created_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0, last_hit REAL
);
CREATE TABLE IF NOT EXISTS automod_log (
  id INTEGER PRIMARY KEY, node_id TEXT NOT NULL, rule_id INTEGER NOT NULL, rule_name TEXT NOT NULL, ip TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS automod_log_at ON automod_log(node_id, at);
CREATE INDEX IF NOT EXISTS automod_log_check ON automod_log(node_id, rule_id, ip, action, at);
CREATE TABLE IF NOT EXISTS revoked_tokens (
  jti TEXT PRIMARY KEY, exp REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS revoked_tokens_exp ON revoked_tokens(exp);
CREATE TABLE IF NOT EXISTS log_offsets (
  node_id TEXT PRIMARY KEY, inode INTEGER NOT NULL, off INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS node_domains (
  node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
  PRIMARY KEY (node_id, domain_id)
);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(settings.data_dir / "app.db", isolation_level=None, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        _local.c = c
    return c


MIGRATIONS = {
    "users": [("totp_secret", "TEXT"), ("totp_enabled", "INTEGER NOT NULL DEFAULT 0"),
              ("totp_last_step", "INTEGER NOT NULL DEFAULT 0"), ("recovery_codes", "TEXT NOT NULL DEFAULT ''"),
              ("max_nodes", "INTEGER NOT NULL DEFAULT 0"),
              ("token_version", "INTEGER NOT NULL DEFAULT 0")],   # +1 = toutes les sessions ouvertes de la personne sont invalidées  # 0 = ne peut pas créer de nœud, -1 = illimité
    "log_offsets": [("head", "TEXT NOT NULL DEFAULT ''")],
    "nodes": [("ban_limit", "INTEGER"), ("ban_overflow", "TEXT NOT NULL DEFAULT ''"), ("mfa_caps", "TEXT NOT NULL DEFAULT ''"), ("deleted_at", "REAL"), ("prometheus_url", "TEXT NOT NULL DEFAULT ''"), ("webhook_url", "TEXT NOT NULL DEFAULT ''"),
              ("modules", "TEXT NOT NULL DEFAULT 'traefik'"),
              ("addresses", "TEXT NOT NULL DEFAULT ''"),
              ("addresses_seen", "TEXT NOT NULL DEFAULT ''"),   # adresses annoncées par le worker lui-même (WORKER_ADDRESSES)
              ("traefik_via", "TEXT NOT NULL DEFAULT ''")],   # nœud dont le Traefik publie les sites de CE nœud (ex. le hub, pour un worker « Docker seul »)   # adresses de la machine (IP, noms) : relier ses conteneurs au Traefik d'un AUTRE nœud   # ce que le nœud sait faire : traefik, docker (annoncé par le worker ; calculé pour le nœud local)
    "credentials": [("provider", "TEXT NOT NULL DEFAULT 'ovh'"), ("secret", "TEXT"), ("options", "TEXT")],  # secret : jeton chiffré des fournisseurs sans clés OVH
    "domains": [("extra", "TEXT NOT NULL DEFAULT ''")],
    "project_templates": [("shared", "INTEGER NOT NULL DEFAULT 1")],   # 0 = privé (son auteur seulement), 1 = partagé ; les modèles d'avant ce réglage restent partagés
    "image_checks": [("latest_tag", "TEXT NOT NULL DEFAULT ''"), ("notified_tag", "TEXT NOT NULL DEFAULT ''")],   # latest_tag : version plus récente trouvée au registre
    "projects": [("track_images", "INTEGER NOT NULL DEFAULT 0"), ("compose_files", "TEXT NOT NULL DEFAULT ''"), ("image_schedule", "TEXT NOT NULL DEFAULT ''"), ("image_last_run", "REAL"), ("folder_id", "INTEGER")],   # folder_id : dossier d'organisation (vide = à la racine)   # image_schedule : planning JSON de la vérification (vide = le réglage par défaut)   # compose_files : liste JSON des fichiers compose choisis (vide = automatique)
    "project_folders": [("kind", "TEXT NOT NULL DEFAULT 'projects'")],   # projects | sites : ce que le dossier range
    "node_members": [("expires_at", "REAL")],  # accès temporaire : le membre perd son rôle à cette date
    "domain_access": [("types", "TEXT NOT NULL DEFAULT ''"), ("subs", "TEXT NOT NULL DEFAULT ''"), ("no_delete", "INTEGER NOT NULL DEFAULT 0"), ("expires_at", "REAL")],  # suppression douce : le nœud est invisible mais récupérable jusqu'à sa purge
}


def init() -> None:
    conn().executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r["name"] for r in rows(f"PRAGMA table_info({table})")}
        for name, ddl in cols:
            if name not in have:
                conn().execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def rows(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    return conn().execute(sql, args).fetchall()


def one(sql: str, args: tuple = ()):
    return conn().execute(sql, args).fetchone()


def run(sql: str, args: tuple = ()) -> int:
    return conn().execute(sql, args).lastrowid


def now() -> float:
    return time.time()
