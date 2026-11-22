# Armadok : installation et maintenance en une commande.
#
#   mkdir armadok && cd armadok
#   curl -fsSLO https://raw.githubusercontent.com/Dr-J-Watson/armadok/main/Makefile
#   make install        # demande : le hub (interface complète) ou un worker (relié à un hub existant)
#
# Ce fichier est autonome : il crée docker-compose.yml, .env, data/ et (pour le hub) le routage Traefik.
# Chaque question accepte une valeur préremplie : « make worker HUB_URL=https://… ENROLL_TOKEN=enr_… » ne pose que le reste.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:
.SILENT:
.DEFAULT_GOAL := help

IMAGE_REPO ?= ghcr.io/dr-j-watson/armadok
IMAGE ?= $(IMAGE_REPO):latest
# Service du hub dans docker-compose.yml
HUB_SVC ?= armadok
export IMAGE

define COMPOSE_YML
# Généré par « make install ». La version commentée de chaque option : docker-compose.example.yml.
# Les $${VARIABLES} viennent du .env : chaque variable y est étiquetée (OBLIGATOIRE / UNE FOIS / TOUJOURS / COMPOSE).
# « make doctor » vérifie l'ensemble ; « make config » valide ce fichier.
services:
  #@certs-service
  armadok:
    image: $${ARMADOK_IMAGE:-$(IMAGE)}
    container_name: armadok
    restart: unless-stopped
    user: "$${UID:-1000}:$${GID:-1000}"
    env_file: .env                       # secrets, compte propriétaire, OVH… (lus par l'application)
    environment:                         # chemins DANS le conteneur : ne pas modifier
      TRAEFIK_DYNAMIC_DIR: /traefik-dynamic
      DATA_DIR: /data
      #@log-env
      #@certs-env
    volumes:
      - $${TRAEFIK_DYNAMIC_PATH}:/traefik-dynamic   # configuration dynamique de Traefik (écriture)
      - ./data:/data                                # base, sauvegardes, audit : À SAUVEGARDER
      #@log-vol
      #@certs-vol
      #@dk-vol
    read_only: true                      # durcissement : lecture seule, aucune capability
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    #@certs-group
    #@dk-group
    mem_limit: 512m
    pids_limit: 200
    networks: [proxy]                    # réseau partagé avec Traefik

networks:
  proxy:
    external: true
    name: $${TRAEFIK_NETWORK:-proxy}
endef
export COMPOSE_YML

define WORKER_YML
services:
  #@certs-service
  worker:
    image: $${WORKER_IMAGE:-$(IMAGE)}
    container_name: armadok-worker
    command: ["python", "-m", "app.worker"]
    restart: unless-stopped
    user: "$${UID:-1000}:$${GID:-1000}"
    environment:
      HUB_URL: $${HUB_URL}
      ENROLL_TOKEN: $${ENROLL_TOKEN:-}
      WORKER_ALLOW_INSECURE: $${WORKER_ALLOW_INSECURE:-}
      TRAEFIK_API_URL: $${TRAEFIK_API_URL:-}   #@tr
      TRAEFIK_DYNAMIC_DIR: /traefik-dynamic   #@tr
      DATA_DIR: /data
      #@log-env
      #@certs-env
      #@wk-env
    volumes:
      - $${TRAEFIK_DYNAMIC_PATH}:/traefik-dynamic   #@tr
      - ./data:/data
      #@log-vol
      #@certs-vol
      #@wk-vol
    healthcheck:
      test: ["CMD", "python", "-m", "app.worker", "--health"]
      interval: 30s
      timeout: 5s
      retries: 3
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    #@wk-group
    mem_limit: 256m
endef
export WORKER_YML

# Worker natif : TOUT est ici, dans le Makefile (aucun script à côté, rien d'autre à télécharger que le code du worker lui-même).
# Au lancement, le texte ci-dessous est écrit dans un fichier temporaire, exécuté, puis supprimé.
# Le code du worker (backend/app) est téléchargé pour la version demandée (NATIVE_REF, ex. v1.11.1 : celle du hub), sinon la dernière.
define NATIVE_SCRIPT
#!/usr/bin/env bash
# Armadok — worker NATIF (sans Docker) : vérifie la machine, installe, démarre au boot, et rend une checklist.
# Ce texte vit dans le Makefile (NATIVE_SCRIPT) : on le lance avec « make worker-native » et ses commandes jumelles.
#
#   make native-check       vérifie seulement (ne modifie rien)
#   make worker-native      vérifie, installe, démarre, attend la connexion au hub
#   make native-update      met à jour le code et les dépendances (garde .env et data/)
#   make native-status      état du service et de la connexion
#   make native-logs / native-restart / native-stop / native-start / native-backup
#   make native-uninstall   retire le service (demande avant de toucher à data/)
#
# Chaque question accepte une valeur préremplie par une variable d'environnement (voir « Variables » plus bas) ;
# NATIVE_YES=1 accepte les valeurs par défaut sans rien demander.
#
# Variables : HUB_URL, ENROLL_TOKEN, NATIVE_DIR (/opt/armadok-worker), NATIVE_REF (version du code : v1.11.0, main…),
#   NATIVE_VENV (true : environnement Python isolé), NATIVE_USER, TRAEFIK_DYNAMIC_DIR, TRAEFIK_ACCESS_LOG, TRAEFIK_CERTS_FILE,
#   NATIVE_CERTS (none | relay | direct), TRAEFIK_API_URL, WORKER_ALLOW_INSECURE (1 : hub en http, essais seulement).
set -uo pipefail

REPO="$${NATIVE_REPO:-Dr-J-Watson/armadok}"
SERVICE=armadok-worker
RELAY=armadok-certsync
UNIT_DIR="$${NATIVE_UNIT_DIR:-/etc/systemd/system}"
CMD="$${1:-install}"
shift || true
ONLY_CHECK=0
for a in "$$@"; do case "$$a" in --check) ONLY_CHECK=1;; esac; done
[ "$$CMD" = check ] && ONLY_CHECK=1

# ── présentation ────────────────────────────────────────────────────────────────────────────────────────────────
if [ -t 1 ] && [ -z "$${NO_COLOR:-}" ]; then B=$$'\033[1m'; D=$$'\033[2m'; G=$$'\033[32m'; Y=$$'\033[33m'; R=$$'\033[31m'; C=$$'\033[36m'; Z=$$'\033[0m'; else B=""; D=""; G=""; Y=""; R=""; C=""; Z=""; fi
LINE='━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
banner() { printf '\n%s%s%s\n  %s%s%s\n%s%s%s\n' "$$C" "$$LINE" "$$Z" "$$B" "$$1" "$$Z" "$$C" "$$LINE" "$$Z"; }
step() { printf '\n%s%s%s\n' "$$B" "$$1" "$$Z"; }
say() { printf '  %s\n' "$$*"; }
hint() { printf '  %s%s%s\n' "$$D" "$$*" "$$Z"; }
die() { printf '\n%s✗ %s%s\n' "$$R" "$$*" "$$Z" >&2; exit 1; }

# ── résultats de vérification : ✅ OK · ⚠ avertissement · ❌ bloquant, chacun avec sa correction ─────────────────────
N_OK=0; N_WARN=0; N_BAD=0
REPORT=()
ok()   { N_OK=$$((N_OK + 1));     REPORT+=("OK|$$1|"); }
warn() { N_WARN=$$((N_WARN + 1)); REPORT+=("WARN|$$1|$${2:-}"); }
bad()  { N_BAD=$$((N_BAD + 1));   REPORT+=("BAD|$$1|$${2:-}"); }
print_report() {
  local title="$$1" line kind msg fix
  step "$$title"
  for line in "$${REPORT[@]}"; do
    kind="$${line%%|*}"; line="$${line#*|}"; msg="$${line%%|*}"; fix="$${line#*|}"
    case "$$kind" in
      OK)   printf '  %s✅%s %s\n' "$$G" "$$Z" "$$msg";;
      WARN) printf '  %s⚠️ %s %s\n' "$$Y" "$$Z" "$$msg"; [ -n "$$fix" ] && printf '       %s→ %s%s\n' "$$D" "$$fix" "$$Z";;
      BAD)  printf '  %s❌%s %s\n' "$$R" "$$Z" "$$msg"; [ -n "$$fix" ] && printf '       %s→ %s%s\n' "$$D" "$$fix" "$$Z";;
    esac
  done
  printf '\n  %s%d OK · %d avertissement(s) · %d bloquant(s)%s\n' "$$B" "$$N_OK" "$$N_WARN" "$$N_BAD" "$$Z"
}
reset_report() { N_OK=0; N_WARN=0; N_BAD=0; REPORT=(); }
plain_report() {   # la même checklist, sans couleurs, pour le fichier install-report.txt
  local line kind msg fix mark
  for line in "$${REPORT[@]}"; do
    kind="$${line%%|*}"; line="$${line#*|}"; msg="$${line%%|*}"; fix="$${line#*|}"
    case "$$kind" in OK) mark="[OK]  ";; WARN) mark="[AVIS]";; *) mark="[ERR] ";; esac
    printf '%s %s\n' "$$mark" "$$msg"; [ -n "$$fix" ] && printf '        -> %s\n' "$$fix"
  done
  printf '\n%d OK, %d avertissement(s), %d bloquant(s)\n' "$$N_OK" "$$N_WARN" "$$N_BAD"
}

# ── questions ───────────────────────────────────────────────────────────────────────────────────────────────────
interactive() { [ -z "$${NATIVE_YES:-}" ] && [ -t 0 ]; }
# Les réponses sont gardées au fur et à mesure (hors jeton) : si vous coupez pour installer pip, par exemple, le script reprend là où il en était.
STATE="$${NATIVE_STATE:-$${XDG_CONFIG_HOME:-$${HOME:-/tmp}/.config}/armadok-worker-install.env}"
RESUME=0
state_has() { [ -f "$$STATE" ] && grep -q "^$$1=" "$$STATE" 2>/dev/null; }
state_get() { if [ -f "$$STATE" ]; then grep -E "^$$1=" "$$STATE" 2>/dev/null | tail -1 | cut -d= -f2-; fi; return 0; }
state_put() { ( umask 077; mkdir -p "$$(dirname "$$STATE")"; { grep -v "^$$1=" "$$STATE" 2>/dev/null; printf '%s=%s\n' "$$1" "$$2"; } > "$$STATE.new" && mv "$$STATE.new" "$$STATE" ) 2>/dev/null; return 0; }
state_clear() { rm -f "$$STATE" 2>/dev/null; return 0; }

# Une question = un titre, quelques lignes d'explication (avec un exemple), puis l'invite sur sa propre ligne.
# ask "titre" "défaut" VARIABLE ["explication"…]  : la variable déjà renseignée répond d'office (rien n'est demandé)
show_q() {
  local l; printf '\n%s▸ %s%s\n' "$$B" "$$1" "$$Z" >&2; shift
  for l in "$$@"; do printf '    %s%s%s\n' "$$D" "$$l" "$$Z" >&2; done
}
ask() {
  local q="$$1" def="$${2:-}" var="$${3:-}" v=""
  if [ $$# -ge 3 ]; then shift 3; else shift $$#; fi
  if [ -n "$$var" ] && [ -n "$${!var:-}" ]; then printf '%s' "$${!var}"; return; fi
  if [ -n "$$var" ] && [ "$$RESUME" = 1 ] && state_has "$$var"; then state_get "$$var"; return; fi   # réponse d'un lancement précédent
  if ! interactive; then printf '%s' "$$def"; return; fi
  show_q "$$q" "$$@"
  read -r -p "    $${B}➜$${Z} Votre réponse$${def:+ [$$def]} : " v </dev/tty || true
  v="$${v:-$$def}"
  [ -z "$$var" ] || state_put "$$var" "$$v"
  printf '%s' "$$v"
}
asks() {   # sans écho (jeton) : asks "titre" VARIABLE ["explication"…]
  local q="$$1" var="$${2:-}" v=""
  if [ $$# -ge 2 ]; then shift 2; else shift $$#; fi
  if [ -n "$$var" ] && [ -n "$${!var:-}" ]; then printf '%s' "$${!var}"; return; fi
  interactive || { printf ''; return; }
  show_q "$$q" "$$@"
  read -r -s -p "    $${B}➜$${Z} Collez ici (rien ne s'affiche), puis Entrée : " v </dev/tty || true; echo >&2
  printf '%s' "$$v"
}
yes_no() {   # yes_no "titre" [défaut o|n] ["explication"…]
  local q="$$1" def="$${2:-n}" v
  if [ $$# -ge 2 ]; then shift 2; else shift $$#; fi
  interactive || { [ "$$def" = o ]; return; }
  show_q "$$q" "$$@"
  read -r -p "    $${B}➜$${Z} Votre réponse [$$([ "$$def" = o ] && echo O/n || echo o/N)] : " v </dev/tty || true
  v="$${v:-$$def}"; case "$$v" in o|O|y|Y|oui) return 0;; *) return 1;; esac
}

# ── outils ──────────────────────────────────────────────────────────────────────────────────────────────────────
is_root() { [ "$$(id -u)" = 0 ]; }
as_user() { local u="$$1"; shift; if command -v runuser >/dev/null 2>&1; then runuser -u "$$u" -- "$$@"; else su -s /bin/sh "$$u" -c "$$(printf '%q ' "$$@")"; fi; }
version_ge() { [ "$$(printf '%s\n%s\n' "$$2" "$$1" | sort -V | head -1)" = "$$2" ]; }   # version_ge 3.12 3.11 → vrai
pick_python() {
  local c
  for c in "$${NATIVE_PYTHON:-}" python3.13 python3.12 python3; do
    [ -n "$$c" ] && command -v "$$c" >/dev/null 2>&1 || continue
    if "$$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then command -v "$$c"; return 0; fi
  done
  return 1
}
env_get() { [ -f "$$1" ] && grep -E "^$$2=" "$$1" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
# path_ok_for USER PATH : l'utilisateur peut-il traverser tous les dossiers parents ? Renvoie le premier qui bloque.
blocking_parent() {
  local u="$$1" p d mode owner
  p="$$(readlink -f "$$2" 2>/dev/null || echo "$$2")"; d="$$(dirname "$$p")"
  while [ "$$d" != "/" ] && [ -n "$$d" ]; do
    mode="$$(stat -c %a "$$d" 2>/dev/null || echo 000)"; owner="$$(stat -c %U "$$d" 2>/dev/null || echo "?")"
    if [ $$((8#$$mode & 1)) -eq 0 ] && [ "$$owner" != "$$u" ]; then
      if ! { command -v getfacl >/dev/null 2>&1 && getfacl -p "$$d" 2>/dev/null | grep -qE "^user:$$u:.*x"; }; then echo "$$d"; return 0; fi
    fi
    d="$$(dirname "$$d")"
  done
  return 1
}
can_test() { is_root || [ "$$(id -un)" = "$$1" ]; }   # tester les droits d'un AUTRE utilisateur demande root
can_write() { as_user "$$1" test -w "$$2" 2>/dev/null && as_user "$$1" test -x "$$2" 2>/dev/null; }
can_read() { as_user "$$1" test -r "$$2" 2>/dev/null; }

# ── paramètres (valeurs actuelles, ou par défaut) ──────────────────────────────────────────────────────────────────
DIR="$${NATIVE_DIR:-/opt/armadok-worker}"
ENVF="$$DIR/.env"
HUB="$$(printf '%s' "$${HUB_URL:-$$(env_get "$$ENVF" HUB_URL)}" | sed 's:/*$$::')"
USE_VENV="$${NATIVE_VENV:-$$(env_get "$$ENVF" NATIVE_VENV)}"; USE_VENV="$${USE_VENV:-true}"
case "$$USE_VENV" in true|1|yes|oui) USE_VENV=true;; *) USE_VENV=false;; esac

# ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# Vérifications
# ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
check_system() {
  [ "$$(uname -s)" = Linux ] && ok "Système : Linux ($$(uname -m))" || bad "Système : $$(uname -s)" "Seul Linux avec systemd est géré. Sur un autre système, utilisez le worker Docker Compose (make worker)."
  if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then ok "systemd présent (démarrage automatique au boot)"
  else bad "systemd absent" "Le service ne pourrait pas démarrer au boot. Utilisez le worker Docker Compose (make worker)."; fi
  if is_root; then ok "Droits : root"
  elif command -v sudo >/dev/null 2>&1; then ok "Droits : sudo disponible (l'installation le demandera)"
  else bad "Ni root ni sudo" "Lancez en root (su -) ou installez sudo."; fi
  local c
  for c in curl tar; do command -v "$$c" >/dev/null 2>&1 && ok "Outil « $$c » présent" || bad "Outil « $$c » absent" "Installez-le (apt install $$c · dnf install $$c · apk add $$c)."; done
  local free; free="$$(df -Pk "$$(dirname "$$DIR")" 2>/dev/null | awk 'NR==2{print int($$4/1024)}')"
  if [ -n "$$free" ] && [ "$$free" -ge 400 ]; then ok "Espace disque : $${free} Mo libres dans $$(dirname "$$DIR")"
  elif [ -n "$$free" ]; then bad "Espace disque : $${free} Mo libres seulement" "Prévoir 400 Mo (code, dépendances Python et données)."; fi
}

check_python() {
  PY="$$(pick_python)" || PY=""
  if [ -z "$$PY" ]; then
    bad "Python 3.12 ou plus récent introuvable" "apt install python3 python3-venv python3-pip (Debian ≥ 13, Ubuntu ≥ 24.04) · dnf install python3.12 · sinon, utilisez le worker Docker Compose."
    return
  fi
  ok "Python : $$("$$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))') ($$PY)"
  if "$$PY" -m pip --version >/dev/null 2>&1; then ok "pip présent"; else bad "pip absent" "apt install python3-pip · dnf install python3-pip"; fi
  if [ "$$USE_VENV" = true ]; then
    if "$$PY" -c 'import ensurepip, venv' 2>/dev/null; then ok "Environnement isolé (venv) : disponible"
    else bad "Le module venv n'est pas installé" "apt install python3-venv · ou relancez et répondez « n » à la question du venv (NATIVE_VENV=false : les dépendances vont alors dans $$DIR/lib, sans venv)."; fi
  else
    ok "Sans venv (NATIVE_VENV=false) : les dépendances iront dans $$DIR/lib, le système n'est pas modifié"
  fi
  if [ -f "$$("$$PY" -c 'import sysconfig; print(sysconfig.get_path("stdlib"))' 2>/dev/null)/EXTERNALLY-MANAGED" ]; then
    [ "$$USE_VENV" = true ] && ok "Python « géré par le système » (PEP 668) : sans effet, le venv le contourne" \
      || warn "Python « géré par le système » (PEP 668)" "pip refusera peut-être l'installation sans venv : le script réessaie avec --break-system-packages, uniquement dans $$DIR/lib."
  fi
}

check_hub() {
  if [ -z "$$HUB" ]; then bad "Adresse du hub non renseignée" "HUB_URL=https://hub.exemple.fr"; return; fi
  case "$$HUB" in
    https://?*) ok "Adresse du hub en HTTPS : $$HUB";;
    http://?*) if [ "$${WORKER_ALLOW_INSECURE:-}" = 1 ]; then warn "Hub en http:// (WORKER_ALLOW_INSECURE=1)" "Les échanges circulent en clair : réservé aux essais."; else bad "Hub en http://" "Le worker exige HTTPS. Pour un essai seulement : WORKER_ALLOW_INSECURE=1."; fi;;
    *) bad "Adresse du hub invalide : $$HUB" "Elle doit commencer par https://"; return;;
  esac
  command -v curl >/dev/null 2>&1 || return
  local host out code; host="$$(printf '%s' "$$HUB" | sed -E 's#^[a-z]+://([^/:]+).*#\1#')"
  if printf '%s' "$$host" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$$'; then
    ok "Le hub est joint par son adresse IP ($$host)"
  elif getent hosts "$$host" >/dev/null 2>&1; then
    local ip; ip="$$(getent hosts "$$host" | awk '{print $$1; exit}')"
    case "$$ip" in
      10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[0-1].*) warn "« $$host » se résout en adresse privée ($$ip)" "Si cette machine est hors de votre réseau local, ce nom doit renvoyer l'adresse publique du hub (DNS interne/« split DNS » ?).";;
      *) ok "« $$host » se résout ($$ip)";;
    esac
  else bad "« $$host » ne se résout pas" "Vérifiez le DNS de cette machine (/etc/resolv.conf)."; fi
  code="$$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 -X POST -H 'Content-Type: application/json' -d '{}' "$$HUB/api/worker/enroll" 2>/tmp/.armadok-curl.$$$$)"   # le chemin du worker lui-même : seul /api/worker est ouvert derrière un filtre IP ; un corps vide est refusé (422) avant tout traitement, rien n'est enrôlé
  out="$$(cat /tmp/.armadok-curl.$$$$ 2>/dev/null)"; rm -f /tmp/.armadok-curl.$$$$
  case "$$code" in
    422) ok "Le hub répond (/api/worker)";;
    404) bad "Le hub répond, mais pas Armadok sur /api/worker" "Vérifiez l'adresse (HUB_URL) et le routage Traefik : /api/worker doit arriver à Armadok.";;
    403) bad "Le hub répond 403" "Un filtre IP (middleware Traefik du hub) refuse cette machine : autorisez son adresse ou ouvrez la route /api/worker (voir « Hub derrière un filtre IP » dans le README).";;
    000) case "$$out" in
           *certificate*|*SSL*) bad "Certificat du hub refusé : $${out##*: }" "Le certificat doit être valide (Let's Encrypt, ou une autorité installée sur cette machine).";;
           *) bad "Le hub ne répond pas" "$${out:-pas de réponse} — pare-feu sortant, DNS ou hub arrêté ?";;
         esac;;
    *) bad "Le hub répond HTTP $$code" "Vérifiez l'adresse (HUB_URL) et le routage Traefik du hub.";;
  esac
  if [ -n "$${ENROLL_TOKEN:-}" ] || [ -n "$$(env_get "$$ENVF" ENROLL_TOKEN)" ] || [ -f "$$DIR/data/identity.json" ]; then
    if [ -f "$$DIR/data/identity.json" ]; then ok "Identité du worker déjà enregistrée (pas de jeton nécessaire)"
    else
      local t="$${ENROLL_TOKEN:-$$(env_get "$$ENVF" ENROLL_TOKEN)}"
      case "$$t" in enr_?*) ok "Jeton d'enrôlement au bon format (usage unique, 15 min : générez-le au dernier moment)";; *) bad "Jeton d'enrôlement invalide" "Il commence par « enr_ » (« Nouveau nœud » dans l'interface du hub).";; esac
    fi
  else warn "Aucun jeton d'enrôlement pour l'instant" "Il sera demandé à l'installation (« Nouveau nœud » dans le hub, valable 15 minutes)."; fi
}

