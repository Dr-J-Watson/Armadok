#!/usr/bin/env bash
# Armadok — worker NATIF (sans Docker) : vérifie la machine, installe, démarre au boot, et rend une checklist.
#
#   scripts/native-worker.sh check       vérifie seulement (ne modifie rien)
#   scripts/native-worker.sh install     vérifie, installe, démarre, attend la connexion au hub
#   scripts/native-worker.sh update      met à jour le code et les dépendances (garde .env et data/)
#   scripts/native-worker.sh status      état du service et de la connexion
#   scripts/native-worker.sh uninstall   retire le service (demande avant de toucher à data/)
#
# Chaque question accepte une valeur préremplie par une variable d'environnement (voir « Variables » plus bas) ;
# NATIVE_YES=1 accepte les valeurs par défaut sans rien demander.
#
# Variables : HUB_URL, ENROLL_TOKEN, NATIVE_DIR (/opt/armadok-worker), NATIVE_REF (version du code : v1.11.0, main…),
#   NATIVE_VENV (true : environnement Python isolé), NATIVE_USER, TRAEFIK_DYNAMIC_DIR, TRAEFIK_ACCESS_LOG, TRAEFIK_CERTS_FILE,
#   NATIVE_CERTS (none | relay | direct), TRAEFIK_API_URL, WORKER_ALLOW_INSECURE (1 : hub en http, essais seulement).
set -uo pipefail

REPO="${NATIVE_REPO:-Dr-J-Watson/armadok}"
SERVICE=armadok-worker
RELAY=armadok-certsync
UNIT_DIR="${NATIVE_UNIT_DIR:-/etc/systemd/system}"
CMD="${1:-install}"
shift || true
ONLY_CHECK=0
for a in "$@"; do case "$a" in --check) ONLY_CHECK=1;; esac; done
[ "$CMD" = check ] && ONLY_CHECK=1

# ── présentation ────────────────────────────────────────────────────────────────────────────────────────────────
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then B=$'\033[1m'; D=$'\033[2m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; Z=$'\033[0m'; else B=""; D=""; G=""; Y=""; R=""; C=""; Z=""; fi
LINE='━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
banner() { printf '\n%s%s%s\n  %s%s%s\n%s%s%s\n' "$C" "$LINE" "$Z" "$B" "$1" "$Z" "$C" "$LINE" "$Z"; }
step() { printf '\n%s%s%s\n' "$B" "$1" "$Z"; }
say() { printf '  %s\n' "$*"; }
hint() { printf '  %s%s%s\n' "$D" "$*" "$Z"; }
die() { printf '\n%s✗ %s%s\n' "$R" "$*" "$Z" >&2; exit 1; }

# ── résultats de vérification : ✅ OK · ⚠ avertissement · ❌ bloquant, chacun avec sa correction ─────────────────────
N_OK=0; N_WARN=0; N_BAD=0
REPORT=()
ok()   { N_OK=$((N_OK + 1));     REPORT+=("OK|$1|"); }
warn() { N_WARN=$((N_WARN + 1)); REPORT+=("WARN|$1|${2:-}"); }
bad()  { N_BAD=$((N_BAD + 1));   REPORT+=("BAD|$1|${2:-}"); }
print_report() {
  local title="$1" line kind msg fix
  step "$title"
  for line in "${REPORT[@]}"; do
    kind="${line%%|*}"; line="${line#*|}"; msg="${line%%|*}"; fix="${line#*|}"
    case "$kind" in
      OK)   printf '  %s✅%s %s\n' "$G" "$Z" "$msg";;
      WARN) printf '  %s⚠️ %s %s\n' "$Y" "$Z" "$msg"; [ -n "$fix" ] && printf '       %s→ %s%s\n' "$D" "$fix" "$Z";;
      BAD)  printf '  %s❌%s %s\n' "$R" "$Z" "$msg"; [ -n "$fix" ] && printf '       %s→ %s%s\n' "$D" "$fix" "$Z";;
    esac
  done
  printf '\n  %s%d OK · %d avertissement(s) · %d bloquant(s)%s\n' "$B" "$N_OK" "$N_WARN" "$N_BAD" "$Z"
}
reset_report() { N_OK=0; N_WARN=0; N_BAD=0; REPORT=(); }
plain_report() {   # la même checklist, sans couleurs, pour le fichier install-report.txt
  local line kind msg fix mark
  for line in "${REPORT[@]}"; do
    kind="${line%%|*}"; line="${line#*|}"; msg="${line%%|*}"; fix="${line#*|}"
    case "$kind" in OK) mark="[OK]  ";; WARN) mark="[AVIS]";; *) mark="[ERR] ";; esac
    printf '%s %s\n' "$mark" "$msg"; [ -n "$fix" ] && printf '        -> %s\n' "$fix"
  done
  printf '\n%d OK, %d avertissement(s), %d bloquant(s)\n' "$N_OK" "$N_WARN" "$N_BAD"
}

