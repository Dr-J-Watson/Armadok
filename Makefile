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

define STACK_YML
# Armadok : pile pour Portainer (Stacks → Add stack → Web editor), générée par « make stack ».
# Portainer n'a pas de fichier .env : les $${VARIABLES} ci-dessous se saisissent dans « Environment variables »
# (« Advanced mode » : collez tout le contenu du fichier stack.env). Chaque variable y est étiquetée.
services:
  #@certs-service
  armadok:
    image: $${ARMADOK_IMAGE:-$(IMAGE)}
    container_name: armadok
    restart: unless-stopped
    # Démarre en root uniquement pour donner /data à PUID:PGID, puis abandonne ses droits (python -m app.drop).
    command: ["python", "-m", "app.drop", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
    user: "0:0"
    cap_add: [CHOWN, SETUID, SETGID]
    environment:
      PUID: "$${PUID:-1000}"                # propriétaire du dossier Traefik : il doit pouvoir y écrire
      PGID: "$${PGID:-1000}"
      #@stack-groups
      # chemins DANS le conteneur : ne pas modifier
      TRAEFIK_DYNAMIC_DIR: /traefik-dynamic
      DATA_DIR: /data
      #@log-env
      #@certs-env
      #@dk-env
      # [OBLIGATOIRE] compte propriétaire et secrets
      ADMIN_USER: $${ADMIN_USER:-admin}
      ADMIN_PASSWORD_HASH_B64: $${ADMIN_PASSWORD_HASH_B64}
      JWT_SECRET: $${JWT_SECRET}
      ENCRYPTION_KEY: $${ENCRYPTION_KEY}
      # [UNE FOIS] valeurs de départ du nœud Principal (ensuite réglées dans « Ce nœud »)
      TRAEFIK_ENTRYPOINTS: $${TRAEFIK_ENTRYPOINTS:-web,websecure}
      TRAEFIK_CERT_RESOLVERS: $${TRAEFIK_CERT_RESOLVERS:-}
      TRAEFIK_API_URL: $${TRAEFIK_API_URL:-}
      PROMETHEUS_URL: $${PROMETHEUS_URL:-}
      DEFAULT_DNS_TARGET: $${DEFAULT_DNS_TARGET:-}
      # [TOUJOURS] compte OVH principal (facultatif)
      OVH_ENDPOINT: $${OVH_ENDPOINT:-ovh-eu}
      OVH_APPLICATION_KEY: $${OVH_APPLICATION_KEY:-}
      OVH_APPLICATION_SECRET: $${OVH_APPLICATION_SECRET:-}
      OVH_CONSUMER_KEY: $${OVH_CONSUMER_KEY:-}
      # [TOUJOURS] sessions, réseau, audit (valeurs par défaut)
      ACCESS_TOKEN_MINUTES: $${ACCESS_TOKEN_MINUTES:-15}
      REFRESH_TOKEN_DAYS: $${REFRESH_TOKEN_DAYS:-7}
      COOKIE_SECURE: $${COOKIE_SECURE:-true}
      FORWARDED_ALLOW_IPS: $${FORWARDED_ALLOW_IPS:-127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}
      AUDIT_MAX_MB: $${AUDIT_MAX_MB:-50}
    volumes:
      - $${TRAEFIK_DYNAMIC_PATH}:/traefik-dynamic   # configuration dynamique de Traefik (écriture)
      - armadok_data:/data                            # base, sauvegardes, audit : volume nommé, À SAUVEGARDER
      #@log-vol
      #@certs-vol
      #@dk-vol
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 512m
    pids_limit: 200
    networks: [proxy]

networks:
  proxy:
    external: true
    name: $${TRAEFIK_NETWORK:-proxy}
endef
export STACK_YML

define WORKER_STACK_YML
# Armadok : pile Portainer d'un WORKER (Stacks → Add stack → Web editor), générée par « make worker-stack ».
# Les $${VARIABLES} se saisissent dans « Environment variables » (« Advanced mode » : collez le contenu de stack.env).
services:
  #@certs-service
  worker:
    image: $${WORKER_IMAGE:-$(IMAGE)}
    container_name: armadok-worker
    restart: unless-stopped
    # Démarre en root uniquement pour donner /data à PUID:PGID, puis abandonne ses droits (python -m app.drop).
    command: ["python", "-m", "app.drop", "python", "-m", "app.worker"]
    user: "0:0"
    cap_add: [CHOWN, SETUID, SETGID]
    environment:
      PUID: "$${PUID:-1000}"                # propriétaire du dossier Traefik : il doit pouvoir y écrire
      PGID: "$${PGID:-1000}"
      HUB_URL: $${HUB_URL}
      ENROLL_TOKEN: $${ENROLL_TOKEN:-}      # usage unique, 15 minutes : inutile après la première connexion
      WORKER_ALLOW_INSECURE: $${WORKER_ALLOW_INSECURE:-}
      TRAEFIK_API_URL: $${TRAEFIK_API_URL:-}   #@tr
      TRAEFIK_DYNAMIC_DIR: /traefik-dynamic   #@tr
      DATA_DIR: /data
      #@log-env
      #@certs-env
      #@wk-env
      #@wk-pgroups
    volumes:
      - $${TRAEFIK_DYNAMIC_PATH}:/traefik-dynamic   # la configuration dynamique (file provider) de ce Traefik   #@tr
      - worker_data:/data                           # identité du worker + sauvegardes
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
    mem_limit: 256m
endef
export WORKER_STACK_YML


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
# ask : question avec valeur par défaut, ou valeur préremplie par une variable (3e argument). asks : idem sans écho (secrets). yes / confirm : o/N, O/n.
ask() { local v="" pre="$${3:-}"; if [ -n "$$pre" ] && [ -n "$${!pre:-}" ]; then v="$${!pre}"; printf '  %s✓%s %s : %s\n' "$$G" "$$Z" "$$1" "$$v" >&2; else read -r -p "  $${C}➜$${Z} $$1 $${D}[$$2]$${Z} : " v || true; fi; printf '%s' "$${v:-$$2}"; }
asks() { local v="" pre="$${2:-}"; if [ -n "$$pre" ] && [ -n "$${!pre:-}" ]; then v="$${!pre}"; printf '  %s✓%s %s : (fourni)\n' "$$G" "$$Z" "$$1" >&2; else read -rs -p "  $${C}➜$${Z} $$1 : " v || true; echo >&2; fi; printf '%s' "$$v"; }
yes() { local v=""; read -r -p "  $${C}➜$${Z} $$1 $${D}[o/N]$${Z} : " v || true; [[ "$${v,,}" =~ ^(o|oui|y|yes)$$ ]]; }
confirm() { local v=""; read -r -p "  $${C}➜$${Z} $$1 $${D}[O/n]$${Z} : " v || true; [[ ! "$${v,,}" =~ ^(n|non|no)$$ ]]; }
image_ref() { case "$$1" in "") echo "$(IMAGE)";; */*|*:*) echo "$$1";; *) echo "$(IMAGE_REPO):$$1";; esac; }
# pick_image VARIABLE_À_REMPLIR : demande « latest » ou un numéro de version précis (ex. 1.0.0) ; sauté si IMAGE a été fourni (IMAGE=… ou variable d'environnement)
pick_image() {
  local ans=""
  if [ -n "$(if $(filter file,$(origin IMAGE)),,1)" ]; then ans="$(IMAGE)"; printf '  %s✓%s Image : %s\n' "$$G" "$$Z" "$$ans" >&2
  else
    hint "Quelle version installer ? « latest » est la plus récente publiée ; un numéro précis (ex. 1.0.0) fige la version. Les versions existantes : github.com/Dr-J-Watson/Armadok/releases"
    read -r -p "  $${C}➜$${Z} Version d'Armadok $${D}[latest]$${Z} : " ans || true
    ans="$$(image_ref "$${ans:-latest}")"
  fi
  printf -v "$$1" '%s' "$$ans"
}
as_root() { if [ "$$(id -u)" = 0 ]; then "$$@"; else sudo "$$@"; fi; }
need_docker() {
  command -v docker >/dev/null || die "Docker est requis."
  docker compose version >/dev/null 2>&1 || die "Docker Compose (v2) est requis."
}
# Source d'une configuration : chemin absolu d'un dossier de l'hôte, OU nom d'un volume Docker déjà existant.
# pick_source ÉTIQUETTE [VARIABLE_PRÉREMPLIE] [opt] -> SRC (chemin ou nom) et SRC_KIND (dir|volume) ; « opt » : vide accepté.
pick_source() {
  local v; v=$$(ask "$$1" "" "$${2:-}"); v=$${v/#\~/$$HOME}
  SRC=""; SRC_KIND=""
  case "$$v" in
    "") [ "$${3:-}" = opt ] && return 0; die "Une valeur est requise.";;
    /*|./*|../*) [ -d "$$v" ] || die "Dossier introuvable : $$v"; SRC=$$(cd "$$v" && pwd); SRC_KIND=dir;;
    *) docker volume inspect "$$v" >/dev/null 2>&1 || die "Volume « $$v » introuvable (docker volume ls)."; SRC="$$v"; SRC_KIND=volume;;
  esac
}
ensure_image() { docker image inspect "$$UTIL_IMAGE" >/dev/null 2>&1 || docker pull "$$UTIL_IMAGE" >/dev/null 2>&1 || { echo "Image $$UTIL_IMAGE introuvable."; exit 1; }; }
# Un volume ne se lit pas depuis l'hôte : on passe par un conteneur jetable (root) qui le voit sur /d.
util() { ensure_image; docker run --rm -u 0 --entrypoint "" "$$@"; }
owner_of() { if [ "$$1" = dir ]; then stat -c '%u:%g' "$$2"; else util -v "$$2:/d:ro" "$$UTIL_IMAGE" stat -c '%u:%g' /d; fi; }
exists_in() { if [ "$$1" = dir ]; then [ -e "$$2/$$3" ]; else util -v "$$2:/d:ro" "$$UTIL_IMAGE" test -e "/d/$$3"; fi; }
write_in() { # KIND SRC FICHIER PROPRIÉTAIRE ; contenu sur l'entrée standard
  if [ "$$1" = dir ]; then cat > "$$2/$$3"; chown "$$4" "$$2/$$3" 2>/dev/null || as_root chown "$$4" "$$2/$$3" || true
  else ensure_image; docker run --rm -i -u 0 --entrypoint "" -v "$$2:/d" "$$UTIL_IMAGE" sh -c 'cat > "/d/$$1" && chown "$$2" "/d/$$1"' _ "$$3" "$$4"; fi
}
# Un volume qui appartient à root : on propose de le céder à 1000:1000 pour que l'interface ne tourne pas en root.
own_volume() { # KIND SRC PROPRIÉTAIRE -> propriétaire à utiliser
  local o="$$3" v=""
  if [ "$$1" = volume ] && [ "$$o" = 0:0 ]; then
    warnl "Ce volume appartient à root : l'interface y tournerait en root." >&2
    if confirm "Le donner à 1000:1000 ?"; then util -v "$$2:/d" "$$UTIL_IMAGE" chown -R 1000:1000 /d && o=1000:1000; fi
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
# --- Questions communes au hub (Docker Compose) et à la pile Portainer. Chaque groupe est expliqué avant d'être posé.
# ask_traefik : DYN DYN_KIND OWNER NET EPS RES CERTS_* LOG_SRC API PROM DNST · ask_admin : AUSER HASH · ask_ovh : OEND AK AS CK · ask_route : DOM MW EP
ask_traefik() {
  sub "Configuration dynamique de Traefik"
  hint "C'est le dossier où Traefik lit ses fichiers de sites (son « file provider »). L'interface y écrit les sites que vous créez,"
  hint "et Traefik les recharge tout seul. Donnez le chemin absolu du dossier, ou le nom d'un volume Docker existant (docker volume ls)."
  echo
  pick_source "Dossier ou volume" TRAEFIK_DYNAMIC_PATH
  DYN="$$SRC"; DYN_KIND="$$SRC_KIND"
  OWNER=$$(own_volume "$$DYN_KIND" "$$DYN" "$$(owner_of "$$DYN_KIND" "$$DYN")")
  good "L'interface tournera avec l'identité $$OWNER (propriétaire de ce $$([ "$$DYN_KIND" = volume ] && echo volume || echo dossier)) pour pouvoir y écrire."

  sub "Réseau Docker partagé avec Traefik"
  hint "Traefik joint l'interface par ce réseau (http://armadok:8000). Pour le connaître : docker network ls (souvent « proxy »)."
  echo
  NET=$$(ask "Nom du réseau" "proxy" TRAEFIK_NETWORK)
  docker network inspect "$$NET" >/dev/null 2>&1 || die "Réseau « $$NET » introuvable (docker network ls). Créez-le ou indiquez le bon nom."
  good "Réseau « $$NET » trouvé."

  sub "Points d'entrée et certificats (valeurs de départ)"
  hint "Les noms des points d'entrée de Traefik (web = port 80, websecure = port 443) et de son résolveur Let's Encrypt, tels qu'écrits dans"
  hint "sa configuration. Proposés dans les formulaires. Lus UNE seule fois : vous les changerez ensuite dans « Ce nœud »."
  echo
  EPS=$$(ask "Points d'entrée (séparés par des virgules)" "web,websecure")
  RES=$$(ask "Résolveur de certificats" "letsencrypt")

  sub "Certificats HTTPS (facultatif)"
  hint "L'interface peut afficher les échéances et signaler les sites sans certificat. Elle ne lit que les noms et les dates :"
  hint "aucune clé privée n'arrive jamais dans le navigateur. Indiquez le dossier (ou volume) qui contient acme.json. Vide = ignorer."
  echo
  pick_source "Dossier ou volume d'acme.json" CERTS_PATH opt
  CERTS_SRC="$$SRC"; CERTS_KIND="$$SRC_KIND"; CERTS_MODE=hub; CERTS_DOCKER=""; TRAEFIK_CTR=traefik; DGID=""; LOG_KIND=dir
  if [ -n "$$CERTS_SRC" ]; then
    exists_in "$$CERTS_KIND" "$$CERTS_SRC" "acme.json" && good "acme.json trouvé." || warnl "acme.json introuvable pour l'instant : il apparaîtra dès que Traefik l'aura créé."
    echo
    hint "Supprimer ou renouveler un certificat depuis l'interface demande de relancer Traefik : l'interface doit alors accéder au socket Docker."
    warnl "Cela lui donne la main sur Docker. À n'accepter que si l'interface est protégée (chaîne privée, 2FA)."
    hint "Sans cela, tout marche sauf supprimer / renouveler."
    echo
    if yes "Autoriser la suppression / le renouvellement ?"; then
      CERTS_DOCKER=1
      TRAEFIK_CTR=$$(ask "Nom du conteneur de Traefik" "traefik")
      DGID=$$(getent group docker | cut -d: -f3 || true)
      [ -n "$$DGID" ] || DGID=$$(ask "Numéro du groupe « docker » (getent group docker)" "")
    fi
  fi

  sub "Gérer Docker depuis l'interface (facultatif)"
  hint "Conteneurs, journaux, projets docker compose (fichiers, déploiement, Git), images, terminal : tout depuis l'interface, relié à vos sites Traefik."
  warnl "Cela donne à l'interface un accès équivalent à ROOT sur cette machine. À n'accepter que si elle est protégée (chaîne privée, 2FA, droits « Docker » limités)."
  hint "Sans cela, tout marche sauf la partie Docker. Vous pourrez l'activer plus tard (DOCKER_MANAGE, PROJECT_ROOTS, socket)."
  echo
  DOCKER_MANAGE=""; PROJECT_ROOTS=""
  if yes "Activer la gestion de Docker ?"; then
    DOCKER_MANAGE=1
    if [ -z "$$DGID" ]; then
      DGID=$$(getent group docker | cut -d: -f3 || true)
      [ -n "$$DGID" ] || DGID=$$(ask "Numéro du groupe « docker » (getent group docker)" "")
    fi
    echo
    hint "Dossiers qui contiennent vos projets docker compose : chemins absolus séparés par des virgules (sans espace)."
    hint "Ils sont montés avec le MÊME chemin dans le conteneur et bornent tout ce que l'interface lit et écrit."
    PROJECT_ROOTS=$$(ask "Dossiers des projets" "$$HOME/docker" PROJECT_ROOTS)
    local r norm=""
    for r in $${PROJECT_ROOTS//,/ }; do
      case "$$r" in /*) ;; *) die "« $$r » n'est pas un chemin absolu.";; esac
      [ -d "$$r" ] || die "Le dossier « $$r » n'existe pas."
      norm+=$${norm:+,}$$(cd "$$r" && pwd)
    done
    PROJECT_ROOTS="$$norm"
    good "Gestion de Docker activée ($$PROJECT_ROOTS)."
  fi

  sub "Journal d'accès de Traefik (facultatif)"
  hint "Active la liste des adresses IP des visiteurs, les attaques probables et le bannissement. Le dossier est monté en lecture seule."
  hint "Traefik doit écrire ce fichier : --accesslog.filepath=/logs/access.log --accesslog.format=json (aide : « Fonctionnalités facultatives »)."
  hint "Chemin absolu du dossier (il doit exister avant le démarrage), ou nom d'un volume Docker existant. Vide = ignorer."
  echo
  pick_source "Dossier ou volume du journal" TRAEFIK_ACCESS_LOG_PATH opt
  LOG_SRC="$$SRC"; LOG_KIND="$$SRC_KIND"

  sub "Autres valeurs de départ (facultatif)"
  hint "Lues UNE seule fois pour remplir le nœud « Principal » ; modifiables ensuite dans « Ce nœud ». Vide = ignorer."
  echo
  API=$$(ask "API de Traefik, pour l'état des sites (ex. http://traefik:8080)" "" TRAEFIK_API_URL)
  PROM=$$(ask "Prometheus, pour les métriques et alertes (ex. http://prometheus:9090)" "" PROMETHEUS_URL)
  DNST=$$(ask "IP proposée pour les nouveaux sous-domaines (IP publique de cette machine)" "" DEFAULT_DNS_TARGET)
}
ask_admin() {
  sub "Compte propriétaire"
  hint "C'est l'administrateur permanent de l'interface. Les autres comptes se créent ensuite dans l'interface (menu Équipe)."
  hint "Le mot de passe n'est jamais écrit en clair : seul son hachage (bcrypt) est enregistré dans le fichier de configuration."
  echo
  AUSER=$$(ask "Identifiant" "admin")
  while :; do
    read -rs -p "  $${C}➜$${Z} Mot de passe $${D}(12 caractères minimum)$${Z} : " PW; echo
    read -rs -p "  $${C}➜$${Z} Confirmez le mot de passe : " PW2; echo
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
  echo
  OEND=$$(ask "Région" "ovh-eu")
  AK=$$(ask "Application key" "")
  AS=""; CK=""
  if [ -n "$$AK" ]; then
    read -rs -p "  $${C}➜$${Z} Application secret : " AS; echo
    read -rs -p "  $${C}➜$${Z} Consumer key $${D}(vide = la générer)$${Z} : " CK; echo
    if [ -z "$$CK" ]; then
      OUT=$$(docker run --rm -e OVH_ENDPOINT="$$OEND" -e OVH_APPLICATION_KEY="$$AK" -e OVH_APPLICATION_SECRET="$$AS" "$${IMG_HUB:-$(IMAGE)}" python -m app.tools.ovh_consumer_key)
      echo "$$OUT" | sed -n '1,2p' | sed 's/^/  /'
      read -r -p "  $${C}➜$${Z} Appuyez sur Entrée une fois la clé validée sur cette page… " _
      CK=$$(echo "$$OUT" | sed -n 's/.*OVH_CONSUMER_KEY=//p')
    fi
  fi
}
ask_route() {
  sub "Adresse de l'interface (facultatif)"
  hint "Pour ouvrir l'interface par une adresse (https://…), un fichier de routage « armadok.yml » est ajouté à la configuration de Traefik."
  hint "Il faudra ensuite créer l'enregistrement DNS de cette adresse vers ce serveur. Vide = pas de routage (vous le ferez vous-même)."
  echo
  DOM=$$(ask "Domaine de l'interface (ex. armadok.exemple.fr)" "")
  MW=""; EP=""
  if [ -n "$$DOM" ]; then
    EP=$${EPS##*,}; [ "$$EP" = "web" ] && EP=websecure; case ",$$EPS," in *,websecure,*) EP=websecure;; esac
    hint "Les protections (middlewares Traefik : liste d'IP, SSO…) limitent l'accès. Sans protection, l'interface est ouverte à tous."
    MW=$$(ask "Middlewares de protection (séparés par des virgules)" "")
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
# install_hub compose|stack : tout le parcours du hub. N'écrit que des fichiers : RIEN n'est démarré.
install_hub() {
  local mode="$$1" label n1 n2 n3 n4 n5
  command -v openssl >/dev/null || die "openssl est requis."
  if [ "$$mode" = stack ]; then
    label="dans Portainer"
    if [ -f docker-compose.stack.yml ] || [ -f stack.env ]; then die "docker-compose.stack.yml ou stack.env existe déjà : rien n'est écrasé (déplacez-les pour régénérer)."; fi
  else
    label="avec Docker Compose"
    if [ -f .env ]; then die "Un .env existe déjà ici : rien n'est écrasé. « make up » le démarre, ou déplacez-le pour réinstaller."; fi
  fi
  banner "Armadok — installation du hub $$label"
  printf '\n'
  say "Cet assistant pose quelques questions, puis écrit les fichiers de configuration."
  if [ "$$mode" = stack ]; then
    say "Il ne démarre rien : vous collerez les deux fichiers produits dans Portainer."
  else
    say "Il ne démarre rien : à la fin, il vous indique la commande à lancer."
  fi
  hint "Les questions facultatives se passent avec Entrée. Vous pourrez tout régler plus tard dans l'interface, sauf les secrets du fichier .env."
  hint "Un récapitulatif vous est montré avant d'écrire quoi que ce soit."
  if [ "$$mode" = compose ] && docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx armadok; then
    echo
    warnl "Un conteneur nommé « armadok » existe déjà sur cette machine. Pour lancer cette nouvelle installation, il faudra"
    hint "d'abord l'arrêter et le supprimer (docker rm -f armadok), ou changer « container_name » dans docker-compose.yml."
  fi
  echo; pick_image IMG_HUB
  hint "Récupération de l'image $$IMG_HUB…"
  docker pull "$$IMG_HUB" >/dev/null 2>&1 || docker image inspect "$$IMG_HUB" >/dev/null 2>&1 || die "Image $$IMG_HUB introuvable (version inexistante ? voir github.com/Dr-J-Watson/Armadok/releases)."
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
  confirm "Écrire les fichiers maintenant ?" || { echo; say "Annulé : rien n'a été écrit."; exit 0; }
  echo
  if [ "$$mode" = stack ]; then
    local S; S=$$(EXTRA_VOLS=$$'  armadok_data:\n' build_compose "$$STACK_YML" ARMADOK_IMAGE)
    if [ "$${CERTS_DOCKER:-}" = 1 ] || [ "$${DOCKER_MANAGE:-}" = 1 ]; then S=$${S//'#@stack-groups'/'PGROUPS: "$${DOCKER_GID}"           # groupe « docker » : conservé pour joindre le socket'}; fi
    S=$$(grep -v '#@stack-groups' <<<"$$S")
    printf '%s\n' "$$S" > docker-compose.stack.yml
    umask 077
    {
      echo "# stack.env : à coller dans Portainer → Stacks → Add stack → « Environment variables » → « Advanced mode »."
      echo "# Généré par « make stack » le $$(date +%F). Étiquettes et explications : .env.example. Contient des SECRETS : ne pas le partager."
      echo; echo "# ── [OBLIGATOIRE · TOUJOURS] compte propriétaire et secrets"
      echo "ADMIN_USER=$$AUSER"
      echo "ADMIN_PASSWORD_HASH_B64=$$(printf '%s' "$$HASH" | base64 -w0)"
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
      echo; echo "# ── [COMPOSE] chemins, réseau, utilisateur (construisent les montages de la pile)"
      echo "ARMADOK_IMAGE=$$IMG_HUB"
      echo "TRAEFIK_DYNAMIC_PATH=$$DYN"
      echo "PUID=$${OWNER%%:*}"
      echo "PGID=$${OWNER##*:}"
      echo "TRAEFIK_NETWORK=$$NET"
      [ -n "$$LOG_SRC" ] && echo "TRAEFIK_ACCESS_LOG_PATH=$$LOG_SRC"
      [ -n "$$CERTS_SRC" ] && echo "CERTS_PATH=$$CERTS_SRC"
      [ -n "$${DGID:-}" ] && echo "DOCKER_GID=$$DGID"
      if [ "$${DOCKER_MANAGE:-}" = 1 ]; then echo; echo "# ── [TOUJOURS] gestion de Docker (accès équivalent à root : droits « docker.* » du nœud)"; echo "DOCKER_MANAGE=true"; echo "PROJECT_ROOTS=$$PROJECT_ROOTS"; fi
    } > stack.env
    chmod 600 stack.env
    good "docker-compose.stack.yml créé (la pile)."
    good "stack.env créé (les variables, droits 600)."
    write_route
    banner "Terminé — il reste à coller les fichiers dans Portainer"
    printf '\n'
    say "$${B}1.$${Z} Dans Portainer : Stacks → Add stack, nom « armadok »."
    say "$${B}2.$${Z} Onglet « Web editor » : collez le contenu de docker-compose.stack.yml."
    cmd "cat docker-compose.stack.yml"
    say "$${B}3.$${Z} Plus bas, « Environment variables » → « Advanced mode » : collez le contenu de stack.env."
    cmd "cat stack.env"
    say "$${B}4.$${Z} Cliquez « Deploy the stack »."
    printf '\n'
    hint "stack.env contient les secrets : effacez-le de cette machine une fois collé (Portainer les garde de son côté)."
    hint "Mise à jour : Stacks → armadok → « Pull and redeploy ». Sauvegarde : le volume « armadok_data » (comptes, clés chiffrées, audit)."
    [ -z "$${DOM:-}" ] || hint "N'oubliez pas l'enregistrement DNS de $$DOM vers ce serveur."
    printf '\n'
    if yes "Afficher les deux fichiers maintenant ?"; then echo; printf '%s── docker-compose.stack.yml ──%s\n' "$$B" "$$Z"; cat docker-compose.stack.yml; echo; printf '%s── stack.env ──%s\n' "$$B" "$$Z"; cat stack.env; fi
  else
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
    write_route
    banner "Terminé — rien n'est démarré"
    printf '\n'
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
  fi
  printf '\n'
}
# install_worker compose|stack : tout le parcours du worker. En mode « stack », rien n'est démarré (pile Portainer à copier-coller).
install_worker() {
  local mode="$$1" label
  if [ "$$mode" = stack ]; then
    label="dans Portainer"
    if [ -f docker-compose.stack.yml ] || [ -f stack.env ]; then die "docker-compose.stack.yml ou stack.env existe déjà : rien n'est écrasé (déplacez-les pour régénérer)."; fi
  else
    label="avec Docker Compose"
    if [ -f .env ]; then die "Un .env existe déjà ici : rien n'est écrasé. « make up » le démarre, ou déplacez-le pour réinstaller."; fi
  fi
  banner "Armadok — installation d'un worker $$label"
  printf '\n'
  say "Un worker relie CETTE machine (son Traefik et/ou son Docker) à l'interface (le hub) installée ailleurs : le hub peut alors la gérer."
  say "Le worker appelle le hub (connexion sortante) : aucun port à ouvrir ici."
  hint "Déroulé : 1) cette machine · 2) l'adresse du hub · 3) le jeton d'enrôlement · 4) écriture, démarrage et connexion."
  hint "Le jeton (valable 15 minutes, une seule fois) est demandé en DERNIER : ne le créez qu'au moment de l'étape 3."
  hint "Raccourci : « Nouveau nœud » dans l'interface du hub affiche une commande toute prête, avec l'adresse et le jeton déjà dedans."
  step "1/4 · Cette machine"
  UTIL_IMAGE="$$(image_ref "$${WORKER_IMAGE:-}")"
  sub "Que doit gérer ce worker ?"
  hint "1) Traefik + DNS : les sites, protections et certificats du Traefik de cette machine (le DNS reste géré par le hub)."
  hint "2) Docker seul : les conteneurs et projets docker compose de cette machine, sans Traefik."
  hint "3) Les deux : Traefik ET Docker, avec la liaison complète (conteneur → règles → visiteurs)."
  echo
  case "$$(ask "Votre choix (1, 2 ou 3)" "1" WORKER_MODULES)" in
    2|docker) WORKER_MODULES=docker; WORKER_NOTR=1; WORKER_DOCKER=1;;
    3|both|traefik,docker|docker,traefik) WORKER_MODULES=traefik,docker; WORKER_NOTR=""; WORKER_DOCKER=1;;
    1|traefik|"") WORKER_MODULES=traefik; WORKER_NOTR=""; WORKER_DOCKER="";;
    *) die "Choix invalide : 1, 2 ou 3.";;
  esac
  DYN=""; DYN_KIND=dir; OWNER=""; CERTS_SRC=""; CERTS_KIND=dir; LOG_SRC=""; LOG_KIND=dir; API=""; NET=""; PROJECT_ROOTS=""; DGID=""; WORKER_ALLOW_EXEC=""; WORKER_ADDRESSES="$${WORKER_ADDRESSES:-}"
  if [ -z "$$WORKER_NOTR" ]; then
  sub "Configuration dynamique de Traefik"
  hint "Le dossier où Traefik lit ses fichiers de sites (file provider). Le worker y écrit ce que le hub lui demande, et rien d'autre."
  hint "Chemin absolu du dossier, ou nom d'un volume Docker existant (docker volume ls)."
  echo
  pick_source "Dossier ou volume" TRAEFIK_DYNAMIC_PATH
  DYN="$$SRC"; DYN_KIND="$$SRC_KIND"
  OWNER=$$(own_volume "$$DYN_KIND" "$$DYN" "$$(owner_of "$$DYN_KIND" "$$DYN")")
  good "Le worker tournera avec l'identité $$OWNER (propriétaire de ce $$([ "$$DYN_KIND" = volume ] && echo volume || echo dossier)) pour pouvoir y écrire."
  sub "Certificats (facultatif)"
  hint "Pour montrer les échéances des certificats dans le hub : dossier ou volume d'acme.json, lu en lecture seule par un relais isolé,"
  hint "qui ne transmet que les noms et les dates (jamais les clés). Vide = ignorer."
  echo
  pick_source "Dossier ou volume d'acme.json" CERTS_PATH opt
  CERTS_SRC="$$SRC"; CERTS_KIND="$$SRC_KIND"
  sub "Journal d'accès (facultatif)"
  hint "Le dossier (ou volume) qui contient le journal d'accès de Traefik (format JSON, access.log). Le worker le LIT chez lui et l'envoie au hub :"
  hint "statistiques de visiteurs, adresses IP, alertes et bannissements automatiques de ce nœud. Vide = ignorer."
  echo
  pick_source "Dossier ou volume du journal d'accès" TRAEFIK_ACCESS_LOG_PATH opt
  LOG_SRC="$$SRC"; LOG_KIND="$$SRC_KIND"
  sub "État en direct des sites (facultatif)"
  hint "Si l'API de Traefik est joignable depuis un réseau Docker, le hub affiche l'état de chaque site. Ex : http://traefik:8080. Vide = ignorer."
  echo
  API=$$(ask "URL de l'API de Traefik" "" TRAEFIK_API_URL)
  if [ -n "$$API" ]; then
  	NET=$$(ask "Réseau Docker partagé avec Traefik (pour joindre son API)" "proxy" TRAEFIK_NETWORK)
  	docker network inspect "$$NET" >/dev/null 2>&1 || die "Réseau « $$NET » introuvable (docker network ls)."
  fi
  fi
  if [ -n "$$WORKER_DOCKER" ]; then
  sub "Docker de cette machine"
  warnl "Cela donne au hub (selon les droits « Docker » du nœud) un accès équivalent à ROOT sur cette machine."
  hint "Dossiers qui contiennent vos projets docker compose : chemins absolus séparés par des virgules (sans espace)."
  hint "Ils sont montés avec le MÊME chemin dans le worker et bornent tout ce que le hub lit et écrit ici."
  echo
  PROJECT_ROOTS=$$(ask "Dossiers des projets" "$$HOME/docker" PROJECT_ROOTS)
  local r norm=""
  for r in $${PROJECT_ROOTS//,/ }; do
    case "$$r" in /*) ;; *) die "« $$r » n'est pas un chemin absolu.";; esac
    [ -d "$$r" ] || die "Le dossier « $$r » n'existe pas."
    norm+=$${norm:+,}$$(cd "$$r" && pwd)
  done
  PROJECT_ROOTS="$$norm"
  DGID=$$(getent group docker | cut -d: -f3 || true)
  [ -n "$$DGID" ] || DGID=$$(ask "Numéro du groupe « docker » (getent group docker)" "")
  [ -n "$$OWNER" ] || { OWNER=$$(stat -c '%u:%g' "$${PROJECT_ROOTS%%,*}"); good "Le worker tournera avec l'identité $$OWNER (propriétaire du premier dossier de projets)."; }
  echo
  hint "Terminal dans un conteneur : réservé au droit « docker.exec » du hub (jamais donné d'office), et refusé ici tant que vous ne l'autorisez pas."
  if yes "Autoriser le terminal dans les conteneurs de cette machine ?"; then WORKER_ALLOW_EXEC=true; fi
  echo
  hint "Sites publiés par le Traefik d'un AUTRE nœud (par exemple celui du hub) : indiquez l'adresse par laquelle ce Traefik joint cette machine"
  hint "(IP du réseau local ou du VPN, ou un nom ; plusieurs : séparées par des virgules). Le hub relie alors tout seul les conteneurs à ses règles."
  hint "Laissez vide si cette machine a son propre Traefik ou n'a pas de site. Ensuite : « Ce nœud » → Docker → « Publier les sites avec le Traefik de… »."
  WORKER_ADDRESSES=$$(ask "Adresse de cette machine pour ce Traefik (facultatif)" "$$(hostname -I 2>/dev/null | awk '{print $$1}')" WORKER_ADDRESSES)
  WORKER_ADDRESSES=$${WORKER_ADDRESSES// /}
  fi
  sub "Image du worker"
  hint "Utilisez la même version que le hub : le protocole entre les deux évolue avec les versions."
  hint "« latest » est la plus récente ; sinon un numéro précis (ex. 1.0.0) ou une image complète."
  echo
  IMG=$$(ask "Version ou image du worker" "latest" WORKER_IMAGE); IMG=$$(image_ref "$$IMG")
  step "2/4 · Le hub"
  sub "Adresse du hub"
  hint "L'adresse (HTTPS) par laquelle l'interface du hub est joignable depuis cette machine."
  echo
  HUB=$$(ask "Adresse du hub (https://…)" "" HUB_URL); HUB=$${HUB%/}
  INSECURE=""
  case "$$HUB" in
    https://?*) ;;
    http://?*) warnl "http:// : les échanges avec le hub circuleraient en clair (réservé aux essais)."
               if [ "$${WORKER_ALLOW_INSECURE:-}" = 1 ] || yes "Continuer quand même ?"; then INSECURE=1; else exit 1; fi;;
    *) die "Adresse invalide : elle doit commencer par https://";;
  esac
  if command -v curl >/dev/null; then
  	if curl -fsS --max-time 8 "$$HUB/api/health" >/dev/null 2>&1; then good "Le hub répond."
  	else warnl "Le hub ne répond pas depuis cette machine (DNS, pare-feu, certificat ?)."; yes "Continuer quand même ?" || exit 1; fi
  fi
  step "3/4 · Jeton d'enrôlement"
  say "Dans l'interface du hub : « Nouveau nœud » (ou « Ce nœud » → Connexion → « Générer un jeton »), puis copiez le jeton affiché (enr_…)."
  hint "Il expire 15 minutes après sa création et ne sert qu'une fois : générez-le maintenant, pas avant."
  echo
  TOKEN=$$(asks "Jeton d'enrôlement (enr_…)" ENROLL_TOKEN)
  case "$$TOKEN" in enr_?*) ;; *) die "Jeton invalide : il commence par « enr_ » (voir « Nouveau nœud » dans l'interface).";; esac
  if [ "$$mode" = stack ]; then step "4/4 · Écriture des fichiers"; else step "4/4 · Écriture, démarrage et connexion"; fi
  echo
  if [ "$$mode" = stack ]; then
    hint "Rien n'est démarré ici : vous collerez les deux fichiers dans Portainer. Le jeton expire 15 minutes après sa création : déployez la pile tout de suite."
    WY="$$WORKER_STACK_YML"
    if [ -n "$$NET" ]; then WY+=$$'\n    networks: [proxy]\n\nnetworks:\n  proxy:\n    external: true\n    name: $${TRAEFIK_NETWORK}'; fi
    S=$$(EXTRA_VOLS=$$'  worker_data:\n' build_compose "$$WY" WORKER_IMAGE)
    printf '%s\n' "$$S" > docker-compose.stack.yml
    umask 077
    {
      echo "# stack.env (worker) : à coller dans Portainer → Stacks → Add stack → « Environment variables » → « Advanced mode »."
      echo "# Généré par « make worker-stack » le $$(date +%F). Contient un jeton d'enrôlement (usage unique, 15 minutes) : ne pas le partager."
      echo; echo "# ── connexion au hub"
      echo "HUB_URL=$$HUB"
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
      echo "PUID=$${OWNER%%:*}"
      echo "PGID=$${OWNER##*:}"
      if [ -n "$$WORKER_DOCKER" ]; then echo; echo "# ── [TOUJOURS] ce que gère le worker (Docker : accès équivalent à root sur cette machine)"; echo "WORKER_MODULES=$$WORKER_MODULES"; echo "PROJECT_ROOTS=$$PROJECT_ROOTS"; echo "DOCKER_GID=$$DGID"; echo "WORKER_ALLOW_EXEC=$$WORKER_ALLOW_EXEC"; echo "WORKER_ADDRESSES=$$WORKER_ADDRESSES"; fi
    } > stack.env
    chmod 600 stack.env
    good "docker-compose.stack.yml créé (la pile)."
    good "stack.env créé (les variables, droits 600)."
    banner "Terminé — il reste à coller les fichiers dans Portainer"
    printf '\n'
    say "$${B}1.$${Z} Dans Portainer, sur la machine de ce Traefik : Stacks → Add stack, nom « armadok-worker »."
    say "$${B}2.$${Z} Onglet « Web editor » : collez le contenu de docker-compose.stack.yml."
    cmd "cat docker-compose.stack.yml"
    say "$${B}3.$${Z} Plus bas, « Environment variables » → « Advanced mode » : collez le contenu de stack.env."
    cmd "cat stack.env"
    say "$${B}4.$${Z} Cliquez « Deploy the stack » (avant l'expiration du jeton)."
    say "$${B}5.$${Z} Dans l'interface du hub, le nœud passe « En ligne »."
    printf '\n'
    hint "Une fois le worker connecté, retirez la variable ENROLL_TOKEN de la pile (Stacks → armadok-worker → Editor) : elle ne sert plus."
    hint "Si le jeton a expiré : « Ce nœud → Connexion » en génère un nouveau ; mettez-le dans ENROLL_TOKEN et redéployez."
    printf '\n'
    if yes "Afficher les deux fichiers maintenant ?"; then echo; printf '%s── docker-compose.stack.yml ──%s\n' "$$B" "$$Z"; cat docker-compose.stack.yml; echo; printf '%s── stack.env ──%s\n' "$$B" "$$Z"; cat stack.env; fi
    return 0
  fi
  hint "Le worker doit démarrer maintenant : le jeton expire vite et ne sert qu'une fois."
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
	@grep -E '^(##@ |[a-zA-Z0-9_-]+:.*## )' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "} /^##@ /{sub(/^##@ /,""); printf "\n%s\n",$$0; next} {printf "  make %-14s %s\n",$$1,$$2}'
	@echo

##@ Installation
install: ## Installation guidée : hub ou worker, avec Docker Compose ou dans Portainer
	eval "$$SH_FUNCS"
	need_docker
	banner "Armadok — installation"
	printf '\n'
	say "Que voulez-vous installer sur cette machine ?"
	printf '\n'
	printf '  %s1)%s %sLe hub, avec Docker Compose%s\n' "$$C" "$$Z" "$$B" "$$Z"
	hint "     L'interface complète (DNS, sites Traefik, utilisateurs, nœuds). Écrit docker-compose.yml et .env ici."
	hint "     Rien n'est démarré : la commande à lancer vous est indiquée à la fin."
	printf '\n'
	printf '  %s2)%s %sLe hub, dans Portainer%s\n' "$$C" "$$Z" "$$B" "$$Z"
	hint "     Même interface, mais pour une pile Portainer. Écrit docker-compose.stack.yml et stack.env,"
	hint "     à copier-coller dans Stacks → Add stack (éditeur web + variables d'environnement)."
	printf '\n'
	printf '  %s3)%s %sUn worker, avec Docker Compose%s\n' "$$C" "$$Z" "$$B" "$$Z"
	hint "     Relie le Traefik et/ou le Docker de CETTE machine à une interface (hub) déjà installée ailleurs. Démarre le worker et attend la connexion."
	printf '\n'
	printf '  %s4)%s %sUn worker, dans Portainer%s\n' "$$C" "$$Z" "$$B" "$$Z"
	hint "     Même chose pour une machine gérée par Portainer : écrit docker-compose.stack.yml et stack.env à copier-coller."
	printf '\n'
	case "$$(ask "Votre choix" "1" MODE)" in
	  1|hub|compose|h) exec $(MAKE) --no-print-directory hub;;
	  2|stack|portainer|p) exec $(MAKE) --no-print-directory stack;;
	  3|worker|w) exec $(MAKE) --no-print-directory worker;;
	  4|worker-stack|wp) exec $(MAKE) --no-print-directory worker-stack;;
	  *) die "Choix invalide.";;
	esac

hub: ## Installe le hub avec Docker Compose (écrit docker-compose.yml et .env, ne démarre rien)
	eval "$$SH_FUNCS"
	need_docker
	install_hub compose

stack: ## Prépare la pile Portainer du hub : docker-compose.stack.yml + stack.env à copier-coller
	eval "$$SH_FUNCS"
	need_docker
	install_hub stack

worker: ## Installe un worker avec Docker Compose (relie le Traefik et/ou le Docker de cette machine à un hub)
	eval "$$SH_FUNCS"
	need_docker
	install_worker compose

worker-stack: ## Prépare la pile Portainer d'un worker (Traefik, Docker ou les deux) : docker-compose.stack.yml + stack.env à copier-coller
	eval "$$SH_FUNCS"
	need_docker
	install_worker stack

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

.PHONY: help install hub stack worker worker-stack up down restart logs status update backup doctor config version password consumer-key reset-2fa