# Choix de l'utilisateur du service : le propriétaire du dossier Traefik, ou un utilisateur dédié (+ droits ciblés).
decide_user() {
  [ -n "$${SVC_USER:-}" ] && return
  local owner uid
  owner="$$(stat -c %U "$$DYN" 2>/dev/null || echo root)"; uid="$$(stat -c %u "$$DYN" 2>/dev/null || echo 0)"
  if [ -n "$${NATIVE_USER:-}" ]; then SVC_USER="$$NATIVE_USER"
  elif [ -f "$$DIR/.service-user" ]; then SVC_USER="$$(cat "$$DIR/.service-user")"
  elif [ "$$uid" -ge 1000 ] 2>/dev/null && [ "$$owner" != root ]; then
    if interactive; then
      case "$$(ask "Sous quel utilisateur faire tourner le worker ?" 1 SVC_CHOICE \
        "Le dossier de Traefik appartient à « $$owner »." \
        "" \
        "  1 = « $$owner » : le plus simple, il a déjà accès aux fichiers de Traefik" \
        "      (aucun droit à modifier)." \
        "" \
        "  2 = un utilisateur dédié « armadok », sans mot de passe ni shell :" \
        "      l'installation lui donne uniquement l'accès aux dossiers de Traefik" \
        "      dont le worker a besoin, et rien d'autre.")" in 2) SVC_USER=armadok;; *) SVC_USER="$$owner";; esac
    else SVC_USER="$$owner"; fi
  else SVC_USER=armadok; fi
}