# ── questions ───────────────────────────────────────────────────────────────────────────────────────────────────
interactive() { [ -z "${NATIVE_YES:-}" ] && [ -t 0 ]; }
# ask "question" "défaut" [VARIABLE]  : la variable préremplie répond d'office
ask() {
  local q="$1" def="${2:-}" var="${3:-}" v=""
  if [ -n "$var" ] && [ -n "${!var:-}" ]; then printf '%s' "${!var}"; return; fi
  if ! interactive; then printf '%s' "$def"; return; fi
  read -r -p "  $q${def:+ [$def]} : " v </dev/tty || true
  printf '%s' "${v:-$def}"
}
asks() {   # sans écho (jeton)
  local q="$1" var="${2:-}" v=""
  if [ -n "$var" ] && [ -n "${!var:-}" ]; then printf '%s' "${!var}"; return; fi
  interactive || { printf ''; return; }
  read -r -s -p "  $q : " v </dev/tty || true; echo >&2
  printf '%s' "$v"
}
yes_no() {   # yes_no "question" [défaut o|n]
  local q="$1" def="${2:-n}" v
  interactive || { [ "$def" = o ]; return; }
  read -r -p "  $q [$([ "$def" = o ] && echo O/n || echo o/N)] : " v </dev/tty || true
  v="${v:-$def}"; case "$v" in o|O|y|Y|oui) return 0;; *) return 1;; esac
}

# ── outils ──────────────────────────────────────────────────────────────────────────────────────────────────────
is_root() { [ "$(id -u)" = 0 ]; }
as_user() { local u="$1"; shift; if command -v runuser >/dev/null 2>&1; then runuser -u "$u" -- "$@"; else su -s /bin/sh "$u" -c "$(printf '%q ' "$@")"; fi; }
version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]; }   # version_ge 3.12 3.11 → vrai
pick_python() {
  local c
  for c in "${NATIVE_PYTHON:-}" python3.13 python3.12 python3; do
    [ -n "$c" ] && command -v "$c" >/dev/null 2>&1 || continue
    if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then command -v "$c"; return 0; fi
  done
  return 1
}
env_get() { [ -f "$1" ] && grep -E "^$2=" "$1" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
# path_ok_for USER PATH : l'utilisateur peut-il traverser tous les dossiers parents ? Renvoie le premier qui bloque.
blocking_parent() {
  local u="$1" p d mode owner
  p="$(readlink -f "$2" 2>/dev/null || echo "$2")"; d="$(dirname "$p")"
  while [ "$d" != "/" ] && [ -n "$d" ]; do
    mode="$(stat -c %a "$d" 2>/dev/null || echo 000)"; owner="$(stat -c %U "$d" 2>/dev/null || echo "?")"
    if [ $((8#$mode & 1)) -eq 0 ] && [ "$owner" != "$u" ]; then
      if ! { command -v getfacl >/dev/null 2>&1 && getfacl -p "$d" 2>/dev/null | grep -qE "^user:$u:.*x"; }; then echo "$d"; return 0; fi
    fi
    d="$(dirname "$d")"
  done
  return 1
}
can_test() { is_root || [ "$(id -un)" = "$1" ]; }   # tester les droits d'un AUTRE utilisateur demande root
can_write() { as_user "$1" test -w "$2" 2>/dev/null && as_user "$1" test -x "$2" 2>/dev/null; }
can_read() { as_user "$1" test -r "$2" 2>/dev/null; }

# ── paramètres (valeurs actuelles, ou par défaut) ──────────────────────────────────────────────────────────────────
DIR="${NATIVE_DIR:-/opt/armadok-worker}"
ENVF="$DIR/.env"
HUB="$(printf '%s' "${HUB_URL:-$(env_get "$ENVF" HUB_URL)}" | sed 's:/*$::')"
USE_VENV="${NATIVE_VENV:-$(env_get "$ENVF" NATIVE_VENV)}"; USE_VENV="${USE_VENV:-true}"
case "$USE_VENV" in true|1|yes|oui) USE_VENV=true;; *) USE_VENV=false;; esac

# ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# Vérifications
# ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
check_system() {
  [ "$(uname -s)" = Linux ] && ok "Système : Linux ($(uname -m))" || bad "Système : $(uname -s)" "Seul Linux avec systemd est géré. Sur un autre système, utilisez le worker Docker Compose (make worker)."
  if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then ok "systemd présent (démarrage automatique au boot)"
  else bad "systemd absent" "Le service ne pourrait pas démarrer au boot. Utilisez le worker Docker Compose (make worker)."; fi
  if is_root; then ok "Droits : root"
  elif command -v sudo >/dev/null 2>&1; then ok "Droits : sudo disponible (l'installation le demandera)"
  else bad "Ni root ni sudo" "Lancez en root (su -) ou installez sudo."; fi
  local c
  for c in curl tar; do command -v "$c" >/dev/null 2>&1 && ok "Outil « $c » présent" || bad "Outil « $c » absent" "Installez-le (apt install $c · dnf install $c · apk add $c)."; done
  local free; free="$(df -Pk "$(dirname "$DIR")" 2>/dev/null | awk 'NR==2{print int($4/1024)}')"
  if [ -n "$free" ] && [ "$free" -ge 400 ]; then ok "Espace disque : ${free} Mo libres dans $(dirname "$DIR")"
  elif [ -n "$free" ]; then bad "Espace disque : ${free} Mo libres seulement" "Prévoir 400 Mo (code, dépendances Python et données)."; fi
}

check_python() {
  PY="$(pick_python)" || PY=""
  if [ -z "$PY" ]; then
    bad "Python 3.12 ou plus récent introuvable" "apt install python3 python3-venv python3-pip (Debian ≥ 13, Ubuntu ≥ 24.04) · dnf install python3.12 · sinon, utilisez le worker Docker Compose."
    return
  fi
  ok "Python : $("$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))') ($PY)"
  if "$PY" -m pip --version >/dev/null 2>&1; then ok "pip présent"; else bad "pip absent" "apt install python3-pip · dnf install python3-pip"; fi
  if [ "$USE_VENV" = true ]; then
    if "$PY" -c 'import ensurepip, venv' 2>/dev/null; then ok "Environnement isolé (venv) : disponible"
    else bad "Le module venv n'est pas installé" "apt install python3-venv · ou relancez avec NATIVE_VENV=false (les dépendances vont alors dans $DIR/lib, sans venv)."; fi
  else
    ok "Sans venv (NATIVE_VENV=false) : les dépendances iront dans $DIR/lib, le système n'est pas modifié"
  fi
  if [ -f "$("$PY" -c 'import sysconfig; print(sysconfig.get_path("stdlib"))' 2>/dev/null)/EXTERNALLY-MANAGED" ]; then
    [ "$USE_VENV" = true ] && ok "Python « géré par le système » (PEP 668) : sans effet, le venv le contourne" \
      || warn "Python « géré par le système » (PEP 668)" "pip refusera peut-être l'installation sans venv : le script réessaie avec --break-system-packages, uniquement dans $DIR/lib."
  fi
}