check_traefik() {
  DYN="$${TRAEFIK_DYNAMIC_DIR:-$$(env_get "$$ENVF" TRAEFIK_DYNAMIC_DIR)}"
  if [ -z "$$DYN" ]; then
    local guess=""
    if command -v docker >/dev/null 2>&1; then guess="$$(docker inspect traefik --format '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' 2>/dev/null | grep -iE 'dynamic|conf' | head -1)"; fi
    DYN="$$(ask "Dossier de configuration dynamique de Traefik" "$$guess" TRAEFIK_DYNAMIC_DIR \
      "Le dossier que Traefik surveille pour ses routeurs et ses services" \
      "(le « file provider », réglé par « directory: » dans traefik.yml)." \
      "Le worker y lit et y écrit les sites. Donnez le chemin complet du DOSSIER." \
      "Exemple : /home/vous/docker/traefik/dynamic")"
  fi
  [ -n "$$DYN" ] || { bad "Dossier dynamique de Traefik non renseigné" "TRAEFIK_DYNAMIC_DIR=/chemin/de/traefik/dynamic"; return; }
  case "$$DYN" in /*) ;; *) bad "« $$DYN » n'est pas un chemin absolu" "Indiquez un chemin commençant par /"; return;; esac
  if [ ! -d "$$DYN" ]; then bad "Dossier introuvable : $$DYN" "Créez-le ou corrigez le chemin (docker inspect traefik, cherchez le montage du file provider)."; return; fi
  ok "Dossier dynamique : $$DYN ($$(find "$$DYN" -type f \( -name '*.yml' -o -name '*.yaml' \) 2>/dev/null | wc -l) fichier(s) YAML)"
  decide_user
  if id "$$SVC_USER" >/dev/null 2>&1 && ! can_test "$$SVC_USER"; then
    warn "Droits de « $$SVC_USER » sur $$DYN : non testés (il faut root pour le faire)" "Relancez avec sudo pour les vérifier ; l'installation les règle de toute façon."
  elif id "$$SVC_USER" >/dev/null 2>&1; then
    if can_write "$$SVC_USER" "$$DYN" 2>/dev/null; then ok "« $$SVC_USER » peut écrire dans $$DYN"
    else
      local blk; blk="$$(blocking_parent "$$SVC_USER" "$$DYN" || true)"
      if [ -n "$$blk" ]; then warn "« $$SVC_USER » ne peut pas traverser $$blk" "À l'installation : setfacl -m u:$$SVC_USER:x $$blk (droit de passage seulement, rien d'autre)."
      else warn "« $$SVC_USER » ne peut pas écrire dans $$DYN" "À l'installation : ACL en écriture sur ce dossier seulement (setfacl -R -m u:$$SVC_USER:rwX)."; fi
    fi
  else
    ok "Utilisateur « $$SVC_USER » : à créer (sans mot de passe ni shell)"
    command -v setfacl >/dev/null 2>&1 && ok "ACL disponibles (setfacl) pour lui donner l'accès au dossier Traefik, et à lui seul" \
      || bad "setfacl absent" "apt install acl · dnf install acl (ou lancez le service sous le propriétaire du dossier : NATIVE_USER=…)."
  fi
  case "$$DYN" in /home/*) warn "Le dossier Traefik est dans /home" "Le service y reçoit un accès en écriture ciblé (ProtectHome en lecture seule, sauf ce dossier).";; esac
  # état en direct (facultatif)
  API="$${TRAEFIK_API_URL:-$$(env_get "$$ENVF" TRAEFIK_API_URL)}"
  if [ -z "$$API" ] && interactive; then
    API="$$(ask "API de Traefik (facultatif)" "" TRAEFIK_API_URL \
      "L'adresse de l'API de Traefik : elle sert à afficher l'état en direct des sites dans le hub." \
      "Elle doit être joignable depuis cette machine (le plus souvent en local)." \
      "Exemple : http://127.0.0.1:8080" \
      "Laissez vide pour ignorer.")"
  fi
  if [ -n "$$API" ]; then
    if curl -fsS --max-time 5 "$$API/api/version" >/dev/null 2>&1; then ok "API de Traefik joignable ($$API) : état en direct des sites"
    else warn "API de Traefik injoignable ($$API)" "Le hub n'affichera pas l'état en direct (tout le reste fonctionne). Si Traefik est dans Docker, publiez son API sur 127.0.0.1:8080."; fi
  else warn "API de Traefik non renseignée (facultatif)" "TRAEFIK_API_URL=http://127.0.0.1:8080 pour afficher l'état en direct des sites."; fi
  # journal d'accès (facultatif)
  LOGF="$${TRAEFIK_ACCESS_LOG:-$$(env_get "$$ENVF" TRAEFIK_ACCESS_LOG)}"
  if [ -z "$$LOGF" ] && interactive; then LOGF="$$(ask "Journal d'accès de Traefik (facultatif)" "" TRAEFIK_ACCESS_LOG \
    "Le FICHIER où Traefik note chaque visite, au format JSON." \
    "Il sert aux statistiques de visiteurs, aux alertes et aux bannissements." \
    "Exemple : /var/log/traefik/access.log" \
    "Laissez vide pour ignorer.")"; fi
  if [ -n "$$LOGF" ]; then
    if [ -f "$$LOGF" ]; then
      ok "Journal d'accès : $$LOGF ($$(du -h "$$LOGF" | cut -f1))"
      head -c 4000 "$$LOGF" | head -1 | grep -q '^{' && ok "Format JSON détecté" || warn "Le journal ne semble pas être en JSON" "Traefik : --accesslog.format=json (sinon les statistiques seront limitées)."
      id "$$SVC_USER" >/dev/null 2>&1 && can_test "$$SVC_USER" && ! can_read "$$SVC_USER" "$$LOGF" && warn "« $$SVC_USER » ne peut pas lire $$LOGF" "À l'installation : ACL en lecture seule sur ce fichier (et droit de passage sur ses dossiers)."
    else warn "Journal d'accès introuvable : $$LOGF" "Traefik doit l'écrire (--accesslog.filepath=$$LOGF --accesslog.format=json). Sans lui, pas de statistiques de visiteurs pour ce nœud."; fi
  else warn "Journal d'accès non branché (facultatif)" "Sans lui : pas de statistiques de visiteurs, d'alertes ni de bannissements pour ce nœud."; fi
  # certificats (facultatif)
  ACME="$${TRAEFIK_CERTS_FILE:-$$(env_get "$$ENVF" ACME_FILE)}"
  CERTS_MODE="$${NATIVE_CERTS:-$$(env_get "$$ENVF" NATIVE_CERTS)}"
  if [ -z "$$ACME" ] && [ -z "$$CERTS_MODE" ] && interactive; then
    local aguess=""
    if command -v docker >/dev/null 2>&1; then aguess="$$(docker inspect traefik --format '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' 2>/dev/null | grep -i acme | head -1)"; fi
    ACME="$$(ask "Certificats de Traefik : fichier acme.json (facultatif)" "$$aguess" TRAEFIK_CERTS_FILE \
      "Le FICHIER acme.json où Traefik range ses certificats Let's Encrypt (pas son dossier)." \
      "Il sert seulement à afficher les certificats et leurs dates d'expiration dans le hub." \
      "Exemple : /var/lib/traefik/acme.json" \
      "Laissez vide pour ignorer.")"
  fi
  if [ -n "$$ACME" ] && [ -d "$$ACME" ]; then   # un dossier : on cherche le fichier dedans
    if [ -f "$$ACME/acme.json" ]; then ok "« $$ACME » est un dossier : j'utilise le fichier $$ACME/acme.json"; ACME="$$ACME/acme.json"
    else warn "« $$ACME » est un dossier, pas le fichier acme.json" "Indiquez le fichier lui-même (par exemple $$ACME/acme.json). Il n'y en a pas dans ce dossier pour l'instant."; ACME=""; CERTS_MODE=none; fi
  fi
  if [ -n "$$ACME" ]; then
    if [ -f "$$ACME" ]; then
      ok "acme.json : $$ACME"
      case "$$(stat -c %a "$$ACME")" in 600|400) ok "acme.json en mode $$(stat -c %a "$$ACME") : les clés privées sont protégées";; *) warn "acme.json en mode $$(stat -c %a "$$ACME")" "Traefik l'exige en 600 : chmod 600 $$ACME";; esac
      if [ -z "$$CERTS_MODE" ]; then
        if interactive; then
          case "$$(ask "Comment lire les certificats ?" 1 CERTS_CHOICE \
            "  1 = relais (recommandé) : un petit service isolé, sans accès au réseau," \
            "      lit acme.json et ne transmet que les noms et les dates." \
            "      Le worker ne voit jamais les clés privées." \
            "" \
            "  2 = direct : le worker lit acme.json lui-même" \
            "      (il a donc accès aux clés privées)." \
            "" \
            "  3 = ignorer : aucun certificat n'est affiché.")" in 2) CERTS_MODE=direct;; 3) CERTS_MODE=none;; *) CERTS_MODE=relay;; esac
        else CERTS_MODE=relay; fi
      fi
    else warn "Fichier acme.json introuvable : $$ACME" "Donnez le chemin complet du fichier (par exemple /var/lib/traefik/acme.json). Sans lui, les certificats ne seront pas affichés (le reste fonctionne)."; CERTS_MODE=none; fi
  else CERTS_MODE="$${CERTS_MODE:-none}"; fi
  [ "$$CERTS_MODE" = none ] && warn "Certificats non affichés (facultatif)" "TRAEFIK_CERTS_FILE=/chemin/acme.json, puis relais ou lecture directe."
}

check_existing() {
  if [ -f "$$UNIT_DIR/$$SERVICE.service" ] || [ -d "$$DIR/app" ]; then
    warn "Une installation existe déjà dans $$DIR" "« update » la met à jour en gardant .env et data/ ; « uninstall » la retire."
  else ok "Aucune installation précédente dans $$DIR"; fi
}

# ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# Installation
# ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
latest_ref() {
  local t
  t="$$(curl -fsS --max-time 10 "https://api.github.com/repos/$$REPO/releases/latest" 2>/dev/null | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)"
  printf '%s' "$${t:-main}"
}
fetch_code() {   # télécharge la version demandée dans $$DIR/app (et requirements.lock)
  local ref="$$1" tmp url
  tmp="$$(mktemp -d)"
  case "$$ref" in v[0-9]*|[0-9]*) url="https://github.com/$$REPO/archive/refs/tags/$${ref}.tar.gz";; *) url="https://github.com/$$REPO/archive/refs/heads/$${ref}.tar.gz";; esac
  say "Téléchargement du code ($$ref)…"
  curl -fsSL --max-time 120 "$$url" -o "$$tmp/src.tgz" || { rm -rf "$$tmp"; die "Téléchargement impossible : $$url"; }
  mkdir -p "$$tmp/x" && tar -xzf "$$tmp/src.tgz" -C "$$tmp/x" --strip-components=1 || { rm -rf "$$tmp"; die "Archive illisible"; }
  [ -d "$$tmp/x/backend/app" ] && [ -f "$$tmp/x/backend/requirements.lock" ] || { rm -rf "$$tmp"; die "L'archive ne contient pas backend/app (version « $$ref » inconnue ?)"; }
  rm -rf "$$DIR/app.new" && cp -a "$$tmp/x/backend/app" "$$DIR/app.new"
  rm -rf "$$DIR/app" && mv "$$DIR/app.new" "$$DIR/app"
  cp "$$tmp/x/backend/requirements.lock" "$$DIR/requirements.lock"
  rm -rf "$$DIR/app/tests" "$$tmp"
  printf '%s\n' "$$ref" > "$$DIR/VERSION"
}
pip_install() {
  if [ "$$USE_VENV" = true ]; then
    [ -x "$$DIR/venv/bin/python" ] || "$$PY" -m venv "$$DIR/venv" || die "Création du venv impossible (apt install python3-venv, ou NATIVE_VENV=false)."
    "$$DIR/venv/bin/pip" install --quiet --disable-pip-version-check --require-hashes -r "$$DIR/requirements.lock" || die "Installation des dépendances impossible."
  else
    mkdir -p "$$DIR/lib"
    "$$PY" -m pip install --quiet --disable-pip-version-check --require-hashes --target "$$DIR/lib" -r "$$DIR/requirements.lock" 2>"$$DIR/.pip.err" \
      || "$$PY" -m pip install --quiet --disable-pip-version-check --break-system-packages --require-hashes --target "$$DIR/lib" -r "$$DIR/requirements.lock" \
      || { cat "$$DIR/.pip.err" >&2; die "Installation des dépendances impossible."; }
    rm -f "$$DIR/.pip.err"
  fi
}
python_bin() { if [ "$$USE_VENV" = true ]; then echo "$$DIR/venv/bin/python"; else echo "$$PY"; fi; }

grant_path() {   # grant_path MODE CHEMIN : droit ciblé pour SVC_USER (r, rw) + droit de passage sur les dossiers parents
  local mode="$$1" path="$$2" blk
  [ "$$SVC_USER" = "$$(stat -c %U "$$path" 2>/dev/null)" ] && return 0
  command -v setfacl >/dev/null 2>&1 || return 1
  while blk="$$(blocking_parent "$$SVC_USER" "$$path")" && [ -n "$$blk" ]; do setfacl -m "u:$$SVC_USER:x" "$$blk" || return 1; done
  if [ -d "$$path" ]; then
    setfacl -R -m "u:$$SVC_USER:$$([ "$$mode" = rw ] && echo rwX || echo rX)" "$$path" && setfacl -d -m "u:$$SVC_USER:$$([ "$$mode" = rw ] && echo rwX || echo rX)" "$$path"
  else setfacl -m "u:$$SVC_USER:r" "$$path"; fi
}

write_env() {
  local tok="$$1"
  ( umask 077
    {
      echo "# .env du worker natif, généré par « make worker-native » le $$(date +%F). Lu par systemd (EnvironmentFile)."
      echo "HUB_URL=$$HUB"
      [ -n "$$tok" ] && { echo "# Jeton d'enrôlement : usage unique, 15 minutes. Retiré dès que le worker est connecté."; echo "ENROLL_TOKEN=$$tok"; }
      [ "$${WORKER_ALLOW_INSECURE:-}" = 1 ] && echo "WORKER_ALLOW_INSECURE=1"
      echo "WORKER_MODULES=traefik"
      echo "TRAEFIK_DYNAMIC_DIR=$$DYN"
      echo "DATA_DIR=$$DIR/data"
      [ -n "$${API:-}" ] && echo "TRAEFIK_API_URL=$$API"
      [ -n "$${LOGF:-}" ] && echo "TRAEFIK_ACCESS_LOG=$$LOGF"
      case "$$CERTS_MODE" in relay) echo "TRAEFIK_CERTS_FILE=$$DIR/data/certs.json";; direct) echo "TRAEFIK_CERTS_FILE=$$ACME";; esac
      echo "# Réglages de l'installation (lus par « make worker-native »)"
      echo "NATIVE_VENV=$$USE_VENV"
      echo "NATIVE_CERTS=$$CERTS_MODE"
      [ -n "$${ACME:-}" ] && echo "ACME_FILE=$$ACME"
    } > "$$ENVF.new" )
  mv "$$ENVF.new" "$$ENVF"; chmod 600 "$$ENVF"; chown root:root "$$ENVF" 2>/dev/null || true
}

write_units() {
  local pypath="$$DIR" rw="$$DIR/data $$DYN" ro=""
  [ "$$USE_VENV" = false ] && pypath="$$DIR:$$DIR/lib"
  mkdir -p "$$UNIT_DIR" || die "Dossier des services introuvable : $$UNIT_DIR"
  [ -n "$${LOGF:-}" ] && ro="$$ro $$(dirname "$$LOGF")"
  [ "$$CERTS_MODE" = direct ] && ro="$$ro $$(dirname "$$ACME")"
  cat > "$$UNIT_DIR/$$SERVICE.service" <<EOF
[Unit]
Description=Armadok worker (Traefik de cette machine, relié au hub)
Documentation=https://github.com/$$REPO
After=network-online.target
Wants=network-online.target

[Service]
User=$$SVC_USER
WorkingDirectory=$$DIR
EnvironmentFile=$$ENVF
Environment=PYTHONPATH=$$pypath
Environment=PYTHONUNBUFFERED=1
ExecStart=$$(python_bin) -m app.worker
Restart=always
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$$rw
$${ro:+ReadOnlyPaths=$${ro# }}
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
MemoryMax=256M

[Install]
WantedBy=multi-user.target
EOF
  if [ "$$CERTS_MODE" = relay ]; then
    cat > "$$UNIT_DIR/$$RELAY.service" <<EOF
[Unit]
Description=Armadok : relevé des certificats (lit acme.json, n'écrit que noms et dates, aucun réseau)
After=local-fs.target

[Service]
User=root
WorkingDirectory=$$DIR
Environment=PYTHONPATH=$$pypath
Environment=CERTSYNC_IN=$$ACME
Environment=CERTSYNC_OUT=$$DIR/data/certs.json
ExecStart=$$(python_bin) -m app.certsync
Restart=always
RestartSec=10
PrivateNetwork=yes
CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$$DIR/data
ReadOnlyPaths=$$(dirname "$$ACME")
MemoryMax=128M

[Install]
WantedBy=multi-user.target
EOF
  else rm -f "$$UNIT_DIR/$$RELAY.service"; fi
}

wait_connected() {   # attend « connecté au hub » dans le journal (90 s)
  local since="$$1"
  for _ in $$(seq 1 45); do
    journalctl -u "$$SERVICE" --since "$$since" --no-pager 2>/dev/null | grep -q "connecté au hub" && return 0
    systemctl is-active --quiet "$$SERVICE" || return 2
    sleep 2
  done
  return 1
}

final_checklist() {
  reset_report
  systemctl is-active --quiet "$$SERVICE" && ok "Service « $$SERVICE » démarré" || bad "Service « $$SERVICE » arrêté" "journalctl -u $$SERVICE -n 50"
  systemctl is-enabled --quiet "$$SERVICE" 2>/dev/null && ok "Démarrage automatique au boot activé" || bad "Démarrage au boot non activé" "systemctl enable $$SERVICE"
  [ "$$(systemctl show -p Restart --value "$$SERVICE" 2>/dev/null)" = always ] && ok "Redémarrage automatique en cas d'arrêt (Restart=always)" || warn "Redémarrage automatique absent"
  [ -f "$$DIR/data/identity.json" ] && ok "Worker enrôlé (identité enregistrée, la clé ne quitte pas cette machine)" || warn "Worker pas encore enrôlé" "Vérifiez le jeton (usage unique, 15 min) : journalctl -u $$SERVICE -n 30"
  if journalctl -u "$$SERVICE" --since "-5min" --no-pager 2>/dev/null | grep -q "connecté au hub"; then ok "Connecté au hub ($$HUB)"; else bad "Pas (encore) connecté au hub" "journalctl -u $$SERVICE -n 30"; fi
  if [ -f "$$DIR/data/identity.json" ] && [ -z "$$(env_get "$$ENVF" ENROLL_TOKEN)" ]; then ok "Jeton d'enrôlement retiré du .env"; elif [ -n "$$(env_get "$$ENVF" ENROLL_TOKEN)" ]; then warn "Jeton encore dans le .env" "Il est inutile après l'enrôlement : retirez la ligne ENROLL_TOKEN."; fi
  [ "$$(stat -c %a "$$ENVF")" = 600 ] && ok ".env en mode 600" || warn ".env en mode $$(stat -c %a "$$ENVF")" "chmod 600 $$ENVF"
  as_user "$$SVC_USER" test -w "$$DYN" && ok "Écriture dans $$DYN par « $$SVC_USER »" || bad "« $$SVC_USER » ne peut pas écrire dans $$DYN" "setfacl -R -m u:$$SVC_USER:rwX $$DYN"
  case "$$CERTS_MODE" in
    relay) systemctl is-active --quiet "$$RELAY" && ok "Certificats : relais actif (sans clés, sans réseau)" || warn "Relais des certificats arrêté" "journalctl -u $$RELAY -n 30";;
    direct) warn "Certificats lus directement par le worker (ses clés privées lui sont accessibles)" "Préférez le relais : NATIVE_CERTS=relay.";;
    *) warn "Certificats non affichés (facultatif)";;
  esac
  [ -n "$${LOGF:-}" ] && { as_user "$$SVC_USER" test -r "$$LOGF" && ok "Journal d'accès lisible" || warn "Journal d'accès illisible par « $$SVC_USER »" "setfacl -m u:$$SVC_USER:r $$LOGF"; }
  [ -f "$$DIR/app/worker.py" ] && ok "Code installé : $$(cat "$$DIR/VERSION" 2>/dev/null) dans $$DIR/app"
}

resume_check() {
  if [ "$${NATIVE_RESUME:-}" = 1 ] && [ -s "$$STATE" ]; then   # « make continue » : on reprend sans reposer la question
    RESUME=1; say "Reprise de l'installation interrompue (réponses gardées dans $$STATE)."
    if [ -z "$${NATIVE_DIR:-}" ] && state_has NATIVE_DIR; then DIR="$$(state_get NATIVE_DIR)"; ENVF="$$DIR/.env"; fi
    return 0
  fi
  [ -s "$$STATE" ] && interactive || return 0
  local lines=() pair k label v
  for pair in "NATIVE_DIR:Dossier d'installation" "HUB_URL:Adresse du hub" "VENV_CHOICE:Environnement Python isolé (o = venv, n = sans)" "TRAEFIK_DYNAMIC_DIR:Dossier dynamique de Traefik" "TRAEFIK_API_URL:API de Traefik" "SVC_CHOICE:Utilisateur du service (1 = propriétaire du dossier, 2 = armadok)" \
              "TRAEFIK_ACCESS_LOG:Journal d'accès" "TRAEFIK_CERTS_FILE:Fichier acme.json" "CERTS_CHOICE:Lecture des certificats (1 = relais, 2 = direct, 3 = ignorer)"; do
    k="$${pair%%:*}"; label="$${pair#*:}"; state_has "$$k" || continue
    v="$$(state_get "$$k")"; lines+=("  • $$label : $${v:-(ignoré)}")
  done
  [ "$${#lines[@]}" -gt 0 ] || return 0
  if yes_no "Reprendre l'installation commencée tout à l'heure ?" o \
      "Vos réponses précédentes ont été gardées (jamais le jeton d'enrôlement) :" "" "$${lines[@]}" "" \
      "Répondez « n » pour tout reposer depuis le début."; then
    RESUME=1
    if [ -z "$${NATIVE_DIR:-}" ] && state_has NATIVE_DIR; then DIR="$$(state_get NATIVE_DIR)"; ENVF="$$DIR/.env"; fi
  else state_clear; fi
}

do_install() {
  banner "Armadok — worker natif (sans Docker)"
  resume_check
  printf '\n'
  hint "Ce worker relie le Traefik de cette machine à votre hub Armadok."
  hint "C'est lui qui appelle le hub (connexion sortante) : aucun port à ouvrir ici."
  hint "Rien n'est modifié tant que les vérifications n'ont pas toutes été faites."
  if [ -z "$${NATIVE_DIR:-}" ] && [ ! -f "$$DIR/.service-user" ] && [ "$$ONLY_CHECK" != 1 ]; then
    DIR="$$(ask "Où installer le worker ?" "$$DIR" NATIVE_DIR \
      "Le dossier qui contiendra le code, l'environnement Python, la configuration (.env)" \
      "et l'identité du nœud. Il est créé s'il n'existe pas." \
      "Exemple : /opt/armadok-worker")"
    DIR="$${DIR%/}"; ENVF="$$DIR/.env"
  fi
  if [ -z "$${NATIVE_VENV:-}" ] && [ -z "$$(env_get "$$ENVF" NATIVE_VENV)" ]; then
    case "$$(ask "Isoler les dépendances Python dans un « venv » ?" o VENV_CHOICE \
      "Un venv est un petit dossier Python réservé au worker : il ne touche pas au Python de la machine." \
      "" \
      "  o = oui (recommandé)" \
      "" \
      "  n = non : à choisir si votre VPS n'a pas le module « python3-venv » ou le refuse." \
      "      Les dépendances vont alors dans le dossier lib/ du worker ;" \
      "      le Python du système n'est pas modifié non plus.")" in n|N|non|false|0) USE_VENV=false;; *) USE_VENV=true;; esac
  fi
  [ -n "$$HUB" ] || HUB="$$(ask "Adresse du hub" "" HUB_URL \
    "L'adresse web de l'interface Armadok, celle que vous ouvrez dans votre navigateur." \
    "Elle doit commencer par https://" \
    "Exemple : https://armadok.exemple.fr" | sed 's:/*$$::')"
  reset_report
  check_system; check_python; check_hub; check_traefik; check_existing
  print_report "Checklist avant installation"
  if [ "$$N_BAD" -gt 0 ]; then printf '\n%s✗ Des points bloquants empêchent l'\''installation : corrigez-les puis relancez.%s\n' "$$R" "$$Z"; [ "$$ONLY_CHECK" = 1 ] || exit 1; fi
  [ "$$ONLY_CHECK" = 1 ] && { [ "$$N_BAD" -gt 0 ] && exit 1; printf '\n%s✓ Cette machine peut accueillir le worker natif.%s\n' "$$G" "$$Z"; exit 0; }
  if [ "$$N_WARN" -gt 0 ] && interactive; then yes_no "Continuer malgré les avertissements ?" o \
    "Les avertissements ⚠ ne bloquent rien : ce sont des fonctions facultatives qui" \
    "ne seront pas disponibles, ou des droits que l'installation règle elle-même." || exit 1; fi
  is_root || { say "Les droits root sont nécessaires : relance via sudo…"; exec sudo -E bash "$$0" install; }

  local tok=""
  if [ ! -f "$$DIR/data/identity.json" ]; then
    tok="$${ENROLL_TOKEN:-$$(env_get "$$ENVF" ENROLL_TOKEN)}"
    [ -n "$$tok" ] || tok="$$(asks "Jeton d'enrôlement" ENROLL_TOKEN \
      "Dans l'interface du hub : « Nouveau nœud », puis copiez le jeton affiché" \
      "(il commence par enr_)." \
      "Il expire 15 minutes après sa création et ne sert qu'une fois :" \
      "générez-le maintenant, pas avant.")"
    case "$$tok" in enr_?*) ;; *) die "Jeton invalide : il commence par « enr_ ».";; esac
  fi

  step "Installation"
  if ! id "$$SVC_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$$DIR" --no-create-home --shell /usr/sbin/nologin "$$SVC_USER" || die "Création de l'utilisateur « $$SVC_USER » impossible."
    mkdir -p "$$DIR" && echo "$$SVC_USER" > "$$DIR/.created-user"
    say "Utilisateur système « $$SVC_USER » créé (sans mot de passe ni shell)."
  fi
  echo "$$SVC_USER" > "$$DIR/.service-user" 2>/dev/null || { mkdir -p "$$DIR"; echo "$$SVC_USER" > "$$DIR/.service-user"; }
  mkdir -p "$$DIR/data"; chmod 755 "$$DIR"; chmod 700 "$$DIR/data"; chown "$$SVC_USER" "$$DIR/data" 2>/dev/null
  # droits ciblés sur Traefik (jamais plus que nécessaire)
  grant_path rw "$$DYN" || die "Impossible de donner l'accès à $$DYN (setfacl). Lancez le service sous le propriétaire du dossier : NATIVE_USER=<utilisateur>."
  [ -n "$${LOGF:-}" ] && { grant_path r "$$LOGF" || warn "Journal d'accès non lisible"; }
  [ "$$CERTS_MODE" = direct ] && { grant_path r "$$ACME" || warn "acme.json non lisible"; }
  fetch_code "$${NATIVE_REF:-$$(latest_ref)}"
  say "Installation des dépendances Python ($$([ "$$USE_VENV" = true ] && echo "venv" || echo "dossier lib, sans venv"))…"
  pip_install
  write_env "$$tok"
  write_units
  chown -R root:root "$$DIR/app" "$$DIR/requirements.lock" 2>/dev/null
  [ -d "$$DIR/venv" ] && chown -R root:root "$$DIR/venv"; [ -d "$$DIR/lib" ] && chown -R root:root "$$DIR/lib"
  systemctl daemon-reload
  local since; since="$$(date '+%Y-%m-%d %H:%M:%S')"
  systemctl enable --now "$$SERVICE" >/dev/null 2>&1 || die "Démarrage du service impossible : journalctl -u $$SERVICE -n 50"
  [ "$$CERTS_MODE" = relay ] && systemctl enable --now "$$RELAY" >/dev/null 2>&1
  step "Connexion au hub"
  say "En attente de la connexion (90 s au plus)…"
  wait_connected "$$since"; local rc=$$?
  if [ "$$rc" = 0 ] && [ -f "$$DIR/data/identity.json" ]; then
    sed -i '/^ENROLL_TOKEN=/d;/^# Jeton d.enrôlement/d' "$$ENVF"
    systemctl restart "$$SERVICE" >/dev/null 2>&1
  fi
  final_checklist
  state_clear   # installé : les réponses sont dans le .env
  print_report "Checklist après installation"
  { echo "Armadok — worker natif — installé le $$(date '+%F %T')"; echo "Dossier : $$DIR · utilisateur : $$SVC_USER · hub : $$HUB"; echo; plain_report; } > "$$DIR/install-report.txt"
  chmod 644 "$$DIR/install-report.txt"
  printf '\n  %sRapport enregistré : %s/install-report.txt%s\n' "$$D" "$$DIR" "$$Z"
  hint "Commandes utiles : journalctl -u $$SERVICE -f · systemctl status $$SERVICE · make native-update · make native-uninstall"
  [ "$$N_BAD" -gt 0 ] && exit 1
  exit 0
}

do_update() {
  is_root || exec sudo -E bash "$$0" update
  [ -f "$$ENVF" ] && [ -d "$$DIR/app" ] || die "Aucune installation dans $$DIR."
  PY="$$(pick_python)" || die "Python 3.12 ou plus récent introuvable."
  SVC_USER="$$(cat "$$DIR/.service-user" 2>/dev/null || echo armadok)"
  local old; old="$$(cat "$$DIR/VERSION" 2>/dev/null || echo ?)"
  fetch_code "$${NATIVE_REF:-$$(latest_ref)}"
  pip_install
  chown -R root:root "$$DIR/app" "$$DIR/requirements.lock" 2>/dev/null
  systemctl restart "$$SERVICE" && say "Mis à jour : $$old → $$(cat "$$DIR/VERSION")."
  systemctl is-active --quiet "$$RELAY" 2>/dev/null && systemctl restart "$$RELAY"
  hint "Utilisez la même version que le hub : le protocole entre les deux évolue avec les versions."
}

do_status() {
  is_root || exec sudo -E bash "$$0" status
  [ -f "$$ENVF" ] || die "Aucune installation dans $$DIR."
  SVC_USER="$$(cat "$$DIR/.service-user" 2>/dev/null || echo armadok)"; DYN="$$(env_get "$$ENVF" TRAEFIK_DYNAMIC_DIR)"; CERTS_MODE="$$(env_get "$$ENVF" NATIVE_CERTS)"; CERTS_MODE="$${CERTS_MODE:-none}"
  ACME="$$(env_get "$$ENVF" ACME_FILE)"; LOGF="$$(env_get "$$ENVF" TRAEFIK_ACCESS_LOG)"
  reset_report; final_checklist; print_report "État du worker natif"
  exit $$((N_BAD > 0))
}

do_uninstall() {
  is_root || exec sudo -E bash "$$0" uninstall
  [ -f "$$UNIT_DIR/$$SERVICE.service" ] || [ -d "$$DIR/app" ] || [ -d "$$DIR/data" ] || die "Aucune installation trouvée dans $$DIR."
  banner "Armadok — désinstallation du worker natif"
  systemctl disable --now "$$SERVICE" >/dev/null 2>&1; systemctl disable --now "$$RELAY" >/dev/null 2>&1
  rm -f "$$UNIT_DIR/$$SERVICE.service" "$$UNIT_DIR/$$RELAY.service"; systemctl daemon-reload
  local u dyn; u="$$(cat "$$DIR/.service-user" 2>/dev/null || true)"; dyn="$$(env_get "$$ENVF" TRAEFIK_DYNAMIC_DIR)"
  if [ -n "$$u" ] && command -v setfacl >/dev/null 2>&1; then
    [ -n "$$dyn" ] && [ -d "$$dyn" ] && setfacl -R -x "u:$$u" "$$dyn" 2>/dev/null && setfacl -R -d -x "u:$$u" "$$dyn" 2>/dev/null
    say "Droits (ACL) de « $$u » retirés du dossier Traefik."
  fi
  : "$${DIR:?}"
  rm -rf "$${DIR:?}/app" "$${DIR:?}/venv" "$${DIR:?}/lib" "$${DIR:?}/requirements.lock" "$${DIR:?}/VERSION"
  say "Service, code et dépendances retirés."
  if [ -d "$$DIR/data" ]; then
    if yes_no "Supprimer aussi l'identité du nœud ?" n \
      "Le dossier data/ contient la clé de ce nœud. Si vous le supprimez," \
      "le hub ne reconnaîtra plus cette machine : il faudra créer un nouveau nœud" \
      "(« Nouveau nœud » dans le hub) et refaire l'enrôlement."; then rm -rf "$$DIR/data" "$$ENVF" "$$DIR/install-report.txt"; say "data/ et .env supprimés."
    else say "data/ et .env conservés dans $$DIR (réinstallation possible sans nouveau jeton)."; fi
  fi
  if [ -f "$$DIR/.created-user" ] && [ ! -d "$$DIR/data" ]; then userdel "$$(cat "$$DIR/.created-user")" 2>/dev/null && say "Utilisateur « $$(cat "$$DIR/.created-user") » supprimé."; fi
  [ -d "$$DIR/data" ] || rm -rf "$$DIR"
  state_clear
  printf '\n%s✓ Désinstallé.%s\n' "$$G" "$$Z"
}

# ── au quotidien ─────────────────────────────────────────────────────────────────────────────────────────────────
need_install() { [ -f "$$ENVF" ] || die "Aucune installation dans $$DIR (si elle est ailleurs : NATIVE_DIR=/chemin)."; }
relay_here() { [ -f "$$UNIT_DIR/$$RELAY.service" ]; }

do_logs() {   # les journaux de systemd : en direct, 100 dernières lignes d'abord (NATIVE_LINES pour changer)
  need_install; is_root || exec sudo -E bash "$$0" logs
  local units=(-u "$$SERVICE"); relay_here && units+=(-u "$$RELAY")
  hint "Journaux du worker (Ctrl+C pour quitter)."
  exec journalctl "$${units[@]}" -f -n "$${NATIVE_LINES:-100}" --no-pager
}

do_service() {   # start | stop | restart
  local act="$$1"
  need_install; is_root || exec sudo -E bash "$$0" "$$act"
  [ -f "$$UNIT_DIR/$$SERVICE.service" ] || die "Le service $$SERVICE n'est pas installé (make worker-native)."
  if relay_here; then systemctl "$$act" "$$RELAY" >/dev/null 2>&1 || true; fi
  systemctl "$$act" "$$SERVICE" || die "systemctl $$act $$SERVICE a échoué : journalctl -u $$SERVICE -n 30"
  case "$$act" in
    stop) say "Worker arrêté. Il redémarrera au prochain démarrage de la machine (pour l'en empêcher : systemctl disable $$SERVICE).";;
    *)
      say "Service $$([ "$$act" = start ] && echo démarré || echo redémarré) : attente de la connexion au hub (30 s au plus)…"
      for _ in $$(seq 1 15); do
        journalctl -u "$$SERVICE" --since "-1min" --no-pager 2>/dev/null | grep -q "connecté au hub" && { printf '  %s✅%s Connecté au hub.\n' "$$G" "$$Z"; return 0; }
        sleep 2
      done
      printf '  %s⚠️ %s Pas encore connecté.\n' "$$Y" "$$Z"; hint "→ journalctl -u $$SERVICE -n 30   ou   make native-logs"
    ;;
  esac
}

do_backup() {   # l'identité du nœud (clé) et la configuration, dans une archive datée du dossier courant
  need_install; is_root || exec sudo -E bash "$$0" backup
  local f; f="armadok-worker-data-$$(date +%Y%m%d-%H%M%S).tar.gz"
  [ -d "$$DIR/data" ] || die "Pas de dossier data/ dans $$DIR (le worker n'a pas encore été enrôlé)."
  ( umask 077; tar czf "$$f" -C "$$DIR" data .env ) || die "Sauvegarde impossible."
  [ -n "$${SUDO_USER:-}" ] && chown "$$SUDO_USER" "$$f" 2>/dev/null
  chmod 600 "$$f"
  printf '  %s✅%s Sauvegarde : %s/%s\n' "$$G" "$$Z" "$$PWD" "$$f"
  hint "Elle contient la CLÉ de ce nœud : gardez-la en lieu sûr. Pour restaurer : arrêtez le worker, extrayez l'archive dans $$DIR, redémarrez."
}

case "$$CMD" in
  install|check) do_install;;
  logs) do_logs;;
  start|stop|restart) do_service "$$CMD";;
  backup) do_backup;;
  update) do_update;;
  status) do_status;;
  uninstall) do_uninstall;;
  *) die "Commande inconnue : $$CMD (check, install, update, status, logs, start, stop, restart, backup, uninstall)";;
esac
endef
export NATIVE_SCRIPT
define NATIVE_RUN
f=$$(mktemp) || exit 1
trap 'rm -f "$$f"' EXIT
printf '%s\n' "$$NATIVE_SCRIPT" > "$$f"
bash "$$f" $(1)
endef

# Fonctions communes aux installations. ask : question avec valeur par défaut, ou valeur préremplie par une variable
# (3e argument). asks : idem sans écho (secrets). yes : confirmation o/N.
define SH_FUNCS
# --- Présentation : couleurs (seulement dans un terminal ; NO_COLOR les coupe), titres, textes d'aide espacés.
if [ -t 1 ] && [ -z "$${NO_COLOR:-}" ]; then B=$$'\033[1m'; D=$$'\033[2m'; G=$$'\033[32m'; Y=$$'\033[33m'; R=$$'\033[31m'; C=$$'\033[36m'; Z=$$'\033[0m'; else B=""; D=""; G=""; Y=""; R=""; C=""; Z=""; fi
LINE='━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
banner() { printf '\n%s%s%s\n  %s%s%s\n%s%s%s\n' "$$C" "$$LINE" "$$Z" "$$B" "$$1" "$$Z" "$$C" "$$LINE" "$$Z"; }
step() { printf '\n\n%s%s▌ Étape %s%s\n' "$$B" "$$C" "$$1" "$$Z"; }
sub() { printf '\n  %s%s%s\n' "$$B" "$$*" "$$Z"; }
say() { printf '  %s\n' "$$*"; }
hint() { printf '  %s%s%s\n' "$$D" "$$*" "$$Z"; }
good() { printf '  %s✓%s %s\n' "$$G" "$$Z" "$$*"; }
warnl() { printf '  %s⚠%s %s\n' "$$Y" "$$Z" "$$*"; }
fail() { printf '  %s✗%s %s\n' "$$R" "$$Z" "$$*" >&2; }
die() { fail "$$*"; exit 1; }
cmd() { printf '      %s%s%s\n' "$$B" "$$*" "$$Z"; }
# Les réponses sont gardées au fur et à mesure dans .armadok-install.state (droits 600, jamais un mot de passe, un jeton ni un secret) :
# si vous coupez l'installation, « make continue » (ou le prochain « make hub » / « make worker ») la reprend là où elle en était.
STATE=.armadok-install.state
state_has() { [ -f "$$STATE" ] && grep -q "^$$1=" "$$STATE" 2>/dev/null; }
state_get() { if [ -f "$$STATE" ]; then grep -E "^$$1=" "$$STATE" 2>/dev/null | tail -1 | cut -d= -f2-; fi; return 0; }
state_put() { ( umask 077; { grep -v "^$$1=" "$$STATE" 2>/dev/null; printf '%s=%s\n' "$$1" "$$2"; } > "$$STATE.new" && mv "$$STATE.new" "$$STATE" ) 2>/dev/null; return 0; }
state_clear() { rm -f "$$STATE" "$$STATE.new" 2>/dev/null; return 0; }
# resume_check hub|worker : propose de reprendre (RESUME_NOW=1, utilisé par « make continue », reprend sans poser la question)
resume_check() {
  RESUME=0
  [ "$${FILES_ONLY:-}" = 1 ] && return 0
  [ "$${INTERACTIVE:-}" = 0 ] && return 0
  [ -s "$$STATE" ] || return 0
  if [ "$$(state_get K_KIND)" != "$$1" ]; then
    [ "$${RESUME_NOW:-}" = 1 ] && die "L'installation interrompue ici est celle d'un « $$(state_get K_KIND) », pas d'un « $$1 »."
    state_clear; return 0   # l'état d'une AUTRE installation : on repart de zéro
  fi
  local lines=() pair k v
  for pair in "INSTALL_MODE:Mode" "WORKER_MODULES:Ce que gère le worker (1 Traefik, 2 Docker, 3 les deux)" "TRAEFIK_DYNAMIC_PATH:Dossier dynamique de Traefik" "TRAEFIK_NETWORK:Réseau Docker" "K_EPS:Points d'entrée" "K_RES:Résolveur" \
              "CERTS_PATH:Dossier d'acme.json" "K_CERTDEL:Suppression des certificats" "K_CTR:Conteneur de Traefik" "K_DOCKER:Gestion de Docker" "PROJECT_ROOTS:Dossiers des projets" "K_EXEC:Terminal dans les conteneurs" \
              "TRAEFIK_ACCESS_LOG_PATH:Dossier du journal" "TRAEFIK_API_URL:API de Traefik" "PROMETHEUS_URL:Prometheus" "DEFAULT_DNS_TARGET:IP par défaut" "K_AUSER:Propriétaire" "K_OEND:Région OVH" "K_DOM:Adresse de l'interface" "K_MW:Protections" \
              "WORKER_ADDRESSES:Adresse de cette machine" "WORKER_IMAGE:Image du worker" "HUB_URL:Adresse du hub"; do
    k="$${pair%%:*}"; state_has "$$k" || continue; v="$$(state_get "$$k")"; lines+=("  • $${pair#*:} : $${v:-(vide)}")
  done
  if [ "$${RESUME_NOW:-}" = 1 ]; then RESUME=1; good "Reprise de l'installation interrompue ($${#lines[@]} réponse(s) gardée(s) dans $$STATE)."; return 0; fi
  [ "$${#lines[@]}" -gt 0 ] || return 0
  if confirm "Reprendre l'installation commencée tout à l'heure ?" "Vos réponses précédentes ont été gardées (jamais un mot de passe, un jeton ni un secret) :" "" "$${lines[@]}" "" "Répondez « n » pour tout reposer depuis le début."; then RESUME=1; else state_clear; fi
}

# Une question = un titre, quelques lignes d'explication (avec un exemple), puis l'invite sur sa propre ligne.
#   ask "titre" "défaut" [VARIABLE] ["explication"…] : la variable déjà renseignée répond d'office (rien n'est demandé).
#   asks "titre" [VARIABLE] ["explication"…] : sans écho (secrets).   yes / confirm "titre" ["explication"…] : o/N, O/n.
show_q() { local l; printf '\n  %s▸ %s%s\n' "$$B" "$$1" "$$Z" >&2; shift; for l in "$$@"; do printf '      %s%s%s\n' "$$D" "$$l" "$$Z" >&2; done; }
ask() { local v="" q="$$1" def="$${2:-}" pre="$${3:-}"; if [ $$# -ge 3 ]; then shift 3; else shift $$#; fi
  if [ -n "$$pre" ] && [ -n "$${!pre:-}" ]; then v="$${!pre}"; printf '  %s✓%s %s : %s\n' "$$G" "$$Z" "$$q" "$$v" >&2
  elif [ -n "$$pre" ] && [ "$${RESUME:-0}" = 1 ] && state_has "$$pre"; then v="$$(state_get "$$pre")"; printf '  %s✓%s %s : %s %s(repris)%s\n' "$$G" "$$Z" "$$q" "$${v:-—}" "$$D" "$$Z" >&2
  elif [ "$${FILES_ONLY:-}" != 1 ]; then
    show_q "$$q" "$$@"; read -r -p "      $${C}➜$${Z} Votre réponse$${def:+ $${D}[$$def]$${Z}} : " v || true
    v="$${v:-$$def}"; [ -z "$$pre" ] || state_put "$$pre" "$$v"
  fi
  printf '%s' "$${v:-$$def}"; }
asks() { local v="" q="$$1" pre="$${2:-}"; if [ $$# -ge 2 ]; then shift 2; else shift $$#; fi
  if [ -n "$$pre" ] && [ -n "$${!pre:-}" ]; then v="$${!pre}"; printf '  %s✓%s %s : (fourni)\n' "$$G" "$$Z" "$$q" >&2
  elif [ "$${FILES_ONLY:-}" != 1 ]; then show_q "$$q" "$$@"; read -rs -p "      $${C}➜$${Z} Saisissez ou collez ici (rien ne s'affiche) : " v || true; echo >&2; fi
  printf '%s' "$$v"; }
yes() { local v="" q="$$1"; shift; [ "$${FILES_ONLY:-}" = 1 ] && return 1; show_q "$$q" "$$@"; read -r -p "      $${C}➜$${Z} Votre réponse $${D}[o/N]$${Z} : " v || true; [[ "$${v,,}" =~ ^(o|oui|y|yes)$$ ]]; }
yesk() { local k="$$1" q="$$2" r; shift
  if [ "$${RESUME:-0}" = 1 ] && state_has "$$k"; then r="$$(state_get "$$k")"; printf '  %s✓%s %s : %s %s(repris)%s\n' "$$G" "$$Z" "$$q" "$$([ "$$r" = o ] && echo oui || echo non)" "$$D" "$$Z" >&2; [ "$$r" = o ]; return; fi
  if yes "$$@"; then state_put "$$k" o; return 0; fi
  [ "$${FILES_ONLY:-}" = 1 ] || state_put "$$k" n
  return 1; }
confirm() { local v="" q="$$1"; shift; [ "$${FILES_ONLY:-}" = 1 ] && return 0; show_q "$$q" "$$@"; read -r -p "      $${C}➜$${Z} Votre réponse $${D}[O/n]$${Z} : " v || true; [[ ! "$${v,,}" =~ ^(n|non|no)$$ ]]; }
image_ref() { case "$$1" in "") echo "$(IMAGE)";; */*|*:*) echo "$$1";; *) echo "$(IMAGE_REPO):$$1";; esac; }
# pick_image VARIABLE_À_REMPLIR : demande « latest » ou un numéro de version précis (ex. 1.0.0) ; sauté si IMAGE a été fourni (IMAGE=… ou variable d'environnement)
pick_image() {
  local ans=""
  if [ "$${FILES_ONLY:-}" = 1 ]; then ans="$(IMAGE)"
  elif [ -n "$(if $(filter file,$(origin IMAGE)),,1)" ]; then ans="$(IMAGE)"; printf '  %s✓%s Image : %s\n' "$$G" "$$Z" "$$ans" >&2
  else
    { [ "$${RESUME:-0}" = 1 ] && state_has K_IMAGE; } || show_q "Quelle version d'Armadok installer ?" "« latest » est la plus récente publiée ; un numéro précis fige la version." "Exemple : 1.11.1" "Les versions existantes : github.com/Dr-J-Watson/Armadok/releases"
    if [ "$${RESUME:-0}" = 1 ] && state_has K_IMAGE; then ans="$$(state_get K_IMAGE)"; printf '  %s✓%s Version : %s %s(repris)%s\n' "$$G" "$$Z" "$$ans" "$$D" "$$Z" >&2
    else read -r -p "      $${C}➜$${Z} Votre réponse $${D}[latest]$${Z} : " ans || true; ans="$${ans:-latest}"; state_put K_IMAGE "$$ans"; fi
    ans="$$(image_ref "$$ans")"
  fi
  printf -v "$$1" '%s' "$$ans"
}
# choose_mode : « interactif » (questions, vérifications) ou « fichiers seulement » (.env et compose avec des valeurs par défaut, rien n'est démarré).
# Peut être fixé d'avance : make hub INTERACTIVE=0 (fichiers seulement) ou INTERACTIVE=1 (interactif).
choose_mode() {
  case "$${INTERACTIVE:-}" in 0|non|no|n) FILES_ONLY=1; return 0;; 1|oui|yes|o|y) FILES_ONLY=""; return 0;; esac
  [ "$${FILES_ONLY:-}" = 1 ] && return 0
  echo
  say "Comment voulez-vous continuer ?"
  printf '\n'
  printf '  %s1)%s %sMode interactif%s\n' "$$C" "$$Z" "$$B" "$$Z"
  hint "     Des questions guidées avec des vérifications (dossiers, réseau, image), puis les fichiers."
  printf '  %s2)%s %sSeulement le .env et le compose%s\n' "$$C" "$$Z" "$$B" "$$Z"
  hint "     Aucune question : valeurs par défaut et secrets générés, rien n'est démarré. Vous modifiez ensuite les fichiers à votre rythme."
  printf '\n'
  case "$$(ask "Quel mode ?" "1" INSTALL_MODE "1 = mode interactif (questions et vérifications)" "2 = seulement le .env et le compose (aucune question, rien n'est démarré)")" in 2|f|fichiers|files) FILES_ONLY=1;; *) FILES_ONLY="";; esac
  echo
}
as_root() { if [ "$$(id -u)" = 0 ]; then "$$@"; else sudo "$$@"; fi; }
need_docker() {
  command -v docker >/dev/null || die "Docker est requis."
  docker compose version >/dev/null 2>&1 || die "Docker Compose (v2) est requis."
}
# Source d'une configuration : chemin absolu d'un dossier de l'hôte, OU nom d'un volume Docker déjà existant.
# pick_source ÉTIQUETTE [VARIABLE_PRÉREMPLIE] [opt] [EXEMPLE] -> SRC (chemin ou nom) et SRC_KIND (dir|volume) ; « opt » : vide accepté.
pick_source() {
  local v h3=""; [ "$${3:-}" = opt ] && h3="Laissez vide pour ignorer."
  v=$$(ask "$$1" "" "$${2:-}" "Le chemin ABSOLU d'un dossier de cette machine, ou le nom d'un volume Docker déjà créé (docker volume ls)." $${4:+"Exemple : $$4"} $${h3:+"$$h3"}); v=$${v/#\~/$$HOME}
  SRC=""; SRC_KIND=""
  if [ "$${FILES_ONLY:-}" = 1 ]; then   # mode fichiers : rien n'est vérifié ; un dossier par défaut est prévu pour la configuration de Traefik
    if [ -z "$$v" ]; then [ "$${3:-}" = opt ] && return 0; v=./traefik-dynamic; fi
    case "$$v" in
      /*|./*|../*) mkdir -p "$$v" 2>/dev/null || true; SRC=$$(cd "$$v" 2>/dev/null && pwd || echo "$$v"); SRC_KIND=dir;;
      *) SRC="$$v"; SRC_KIND=volume;;
    esac
    return 0
  fi
  case "$$v" in
    "") [ "$${3:-}" = opt ] && return 0; die "Une valeur est requise.";;
    /*|./*|../*) [ -d "$$v" ] || die "Dossier introuvable : $$v"; SRC=$$(cd "$$v" && pwd); SRC_KIND=dir;;
    *) docker volume inspect "$$v" >/dev/null 2>&1 || die "Volume « $$v » introuvable (docker volume ls)."; SRC="$$v"; SRC_KIND=volume;;
  esac
}
ensure_image() { docker image inspect "$$UTIL_IMAGE" >/dev/null 2>&1 || docker pull "$$UTIL_IMAGE" >/dev/null 2>&1 || { echo "Image $$UTIL_IMAGE introuvable."; exit 1; }; }
# Un volume ne se lit pas depuis l'hôte : on passe par un conteneur jetable (root) qui le voit sur /d.
util() { ensure_image; docker run --rm -u 0 --entrypoint "" "$$@"; }
owner_of() { if [ "$${FILES_ONLY:-}" = 1 ]; then echo "$$(id -u):$$(id -g)"; elif [ "$$1" = dir ]; then stat -c '%u:%g' "$$2"; else util -v "$$2:/d:ro" "$$UTIL_IMAGE" stat -c '%u:%g' /d; fi; }
exists_in() { if [ "$$1" = dir ]; then [ -e "$$2/$$3" ]; else util -v "$$2:/d:ro" "$$UTIL_IMAGE" test -e "/d/$$3"; fi; }
write_in() { # KIND SRC FICHIER PROPRIÉTAIRE ; contenu sur l'entrée standard
  if [ "$$1" = dir ]; then cat > "$$2/$$3"; chown "$$4" "$$2/$$3" 2>/dev/null || as_root chown "$$4" "$$2/$$3" || true
  else ensure_image; docker run --rm -i -u 0 --entrypoint "" -v "$$2:/d" "$$UTIL_IMAGE" sh -c 'cat > "/d/$$1" && chown "$$2" "/d/$$1"' _ "$$3" "$$4"; fi
}
# Un volume qui appartient à root : on propose de le céder à 1000:1000 pour que l'interface ne tourne pas en root.
own_volume() { # KIND SRC PROPRIÉTAIRE -> propriétaire à utiliser
  local o="$$3" v=""
  if [ "$${FILES_ONLY:-}" != 1 ] && [ "$$1" = volume ] && [ "$$o" = 0:0 ]; then
    warnl "Ce volume appartient à root : l'interface y tournerait en root." >&2
    if confirm "Donner ce volume à l'utilisateur 1000:1000 ?" "Le volume appartient à root : sans cela, l'interface y tournerait en root." "Recommandé : « o » (seul le propriétaire des fichiers change)."; then util -v "$$2:/d" "$$UTIL_IMAGE" chown -R 1000:1000 /d && o=1000:1000; fi
  fi
  printf '%s' "$$o"
}
# build_compose MODÈLE NOM_VARIABLE_IMAGE : applique au modèle le volume Docker (config dynamique) et les certificats (hub : lus par l'interface ; worker : relais isolé).
build_compose() {
  local c="$$1" img="$$2" vols="$${EXTRA_VOLS:-}" blk in
  if [ "$${DYN_KIND:-dir}" = volume ]; then
    c=$${c//'- $${TRAEFIK_DYNAMIC_PATH}:/traefik-dynamic'/'- dynamic:/traefik-dynamic'}
    vols+=$$'  dynamic:\n    external: true\n    name: $${TRAEFIK_DYNAMIC_PATH}\n'
  fi
  if [ -n "$${LOG_SRC:-}" ]; then
    c=$${c//'#@log-env'/'TRAEFIK_ACCESS_LOG: /traefik-logs/access.log'}
    if [ "$${LOG_KIND:-dir}" = volume ]; then
      c=$${c//'#@log-vol'/'- logs:/traefik-logs:ro'}
      vols+=$$'  logs:\n    external: true\n    name: $${TRAEFIK_ACCESS_LOG_PATH}\n'
    else
      c=$${c//'#@log-vol'/'- $${TRAEFIK_ACCESS_LOG_PATH}:/traefik-logs:ro'}
    fi
  fi
  c=$$(grep -v '#@log' <<<"$$c")
  # Worker « Docker seul » : les lignes Traefik (marquées #@tr) disparaissent ; sinon seul le marqueur est retiré.
  if [ "$${WORKER_NOTR:-}" = 1 ]; then c=$$(grep -v '#@tr$$' <<<"$$c"); else c=$$(sed 's/ *#@tr$$//' <<<"$$c"); fi
  if [ "$${WORKER_DOCKER:-}" = 1 ]; then
    # Worker qui gère Docker : socket + dossiers des projets (MÊME chemin dedans et dehors) ; le terminal n'est ouvert que sur demande (WORKER_ALLOW_EXEC).
    local wv="- /var/run/docker.sock:/var/run/docker.sock   # ATTENTION : accès équivalent à root sur cette machine" wr
    for wr in $${PROJECT_ROOTS//,/ }; do wv+=$$'\n      '"- $$wr:$$wr"; done
    c=$${c//'#@wk-vol'/"$$wv"}
    c=$${c//'#@wk-env'/'WORKER_MODULES: $${WORKER_MODULES}'$$'\n      '"DOCKER_SOCKET: /var/run/docker.sock"$$'\n      '"PROJECT_ROOTS: "'$${PROJECT_ROOTS}'$$'\n      '"WORKER_ALLOW_EXEC: "'$${WORKER_ALLOW_EXEC:-}'$$'\n      '"WORKER_ADDRESSES: "'$${WORKER_ADDRESSES:-}'}
    c=$${c//'#@wk-pgroups'/'PGROUPS: "$${DOCKER_GID}"'}
    c=$${c//'#@wk-group'/'group_add: ["$${DOCKER_GID}"]'}
  else
    c=$$(grep -v '#@wk' <<<"$$c")
  fi
  if [ -n "$${CERTS_SRC:-}" ]; then
    if [ "$$CERTS_KIND" = volume ]; then in=certsin; vols+=$$'  certsin:\n    external: true\n    name: $${CERTS_PATH}\n'; else in='$${CERTS_PATH}'; fi
    if [ "$${CERTS_MODE:-relay}" = hub ]; then
      # Hub : l'interface lit acme.json elle-même (noms et dates seulement) ; avec le socket Docker elle peut aussi supprimer / renouveler (arrêt puis relance de Traefik).
      c=$${c//'#@certs-env'/"TRAEFIK_CERTS_FILE: /traefik-certs/acme.json"$$'\n      '"TRAEFIK_CONTAINER: $${TRAEFIK_CTR:-traefik}"}
      if [ "$${CERTS_DOCKER:-}" = 1 ]; then
        c=$${c//'#@certs-vol'/"- $$in:/traefik-certs"$$'\n      '"- /var/run/docker.sock:/var/run/docker.sock"}
        c=$${c//'#@certs-group'/'group_add: ["$${DOCKER_GID}"]'}
      else
        c=$${c//'#@certs-vol'/"- $$in:/traefik-certs:ro"}
      fi
      c=$$(grep -v '#@certs' <<<"$$c")
    else
      vols+=$$'  certs_meta:\n'
      blk=$$(cat <<EOT
  certs:
    image: \$${$$img:-$(IMAGE)}
    container_name: armadok-certs-relay
    command: ["python", "-m", "app.certsync"]
    restart: unless-stopped
    user: "0:0"
    network_mode: none
    read_only: true
    cap_drop: [ALL]
    cap_add: [DAC_READ_SEARCH]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 64m
    healthcheck: { disable: true }
    environment:
      CERTSYNC_IN: /certs-in/\$${ACME_FILE:-acme.json}
    volumes:
      - $$in:/certs-in:ro
      - certs_meta:/certs-out
EOT
)
      c=$${c//'#@certs-env'/'TRAEFIK_CERTS_FILE: /traefik-certs/certs.json'}
      c=$${c//'#@certs-vol'/'- certs_meta:/traefik-certs:ro'}
      c=$${c//'  #@certs-service'/"$$blk"}
      c=$$(grep -v '#@certs-group' <<<"$$c")
    fi
  else
    c=$$(grep -v '#@certs' <<<"$$c")
  fi
  if [ "$${DOCKER_MANAGE:-}" = 1 ]; then
    # Gestion de Docker : le socket (déjà monté si les certificats l'ont demandé) et les dossiers des projets, montés avec le MÊME chemin dedans et dehors.
    local dkv="" r
    [ "$${CERTS_DOCKER:-}" = 1 ] || dkv="- /var/run/docker.sock:/var/run/docker.sock"
    for r in $${PROJECT_ROOTS//,/ }; do dkv+=$${dkv:+$$'\n      '}"- $$r:$$r"; done
    c=$${c//'#@dk-vol'/"$$dkv"}
    c=$${c//'#@dk-env'/'DOCKER_MANAGE: $${DOCKER_MANAGE:-}'$$'\n      '"PROJECT_ROOTS: $${PROJECT_ROOTS}"}
    if [ "$${CERTS_DOCKER:-}" = 1 ]; then c=$$(grep -v '#@dk-group' <<<"$$c"); else c=$${c//'#@dk-group'/'group_add: ["$${DOCKER_GID}"]'}; fi
  else
    c=$$(grep -v '#@dk' <<<"$$c")
  fi
  [ -n "$$vols" ] && c+=$$'\n\nvolumes:\n'"$${vols%$$'\n'}"
  printf '%s\n' "$$c"
}
# --- Questions du hub. Chaque groupe est expliqué avant d'être posé.
# ask_traefik : DYN DYN_KIND OWNER NET EPS RES CERTS_* LOG_SRC API PROM DNST · ask_admin : AUSER HASH · ask_ovh : OEND AK AS CK · ask_route : DOM MW EP
ask_traefik() {
  sub "Configuration dynamique de Traefik"
  hint "C'est le dossier où Traefik lit ses fichiers de sites (son « file provider »). L'interface y écrit les sites que vous créez,"
  hint "et Traefik les recharge tout seul. Donnez le chemin absolu du dossier, ou le nom d'un volume Docker existant (docker volume ls)."
  pick_source "Dossier de configuration dynamique de Traefik" TRAEFIK_DYNAMIC_PATH "" /home/vous/docker/traefik/dynamic
  DYN="$$SRC"; DYN_KIND="$$SRC_KIND"
  OWNER=$$(own_volume "$$DYN_KIND" "$$DYN" "$$(owner_of "$$DYN_KIND" "$$DYN")")
  good "L'interface tournera avec l'identité $$OWNER (propriétaire de ce $$([ "$$DYN_KIND" = volume ] && echo volume || echo dossier)) pour pouvoir y écrire."

  NET=$$(ask "Nom du réseau Docker partagé" "proxy" TRAEFIK_NETWORK "Le réseau que Traefik et l'interface ont en commun : Traefik joint l'interface par lui." "Pour le connaître : docker network ls" "Exemple : proxy")
  if [ "$${FILES_ONLY:-}" = 1 ]; then hint "Le réseau « $$NET » doit exister avant le démarrage : docker network create $$NET"
  else
    docker network inspect "$$NET" >/dev/null 2>&1 || die "Réseau « $$NET » introuvable (docker network ls). Créez-le ou indiquez le bon nom."
    good "Réseau « $$NET » trouvé."
  fi

  sub "Points d'entrée et certificats (valeurs de départ)"
  hint "Proposés dans les formulaires de l'interface. Lus UNE seule fois : vous les changerez ensuite dans « Ce nœud »."
  EPS=$$(ask "Points d'entrée de Traefik" "web,websecure" K_EPS "Les noms écrits dans la configuration de Traefik : web = port 80, websecure = port 443." "Plusieurs noms : séparez-les par des virgules." "Exemple : web,websecure")
  RES=$$(ask "Résolveur de certificats Let's Encrypt" "letsencrypt" K_RES "Le nom du résolveur défini dans la configuration de Traefik (certificatesResolvers)." "Exemple : letsencrypt")

  sub "Certificats HTTPS (facultatif)"
  hint "L'interface peut afficher les échéances et signaler les sites sans certificat. Elle ne lit que les noms et les dates :"
  hint "aucune clé privée n'arrive jamais dans le navigateur. Indiquez le dossier (ou volume) qui contient acme.json. Vide = ignorer."
  pick_source "Dossier qui contient acme.json (certificats)" CERTS_PATH opt /var/lib/traefik
  CERTS_SRC="$$SRC"; CERTS_KIND="$$SRC_KIND"; CERTS_MODE=hub; CERTS_DOCKER=""; TRAEFIK_CTR=traefik; DGID=""; LOG_KIND=dir
  if [ -n "$$CERTS_SRC" ]; then
    exists_in "$$CERTS_KIND" "$$CERTS_SRC" "acme.json" && good "acme.json trouvé." || warnl "acme.json introuvable pour l'instant : il apparaîtra dès que Traefik l'aura créé."
    echo
    hint "Supprimer ou renouveler un certificat depuis l'interface demande de relancer Traefik : l'interface doit alors accéder au socket Docker."
    warnl "Cela lui donne la main sur Docker. À n'accepter que si l'interface est protégée (chaîne privée, 2FA)."
    hint "Sans cela, tout marche sauf supprimer / renouveler."
    if yesk K_CERTDEL "Autoriser la suppression et le renouvellement des certificats ?" "Répondez « n » si vous n'en avez pas besoin : tout le reste fonctionne."; then
      CERTS_DOCKER=1
      TRAEFIK_CTR=$$(ask "Nom du conteneur de Traefik" "traefik" K_CTR "Le nom du conteneur, pour que l'interface puisse le relancer." "Pour le connaître : docker ps" "Exemple : traefik")
      DGID=$$(getent group docker | cut -d: -f3 || true)
      [ -n "$$DGID" ] || DGID=$$(ask "Numéro du groupe « docker »" "" K_DGID "Il donne accès au socket Docker sans être root." "Pour le trouver : getent group docker | cut -d: -f3" "Exemple : 999")
    fi
  fi

  sub "Gérer Docker depuis l'interface (facultatif)"
  hint "Conteneurs, journaux, projets docker compose (fichiers, déploiement, Git), images, terminal : tout depuis l'interface, relié à vos sites Traefik."
  warnl "Cela donne à l'interface un accès équivalent à ROOT sur cette machine. À n'accepter que si elle est protégée (chaîne privée, 2FA, droits « Docker » limités)."
  hint "Sans cela, tout marche sauf la partie Docker. Vous pourrez l'activer plus tard (DOCKER_MANAGE, PROJECT_ROOTS, socket)."
  echo
  DOCKER_MANAGE=""; PROJECT_ROOTS=""
  if yesk K_DOCKER "Activer la gestion de Docker depuis l'interface ?" "Répondez « n » pour garder la partie Traefik et DNS seule : vous pourrez l'activer plus tard."; then
    DOCKER_MANAGE=1
    if [ -z "$$DGID" ]; then
      DGID=$$(getent group docker | cut -d: -f3 || true)
      [ -n "$$DGID" ] || DGID=$$(ask "Numéro du groupe « docker »" "" K_DGID "Il donne accès au socket Docker sans être root." "Pour le trouver : getent group docker | cut -d: -f3" "Exemple : 999")
    fi
    PROJECT_ROOTS=$$(ask "Dossiers de vos projets docker compose" "$$HOME/docker" PROJECT_ROOTS "Les chemins ABSOLUS, séparés par des virgules, sans espace." "Montés avec le même chemin dans le conteneur ; rien n'est lu ni écrit en dehors." "Avec plusieurs dossiers, la création d'un projet demandera lequel utiliser." "Exemple : /opt/stacks,/srv/apps")
    local r norm=""
    for r in $${PROJECT_ROOTS//,/ }; do
      case "$$r" in /*) ;; *) die "« $$r » n'est pas un chemin absolu.";; esac
      [ -d "$$r" ] || { [ "$${FILES_ONLY:-}" = 1 ] && warnl "Le dossier « $$r » n'existe pas encore : créez-le avant de démarrer." || die "Le dossier « $$r » n'existe pas."; }
      norm+=$${norm:+,}$$(cd "$$r" 2>/dev/null && pwd || echo "$$r")
    done
    PROJECT_ROOTS="$$norm"
    good "Gestion de Docker activée ($$PROJECT_ROOTS)."
  fi

  sub "Journal d'accès de Traefik (facultatif)"
  hint "Active la liste des adresses IP des visiteurs, les attaques probables et le bannissement. Le dossier est monté en lecture seule."
  hint "Traefik doit écrire ce fichier : --accesslog.filepath=/logs/access.log --accesslog.format=json (aide : « Fonctionnalités facultatives »)."
  hint "Chemin absolu du dossier (il doit exister avant le démarrage), ou nom d'un volume Docker existant. Vide = ignorer."
  pick_source "Dossier du journal d'accès de Traefik" TRAEFIK_ACCESS_LOG_PATH opt /var/log/traefik
  LOG_SRC="$$SRC"; LOG_KIND="$$SRC_KIND"

  sub "Autres valeurs de départ (facultatif)"
  hint "Lues UNE seule fois pour remplir le nœud « Principal » ; modifiables ensuite dans « Ce nœud ». Vide = ignorer."
  API=$$(ask "API de Traefik (facultatif)" "" TRAEFIK_API_URL "Pour afficher l'état en direct des sites. Elle doit être joignable depuis le conteneur de l'interface." "Exemple : http://traefik:8080" "Laissez vide pour ignorer.")
  PROM=$$(ask "Prometheus (facultatif)" "" PROMETHEUS_URL "Pour les métriques et les alertes." "Exemple : http://prometheus:9090" "Laissez vide pour ignorer.")
  DNST=$$(ask "Adresse IP proposée pour les nouveaux sous-domaines (facultatif)" "" DEFAULT_DNS_TARGET "Proposée quand vous créez un site : en général l'IP PUBLIQUE de cette machine." "Exemple : 203.0.113.10" "Laissez vide pour ignorer.")
}
ask_admin() {
  sub "Compte propriétaire"
  hint "C'est l'administrateur permanent de l'interface. Les autres comptes se créent ensuite dans l'interface (menu Équipe)."
  hint "Le mot de passe n'est jamais écrit en clair : seul son hachage (bcrypt) est enregistré dans le fichier de configuration."
  echo
  if [ "$${FILES_ONLY:-}" = 1 ]; then
    AUSER=$$(ask "Identifiant du propriétaire" "admin"); HASH=""
    warnl "Mot de passe non défini : « make password » en génère le hachage, à coller dans ADMIN_PASSWORD_HASH (.env)."
    return 0
  fi
  AUSER=$$(ask "Identifiant du propriétaire" "admin" K_AUSER "Le nom de connexion de l'administrateur permanent de l'interface." "Exemple : admin")
  show_q "Mot de passe du propriétaire" "12 caractères au moins. Rien ne s'affiche pendant la saisie." "Il n'est jamais écrit en clair : seul son hachage l'est."
  while :; do
    read -rs -p "      $${C}➜$${Z} Mot de passe : " PW; echo
    read -rs -p "      $${C}➜$${Z} Confirmez le mot de passe : " PW2; echo
    if [ "$$PW" != "$$PW2" ]; then fail "Les deux saisies sont différentes : recommencez."
    elif [ $${#PW} -lt 12 ]; then fail "Trop court : 12 caractères minimum."
    else break; fi
  done
  HASH=$$(printf '%s' "$$PW" | docker run --rm -i "$${IMG_HUB:-$(IMAGE)}" python -c "import sys,bcrypt;print(bcrypt.hashpw(sys.stdin.read().encode()[:72],bcrypt.gensalt(12)).decode())")
  good "Mot de passe pris en compte (haché)."
}
ask_ovh() {
  sub "Compte OVH principal (facultatif)"
  hint "Si votre DNS est chez OVH, l'interface peut créer les enregistrements de vos sites. Laissez vide pour ignorer : DuckDNS, AdGuard Home,"
  hint "Pi-hole, DNS local et d'autres comptes OVH s'ajoutent ensuite dans l'interface."
  hint "Clé à créer sur https://api.ovh.com/createToken/ : GET, POST, PUT et DELETE sur /domain/zone et /domain/zone/*, sans limite de durée."
  OEND=$$(ask "Région de votre compte OVH" "ovh-eu" K_OEND "Le point d'accès de l'API OVH : ovh-eu (Europe), ovh-ca (Canada) ou ovh-us (États-Unis).")
  AK=$$(ask "Clé d'application OVH (Application key)" "" K_AK "Celle de l'application que vous avez créée sur https://api.ovh.com/createApp/" "Laissez vide pour ne pas utiliser OVH.")
  AS=""; CK=""
  if [ -n "$$AK" ]; then
    show_q "Secret d'application OVH (Application secret)" "Donné avec la clé d'application. Rien ne s'affiche pendant la saisie."
    read -rs -p "      $${C}➜$${Z} Saisissez ou collez ici : " AS; echo
    show_q "Clé de consommateur OVH (Consumer key)" "Elle donne les droits sur vos zones DNS." "Laissez vide pour la générer : une adresse de validation s'affichera. Rien ne s'affiche pendant la saisie."
    read -rs -p "      $${C}➜$${Z} Saisissez ou collez ici (vide = la générer) : " CK; echo
    if [ -z "$$CK" ]; then
      OUT=$$(docker run --rm -e OVH_ENDPOINT="$$OEND" -e OVH_APPLICATION_KEY="$$AK" -e OVH_APPLICATION_SECRET="$$AS" "$${IMG_HUB:-$(IMAGE)}" python -m app.tools.ovh_consumer_key)
      echo "$$OUT" | sed -n '1,2p' | sed 's/^/  /'
      show_q "Validez la clé sur OVH" "Ouvrez l'adresse affichée ci-dessus dans votre navigateur, connectez-vous et validez les droits demandés."
      read -r -p "      $${C}➜$${Z} Appuyez sur Entrée une fois la clé validée… " _
      CK=$$(echo "$$OUT" | sed -n 's/.*OVH_CONSUMER_KEY=//p')
    fi
  fi
}
ask_route() {
  sub "Adresse de l'interface (facultatif)"
  hint "Pour ouvrir l'interface par une adresse (https://…), un fichier de routage « armadok.yml » est ajouté à la configuration de Traefik."
  hint "Il faudra ensuite créer l'enregistrement DNS de cette adresse vers ce serveur. Vide = pas de routage (vous le ferez vous-même)."
  DOM=$$(ask "Adresse web de l'interface (facultatif)" "" K_DOM "Celle par laquelle vous ouvrirez l'interface. Un fichier armadok.yml la relie à Traefik." "Exemple : armadok.exemple.fr" "Laissez vide pour ne créer aucun routage.")
  MW=""; EP=""
  if [ -n "$$DOM" ]; then
    EP=$${EPS##*,}; [ "$$EP" = "web" ] && EP=websecure; case ",$$EPS," in *,websecure,*) EP=websecure;; esac
    MW=$$(ask "Protections de l'accès (middlewares Traefik, facultatif)" "" K_MW "Liste d'adresses autorisées, SSO… : les noms des middlewares, séparés par des virgules." "Sans protection, l'interface est ouverte à tous." "Exemple : chain-private@file" "Laissez vide pour aucune protection.")
    [ -n "$$MW" ] || warnl "Aucune protection : pensez à en ajouter une dans le fichier armadok.yml."
  fi
}
write_route() {
  [ -n "$${DOM:-}" ] || return 0
  if exists_in "$$DYN_KIND" "$$DYN" armadok.yml; then warnl "armadok.yml existe déjà dans la configuration de Traefik : conservé."; return 0; fi
  {
    echo "http:"; echo "  routers:"; echo "    armadok:"
    echo "      rule: \"Host(\`$$DOM\`)\""
    echo "      entryPoints: [\"$$EP\"]"; echo "      service: armadok"
    echo "      tls:"; echo "        certResolver: $$RES"
    if [ -n "$$MW" ]; then echo "      middlewares:"; IFS=, read -ra M <<< "$$MW"; for m in "$${M[@]}"; do echo "        - $${m// /}"; done; fi
    echo "  services:"; echo "    armadok:"; echo "      loadBalancer:"; echo "        servers:"
    echo "          - url: \"http://armadok:8000\""
  } | write_in "$$DYN_KIND" "$$DYN" armadok.yml "$$OWNER"
  good "armadok.yml créé dans la configuration de Traefik (https://$$DOM)."
}
# Le récapitulatif : tout ce qui va être écrit, avant de l'écrire.
recap() {
  local kindtxt; [ "$$DYN_KIND" = volume ] && kindtxt="volume" || kindtxt="dossier"
  printf '\n'
  say "$${B}Traefik$${Z}"
  say "  Configuration dynamique : $$DYN ($$kindtxt, identité $$OWNER)"
  say "  Réseau Docker           : $$NET"
  say "  Points d'entrée         : $$EPS · résolveur : $${RES:-—}"
  say "  Certificats             : $${CERTS_SRC:-non} $${CERTS_DOCKER:+(suppression / renouvellement via le socket Docker)}"
  say "  Journal d'accès         : $${LOG_SRC:-non}$${LOG_SRC:+ ($${LOG_KIND/dir/dossier})}"
  say "  Gestion de Docker       : $$([ "$${DOCKER_MANAGE:-}" = 1 ] && echo "oui (projets : $$PROJECT_ROOTS)" || echo non)"
  say "$${B}Compte$${Z}"
  say "  Propriétaire            : $$AUSER"
  say "  OVH                     : $$([ -n "$$AK" ] && echo "$$OEND (clé fournie)" || echo non)"
  say "$${B}Interface$${Z}"
  if [ -n "$${DOM:-}" ]; then say "  Adresse                 : https://$$DOM (routage armadok.yml créé)"; else say "  Adresse                 : aucune (pas de routage créé)"; fi
  say "  Valeurs de départ       : API $${API:-—} · Prometheus $${PROM:-—} · IP par défaut $${DNST:-—}"
}
# install_hub : tout le parcours du hub. N'écrit que des fichiers : RIEN n'est démarré.
install_hub() {
  local n1 n2 n3 n4 n5
  command -v openssl >/dev/null || die "openssl est requis."
  if [ -f .env ]; then die "Un .env existe déjà ici : rien n'est écrasé. « make up » le démarre, ou déplacez-le pour réinstaller."; fi
  banner "Armadok — installation du hub"
  printf '\n'
  resume_check hub
  choose_mode
  [ "$${FILES_ONLY:-}" = 1 ] || state_put K_KIND hub
  if [ "$${FILES_ONLY:-}" = 1 ]; then
    say "Mode fichiers : aucune question. Il écrit docker-compose.yml et .env (valeurs par défaut, secrets générés) sans rien démarrer."
  else
    say "Cet assistant pose quelques questions, puis écrit les fichiers de configuration."
    say "Il ne démarre rien : à la fin, il vous indique la commande à lancer."
    hint "Les questions facultatives se passent avec Entrée. Vous pourrez tout régler plus tard dans l'interface, sauf les secrets du fichier .env."
    hint "Un récapitulatif vous est montré avant d'écrire quoi que ce soit."
  fi
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx armadok; then
    echo
    warnl "Un conteneur nommé « armadok » existe déjà sur cette machine. Pour lancer cette nouvelle installation, il faudra"
    hint "d'abord l'arrêter et le supprimer (docker rm -f armadok), ou changer « container_name » dans docker-compose.yml."
  fi
  echo; pick_image IMG_HUB
  if [ "$${FILES_ONLY:-}" != 1 ]; then
    hint "Récupération de l'image $$IMG_HUB…"
    docker pull "$$IMG_HUB" >/dev/null 2>&1 || docker image inspect "$$IMG_HUB" >/dev/null 2>&1 || die "Image $$IMG_HUB introuvable (version inexistante ? voir github.com/Dr-J-Watson/Armadok/releases)."
  fi
  UTIL_IMAGE="$$IMG_HUB"
  step "1/5 · Traefik"
  ask_traefik
  step "2/5 · Compte administrateur"
  ask_admin
  step "3/5 · Compte OVH"
  ask_ovh
  step "4/5 · Adresse de l'interface"
  ask_route
  step "5/5 · Récapitulatif"
  recap
  echo
  confirm "Écrire les fichiers maintenant ?" "Le récapitulatif ci-dessus est ce qui va être écrit. Rien n'est démarré." || { echo; say "Annulé : rien n'a été écrit."; exit 0; }
  echo
    mkdir -p data
    [ "$$(stat -c '%u:%g' data)" = "$$OWNER" ] || as_root chown "$$OWNER" data
    build_compose "$$COMPOSE_YML" ARMADOK_IMAGE > docker-compose.yml
    umask 077
    {
      echo "# .env généré par « make install » le $$(date +%F). Étiquettes et explications : .env.example."
      echo "# « make doctor » vérifie ce fichier. Modifier une valeur [TOUJOURS] ou [COMPOSE] puis : make up"
      echo; echo "# ── [OBLIGATOIRE · TOUJOURS] compte propriétaire et secrets (ne jamais partager ce fichier)"
      echo "ADMIN_USER=$$AUSER"
      echo "ADMIN_PASSWORD_HASH='$$HASH'"
      echo "JWT_SECRET=$$(openssl rand -hex 32)"
      echo "ENCRYPTION_KEY=$$(openssl rand -hex 32)"
      echo; echo "# ── [UNE FOIS] valeurs de départ du nœud Principal : lues au premier démarrage, ensuite réglées dans « Ce nœud »"
      echo "TRAEFIK_ENTRYPOINTS=$$EPS"
      echo "TRAEFIK_CERT_RESOLVERS=$$RES"
      echo "TRAEFIK_API_URL=$$API"
      echo "PROMETHEUS_URL=$$PROM"
      echo "DEFAULT_DNS_TARGET=$$DNST"
      echo; echo "# ── [TOUJOURS] compte OVH principal (facultatif)"
      echo "OVH_ENDPOINT=$$OEND"
      echo "OVH_APPLICATION_KEY=$$AK"
      echo "OVH_APPLICATION_SECRET=$$AS"
      echo "OVH_CONSUMER_KEY=$$CK"
      echo; echo "# ── [COMPOSE] chemins, réseau, utilisateur (lus par Docker Compose pour construire les montages)"
      echo "ARMADOK_IMAGE=$$IMG_HUB"
      echo "TRAEFIK_DYNAMIC_PATH=$$DYN"
      echo "UID=$${OWNER%%:*}"
      echo "GID=$${OWNER##*:}"
      echo "TRAEFIK_NETWORK=$$NET"
      [ -n "$$LOG_SRC" ] && echo "TRAEFIK_ACCESS_LOG_PATH=$$LOG_SRC"
      [ -n "$$CERTS_SRC" ] && echo "CERTS_PATH=$$CERTS_SRC"
      [ -n "$${DGID:-}" ] && echo "DOCKER_GID=$$DGID"
      if [ "$${DOCKER_MANAGE:-}" = 1 ]; then echo; echo "# ── [TOUJOURS] gestion de Docker (accès équivalent à root : droits « docker.* » du nœud)"; echo "DOCKER_MANAGE=true"; echo "PROJECT_ROOTS=$$PROJECT_ROOTS"; fi
    } > .env
    chmod 600 .env
    good "docker-compose.yml créé."
    good ".env créé (droits 600, secrets générés)."
    good "data/ créé (base, sauvegardes, audit : à sauvegarder)."
    state_clear   # installé : les réponses sont dans le .env
    write_route
    banner "Terminé — rien n'est démarré"
    printf '\n'
    if [ "$${FILES_ONLY:-}" = 1 ]; then
      say "$${B}À faire AVANT de démarrer :$${Z}"
      say "  • mot de passe : « make password », puis collez le résultat dans ADMIN_PASSWORD_HASH (.env)"
      say "  • réseau Docker partagé avec Traefik : docker network create $$NET   (s'il n'existe pas)"
      say "  • dossier de la configuration de Traefik : $$DYN (changez TRAEFIK_DYNAMIC_PATH dans le .env si besoin)"
      say "  • relisez le .env : chaque valeur est expliquée dans .env.example"
      printf '\n'
    fi
    say "$${B}Pour démarrer l'interface :$${Z}"
    cmd "docker compose up -d"
    hint "(ou simplement : make up)"
    printf '\n'
    say "$${B}Ensuite :$${Z}"
    say "  • connectez-vous avec l'identifiant « $$AUSER »$${DOM:+ sur https://$$DOM}"
    say "  • vérifiez que tout est en ordre : make doctor"
    [ -z "$${DOM:-}" ] || say "  • créez l'enregistrement DNS de $$DOM vers ce serveur"
    say "  • gérer un Traefik sur une AUTRE machine : « Nouveau nœud » dans l'interface, qui affiche la commande à y coller"
    printf '\n'
    hint "Commandes utiles : make up · make logs · make status · make update · make backup · make doctor · make help"
  printf '\n'
}
# install_worker : tout le parcours du worker (il démarre et attend sa connexion ; en mode fichiers, seuls docker-compose.yml et .env sont écrits).
install_worker() {
  if [ -f .env ]; then die "Un .env existe déjà ici : rien n'est écrasé. « make up » le démarre, ou déplacez-le pour réinstaller."; fi
  banner "Armadok — installation d'un worker"
  printf '\n'
  resume_check worker
  choose_mode
  [ "$${FILES_ONLY:-}" = 1 ] || state_put K_KIND worker
  say "Un worker relie CETTE machine (son Traefik et/ou son Docker) à l'interface (le hub) installée ailleurs : le hub peut alors la gérer."
  say "Le worker appelle le hub (connexion sortante) : aucun port à ouvrir ici."
  if [ "$${FILES_ONLY:-}" = 1 ]; then
    hint "Mode fichiers : aucune question. Les chemins par défaut sont écrits ; l'adresse du hub et le jeton sont à renseigner dans le .env."
  else
    hint "Déroulé : 1) cette machine · 2) l'adresse du hub · 3) le jeton d'enrôlement · 4) écriture, démarrage et connexion."
    hint "Le jeton (valable 15 minutes, une seule fois) est demandé en DERNIER : ne le créez qu'au moment de l'étape 3."
    hint "Raccourci : « Nouveau nœud » dans l'interface du hub affiche une commande toute prête, avec l'adresse et le jeton déjà dedans."
  fi
  step "1/4 · Cette machine"
  UTIL_IMAGE="$$(image_ref "$${WORKER_IMAGE:-}")"
  case "$$(ask "Que doit gérer ce worker ?" "1" WORKER_MODULES "1 = Traefik + DNS : les sites, protections et certificats du Traefik de cette machine (le DNS reste géré par le hub)." "2 = Docker seul : les conteneurs et projets docker compose de cette machine, sans Traefik." "3 = les deux : Traefik ET Docker, avec la liaison complète (conteneur → règles → visiteurs).")" in
    2|docker) WORKER_MODULES=docker; WORKER_NOTR=1; WORKER_DOCKER=1;;
    3|both|traefik,docker|docker,traefik) WORKER_MODULES=traefik,docker; WORKER_NOTR=""; WORKER_DOCKER=1;;
    1|traefik|"") WORKER_MODULES=traefik; WORKER_NOTR=""; WORKER_DOCKER="";;
    *) die "Choix invalide : 1, 2 ou 3.";;
  esac
  DYN=""; DYN_KIND=dir; OWNER=""; CERTS_SRC=""; CERTS_KIND=dir; LOG_SRC=""; LOG_KIND=dir; API=""; NET=""; PROJECT_ROOTS=""; DGID=""; WORKER_ALLOW_EXEC=""; WORKER_ADDRESSES="$${WORKER_ADDRESSES:-}"
  if [ -z "$$WORKER_NOTR" ]; then
  sub "Configuration dynamique de Traefik"
  hint "Le dossier où Traefik lit ses fichiers de sites (file provider). Le worker y écrit ce que le hub lui demande, et rien d'autre."
  pick_source "Dossier de configuration dynamique de Traefik" TRAEFIK_DYNAMIC_PATH "" /home/vous/docker/traefik/dynamic
  DYN="$$SRC"; DYN_KIND="$$SRC_KIND"
  OWNER=$$(own_volume "$$DYN_KIND" "$$DYN" "$$(owner_of "$$DYN_KIND" "$$DYN")")
  good "Le worker tournera avec l'identité $$OWNER (propriétaire de ce $$([ "$$DYN_KIND" = volume ] && echo volume || echo dossier)) pour pouvoir y écrire."
  sub "Certificats (facultatif)"
  hint "Pour montrer les échéances des certificats dans le hub : dossier ou volume d'acme.json, lu en lecture seule par un relais isolé,"
  hint "qui ne transmet que les noms et les dates (jamais les clés). Vide = ignorer."
  pick_source "Dossier qui contient acme.json (certificats)" CERTS_PATH opt /var/lib/traefik
  CERTS_SRC="$$SRC"; CERTS_KIND="$$SRC_KIND"
  sub "Journal d'accès (facultatif)"
  hint "Le dossier (ou volume) qui contient le journal d'accès de Traefik (format JSON, access.log). Le worker le LIT chez lui et l'envoie au hub :"
  hint "statistiques de visiteurs, adresses IP, alertes et bannissements automatiques de ce nœud. Vide = ignorer."
  pick_source "Dossier du journal d'accès de Traefik" TRAEFIK_ACCESS_LOG_PATH opt /var/log/traefik
  LOG_SRC="$$SRC"; LOG_KIND="$$SRC_KIND"
  sub "État en direct des sites (facultatif)"
  hint "Si l'API de Traefik est joignable depuis un réseau Docker, le hub affiche l'état de chaque site. Ex : http://traefik:8080. Vide = ignorer."
  API=$$(ask "Adresse de l'API de Traefik (facultatif)" "" TRAEFIK_API_URL "Elle doit être joignable depuis le conteneur du worker, par un réseau Docker." "Exemple : http://traefik:8080" "Laissez vide pour ignorer.")
  if [ -n "$$API" ]; then
  	NET=$$(ask "Réseau Docker partagé avec Traefik" "proxy" TRAEFIK_NETWORK "Le réseau qui permet au worker de joindre l'API de Traefik." "Pour le connaître : docker network ls" "Exemple : proxy")
  	docker network inspect "$$NET" >/dev/null 2>&1 || die "Réseau « $$NET » introuvable (docker network ls)."
  fi
  fi
  if [ -n "$$WORKER_DOCKER" ]; then
  sub "Docker de cette machine"
  warnl "Cela donne au hub (selon les droits « Docker » du nœud) un accès équivalent à ROOT sur cette machine."
  PROJECT_ROOTS=$$(ask "Dossiers de vos projets docker compose" "$$HOME/docker" PROJECT_ROOTS "Les chemins ABSOLUS, séparés par des virgules, sans espace." "Montés avec le même chemin dans le conteneur ; rien n'est lu ni écrit en dehors." "Avec plusieurs dossiers, la création d'un projet demandera lequel utiliser." "Exemple : /opt/stacks,/srv/apps")
  local r norm=""
  for r in $${PROJECT_ROOTS//,/ }; do
    case "$$r" in /*) ;; *) die "« $$r » n'est pas un chemin absolu.";; esac
    [ -d "$$r" ] || { [ "$${FILES_ONLY:-}" = 1 ] && warnl "Le dossier « $$r » n'existe pas encore : créez-le avant de démarrer." || die "Le dossier « $$r » n'existe pas."; }
    norm+=$${norm:+,}$$(cd "$$r" 2>/dev/null && pwd || echo "$$r")
  done
  PROJECT_ROOTS="$$norm"
  DGID=$$(getent group docker | cut -d: -f3 || true)
  [ -n "$$DGID" ] || DGID=$$(ask "Numéro du groupe « docker »" "" K_DGID "Il donne accès au socket Docker sans être root." "Pour le trouver : getent group docker | cut -d: -f3" "Exemple : 999")
  [ -n "$$OWNER" ] || { OWNER=$$(stat -c '%u:%g' "$${PROJECT_ROOTS%%,*}" 2>/dev/null || echo "$$(id -u):$$(id -g)"); good "Le worker tournera avec l'identité $$OWNER (propriétaire du premier dossier de projets)."; }
  if yesk K_EXEC "Autoriser le terminal dans les conteneurs de cette machine ?" "Ouvrir un terminal dans un conteneur, et lancer des commandes depuis le hub (onglet Avancé, Makefile)." "Le droit « terminal » du hub n'est jamais donné d'office, et reste refusé ici tant que vous ne l'autorisez pas." "Répondez « n » en cas de doute : vous pourrez l'activer plus tard (WORKER_ALLOW_EXEC)."; then WORKER_ALLOW_EXEC=true; fi
  WORKER_ADDRESSES=$$(ask "Adresse de cette machine, pour le Traefik d'un autre nœud (facultatif)" "$$(hostname -I 2>/dev/null | awk '{print $$1}')" WORKER_ADDRESSES "Si les sites de cette machine sont publiés par le Traefik d'un AUTRE nœud (celui du hub, par exemple) :" "l'adresse par laquelle ce Traefik joint cette machine (IP du réseau local ou du VPN, ou un nom)." "Plusieurs adresses : séparez-les par des virgules." "Laissez vide si cette machine a son propre Traefik ou aucun site." "Ensuite : « Ce nœud » → Docker → « Publier les sites avec le Traefik de… ».")
  WORKER_ADDRESSES=$${WORKER_ADDRESSES// /}
  fi
  IMG=$$(ask "Version ou image du worker" "latest" WORKER_IMAGE "Prenez la MÊME version que le hub : le protocole entre les deux évolue avec les versions." "« latest » = la plus récente ; sinon un numéro précis, ou une image complète." "Exemple : 1.11.1"); IMG=$$(image_ref "$$IMG")
  step "2/4 · Le hub"
  HUB=$$(ask "Adresse du hub" "" HUB_URL "L'adresse web de l'interface Armadok, celle que vous ouvrez dans votre navigateur." "Elle doit commencer par https://" "Exemple : https://armadok.exemple.fr"); HUB=$${HUB%/}
  INSECURE=""
  if [ "$${FILES_ONLY:-}" = 1 ]; then
    [ -n "$$HUB" ] || HUB=https://hub.exemple.fr
    case "$$HUB" in http://?*) INSECURE=1;; esac
  else
  case "$$HUB" in
    https://?*) ;;
    http://?*) warnl "http:// : les échanges avec le hub circuleraient en clair (réservé aux essais)."
               if [ "$${WORKER_ALLOW_INSECURE:-}" = 1 ] || yes "Continuer avec une adresse en http:// ?" "Tout ce qui passe entre ce worker et le hub (dont le jeton) circulerait en clair." "À réserver aux essais."; then INSECURE=1; else exit 1; fi;;
    *) die "Adresse invalide : elle doit commencer par https://";;
  esac
  if command -v curl >/dev/null; then
  	HCODE="$$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 -X POST -H 'Content-Type: application/json' -d '{}' "$$HUB/api/worker/enroll" 2>/dev/null)"   # le chemin du worker lui-même : seul /api/worker est ouvert derrière un filtre IP ; un corps vide est refusé (422) avant tout traitement, rien n'est enrôlé
  	case "$$HCODE" in
  	  422) good "Le hub répond.";;
  	  403) warnl "Le hub répond 403 : un filtre IP (middleware Traefik) refuse cette machine. Autorisez-la ou ouvrez la route /api/worker (voir « Hub derrière un filtre IP » dans le README)."; yes "Continuer malgré ce refus ?" "Le worker ne pourra pas s'enrôler tant que le filtre IP du hub bloque cette machine." || exit 1;;
  	  404) warnl "Le hub répond mais pas Armadok sur /api/worker (routage Traefik ? adresse ?)."; yes "Continuer malgré tout ?" "Vérifiez l'adresse du hub et la route /api/worker de son Traefik." || exit 1;;
  	  *) warnl "Le hub ne répond pas depuis cette machine (DNS, pare-feu, certificat ?)."; yes "Continuer malgré tout ?" "Le worker risque de ne pas pouvoir se connecter : vérifiez le DNS, le pare-feu sortant et le certificat du hub." || exit 1;;
  	esac
  fi
  fi
  step "3/4 · Jeton d'enrôlement"
  say "Dans l'interface du hub : « Nouveau nœud » (ou « Ce nœud » → Connexion → « Générer un jeton »), puis copiez le jeton affiché (enr_…)."
  TOKEN=$$(asks "Jeton d'enrôlement" ENROLL_TOKEN "Il commence par enr_ et s'obtient dans l'interface du hub (« Nouveau nœud »)." "Il expire 15 minutes après sa création et ne sert qu'une fois :" "générez-le maintenant, pas avant.")
  [ "$${FILES_ONLY:-}" != 1 ] || [ -n "$$TOKEN" ] || TOKEN=enr_A_REMPLACER
  case "$$TOKEN" in enr_?*) ;; *) die "Jeton invalide : il commence par « enr_ » (voir « Nouveau nœud » dans l'interface).";; esac
  if [ "$${FILES_ONLY:-}" = 1 ]; then step "4/4 · Écriture des fichiers"; else step "4/4 · Écriture, démarrage et connexion"; fi
  echo
  [ "$${FILES_ONLY:-}" = 1 ] || hint "Le worker doit démarrer maintenant : le jeton expire vite et ne sert qu'une fois."
  WY="$$WORKER_YML"
  if [ -n "$$NET" ]; then WY+=$$'\n    networks: [proxy]\n\nnetworks:\n  proxy:\n    external: true\n    name: $${TRAEFIK_NETWORK}'; fi
  mkdir -p data
  [ "$$(stat -c '%u:%g' data)" = "$$OWNER" ] || as_root chown "$$OWNER" data
  build_compose "$$WY" WORKER_IMAGE > docker-compose.yml
  umask 077
  {
    echo "# .env du worker, généré par « make install » le $$(date +%F). « make doctor » le vérifie."
    echo; echo "# ── connexion au hub"
    echo "HUB_URL=$$HUB"
    echo "# Jeton d'enrôlement : USAGE UNIQUE, 15 minutes. Retiré automatiquement dès que le worker est connecté."
    echo "ENROLL_TOKEN=$$TOKEN"
    [ -n "$$INSECURE" ] && echo "WORKER_ALLOW_INSECURE=1"
    echo; echo "# ── [TOUJOURS] Traefik de cette machine"
    echo "TRAEFIK_API_URL=$$API"
    echo; echo "# ── [COMPOSE] chemins, réseau, utilisateur"
    echo "WORKER_IMAGE=$$IMG"
    [ -n "$$DYN" ] && echo "TRAEFIK_DYNAMIC_PATH=$$DYN"
    [ -n "$$CERTS_SRC" ] && echo "CERTS_PATH=$$CERTS_SRC"
    [ -n "$$LOG_SRC" ] && echo "TRAEFIK_ACCESS_LOG_PATH=$$LOG_SRC"
    echo "TRAEFIK_NETWORK=$${NET:-proxy}"
    echo "UID=$${OWNER%%:*}"
    echo "GID=$${OWNER##*:}"
    if [ -n "$$WORKER_DOCKER" ]; then echo; echo "# ── [TOUJOURS] ce que gère le worker (Docker : accès équivalent à root sur cette machine)"; echo "WORKER_MODULES=$$WORKER_MODULES"; echo "PROJECT_ROOTS=$$PROJECT_ROOTS"; echo "DOCKER_GID=$$DGID"; echo "WORKER_ALLOW_EXEC=$$WORKER_ALLOW_EXEC"; echo "WORKER_ADDRESSES=$$WORKER_ADDRESSES"; fi
  } > .env
  chmod 600 .env
  good "docker-compose.yml et .env (droits 600) créés, data/ prêt."
  state_clear   # installé : les réponses sont dans le .env
  if [ "$${FILES_ONLY:-}" = 1 ]; then
    banner "Terminé — rien n'est démarré"
    printf '\n'
    say "$${B}À faire AVANT de démarrer :$${Z}"
    say "  • HUB_URL et ENROLL_TOKEN dans le .env (jeton : « Nouveau nœud » dans le hub ; valable 15 minutes, usage unique)"
    say "  • vérifiez les chemins (Traefik, projets Docker) et le réseau Docker s'il y en a un"
    printf '\n'
    say "$${B}Puis :$${Z}"
    cmd "make up"
    hint "Commandes utiles : make logs · make status · make update · make backup · make doctor · make help"
    printf '\n'
    return 0
  fi
  docker pull "$$IMG" >/dev/null 2>&1 || docker image inspect "$$IMG" >/dev/null 2>&1 || die "Image $$IMG introuvable."
  docker compose up -d
  echo
  say "Connexion au hub…"
  OK=""
  for _ in $$(seq 1 30); do
    L=$$(docker compose logs --no-color worker 2>&1 || true)
    if grep -q "connecté au hub" <<<"$$L"; then OK=1; break; fi
    if grep -qE "Enrôlement refusé|Hub injoignable|Aucune identité|HUB_URL" <<<"$$L"; then break; fi
    sleep 2
  done
  if [ -n "$$OK" ]; then
    sed -i '/^ENROLL_TOKEN=/d' .env   # le jeton est à usage unique : inutile de le garder
    banner "Terminé — le worker est connecté"
    printf '\n'
    good "Le jeton, devenu inutile, a été retiré du .env."
    say "Retournez dans l'interface du hub : le nœud est « En ligne »."
    printf '\n'
    hint "Commandes utiles : make logs · make status · make update · make backup · make doctor · make help"
  else
    echo; fail "Le worker ne s'est pas connecté. Dernières lignes :"; docker compose logs --no-color --tail=8 worker 2>&1 | sed 's/^/      /'
    docker compose stop >/dev/null 2>&1 || true
    echo
    say "Un jeton n'est valable que 15 minutes et une seule fois : générez-en un nouveau (« Ce nœud → Connexion » dans l'interface),"
    say "mettez-le dans ENROLL_TOKEN dans .env, puis :"
    cmd "make up"
    exit 1
  fi
}
only_hub() { grep -qE '^  armadok:' docker-compose.yml 2>/dev/null || die "Cette commande ne s'applique qu'au hub."; }
endef
export SH_FUNCS

help: ## Affiche cette aide
	@grep -E '^(##@ |[a-zA-Z0-9_-]+:.*## )' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "} /^##@ /{sub(/^##@ /,""); printf "\n%s\n",$$0; next} {printf "  make %-17s %s\n",$$1,$$2}'
	@echo

##@ Installation
install: ## Installation guidée : hub ou worker (interactive, ou seulement le .env et le compose)
	eval "$$SH_FUNCS"
	banner "Armadok — installation"
	printf '\n'
	say "Que voulez-vous installer sur cette machine ?"
	printf '\n'
	printf '  %s1)%s %sLe hub, avec Docker Compose%s\n' "$$C" "$$Z" "$$B" "$$Z"
	hint "     L'interface complète (DNS, sites Traefik, utilisateurs, nœuds). Écrit docker-compose.yml et .env ici."
	hint "     Rien n'est démarré : la commande à lancer vous est indiquée à la fin."
	printf '\n'
	printf '  %s2)%s %sUn worker, avec Docker Compose%s\n' "$$C" "$$Z" "$$B" "$$Z"
	hint "     Relie le Traefik et/ou le Docker de CETTE machine à une interface (hub) déjà installée ailleurs. Démarre le worker et attend la connexion (sauf en mode fichiers)."
	printf '\n'
	printf '  %s3)%s %sUn worker NATIF, sans Docker%s\n' "$$C" "$$Z" "$$B" "$$Z"
	hint "     Un service systemd qui démarre au boot, avec vérification de la machine et checklist. Pour un VPS où Docker n'est pas voulu."
	printf '\n'
	case "$$(ask "Que voulez-vous installer ?" "1" MODE "1 = le hub (Docker Compose)" "2 = un worker (Docker Compose)" "3 = un worker natif, sans Docker (service systemd)")" in
	  1|hub|compose|h) exec $(MAKE) --no-print-directory hub;;
	  2|worker|w) exec $(MAKE) --no-print-directory worker;;
	  3|native|n) exec $(MAKE) --no-print-directory worker-native;;
	  *) die "Choix invalide.";;
	esac

continue: ## Reprend l'installation interrompue (hub, worker ou worker natif) avec les réponses déjà données
	eval "$$SH_FUNCS"
	NSTATE="$${XDG_CONFIG_HOME:-$$HOME/.config}/armadok-worker-install.env"
	case "$$(state_get K_KIND)" in
	  hub) exec env RESUME_NOW=1 INTERACTIVE=1 $(MAKE) --no-print-directory hub;;
	  worker) exec env RESUME_NOW=1 INTERACTIVE=1 $(MAKE) --no-print-directory worker;;
	esac
	if [ -s "$$NSTATE" ]; then exec env NATIVE_RESUME=1 $(MAKE) --no-print-directory worker-native; fi
	die "Aucune installation interrompue ici. Lancez « make install » pour commencer (les réponses sont gardées au fur et à mesure)."

hub: ## Installe le hub avec Docker Compose (écrit docker-compose.yml et .env, ne démarre rien)
	eval "$$SH_FUNCS"
	need_docker
	install_hub

worker: ## Installe un worker avec Docker Compose (relie le Traefik et/ou le Docker de cette machine à un hub)
	eval "$$SH_FUNCS"
	need_docker
	install_worker

##@ Installation native (sans Docker)
worker-native: ## Installe un worker SANS Docker (service systemd) : vérifie la machine, installe, rend une checklist
	$(call NATIVE_RUN,install)

native-check: ## Vérifie seulement si cette machine peut accueillir le worker natif (ne modifie rien)
	$(call NATIVE_RUN,check)

native-status: ## État du worker natif : service, démarrage au boot, connexion au hub, droits
	$(call NATIVE_RUN,status)

native-logs: ## Affiche les journaux du worker natif en direct (journalctl)
	$(call NATIVE_RUN,logs)

native-restart: ## Redémarre le worker natif et attend sa connexion au hub
	$(call NATIVE_RUN,restart)

native-stop: ## Arrête le worker natif (il redémarrera au prochain démarrage de la machine)
	$(call NATIVE_RUN,stop)

native-start: ## Démarre le worker natif et attend sa connexion au hub
	$(call NATIVE_RUN,start)

native-backup: ## Sauvegarde l'identité (clé) et la configuration du worker natif dans une archive datée
	$(call NATIVE_RUN,backup)

native-update: ## Met à jour le worker natif (code et dépendances), en gardant .env et data/
	$(call NATIVE_RUN,update)

native-uninstall: ## Retire le worker natif (service, code ; demande avant de toucher à data/)
	$(call NATIVE_RUN,uninstall)

native-script:
	@printf '%s\n' "$$NATIVE_SCRIPT"

##@ Au quotidien
up: ## Démarre (ou applique un changement du .env)
	docker compose up -d

down: ## Arrête
	docker compose down

logs: ## Affiche les logs
	docker compose logs -f --tail=100

restart: ## Redémarre le service sans recréer le conteneur
	docker compose restart

update: ## Met à jour vers la dernière version
	docker compose pull && docker compose up -d

backup: ## Sauvegarde data/ dans une archive datée (base du hub, ou identité du worker)
	f="armadok-data-$$(date +%Y%m%d-%H%M%S).tar.gz"; tar czf "$$f" data && chmod 600 "$$f" && echo "Sauvegarde : $$f (contient la base : gardez-la en lieu sûr)"

status: ## État du service (et, pour un worker, sa connexion au hub)
	@docker compose ps
	if grep -q '^  worker:' docker-compose.yml 2>/dev/null; then echo; docker compose logs --no-color --tail=5 worker 2>&1 | sed 's/^/  /'; fi

##@ Diagnostic
doctor: ## Vérifie l'installation : .env, secrets, dossiers, droits, réseau, conteneur
	eval "$$SH_FUNCS"
	FAIL=0
	ok() { echo "  ✓ $$*"; }; bad() { echo "  ✗ $$*"; FAIL=1; }; warn() { echo "  ⚠ $$*"; }
	envget() { grep -E "^$$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed -e "s/^'//" -e "s/'$$//" -e 's/^"//' -e 's/"$$//' || true; }
	[ -f .env ] && [ -f docker-compose.yml ] || { echo "✗ .env ou docker-compose.yml introuvable ici : lancez « make install » (ou placez-vous dans le bon dossier)."; exit 1; }
	need_docker
	if grep -qE '^  armadok:' docker-compose.yml; then KIND=hub; else KIND=worker; fi
	echo "Installation : $$KIND"; echo
	echo "Fichiers"
	P=$$(stat -c %a .env); [ "$$P" = 600 ] && ok ".env : droits 600" || warn ".env : droits $$P (conseillé : chmod 600 .env, il contient des secrets)"
	[ -d data ] && ok "data/ présent" || bad "data/ absent (mkdir data)"
	if docker compose config -q 2>/dev/null; then ok "docker-compose.yml valide"; else bad "docker-compose.yml invalide : docker compose config"; fi
	echo; echo "Variables"
	if [ "$$KIND" = hub ]; then
	  v=$$(envget JWT_SECRET); [ $${#v} -ge 32 ] && ok "JWT_SECRET (≥ 32 caractères)" || bad "JWT_SECRET manquant ou trop court : openssl rand -hex 32"
	  v=$$(envget ENCRYPTION_KEY); if [ -z "$$v" ]; then warn "ENCRYPTION_KEY vide : dérivée de JWT_SECRET (à définir : openssl rand -hex 32)"; elif [ $${#v} -ge 32 ]; then ok "ENCRYPTION_KEY (≥ 32 caractères)"; else bad "ENCRYPTION_KEY trop courte (32 minimum)"; fi
	  [ -n "$$(envget ADMIN_PASSWORD_HASH)$$(envget ADMIN_PASSWORD)" ] && ok "compte propriétaire défini ($$(envget ADMIN_USER))" || bad "ADMIN_PASSWORD_HASH manquant : make password"
	  [ -n "$$(envget ADMIN_PASSWORD)" ] && warn "ADMIN_PASSWORD en clair : préférez ADMIN_PASSWORD_HASH (make password)"
	  [ "$$(envget COOKIE_SECURE)" = false ] && warn "COOKIE_SECURE=false : à réserver aux essais en HTTP"
	  if [ -f data/app.db ]; then
	    for k in TRAEFIK_ENTRYPOINTS TRAEFIK_CERT_RESOLVERS TRAEFIK_API_URL PROMETHEUS_URL DEFAULT_DNS_TARGET; do [ -n "$$(envget $$k)" ] && echo "  ℹ $$k : valeur de DÉPART, déjà utilisée (base existante) ; la modifier ici ne change rien, réglez-la dans « Ce nœud »."; done
	  fi
	else
	  v=$$(envget HUB_URL); [ -n "$$v" ] && ok "HUB_URL : $$v" || bad "HUB_URL manquant"
	  [ -n "$$(envget ENROLL_TOKEN)" ] && warn "ENROLL_TOKEN encore dans le .env : le worker n'est pas (ou pas encore) enrôlé (jeton à usage unique, 15 min)"
	fi
	WM=$$(envget WORKER_MODULES); WM=$${WM:-traefik}
	echo; echo "Dossiers et réseau"
	D=$$(envget TRAEFIK_DYNAMIC_PATH)
	[ "$$KIND" = worker ] && [ "$$WM" = docker ] && D=docker-only
	case "$$D" in
	  docker-only) ok "worker « Docker seul » : pas de dossier Traefik";;
	  /*|./*|../*) if [ -d "$$D" ]; then
	        ok "TRAEFIK_DYNAMIC_PATH : $$D"
	        O=$$(stat -c %u:%g "$$D"); W=$$(envget UID):$$(envget GID)
	        [ "$$O" = "$$W" ] && ok "propriétaire $$O = UID:GID du .env" || warn "le dossier appartient à $$O mais le conteneur tourne en $$W : il risque de ne pas pouvoir écrire"
	      else bad "TRAEFIK_DYNAMIC_PATH introuvable : $$D"; fi;;
	  "") bad "TRAEFIK_DYNAMIC_PATH manquant";;
	  *) docker volume inspect "$$D" >/dev/null 2>&1 && ok "volume Docker : $$D" || bad "volume « $$D » introuvable (docker volume ls)";;
	esac
	N=$$(envget TRAEFIK_NETWORK); N=$${N:-proxy}
	docker network inspect "$$N" >/dev/null 2>&1 && ok "réseau Docker « $$N »" || bad "réseau « $$N » introuvable (docker network ls)"
	L=$$(envget TRAEFIK_ACCESS_LOG_PATH)
	if [ -n "$$L" ]; then
	  case "$$L" in
	    /*|./*|../*) [ -d "$$L" ] && ok "journal d'accès : $$L" || bad "TRAEFIK_ACCESS_LOG_PATH introuvable : $$L"; [ -f "$$L/access.log" ] || warn "pas encore de access.log dans $$L (Traefik doit l'écrire : --accesslog.filepath=/logs/access.log --accesslog.format=json)";;
	    *) docker volume inspect "$$L" >/dev/null 2>&1 && ok "journal d'accès : volume $$L" || bad "volume « $$L » introuvable (docker volume ls)";;
	  esac
	fi
	C=$$(envget CERTS_PATH)
	if [ -n "$$C" ]; then
	  case "$$C" in /*|./*|../*) [ -d "$$C" ] && ok "certificats : $$C" || bad "CERTS_PATH introuvable : $$C"; [ -f "$$C/acme.json" ] || warn "acme.json absent de $$C pour l'instant";; *) docker volume inspect "$$C" >/dev/null 2>&1 && ok "certificats : volume $$C" || bad "volume « $$C » introuvable";; esac
	fi
	if grep -q 'docker.sock' docker-compose.yml; then
	  G=$$(envget DOCKER_GID); R=$$(getent group docker | cut -d: -f3 || true)
	  if [ -z "$$G" ]; then bad "socket Docker monté mais DOCKER_GID vide : getent group docker | cut -d: -f3"
	  elif [ -n "$$R" ] && [ "$$G" != "$$R" ]; then warn "DOCKER_GID=$$G alors que le groupe docker de cette machine est $$R"
	  else ok "DOCKER_GID : $$G"; fi
	  warn "socket Docker monté : l'interface a la main sur Docker (protégez-la, exigez la 2FA pour les certificats, ou utilisez un proxy de socket)"
	fi
	if [ "$$(envget DOCKER_MANAGE)" = true ] || { [ "$$KIND" = worker ] && [ "$${WM#*docker}" != "$$WM" ]; }; then
	  echo; echo "Gestion de Docker"
	  grep -q 'docker.sock' docker-compose.yml && ok "socket Docker monté" || bad "la gestion de Docker est activée mais le socket n'est pas monté dans docker-compose.yml"
	  [ "$$KIND" = worker ] && { [ "$$(envget WORKER_ALLOW_EXEC)" = true ] && warn "WORKER_ALLOW_EXEC=true : le hub peut ouvrir un terminal dans les conteneurs de cette machine (droit « docker.exec » du hub)" || ok "terminal dans les conteneurs : refusé (WORKER_ALLOW_EXEC non défini)"; }
	  PR=$$(envget PROJECT_ROOTS); [ -n "$$PR" ] || warn "PROJECT_ROOTS vide : la page Projets restera vide"
	  for r in $${PR//,/ }; do
	    [ -d "$$r" ] && ok "projets : $$r" || bad "PROJECT_ROOTS : dossier introuvable : $$r"
	    grep -qF "$$r:$$r" docker-compose.yml || warn "$$r n'est pas monté AVEC LE MÊME CHEMIN dans docker-compose.yml (les chemins relatifs des compose seraient faux)"
	  done
	  warn "gestion de Docker active : accès équivalent à root sur cette machine (limitez les droits « docker.* », exigez la 2FA)"
	fi
	echo; echo "Conteneur"
	S=$$(docker compose ps --status running --services 2>/dev/null | head -1 || true)
	if [ -n "$$S" ]; then
	  ok "« $$S » tourne"
	  if [ "$$KIND" = hub ]; then docker compose exec -T "$$S" python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" >/dev/null 2>&1 && ok "l'API répond" || bad "l'API ne répond pas : make logs"; fi
	else warn "le service ne tourne pas (make up)"; fi
	echo; [ "$$FAIL" = 0 ] && echo "Tout est en ordre (les ⚠ sont des conseils)." || { echo "Des points sont à corriger (✗)."; exit 1; }

config: ## Valide docker-compose.yml avec le .env (affiche la configuration finale)
	docker compose config

version: ## Version de l'interface en cours d'exécution
	docker compose exec -T "$$(docker compose ps --services | head -1)" python -c "import os; print(os.environ.get('APP_VERSION','dev'))"

##@ Outils
password: ## Génère un hash de mot de passe (hub : pour ADMIN_PASSWORD_HASH)
	eval "$$SH_FUNCS"; only_hub
	docker compose run --rm $(HUB_SVC) python -m app.tools.hash_password

consumer-key: ## Génère une consumer key OVH (hub)
	eval "$$SH_FUNCS"; only_hub
	docker compose run --rm $(HUB_SVC) python -m app.tools.ovh_consumer_key

reset-2fa: ## Désactive la double authentification (hub) : make reset-2fa NAME=utilisateur
	@eval "$$SH_FUNCS"; only_hub
	@[ -n "$(NAME)" ] || { echo "Usage : make reset-2fa NAME=utilisateur"; exit 1; }
	docker compose run --rm $(HUB_SVC) python -m app.tools.reset_2fa "$(NAME)"

.PHONY: help install continue native-script hub worker worker-native native-check native-status native-logs native-restart native-stop native-start native-backup native-update native-uninstall up down restart logs status update backup doctor config version password consumer-key reset-2fa