check_hub() {
  if [ -z "$HUB" ]; then bad "Adresse du hub non renseignée" "HUB_URL=https://hub.exemple.fr"; return; fi
  case "$HUB" in
    https://?*) ok "Adresse du hub en HTTPS : $HUB";;
    http://?*) if [ "${WORKER_ALLOW_INSECURE:-}" = 1 ]; then warn "Hub en http:// (WORKER_ALLOW_INSECURE=1)" "Les échanges circulent en clair : réservé aux essais."; else bad "Hub en http://" "Le worker exige HTTPS. Pour un essai seulement : WORKER_ALLOW_INSECURE=1."; fi;;
    *) bad "Adresse du hub invalide : $HUB" "Elle doit commencer par https://"; return;;
  esac
  command -v curl >/dev/null 2>&1 || return
  local host out code; host="$(printf '%s' "$HUB" | sed -E 's#^[a-z]+://([^/:]+).*#\1#')"
  if getent hosts "$host" >/dev/null 2>&1; then
    local ip; ip="$(getent hosts "$host" | awk '{print $1; exit}')"
    case "$ip" in
      10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[0-1].*) warn "« $host » se résout en adresse privée ($ip)" "Si cette machine est hors de votre réseau local, ce nom doit renvoyer l'adresse publique du hub (DNS interne/« split DNS » ?).";;
      *) ok "« $host » se résout ($ip)";;
    esac
  else bad "« $host » ne se résout pas" "Vérifiez le DNS de cette machine (/etc/resolv.conf)."; fi
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 -X POST -H 'Content-Type: application/json' -d '{}' "$HUB/api/worker/enroll" 2>/tmp/.armadok-curl.$$)"   # le chemin du worker lui-même : seul /api/worker est ouvert derrière un filtre IP ; un corps vide est refusé (422) avant tout traitement, rien n'est enrôlé
  out="$(cat /tmp/.armadok-curl.$$ 2>/dev/null)"; rm -f /tmp/.armadok-curl.$$
  case "$code" in
    422) ok "Le hub répond (/api/worker)";;
    404) bad "Le hub répond, mais pas Armadok sur /api/worker" "Vérifiez l'adresse (HUB_URL) et le routage Traefik : /api/worker doit arriver à Armadok.";;
    403) bad "Le hub répond 403" "Un filtre IP (middleware Traefik du hub) refuse cette machine : autorisez son adresse ou ouvrez la route /api/worker (voir « Hub derrière un filtre IP » dans le README).";;
    000) case "$out" in
           *certificate*|*SSL*) bad "Certificat du hub refusé : ${out##*: }" "Le certificat doit être valide (Let's Encrypt, ou une autorité installée sur cette machine).";;
           *) bad "Le hub ne répond pas" "${out:-pas de réponse} — pare-feu sortant, DNS ou hub arrêté ?";;
         esac;;
    *) bad "Le hub répond HTTP $code" "Vérifiez l'adresse (HUB_URL) et le routage Traefik du hub.";;
  esac
  if [ -n "${ENROLL_TOKEN:-}" ] || [ -n "$(env_get "$ENVF" ENROLL_TOKEN)" ] || [ -f "$DIR/data/identity.json" ]; then
    if [ -f "$DIR/data/identity.json" ]; then ok "Identité du worker déjà enregistrée (pas de jeton nécessaire)"
    else
      local t="${ENROLL_TOKEN:-$(env_get "$ENVF" ENROLL_TOKEN)}"
      case "$t" in enr_?*) ok "Jeton d'enrôlement au bon format (usage unique, 15 min : générez-le au dernier moment)";; *) bad "Jeton d'enrôlement invalide" "Il commence par « enr_ » (« Nouveau nœud » dans l'interface du hub).";; esac
    fi
  else warn "Aucun jeton d'enrôlement pour l'instant" "Il sera demandé à l'installation (« Nouveau nœud » dans le hub, valable 15 minutes)."; fi
}

# Choix de l'utilisateur du service : le propriétaire du dossier Traefik, ou un utilisateur dédié (+ droits ciblés).
decide_user() {
  [ -n "${SVC_USER:-}" ] && return
  local owner uid
  owner="$(stat -c %U "$DYN" 2>/dev/null || echo root)"; uid="$(stat -c %u "$DYN" 2>/dev/null || echo 0)"
  if [ -n "${NATIVE_USER:-}" ]; then SVC_USER="$NATIVE_USER"
  elif [ -f "$DIR/.service-user" ]; then SVC_USER="$(cat "$DIR/.service-user")"
  elif [ "$uid" -ge 1000 ] 2>/dev/null && [ "$owner" != root ]; then
    if interactive; then
      say "Le dossier Traefik appartient à « $owner »."
      hint "1) lancer le worker sous « $owner » (aucun droit à modifier)   2) créer un utilisateur dédié « armadok » (droits ciblés par ACL)"
      case "$(ask "Votre choix (1 ou 2)" 1)" in 2) SVC_USER=armadok;; *) SVC_USER="$owner";; esac
    else SVC_USER="$owner"; fi
  else SVC_USER=armadok; fi
}

check_traefik() {
  DYN="${TRAEFIK_DYNAMIC_DIR:-$(env_get "$ENVF" TRAEFIK_DYNAMIC_DIR)}"
  if [ -z "$DYN" ]; then
    local guess=""
    if command -v docker >/dev/null 2>&1; then guess="$(docker inspect traefik --format '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' 2>/dev/null | grep -iE 'dynamic|conf' | head -1)"; fi
    DYN="$(ask "Dossier de configuration dynamique de Traefik (file provider)" "$guess" TRAEFIK_DYNAMIC_DIR)"
  fi
  [ -n "$DYN" ] || { bad "Dossier dynamique de Traefik non renseigné" "TRAEFIK_DYNAMIC_DIR=/chemin/de/traefik/dynamic"; return; }
  case "$DYN" in /*) ;; *) bad "« $DYN » n'est pas un chemin absolu" "Indiquez un chemin commençant par /"; return;; esac
  if [ ! -d "$DYN" ]; then bad "Dossier introuvable : $DYN" "Créez-le ou corrigez le chemin (docker inspect traefik, cherchez le montage du file provider)."; return; fi
  ok "Dossier dynamique : $DYN ($(find "$DYN" -type f \( -name '*.yml' -o -name '*.yaml' \) 2>/dev/null | wc -l) fichier(s) YAML)"
  decide_user
  if id "$SVC_USER" >/dev/null 2>&1 && ! can_test "$SVC_USER"; then
    warn "Droits de « $SVC_USER » sur $DYN : non testés (il faut root pour le faire)" "Relancez avec sudo pour les vérifier ; l'installation les règle de toute façon."
  elif id "$SVC_USER" >/dev/null 2>&1; then
    if can_write "$SVC_USER" "$DYN" 2>/dev/null; then ok "« $SVC_USER » peut écrire dans $DYN"
    else
      local blk; blk="$(blocking_parent "$SVC_USER" "$DYN" || true)"
      if [ -n "$blk" ]; then warn "« $SVC_USER » ne peut pas traverser $blk" "À l'installation : setfacl -m u:$SVC_USER:x $blk (droit de passage seulement, rien d'autre)."
      else warn "« $SVC_USER » ne peut pas écrire dans $DYN" "À l'installation : ACL en écriture sur ce dossier seulement (setfacl -R -m u:$SVC_USER:rwX)."; fi
    fi
  else
    ok "Utilisateur « $SVC_USER » : à créer (sans mot de passe ni shell)"
    command -v setfacl >/dev/null 2>&1 && ok "ACL disponibles (setfacl) pour lui donner l'accès au dossier Traefik, et à lui seul" \
      || bad "setfacl absent" "apt install acl · dnf install acl (ou lancez le service sous le propriétaire du dossier : NATIVE_USER=…)."
  fi
  case "$DYN" in /home/*) warn "Le dossier Traefik est dans /home" "Le service y reçoit un accès en écriture ciblé (ProtectHome en lecture seule, sauf ce dossier).";; esac
  # état en direct (facultatif)
  API="${TRAEFIK_API_URL:-$(env_get "$ENVF" TRAEFIK_API_URL)}"
  if [ -n "$API" ]; then
    if curl -fsS --max-time 5 "$API/api/version" >/dev/null 2>&1; then ok "API de Traefik joignable ($API) : état en direct des sites"
    else warn "API de Traefik injoignable ($API)" "Le hub n'affichera pas l'état en direct (tout le reste fonctionne). Si Traefik est dans Docker, publiez son API sur 127.0.0.1:8080."; fi
  else warn "API de Traefik non renseignée (facultatif)" "TRAEFIK_API_URL=http://127.0.0.1:8080 pour afficher l'état en direct des sites."; fi
  # journal d'accès (facultatif)
  LOGF="${TRAEFIK_ACCESS_LOG:-$(env_get "$ENVF" TRAEFIK_ACCESS_LOG)}"
  if [ -z "$LOGF" ] && interactive; then LOGF="$(ask "Journal d'accès de Traefik, JSON (vide = ignorer)" "" TRAEFIK_ACCESS_LOG)"; fi
  if [ -n "$LOGF" ]; then
    if [ -f "$LOGF" ]; then
      ok "Journal d'accès : $LOGF ($(du -h "$LOGF" | cut -f1))"
      head -c 4000 "$LOGF" | head -1 | grep -q '^{' && ok "Format JSON détecté" || warn "Le journal ne semble pas être en JSON" "Traefik : --accesslog.format=json (sinon les statistiques seront limitées)."
      id "$SVC_USER" >/dev/null 2>&1 && can_test "$SVC_USER" && ! can_read "$SVC_USER" "$LOGF" && warn "« $SVC_USER » ne peut pas lire $LOGF" "À l'installation : ACL en lecture seule sur ce fichier (et droit de passage sur ses dossiers)."
    else warn "Journal d'accès introuvable : $LOGF" "Traefik doit l'écrire (--accesslog.filepath=$LOGF --accesslog.format=json). Sans lui, pas de statistiques de visiteurs pour ce nœud."; fi
  else warn "Journal d'accès non branché (facultatif)" "Sans lui : pas de statistiques de visiteurs, d'alertes ni de bannissements pour ce nœud."; fi
  # certificats (facultatif)
  ACME="${TRAEFIK_CERTS_FILE:-$(env_get "$ENVF" ACME_FILE)}"
  CERTS_MODE="${NATIVE_CERTS:-$(env_get "$ENVF" NATIVE_CERTS)}"
  if [ -z "$ACME" ] && [ -z "$CERTS_MODE" ] && interactive; then
    ACME="$(ask "acme.json de Traefik, pour afficher les certificats (vide = ignorer)" "" TRAEFIK_CERTS_FILE)"
  fi
  if [ -n "$ACME" ]; then
    if [ -f "$ACME" ]; then
      ok "acme.json : $ACME"
      case "$(stat -c %a "$ACME")" in 600|400) ok "acme.json en mode $(stat -c %a "$ACME") : les clés privées sont protégées";; *) warn "acme.json en mode $(stat -c %a "$ACME")" "Traefik l'exige en 600 : chmod 600 $ACME";; esac
      if [ -z "$CERTS_MODE" ]; then
        if interactive; then
          hint "Relais (recommandé) : un petit service isolé lit acme.json (sans réseau) et ne transmet que noms et dates. Le worker ne voit jamais les clés."
          hint "Direct : le worker lit acme.json lui-même (donc ses clés privées)."
          case "$(ask "Certificats : 1) relais (recommandé)  2) direct  3) ignorer" 1)" in 2) CERTS_MODE=direct;; 3) CERTS_MODE=none;; *) CERTS_MODE=relay;; esac
        else CERTS_MODE=relay; fi
      fi
    else warn "acme.json introuvable : $ACME" "Les certificats ne seront pas affichés (le reste fonctionne)."; CERTS_MODE=none; fi
  else CERTS_MODE="${CERTS_MODE:-none}"; fi
  [ "$CERTS_MODE" = none ] && warn "Certificats non affichés (facultatif)" "TRAEFIK_CERTS_FILE=/chemin/acme.json, puis relais ou lecture directe."
}

check_existing() {
  if [ -f "$UNIT_DIR/$SERVICE.service" ] || [ -d "$DIR/app" ]; then
    warn "Une installation existe déjà dans $DIR" "« update » la met à jour en gardant .env et data/ ; « uninstall » la retire."
  else ok "Aucune installation précédente dans $DIR"; fi
}

# ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# Installation
# ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
latest_ref() {
  local t
  t="$(curl -fsS --max-time 10 "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)"
  printf '%s' "${t:-main}"
}
fetch_code() {   # télécharge la version demandée dans $DIR/app (et requirements.lock)
  local ref="$1" tmp url
  tmp="$(mktemp -d)"
  case "$ref" in v[0-9]*|[0-9]*) url="https://github.com/$REPO/archive/refs/tags/${ref}.tar.gz";; *) url="https://github.com/$REPO/archive/refs/heads/${ref}.tar.gz";; esac
  say "Téléchargement du code ($ref)…"
  curl -fsSL --max-time 120 "$url" -o "$tmp/src.tgz" || { rm -rf "$tmp"; die "Téléchargement impossible : $url"; }
  mkdir -p "$tmp/x" && tar -xzf "$tmp/src.tgz" -C "$tmp/x" --strip-components=1 || { rm -rf "$tmp"; die "Archive illisible"; }
  [ -d "$tmp/x/backend/app" ] && [ -f "$tmp/x/backend/requirements.lock" ] || { rm -rf "$tmp"; die "L'archive ne contient pas backend/app (version « $ref » inconnue ?)"; }
  rm -rf "$DIR/app.new" && cp -a "$tmp/x/backend/app" "$DIR/app.new"
  rm -rf "$DIR/app" && mv "$DIR/app.new" "$DIR/app"
  cp "$tmp/x/backend/requirements.lock" "$DIR/requirements.lock"
  rm -rf "$DIR/app/tests" "$tmp"
  printf '%s\n' "$ref" > "$DIR/VERSION"
}
pip_install() {
  if [ "$USE_VENV" = true ]; then
    [ -x "$DIR/venv/bin/python" ] || "$PY" -m venv "$DIR/venv" || die "Création du venv impossible (apt install python3-venv, ou NATIVE_VENV=false)."
    "$DIR/venv/bin/pip" install --quiet --disable-pip-version-check --require-hashes -r "$DIR/requirements.lock" || die "Installation des dépendances impossible."
  else
    mkdir -p "$DIR/lib"
    "$PY" -m pip install --quiet --disable-pip-version-check --require-hashes --target "$DIR/lib" -r "$DIR/requirements.lock" 2>"$DIR/.pip.err" \
      || "$PY" -m pip install --quiet --disable-pip-version-check --break-system-packages --require-hashes --target "$DIR/lib" -r "$DIR/requirements.lock" \
      || { cat "$DIR/.pip.err" >&2; die "Installation des dépendances impossible."; }
    rm -f "$DIR/.pip.err"
  fi
}
python_bin() { if [ "$USE_VENV" = true ]; then echo "$DIR/venv/bin/python"; else echo "$PY"; fi; }

grant_path() {   # grant_path MODE CHEMIN : droit ciblé pour SVC_USER (r, rw) + droit de passage sur les dossiers parents
  local mode="$1" path="$2" blk
  [ "$SVC_USER" = "$(stat -c %U "$path" 2>/dev/null)" ] && return 0
  command -v setfacl >/dev/null 2>&1 || return 1
  while blk="$(blocking_parent "$SVC_USER" "$path")" && [ -n "$blk" ]; do setfacl -m "u:$SVC_USER:x" "$blk" || return 1; done
  if [ -d "$path" ]; then
    setfacl -R -m "u:$SVC_USER:$([ "$mode" = rw ] && echo rwX || echo rX)" "$path" && setfacl -d -m "u:$SVC_USER:$([ "$mode" = rw ] && echo rwX || echo rX)" "$path"
  else setfacl -m "u:$SVC_USER:r" "$path"; fi
}

write_env() {
  local tok="$1"
  ( umask 077
    {
      echo "# .env du worker natif, généré par native-worker.sh le $(date +%F). Lu par systemd (EnvironmentFile)."
      echo "HUB_URL=$HUB"
      [ -n "$tok" ] && { echo "# Jeton d'enrôlement : usage unique, 15 minutes. Retiré dès que le worker est connecté."; echo "ENROLL_TOKEN=$tok"; }
      [ "${WORKER_ALLOW_INSECURE:-}" = 1 ] && echo "WORKER_ALLOW_INSECURE=1"
      echo "WORKER_MODULES=traefik"
      echo "TRAEFIK_DYNAMIC_DIR=$DYN"
      echo "DATA_DIR=$DIR/data"
      [ -n "${API:-}" ] && echo "TRAEFIK_API_URL=$API"
      [ -n "${LOGF:-}" ] && echo "TRAEFIK_ACCESS_LOG=$LOGF"
      case "$CERTS_MODE" in relay) echo "TRAEFIK_CERTS_FILE=$DIR/data/certs.json";; direct) echo "TRAEFIK_CERTS_FILE=$ACME";; esac
      echo "# Réglages de l'installation (lus par native-worker.sh)"
      echo "NATIVE_VENV=$USE_VENV"
      echo "NATIVE_CERTS=$CERTS_MODE"
      [ -n "${ACME:-}" ] && echo "ACME_FILE=$ACME"
    } > "$ENVF.new" )
  mv "$ENVF.new" "$ENVF"; chmod 600 "$ENVF"; chown root:root "$ENVF" 2>/dev/null || true
}

write_units() {
  local pypath="$DIR" rw="$DIR/data $DYN" ro=""
  [ "$USE_VENV" = false ] && pypath="$DIR:$DIR/lib"
  mkdir -p "$UNIT_DIR" || die "Dossier des services introuvable : $UNIT_DIR"
  [ -n "${LOGF:-}" ] && ro="$ro $(dirname "$LOGF")"
  [ "$CERTS_MODE" = direct ] && ro="$ro $(dirname "$ACME")"
  cat > "$UNIT_DIR/$SERVICE.service" <<EOF
[Unit]
Description=Armadok worker (Traefik de cette machine, relié au hub)
Documentation=https://github.com/$REPO
After=network-online.target
Wants=network-online.target

[Service]
User=$SVC_USER
WorkingDirectory=$DIR
EnvironmentFile=$ENVF
Environment=PYTHONPATH=$pypath
Environment=PYTHONUNBUFFERED=1
ExecStart=$(python_bin) -m app.worker
Restart=always
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$rw
${ro:+ReadOnlyPaths=${ro# }}
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
MemoryMax=256M

[Install]
WantedBy=multi-user.target
EOF
  if [ "$CERTS_MODE" = relay ]; then
    cat > "$UNIT_DIR/$RELAY.service" <<EOF
[Unit]
Description=Armadok : relevé des certificats (lit acme.json, n'écrit que noms et dates, aucun réseau)
After=local-fs.target

[Service]
User=root
WorkingDirectory=$DIR
Environment=PYTHONPATH=$pypath
Environment=CERTSYNC_IN=$ACME
Environment=CERTSYNC_OUT=$DIR/data/certs.json
ExecStart=$(python_bin) -m app.certsync
Restart=always
RestartSec=10
PrivateNetwork=yes
CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$DIR/data
ReadOnlyPaths=$(dirname "$ACME")
MemoryMax=128M

[Install]
WantedBy=multi-user.target
EOF
  else rm -f "$UNIT_DIR/$RELAY.service"; fi
}

wait_connected() {   # attend « connecté au hub » dans le journal (90 s)
  local since="$1"
  for _ in $(seq 1 45); do
    journalctl -u "$SERVICE" --since "$since" --no-pager 2>/dev/null | grep -q "connecté au hub" && return 0
    systemctl is-active --quiet "$SERVICE" || return 2
    sleep 2
  done
  return 1
}

final_checklist() {
  reset_report
  systemctl is-active --quiet "$SERVICE" && ok "Service « $SERVICE » démarré" || bad "Service « $SERVICE » arrêté" "journalctl -u $SERVICE -n 50"
  systemctl is-enabled --quiet "$SERVICE" 2>/dev/null && ok "Démarrage automatique au boot activé" || bad "Démarrage au boot non activé" "systemctl enable $SERVICE"
  [ "$(systemctl show -p Restart --value "$SERVICE" 2>/dev/null)" = always ] && ok "Redémarrage automatique en cas d'arrêt (Restart=always)" || warn "Redémarrage automatique absent"
  [ -f "$DIR/data/identity.json" ] && ok "Worker enrôlé (identité enregistrée, la clé ne quitte pas cette machine)" || warn "Worker pas encore enrôlé" "Vérifiez le jeton (usage unique, 15 min) : journalctl -u $SERVICE -n 30"
  if journalctl -u "$SERVICE" --since "-5min" --no-pager 2>/dev/null | grep -q "connecté au hub"; then ok "Connecté au hub ($HUB)"; else bad "Pas (encore) connecté au hub" "journalctl -u $SERVICE -n 30"; fi
  if [ -f "$DIR/data/identity.json" ] && [ -z "$(env_get "$ENVF" ENROLL_TOKEN)" ]; then ok "Jeton d'enrôlement retiré du .env"; elif [ -n "$(env_get "$ENVF" ENROLL_TOKEN)" ]; then warn "Jeton encore dans le .env" "Il est inutile après l'enrôlement : retirez la ligne ENROLL_TOKEN."; fi
  [ "$(stat -c %a "$ENVF")" = 600 ] && ok ".env en mode 600" || warn ".env en mode $(stat -c %a "$ENVF")" "chmod 600 $ENVF"
  as_user "$SVC_USER" test -w "$DYN" && ok "Écriture dans $DYN par « $SVC_USER »" || bad "« $SVC_USER » ne peut pas écrire dans $DYN" "setfacl -R -m u:$SVC_USER:rwX $DYN"
  case "$CERTS_MODE" in
    relay) systemctl is-active --quiet "$RELAY" && ok "Certificats : relais actif (sans clés, sans réseau)" || warn "Relais des certificats arrêté" "journalctl -u $RELAY -n 30";;
    direct) warn "Certificats lus directement par le worker (ses clés privées lui sont accessibles)" "Préférez le relais : NATIVE_CERTS=relay.";;
    *) warn "Certificats non affichés (facultatif)";;
  esac
  [ -n "${LOGF:-}" ] && { as_user "$SVC_USER" test -r "$LOGF" && ok "Journal d'accès lisible" || warn "Journal d'accès illisible par « $SVC_USER »" "setfacl -m u:$SVC_USER:r $LOGF"; }
  [ -f "$DIR/app/worker.py" ] && ok "Code installé : $(cat "$DIR/VERSION" 2>/dev/null) dans $DIR/app"
}

do_install() {
  banner "Armadok — worker natif (sans Docker)"
  hint "Le worker appelle le hub (connexion sortante) : aucun port à ouvrir ici. Rien n'est modifié tant que les vérifications ont un point bloquant."
  [ -n "$HUB" ] || HUB="$(ask "Adresse du hub (https://…)" "" HUB_URL | sed 's:/*$::')"
  reset_report
  check_system; check_python; check_hub; check_traefik; check_existing
  print_report "Checklist avant installation"
  if [ "$N_BAD" -gt 0 ]; then printf '\n%s✗ Des points bloquants empêchent l'\''installation : corrigez-les puis relancez.%s\n' "$R" "$Z"; [ "$ONLY_CHECK" = 1 ] || exit 1; fi
  [ "$ONLY_CHECK" = 1 ] && { [ "$N_BAD" -gt 0 ] && exit 1; printf '\n%s✓ Cette machine peut accueillir le worker natif.%s\n' "$G" "$Z"; exit 0; }
  if [ "$N_WARN" -gt 0 ] && interactive; then yes_no "Continuer malgré les avertissements ?" o || exit 1; fi
  is_root || { say "Les droits root sont nécessaires : relance via sudo…"; exec sudo -E bash "$0" install; }

  local tok=""
  if [ ! -f "$DIR/data/identity.json" ]; then
    step "Jeton d'enrôlement"
    hint "« Nouveau nœud » dans l'interface du hub. Il expire 15 minutes après sa création et ne sert qu'une fois."
    tok="${ENROLL_TOKEN:-$(env_get "$ENVF" ENROLL_TOKEN)}"; [ -n "$tok" ] || tok="$(asks "Jeton d'enrôlement (enr_…)" ENROLL_TOKEN)"
    case "$tok" in enr_?*) ;; *) die "Jeton invalide : il commence par « enr_ ».";; esac
  fi

  step "Installation"
  if ! id "$SVC_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$DIR" --no-create-home --shell /usr/sbin/nologin "$SVC_USER" || die "Création de l'utilisateur « $SVC_USER » impossible."
    mkdir -p "$DIR" && echo "$SVC_USER" > "$DIR/.created-user"
    say "Utilisateur système « $SVC_USER » créé (sans mot de passe ni shell)."
  fi
  echo "$SVC_USER" > "$DIR/.service-user" 2>/dev/null || { mkdir -p "$DIR"; echo "$SVC_USER" > "$DIR/.service-user"; }
  mkdir -p "$DIR/data"; chmod 755 "$DIR"; chmod 700 "$DIR/data"; chown "$SVC_USER" "$DIR/data" 2>/dev/null
  # droits ciblés sur Traefik (jamais plus que nécessaire)
  grant_path rw "$DYN" || die "Impossible de donner l'accès à $DYN (setfacl). Lancez le service sous le propriétaire du dossier : NATIVE_USER=<utilisateur>."
  [ -n "${LOGF:-}" ] && { grant_path r "$LOGF" || warn "Journal d'accès non lisible"; }
  [ "$CERTS_MODE" = direct ] && { grant_path r "$ACME" || warn "acme.json non lisible"; }
  fetch_code "${NATIVE_REF:-$(latest_ref)}"
  say "Installation des dépendances Python ($([ "$USE_VENV" = true ] && echo "venv" || echo "dossier lib, sans venv"))…"
  pip_install
  write_env "$tok"
  write_units
  chown -R root:root "$DIR/app" "$DIR/requirements.lock" 2>/dev/null
  [ -d "$DIR/venv" ] && chown -R root:root "$DIR/venv"; [ -d "$DIR/lib" ] && chown -R root:root "$DIR/lib"
  systemctl daemon-reload
  local since; since="$(date '+%Y-%m-%d %H:%M:%S')"
  systemctl enable --now "$SERVICE" >/dev/null 2>&1 || die "Démarrage du service impossible : journalctl -u $SERVICE -n 50"
  [ "$CERTS_MODE" = relay ] && systemctl enable --now "$RELAY" >/dev/null 2>&1
  step "Connexion au hub"
  say "En attente de la connexion (90 s au plus)…"
  wait_connected "$since"; local rc=$?
  if [ "$rc" = 0 ] && [ -f "$DIR/data/identity.json" ]; then
    sed -i '/^ENROLL_TOKEN=/d;/^# Jeton d.enrôlement/d' "$ENVF"
    systemctl restart "$SERVICE" >/dev/null 2>&1
  fi
  final_checklist
  print_report "Checklist après installation"
  { echo "Armadok — worker natif — installé le $(date '+%F %T')"; echo "Dossier : $DIR · utilisateur : $SVC_USER · hub : $HUB"; echo; plain_report; } > "$DIR/install-report.txt"
  chmod 644 "$DIR/install-report.txt"
  printf '\n  %sRapport enregistré : %s/install-report.txt%s\n' "$D" "$DIR" "$Z"
  hint "Commandes utiles : journalctl -u $SERVICE -f · systemctl status $SERVICE · make native-update · make native-uninstall"
  [ "$N_BAD" -gt 0 ] && exit 1
  exit 0
}

do_update() {
  is_root || exec sudo -E bash "$0" update
  [ -f "$ENVF" ] && [ -d "$DIR/app" ] || die "Aucune installation dans $DIR."
  PY="$(pick_python)" || die "Python 3.12 ou plus récent introuvable."
  SVC_USER="$(cat "$DIR/.service-user" 2>/dev/null || echo armadok)"
  local old; old="$(cat "$DIR/VERSION" 2>/dev/null || echo ?)"
  fetch_code "${NATIVE_REF:-$(latest_ref)}"
  pip_install
  chown -R root:root "$DIR/app" "$DIR/requirements.lock" 2>/dev/null
  systemctl restart "$SERVICE" && say "Mis à jour : $old → $(cat "$DIR/VERSION")."
  systemctl is-active --quiet "$RELAY" 2>/dev/null && systemctl restart "$RELAY"
  hint "Utilisez la même version que le hub : le protocole entre les deux évolue avec les versions."
}

do_status() {
  is_root || exec sudo -E bash "$0" status
  [ -f "$ENVF" ] || die "Aucune installation dans $DIR."
  SVC_USER="$(cat "$DIR/.service-user" 2>/dev/null || echo armadok)"; DYN="$(env_get "$ENVF" TRAEFIK_DYNAMIC_DIR)"; CERTS_MODE="$(env_get "$ENVF" NATIVE_CERTS)"; CERTS_MODE="${CERTS_MODE:-none}"
  ACME="$(env_get "$ENVF" ACME_FILE)"; LOGF="$(env_get "$ENVF" TRAEFIK_ACCESS_LOG)"
  reset_report; final_checklist; print_report "État du worker natif"
  exit $((N_BAD > 0))
}

do_uninstall() {
  is_root || exec sudo -E bash "$0" uninstall
  [ -f "$UNIT_DIR/$SERVICE.service" ] || [ -d "$DIR/app" ] || [ -d "$DIR/data" ] || die "Aucune installation trouvée dans $DIR."
  banner "Armadok — désinstallation du worker natif"
  systemctl disable --now "$SERVICE" >/dev/null 2>&1; systemctl disable --now "$RELAY" >/dev/null 2>&1
  rm -f "$UNIT_DIR/$SERVICE.service" "$UNIT_DIR/$RELAY.service"; systemctl daemon-reload
  local u dyn; u="$(cat "$DIR/.service-user" 2>/dev/null || true)"; dyn="$(env_get "$ENVF" TRAEFIK_DYNAMIC_DIR)"
  if [ -n "$u" ] && command -v setfacl >/dev/null 2>&1; then
    [ -n "$dyn" ] && [ -d "$dyn" ] && setfacl -R -x "u:$u" "$dyn" 2>/dev/null && setfacl -R -d -x "u:$u" "$dyn" 2>/dev/null
    say "Droits (ACL) de « $u » retirés du dossier Traefik."
  fi
  : "${DIR:?}"
  rm -rf "${DIR:?}/app" "${DIR:?}/venv" "${DIR:?}/lib" "${DIR:?}/requirements.lock" "${DIR:?}/VERSION"
  say "Service, code et dépendances retirés."
  if [ -d "$DIR/data" ]; then
    if yes_no "Supprimer aussi data/ (identité du nœud : il faudra créer un nouveau nœud) ?" n; then rm -rf "$DIR/data" "$ENVF" "$DIR/install-report.txt"; say "data/ et .env supprimés."
    else say "data/ et .env conservés dans $DIR (réinstallation possible sans nouveau jeton)."; fi
  fi
  if [ -f "$DIR/.created-user" ] && [ ! -d "$DIR/data" ]; then userdel "$(cat "$DIR/.created-user")" 2>/dev/null && say "Utilisateur « $(cat "$DIR/.created-user") » supprimé."; fi
  [ -d "$DIR/data" ] || rm -rf "$DIR"
  printf '\n%s✓ Désinstallé.%s\n' "$G" "$Z"
}

case "$CMD" in
  install|check) do_install;;
  update) do_update;;
  status) do_status;;
  uninstall) do_uninstall;;
  *) die "Commande inconnue : $CMD (check, install, update, status, uninstall)";;
esac
