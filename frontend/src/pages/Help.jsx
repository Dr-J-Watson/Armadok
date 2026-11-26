import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ArrowRight, BookMarked, BookOpen, Boxes, Check, ChevronDown, FileLock2, Hammer, HelpCircle, Home as HomeIcon, Keyboard, ToggleRight, FolderTree, Globe, LayoutList, LifeBuoy, Lock, Network, Rocket, SearchX, ShieldCheck, Stethoscope, Wrench } from 'lucide-react'
import { Badge, Btn, CopyBtn, SearchBox } from '../components/ui.jsx'
import ComposeBuilder from './ComposeBuilder.jsx'
import { FAQ, GLOSSARY, GUIDE, START, TRICKS } from './helpGuide.jsx'
import { Keys, SHORTCUTS, SHORTCUT_NOTE } from '../lib/shortcuts.jsx'
import { useAuth } from '../auth.jsx'
import { useNode } from '../node.jsx'
import { useFeatures } from '../lib/features.js'
import { useGo } from '../lib/nav.js'

// Pense-bête illustré par une installation type (noms, domaines, adresses et clés anonymisés : exemple.fr, 203.0.113.10…).
// Chaque point est cochable ; l'état est gardé dans ce navigateur seulement (rien n'est envoyé au serveur).
// Champs d'un point : t (titre), d (explication), code (exemple), where (où ça se règle), apply (comment l'appliquer), warn (le piège).

// ---------------------------------------------------------------- exemples de code (anonymisés)

const TREE = `traefik/                          ← dossier de la pile Traefik
├── docker-compose.yml            ← configuration STATIQUE (arguments de Traefik)
├── .env                          ← UID/GID, groupe docker, clés OVH
├── traefik_letsencrypt/
│   └── acme.json                 ← certificats (droits 600)
└── traefik-dynamic/              ← configuration DYNAMIQUE (surveillée)
    ├── apps/
    │   ├── exemple.fr/           ← un sous-dossier par domaine
    │   │   ├── grafana-svc.yml   ← un fichier par site : routeur + service
    │   │   └── traefik.yml       ← le dashboard de Traefik lui-même
    │   └── mon-app.yml
    └── middlewares/
        ├── auth/                 ← identification (basicAuth…)
        ├── chains/               ← chaînes : chain-private, chain-public…
        ├── headers/              ← en-têtes de sécurité
        └── ip/                   ← restrictions d'adresse (ip-allow-lan)`

const FLOW = `Navigateur ── grafana.exemple.fr
   │  DNS : grafana.exemple.fr → votre IP (publique ou privée)
   ▼
Box / routeur : 80 et 443 redirigés vers la machine (sites publics)
   ▼
Traefik : entrypoint « websecure » (:443)
   │  1. routeur : la règle Host(…) correspond-elle ?
   │  2. middlewares dans l'ordre : chain-private → pages d'erreur,
   │     en-têtes de sécurité, restriction d'adresse
   │  3. TLS : certificat obtenu avec le résolveur du routeur
   ▼
Service : http://grafana:3000   (nom du conteneur + port INTERNE)
   ▼
Conteneur « grafana » sur le réseau Docker partagé « proxy »`

const CODE_COMPOSE = `services:
  traefik:
    image: traefik:v3.7
    container_name: traefik
    restart: unless-stopped
    user: "\${UID}:\${GID}"          # Traefik ne tourne pas en root
    group_add:
      - "\${DOCKER_GID}"             # …mais peut lire le socket Docker
    environment:                     # clés du fournisseur DNS (voir Certificats)
      - OVH_ENDPOINT=\${OVH_ENDPOINT}
      - OVH_APPLICATION_KEY=\${OVH_APPLICATION_KEY}
      - OVH_APPLICATION_SECRET=\${OVH_APPLICATION_SECRET}
      - OVH_CONSUMER_KEY=\${OVH_CONSUMER_KEY}
    command:
      - "--api.dashboard=true"
      - "--log.level=INFO"
      - "--accesslog=true"
      - "--providers.docker=true"
      - "--providers.docker.exposedbydefault=false"
      - "--providers.file.directory=/etc/traefik/dynamic"
      - "--providers.file.watch=true"
      # … entrypoints et résolveurs : voir les onglets suivants`

const CODE_EP = `      - "--entrypoints.web.address=:80"
      - "--entrypoints.web.http.redirections.entrypoint.to=websecure"
      - "--entrypoints.web.http.redirections.entrypoint.scheme=https"
      - "--entrypoints.websecure.address=:443"
      - "--entrypoints.websecure.http3=true"
      - "--entrypoints.websecure.http.tls=true"
      - "--entrypoints.wireguard.address=:51820/udp"`

const CODE_PORTS = `    ports:
      - "80:80/tcp"
      - "443:443/tcp"
      - "443:443/udp"        # HTTP/3 (QUIC) : nécessite aussi l'UDP
      - "51820:51820/udp"    # entrypoint « wireguard »`

const CODE_RESOLVERS = `      # Résolveur 1 : validation HTTP — pour les sites publics (ports 80/443 ouverts)
      - "--certificatesresolvers.lehttp.acme.email=vous@exemple.fr"
      - "--certificatesresolvers.lehttp.acme.storage=/letsencrypt/acme.json"
      - "--certificatesresolvers.lehttp.acme.httpchallenge=true"
      - "--certificatesresolvers.lehttp.acme.httpchallenge.entrypoint=web"

      # Résolveur 2 : validation DNS (OVH) — pour les sites locaux, sans port ouvert
      - "--certificatesresolvers.lednsovh.acme.email=vous@exemple.fr"
      - "--certificatesresolvers.lednsovh.acme.storage=/letsencrypt/acme.json"
      - "--certificatesresolvers.lednsovh.acme.dnschallenge=true"
      - "--certificatesresolvers.lednsovh.acme.dnschallenge.provider=ovh"`

const CODE_ENVT = `# .env à côté du docker-compose.yml de Traefik (chmod 600)
UID=1000
GID=1000
DOCKER_GID=984                 # getent group docker | cut -d: -f3
OVH_ENDPOINT=ovh-eu
OVH_APPLICATION_KEY=…
OVH_APPLICATION_SECRET=…
OVH_CONSUMER_KEY=…`

const CODE_SITE = `# traefik-dynamic/apps/exemple.fr/grafana-svc.yml
http:
  routers:
    grafana:
      rule: "Host(\`grafana.exemple.fr\`)"
      entryPoints:
        - websecure
      service: grafana-svc
      tls:
        certResolver: lehttp        # exactement le nom du résolveur
      middlewares:
        - chain-private             # restreint aux adresses de confiance
  services:
    grafana-svc:
      loadBalancer:
        servers:
          - url: "http://grafana:3000"   # conteneur : port INTERNE`

const CODE_LOCAL = `    grafana-local:
      rule: "Host(\`grafana.maison.exemple.fr\`)"
      entryPoints: [websecure]
      service: grafana-svc
      tls:
        certResolver: lednsovh      # validation DNS : aucun port ouvert nécessaire
      middlewares:
        - chain-private`

const CODE_WEBHOOK = `    # Un service public mais limité à UN chemin et UNE méthode (ex. un webhook) :
    app-hooks:
      rule: "Host(\`app.exemple.fr\`) && PathPrefix(\`/api/hooks/\`) && Method(\`POST\`)"
      priority: 100                 # passe avant le routeur « Host » plus général
      entryPoints: [websecure]
      service: app
      tls:
        certResolver: lehttp
      middlewares:
        - chain-public-api`

const CODE_CHAINS = `# middlewares/chains/chain-private.yml — pour tout ce qui reste chez vous
http:
  middlewares:
    chain-private:
      chain:
        middlewares:
          - errors-custom-pages    # jolies pages 4xx/5xx
          - headers-security       # HSTS, nosniff, referrer-policy…
          - ip-allow-lan           # PUIS la restriction d'adresse

# middlewares/chains/chain-public.yml — pour un site volontairement public
http:
  middlewares:
    chain-public:
      chain:
        middlewares:
          - errors-custom-pages
          - headers-security`

const CODE_IP = `# middlewares/ip/ip-allow-lan.yml
http:
  middlewares:
    ip-allow-lan:
      ipAllowList:
        sourceRange:
          - 10.0.0.0/8          # réseaux privés
          - 172.16.0.0/12       # (dont les réseaux Docker)
          - 192.168.0.0/16
          - 203.0.113.10/32     # votre propre IP publique (voir le piège)`

const CODE_ERR = `http:
  middlewares:
    errors-custom-pages:
      errors:
        status: ["400-405", "408", "429", "500-599"]
        service: "error-pages-svc@file"   # petit conteneur nginx qui sert les pages
        query: "/{status}.html"`

const CODE_NET = `# L'application rejoint le même réseau que Traefik
services:
  grafana:
    networks: [proxy]
networks:
  proxy:
    external: true            # créé une fois : docker network create proxy`

const CODE_DAEMON = `{
    "dns": ["1.1.1.1", "8.8.8.8"],
    "min-api-version": "1.24",
    "live-restore": true,
    "runtimes": { "nvidia": { "args": [], "path": "nvidia-container-runtime" } }
}`

const CODE_WORKER_ROUTE = `http:
  routers:
    armadok-worker:
      rule: "Host(\`hub.exemple.fr\`) && PathPrefix(\`/api/worker\`)"
      priority: 100
      entryPoints: [websecure]
      service: armadok-svc          # le même service que le routeur du hub
      tls:
        certResolver: lehttp
      middlewares: [chain-public-api]   # sans filtre IP : le worker est protégé par son jeton, puis par sa signature`

const CODE_UPDATE = `sudo apt update
sudo apt install --only-upgrade docker-ce docker-ce-cli docker-compose-plugin docker-buildx-plugin containerd.io
docker version --format '{{.Server.Version}}'`

const CODE_METRICS = `# Traefik (docker-compose.yml, « command »)
      - "--metrics.prometheus=true"
      - "--metrics.prometheus.addEntryPointsLabels=true"
      - "--metrics.prometheus.addServicesLabels=true"
      # les métriques sont servies sur l'entrypoint interne « traefik » (:8080), sans le publier

# prometheus.yml
scrape_configs:
  - job_name: traefik
    static_configs:
      - targets: ["traefik:8080"]      # Prometheus et Traefik sur le même réseau Docker

# Cette interface : « Ce nœud » → Surveillance → Adresse de Prometheus
http://prometheus:9090`

const CODE_ACCESSLOG = `# Traefik (command) : un fichier JSON, avec le navigateur du visiteur
      - "--accesslog=true"
      - "--accesslog.format=json"
      - "--accesslog.filepath=/logs/access.log"
      - "--accesslog.fields.headers.names.User-Agent=keep"
# Traefik (volumes)
      - ./logs:/logs

# Cette interface : docker-compose.yml (volume en lecture seule + variable du conteneur)
    volumes:
      - \${TRAEFIK_ACCESS_LOG_PATH}:/traefik-logs:ro
    environment:
      TRAEFIK_ACCESS_LOG: /traefik-logs/access.log

# Cette interface : .env (dossier de l'hôte, qui doit exister avant le démarrage)
TRAEFIK_ACCESS_LOG_PATH=../traefik/logs`

const CODE_LOGROTATE = `# /etc/logrotate.d/traefik-access  (l'interface gère la rotation et la troncature)
/chemin/vers/traefik/logs/access.log {
    weekly
    rotate 2
    compress
    missingok
    copytruncate
}`

const CODE_WEBUI = `# .env de cette interface
TRAEFIK_DYNAMIC_PATH=/chemin/vers/traefik/traefik-dynamic   # monté en ÉCRITURE (toujours lu)
TRAEFIK_NETWORK=proxy                                       # toujours lu
CERTS_PATH=/chemin/vers/traefik/traefik_letsencrypt         # toujours lu : le DOSSIER d'acme.json (voir / supprimer / renouveler les certificats)
DOCKER_GID=999                                              # toujours lu : groupe « docker » (getent group docker | cut -d: -f3), pour relancer Traefik

# VALEURS DE DÉPART seulement : lues UNE FOIS, à la création du nœud « Principal ».
# Les changer ensuite ne fait rien : voir « Ce nœud » dans l'interface.
TRAEFIK_ENTRYPOINTS=web,websecure,wireguard
TRAEFIK_CERT_RESOLVERS=lehttp,lednsovh
TRAEFIK_API_URL=http://traefik:8080
PROMETHEUS_URL=http://prometheus:9090
DEFAULT_DNS_TARGET=

# TOUJOURS relu : dossier de l'hôte du journal d'accès de Traefik (monté en lecture seule ; TRAEFIK_ACCESS_LOG est posée par le compose)
TRAEFIK_ACCESS_LOG_PATH=../traefik/logs`

const CODE_CERTS = `# docker-compose.yml de l'interface, service « armadok »
    environment:
      TRAEFIK_CERTS_FILE: /traefik-certs/acme.json
      TRAEFIK_CONTAINER: traefik            # nom du conteneur de Traefik
    volumes:
      - \${CERTS_PATH}:/traefik-certs                  # le DOSSIER qui contient acme.json
      - /var/run/docker.sock:/var/run/docker.sock     # seulement pour supprimer / renouveler
    group_add: ["\${DOCKER_GID}"]

# .env de l'interface
CERTS_PATH=../traefik/traefik_letsencrypt
DOCKER_GID=999    # le vôtre : getent group docker | cut -d: -f3`

const CODE_TEST = `# Où en est un site ?
dig +short grafana.exemple.fr @1.1.1.1            # le DNS public répond-il ?
curl -vI https://grafana.exemple.fr 2>&1 | grep -E "subject|issuer|HTTP/"
docker logs traefik --since 10m 2>&1 | grep -iE "acme|error|lednsovh|lehttp"`

// ---------------------------------------------------------------- contenu

const SECTIONS = [
  {
    id: 'vue', label: 'Vue d\'ensemble', icon: <LayoutList size={15} />,
    intro: 'Comment les pièces s\'emboîtent dans une installation type (celle qui sert d\'exemple à toute cette aide), et où se règle chaque chose.',
    table: {
      head: ['Ce que vous voulez changer', 'Où ça se règle', 'Comment l\'appliquer'],
      rows: [
        ['Entrypoints, résolveurs de certificats, providers, journaux', 'docker-compose.yml de Traefik (« command »)', 'docker compose up -d (recrée Traefik, quelques secondes de coupure)'],
        ['Sites, services, middlewares, chaînes', 'Fichiers de traefik-dynamic/ (ou cette interface)', 'Automatique (watch) : rien à relancer'],
        ['Clés du DNS, identifiants de Traefik', '.env de Traefik + « environment » du compose', 'docker compose up -d (restart ne relit pas le .env)'],
        ['Comptes, droits, clés DNS de l\'interface', 'Cette interface (base chiffrée dans /data)', 'Immédiat'],
        ['Entrypoints, résolveurs de certificats et IP par défaut proposés dans les formulaires', 'Page « Ce nœud » de l\'interface (le .env ne sert qu\'au tout premier démarrage)', 'Immédiat, sans relancer'],
        ['Chemin des fichiers Traefik, réseau, dossier des certificats', '.env de l\'interface', 'docker compose up -d dans son dossier'],
        ['Comportement de Docker (live-restore, DNS)', '/etc/docker/daemon.json', 'systemctl reload docker (live-restore) ou restart'],
        ['Adresse de Prometheus, webhook d\'alerte', 'Page « Ce nœud » → Surveillance', 'Immédiat'],
        ['Fichier du journal d\'accès (adresses IP)', 'TRAEFIK_ACCESS_LOG_PATH dans le .env de l\'interface (le compose monte le dossier en lecture seule)', 'docker compose up -d dans son dossier'],
        ['Redirections de ports, IP publique', 'Votre box / routeur', 'Selon la box'],
      ],
    },
    items: [
      { id: 'd-multiselect', t: 'Docker : sélection multiple et actions groupées', d: 'Dans Docker, cochez des lignes (Maj+clic sur une case sélectionne toute la plage depuis la dernière case cliquée ; la case de l\'en-tête coche tout) : une barre apparaît en bas avec les actions possibles. Conteneurs : Démarrer, Redémarrer, Arrêter, Supprimer (les conteneurs de l\'interface et de Traefik sont épargnés ; un conteneur en marche est arrêté avant d\'être supprimé, ses volumes sont conservés). Images, réseaux, volumes : Supprimer, seulement pour ceux qu\'aucun conteneur n\'utilise. Chaque ligne de conteneur a aussi ses boutons : Logs, Redémarrer, Arrêter / Démarrer, Supprimer. Les actions sont faites l\'une après l\'autre ; un résumé indique celles qui ont échoué et pourquoi.' },
      { id: 'v-templates', t: 'Projets : les Modèles (dossiers, édition, import / export zip)', d: 'Dans l\'onglet Gérés, le bouton « Modèles » à côté de « Projets » (en haut de la liste) ouvre vos modèles de projet, rangés comme les projets : en dossiers et sous-dossiers (glisser-déposer, « Ranger »), en cartes ou en liste triable. Cochez-en plusieurs (Maj+clic : une plage) pour les exporter, les ranger, les partager, les rendre privés ou les supprimer. Cliquez un modèle pour explorer et modifier ses fichiers un par un (créer, renommer, déplacer, retirer ; les variables du .env sont listées) ; « Créer un projet » le déroule, « Dupliquer » en fait une copie privée modifiable (utile pour un modèle intégré ou d\'un autre). « Nouveau modèle » en crée un minimal. Exporter donne une archive zip : un modèle seul = ses fichiers à la racine ; plusieurs, ou un dossier = un sous-dossier par modèle, rangés comme dans la liste, avec un petit fichier armadok-template.json (nom, description). Les valeurs des secrets du .env sont vidées par défaut ; on peut les inclure pour ses modèles privés (droit « .env »). « Importer (.zip) » (ou glissez l\'archive sur l\'onglet) lit l\'archive sans jamais l\'exécuter, montre un aperçu, puis crée les modèles ; un nom déjà pris : garder les deux, remplacer le sien ou ignorer. Une archive sans manifeste (un dossier de projet zippé) devient un modèle. Les modèles importés sont privés par défaut ; partagés, leurs secrets sont vidés. Limites : archive de 2,9 Mo au plus, 60 fichiers de 256 Ko et 1 Mo par modèle, texte uniquement. Les modèles sont communs à tous les nœuds.', where: 'Projets → Gérés → Modèles', apply: 'Immédiat' },
      { id: 'v-files', t: 'Projets : fichiers (sélection multiple, import, téléchargement)', d: 'Dans l\'onglet Fichiers d\'un projet, cochez des fichiers et des dossiers (la ligne « Racine du projet » en haut sélectionne tout ; Maj+clic : toute la plage ; cocher un dossier coche tout son contenu, sous-dossiers compris, et le décocher fait l\'inverse ; vous pouvez ensuite décocher un fichier ou un sous-dossier en particulier, le dossier apparaît alors à moitié coché et seul le reste est concerné) : une barre propose Télécharger (un fichier tel quel, sinon une archive zip qui garde l\'arborescence), Déplacer (vers un dossier choisi) et Supprimer. Pour envoyer des fichiers de votre ordinateur : boutons « Importer des fichiers » et « Importer un dossier » en haut de la liste (ils visent le dossier sélectionné, sinon la racine), ou glissez-déposez-les directement sur le dossier voulu de la liste (il se met en surbrillance ; sur un fichier, ils vont dans son dossier ; sur la « Racine du projet », à la racine ; ailleurs, dans le dossier sélectionné). Les fichiers existants ne sont remplacés qu\'après confirmation (les textes remplacés restent dans l\'historique). Limites (levées par le droit « Transferts et suppressions sans limite de taille », donné d\'office aux administrateurs du nœud) : 500 fichiers par import, 100 Mo par fichier importé ou par téléchargement, suppression d\'un dossier jusqu\'à 300 fichiers ou 5 Mo ; le message de refus nomme le droit à demander. Les .env demandent le droit sur les secrets.', warn: 'Un nœud géré par un worker doit être à jour pour cela : un ancien worker répond « Opération non autorisée ».' },
      { id: 'v-sidebar', t: 'Barre latérale : catégories repliables et mode icônes', d: 'Cliquez sur le titre d\'une catégorie (Docker, Sites, DNS…) pour la replier ou la déplier. « Replier la barre » en bas ne laisse que les icônes (survolez-en une pour son explication). Vos choix sont retenus dans ce navigateur. Ctrl K cherche aussi dans les enregistrements DNS (sous-domaines) de vos domaines, dès deux lettres tapées. Pour créer un site, utilisez le bouton « Nouveau site » de la page Sites, de l\'accueil, ou Ctrl K.' },
      { id: 'v-keys', t: 'Raccourcis clavier et menu à trois points (⋯) des sites', d: '« Affichage complet » (sélecteur de nœud, au-dessus de « Nouveau nœud ») montre tous vos nœuds à la fois dans Docker, Projets et Sites. Pour changer de nœud sans quitter la page : g puis 1 à 9 (numéro du nœud dans le sélecteur), g n (suivant), g p (précédent). Ctrl K (ou /) ouvre la recherche : sites, pages, domaines, certificats, ou une adresse IP à chercher dans la surveillance. « g » puis « a » (accueil), « s » (sites), « d » (domaines), « m » (surveillance), « t » (Traefik avancé), « c » (certificats) ; « n » crée un site ; « ? » affiche la liste. Les raccourcis sont inactifs pendant la saisie. Sur chaque carte de site, le menu à trois points (⋯) regroupe : copier l\'adresse, ouvrir, tester (nom, certificat, réponse), statistiques, visiteurs, certificat, enregistrement DNS et duplication. Les chiffres de l\'accueil et de la surveillance, les noms de sites, les adresses et les lignes de l\'activité sont cliquables ; « ← Retour » ramène à la page d\'où l\'on vient. Après un bannissement, une validation, une pause ou une sourdine, « Annuler » reste proposé quelques secondes.' },
      { id: 'v-flow', t: 'Le trajet d\'une requête', d: 'Quand quelque chose ne marche pas, suivez ce chemin de haut en bas : DNS, ports, entrypoint, règle du routeur, middlewares, certificat, service, conteneur. Un problème se situe presque toujours sur une seule de ces marches.', code: FLOW },
      { id: 'v-tree', t: 'Les dossiers d\'une installation type', d: 'Tout est dans un dossier unique : le compose et le .env (configuration statique), acme.json (certificats) et traefik-dynamic (configuration dynamique, surveillée). Cette interface ne modifie que traefik-dynamic.', code: TREE },
      { id: 'v-static', t: 'Statique ou dynamique ?', d: 'Statique = ce qui ne change qu\'en recréant Traefik (entrypoints, résolveurs de certificats, providers). Dynamique = ce que Traefik relit seul dès qu\'un fichier change (routeurs, services, middlewares, TLS).', warn: 'Déclarer un résolveur de certificats dans un fichier dynamique ne fait rien : il serait ignoré. Il va obligatoirement dans la configuration statique.' },
    ],
  },
  {
    id: 'options', label: 'Fonctionnalités facultatives', icon: <ToggleRight size={15} />,
    intro: 'Certaines parties de l\'interface n\'apparaissent que si vous les branchez : tant qu\'elles ne le sont pas, elles sont CACHÉES (pas de page vide ni d\'erreur). Voici ce qui apparaît, et ce qu\'il faut faire pour chacune.',
    table: {
      head: ['Fonctionnalité', 'Ce qui apparaît', 'Ce qu\'il faut brancher'],
      rows: [
        ['Certificats', 'Onglet « Certificats » (Traefik avancé), échéances et pastilles sur les sites ; boutons Supprimer / Renouveler / Redemander', 'Rien d\'autre qu\'une configuration de l\'interface : acme.json monté (CERTS_PATH) et, pour supprimer / renouveler, le socket Docker (Aide → Certificats)'],
        ['Métriques', 'Surveillance → Vue d\'ensemble (totaux, sites les plus sollicités / lents, codes, méthodes), Sites, Explorateur', 'Traefik qui expose ses métriques + Prometheus + son adresse dans « Ce nœud »'],
        ['Alertes', 'Surveillance → Alertes (et pastille rouge dans le menu)', 'Prometheus (règles de trafic, certificats…) et/ou le journal d\'accès (adresse qui insiste) ; webhook facultatif'],
        ['Adresses IP', 'Surveillance → Adresses IP', 'Le journal d\'accès de Traefik, en fichier, monté en lecture seule'],
        ['Bannissement', 'Surveillance → Bannies, bouton « Bannir »', 'Rien : une règle Traefik est écrite dans le dossier dynamique'],
        ['État des sites', 'Pastilles « actif / en erreur » sur les sites', 'L\'API de Traefik (adresse dans « Ce nœud »)'],
        ['DNS automatique', 'Bloc DNS de l\'assistant de site', 'Un compte DNS (OVH, DuckDNS, AdGuard Home, Pi-hole)'],
      ],
    },
    items: [
      { id: 'o-builder', t: 'Générer les fichiers : le constructeur', d: 'L\'onglet « Constructeur de piles » (le premier onglet de cette page) produit, à partir de cases à cocher, le compose et le .env de Traefik (entrypoints, résolveurs HTTP / DNS, métriques, journal d\'accès…), sa configuration dynamique (chaîne privée, routeur de cette interface), Prometheus, puis le compose et le .env de cette interface, avec les commandes à lancer. Les secrets sont tirés dans votre navigateur et ne sont jamais enregistrés ; les fichiers ont été vérifiés par docker compose et par un vrai Traefik.', where: 'Aide → Constructeur de piles', apply: 'Copier / télécharger les fichiers' },
      { id: 'o-explorer', t: 'L\'Explorateur : regarder n\'importe quelle mesure', d: 'Trois façons : « Questions courantes » (trafic par site, erreurs 5xx, latence p95, jours avant expiration…, un clic), « Constructeur » (choisir une mesure dans la liste de Prometheus, un calcul — débit, médiane, p95… —, regrouper les courbes par site ou par code, filtrer), ou PromQL libre. Durées de 15 min à 30 jours ou durée libre (1 minute à 90 jours), actualisation automatique, clic sur une ligne pour masquer une courbe, dernière valeur / min / moyenne / max, export CSV. Tout est en lecture seule.', where: 'Surveillance → Explorateur', apply: 'Immédiat', warn: 'Prometheus ne garde que sa durée de rétention (15 jours par défaut) : au-delà, les courbes sont vides. Droit « Interroger Prometheus librement (PromQL) » requis.' },
      { id: 'o-metrics', t: 'Métriques et alertes : Prometheus', d: 'Traefik expose ses métriques (requêtes, codes de réponse, latence, connexions, certificats) ; Prometheus les lit ; cette interface l\'interroge, en lecture seule. Les métriques par service demandent addServicesLabels. L\'adresse de Prometheus se renseigne dans « Ce nœud » (réservé aux administrateurs de la plateforme : le serveur appelle lui-même cette adresse).', code: CODE_METRICS, where: 'compose de Traefik, prometheus.yml, « Ce nœud »', apply: 'docker compose up -d (Traefik), rechargement de Prometheus', warn: 'PROMETHEUS_URL du .env n\'est qu\'une valeur de départ (comme les listes de résolveurs) : sur une installation existante, saisissez l\'adresse dans « Ce nœud ».' },
      { id: 'o-alerts', t: 'Les alertes de l\'interface', d: 'Chaque alerte peut être mise en sourdine 1 h ou 24 h (onglet Alertes) : elle reste affichée mais n\'envoie plus de message au webhook. Dix règles prêtes : trop d\'erreurs 5xx, service lent, certificat bientôt expiré, cible Prometheus injoignable, configuration refusée par Traefik, serveur hors service, pic de trafic, « une adresse insiste », « tentative d\'exploitation » (injection, exécution de commandes) et « fichier sensible servi ». Chacune a son seuil, sa durée minimale et sa gravité. Une alerte déclenchée ou résolue est signalée en direct, écrite dans l\'historique (30 jours) et envoyée au webhook du nœud (Discord, Slack, ntfy, n8n…). Les alertes de vos propres règles Prometheus s\'affichent à côté, en lecture seule.', where: 'Surveillance → Alertes ; webhook dans « Ce nœud »', apply: 'Immédiat' },
      { id: 'o-accesslog', t: 'Adresses IP : le journal d\'accès de Traefik', d: 'Prometheus ne sait pas compter par visiteur (une étiquette par adresse ferait exploser sa mémoire). Les adresses viennent donc du journal d\'accès, écrit dans un fichier que cette interface lit en continu (JSON de préférence ; le format commun marche aussi, sans le nom du site). Seuls sont gardés, 7 jours : l\'adresse, des compteurs par heure, les chemins demandés SANS leurs paramètres, le site et le navigateur. Au-delà, tout est purgé.', code: CODE_ACCESSLOG, where: 'compose de Traefik + compose de l\'interface (.env : TRAEFIK_ACCESS_LOG_PATH)', apply: 'docker compose up -d (les deux)', warn: 'Avec « accesslog.filepath », Traefik n\'écrit plus les accès sur sa sortie : « docker logs traefik » ne les montrera plus. Le fichier grossit sans fin : prévoyez une rotation.' },
      { id: 'o-attacks', t: 'Attaques probables et leur type', d: 'Chaque requête du journal d\'accès est comparée à des signatures : recherche de fichiers sensibles (.env, .git…), balayage d\'applications (WordPress, phpMyAdmin…), remontée de répertoires, injection SQL, XSS, exécution de commandes (Log4Shell…), outils d\'attaque connus (sqlmap, nikto, zgrab…). S\'y ajoutent trois types déduits des compteurs : essai de mots de passe (20 échecs de connexion), énumération de chemins (20 chemins refusés) et inondation (1 500 requêtes dans l\'heure). Chaque adresse porte ses étiquettes, filtrables par type.', where: 'Surveillance → Adresses IP', apply: 'Immédiat', warn: 'Ce sont des indices, pas des verdicts : des faux positifs sont possibles, et on ne voit ni le corps des requêtes POST ni ce qui est refusé avant Traefik. Les paramètres des requêtes sont analysés mais jamais conservés (seulement le type, et un exemple de chemin sans paramètres).' },
      { id: 'o-leak', t: 'Fichier sensible servi : la vraie alerte', d: 'Une demande de /.env qui reçoit 404 est un simple balayage. Qui reçoit 200, c\'est peut-être une fuite : le fichier a été lu. L\'interface la met en rouge (bandeau et alerte critique « Fichier sensible servi »). Que faire : retirer le fichier du site, changer les secrets qu\'il contient, vérifier les journaux. Une page d\'administration qui répond 200 n\'est PAS comptée comme attaque (c\'est peut-être la vôtre).' },
      { id: 'o-rotate', t: 'Faire tourner le journal d\'accès', d: 'Traefik ne tourne pas ses journaux tout seul. « copytruncate » est le plus simple : l\'interface détecte la troncature et reprend au début.', code: CODE_LOGROTATE },
      { id: 'o-reveal', t: 'Voir les adresses en clair, ou masquées', d: 'Les adresses IP sont des données personnelles. Sans le droit « Voir les adresses IP en clair », elles sont masquées côté serveur (203.0.xxx.xxx) : le navigateur ne les reçoit jamais. Cette personne peut quand même voir l\'activité, et bannir depuis la liste (par une référence opaque). Les alertes et l\'historique sont masqués de la même façon. Le rôle Auditeur voit tout sauf les adresses ; Administrateur et Propriétaire ont tout.', where: 'Droits → rôles', apply: 'Immédiat' },
      { id: 'o-geo', t: 'Localiser les adresses (pays, ville, carte)', d: 'Surveillance → Adresses IP propose, d\'un clic, de télécharger la base gratuite DB-IP « City Lite » (environ 130 Mo, installée dans le dossier de données). La localisation se fait ensuite sur votre serveur : aucune adresse n\'est envoyée à un service tiers. Le pays est fiable, la ville approximative (celle du fournisseur d\'accès). Sans le droit de voir les adresses en clair, seul le pays apparaît. Une carte du monde montre les adresses (taille = requêtes, rouge = attaque).', where: 'Surveillance → Adresses IP → « Télécharger la base »', apply: 'Immédiat', warn: 'Base DB-IP sous licence CC BY 4.0 : à rafraîchir de temps en temps (même bouton). Il faut que le serveur puisse joindre download.db-ip.com une fois.' },
      { id: 'o-trust', t: 'Valider une adresse (ne plus la marquer)', d: 'Une adresse que vous reconnaissez (vous, un collègue, une supervision) se valide d\'un clic : elle n\'est plus marquée suspecte ni comme attaque, ne déclenche plus d\'alerte et ne peut pas être bannie ; ses requêtes restent visibles (filtre « Validées »). On peut valider une adresse ou un réseau jusqu\'à /16, avec une note. Le réseau local (10.x, 172.16-31.x, 192.168.x, loopback) est validé d\'office, sans rien enregistrer. Se retire à tout moment.', where: 'Surveillance → Adresses IP (bouton « Valider ») et onglet « Validées »', apply: 'Immédiat', warn: 'Valider ne change rien pour Traefik : c\'est un marquage de l\'interface. Une adresse validée qui se fait pirater ne sera plus signalée : ne validez que ce que vous maîtrisez.' },
      { id: 'o-automod', t: 'Automod : bannir automatiquement selon des conditions', d: 'Des règles du type « si l\'adresse vient de ces pays ET est détectée comme suspecte, alors la bannir 24 h ». Conditions combinables (toutes doivent être vraies) : pays (dans la liste, ou PAS dans la liste), détectée comme suspecte, type d\'attaque (injection SQL, exécution de commandes, fichier sensible, scanner…), nombre minimal de requêtes ou d\'erreurs sur 24 h. Cinq modèles prêts (suspectes de certains pays, hors de mes pays, attaques graves, balayeurs insistants, scanners connus). Le serveur applique les règles toutes les 30 secondes d\'après le journal d\'accès. Chaque règle a deux modes : OBSERVATION (rien n\'est banni, le journal dit ce qui l\'aurait été : à utiliser d\'abord) puis BLOCAGE (bannissement réel : 403 partout, durée au choix). Un bouton « Qui serait visé maintenant ? » donne un aperçu sans agir.', where: 'Surveillance → Automod', apply: 'Immédiat', warn: 'Garde-fous : jamais le réseau local ni une adresse validée, jamais une adresse sans pays connu pour une condition de pays, au plus 25 adresses par passe (au-delà la règle se suspend d\'elle-même et le journal le dit). Une règle « tout sauf mon pays » bloque aussi vos services externes et vos déplacements : testez-la en observation. Les conditions de pays demandent la base de localisation. Droit dédié : « Gérer l\'automod ».' },
      { id: 'o-ban', t: 'Bannir une adresse', d: 'Traefik n\'a pas de liste noire : l\'interface écrit un routeur de très haute priorité, ClientIP(`adresse`), qui répond 403 sur TOUS les sites du nœud, en HTTP comme en HTTPS. Vérifié sur Traefik 3.7. Durée au choix (1 h à définitif) ; les bannissements temporaires se lèvent seuls. Garde-fous : jamais d\'adresse privée ou locale, jamais la vôtre, réseaux jusqu\'à /16 seulement, 2000 bannissements par défaut ; limite réglable ou illimitée dans l\'onglet Bannies, avec compteur, et au débordement : refuser les nouvelles adresses, ou lever d\'anciens bannissements d\'automod pour faire de la place.', where: 'Surveillance → Adresses IP / Bannies', apply: 'Immédiat (Traefik relit le fichier)', warn: 'Traefik ne bannit que ce qu\'il voit : derrière Cloudflare ou un autre proxy, il voit l\'adresse du proxy, pas celle du visiteur (configurez les en-têtes de confiance de Traefik). Pour un blocage plus bas niveau (pare-feu, fail2ban, CrowdSec), c\'est hors de cette interface.' },
      { id: 'o-remote', t: 'Nœuds distants', d: 'Pour l\'instant, métriques, alertes et adresses IP ne fonctionnent que sur le nœud principal (cette interface appelle Prometheus et lit le journal elle-même). Le bannissement, lui, marche sur tous les nœuds, puisqu\'il passe par le dossier de Traefik.' },
      { id: 'o-hidden', t: 'Pourquoi je ne vois pas « Surveillance » ?', d: 'Soit rien n\'est branché sur ce nœud (voir le tableau), soit vos droits ne couvrent pas ces écrans (métriques, alertes, adresses IP : droits distincts, qui demandent de voir tout Traefik). Un administrateur peut vous les attribuer dans « Droits ».' },
    ],
  },
  {
    id: 'traefik', label: 'Traefik (statique)', icon: <Wrench size={15} />,
    intro: 'Le docker-compose.yml de Traefik : ce qui est lu une seule fois au démarrage.',
    items: [
      { id: 't-compose', t: 'Le service Traefik', d: 'Image épinglée sur une version (traefik:v3.x), redémarrage automatique, et exécution avec un utilisateur normal plutôt que root. Le groupe « docker » est ajouté pour lire le socket sans être administrateur.', code: CODE_COMPOSE, where: 'docker-compose.yml', apply: 'docker compose up -d', warn: 'DOCKER_GID n\'est pas toujours 999 : lisez le vôtre avec « getent group docker ». Un mauvais numéro donne « permission denied » sur le socket.' },
      { id: 't-env', t: 'Le .env de Traefik', d: 'Les valeurs de ${…} dans le compose viennent du .env placé à côté. Rien de secret n\'est écrit dans le compose lui-même.', code: CODE_ENVT, where: '.env à côté du compose', apply: 'docker compose up -d', warn: 'Un simple « docker restart » ou « docker compose restart » ne relit pas le .env : il faut recréer le conteneur avec « up -d ».' },
      { id: 't-providers', t: 'Deux sources de configuration : Docker et fichiers', d: 'Le provider fichier (directory + watch) lit traefik-dynamic/. Le provider Docker lit les étiquettes des conteneurs : avec exposedbydefault=false, seul un conteneur qui a traefik.enable=true est exposé. Si vous ne gérez que par fichiers (cas ici), le provider Docker n\'est pas indispensable.', where: 'command du compose', apply: 'docker compose up -d' },
      { id: 't-ep', t: 'Les entrypoints : les portes d\'entrée', d: 'web (80) redirige tout vers websecure (443) ; websecure active le TLS par défaut et HTTP/3. Un entrypoint UDP (ici wireguard) permet de faire passer autre chose que du web. Les noms doivent être repris tels quels dans les routeurs, et dans la liste des EntryPoints du nœud (page « Ce nœud »).', code: CODE_EP, where: 'command du compose', apply: 'docker compose up -d' },
      { id: 't-ports', t: 'Les ports publiés correspondent aux entrypoints', d: 'Un entrypoint sans port publié n\'est pas joignable de l\'extérieur du conteneur.', code: CODE_PORTS, where: 'ports du compose', apply: 'docker compose up -d', warn: 'HTTP/3 (http3=true) utilise l\'UDP 443 : oubliez « 443:443/udp » et les clients retombent sur HTTP/2 sans erreur visible.' },
      { id: 't-redirect', t: 'La redirection HTTP → HTTPS se fait une fois, sur l\'entrypoint', d: 'Inutile de la répéter dans chaque site. Elle est déjà dans l\'exemple de l\'onglet précédent (web → websecure).' },
      { id: 't-obs', t: 'Journaux et métriques', d: 'accesslog=true écrit chaque requête (utile pour comprendre un 403 ou repérer des scans) ; les métriques Prometheus sont servies sur un entrypoint interne qui n\'est pas publié.' },
    ],
  },
  {
    id: 'certs', label: 'Certificats HTTPS', icon: <FileLock2 size={15} />,
    intro: 'Un site en HTTPS a besoin d\'un certificat. Traefik l\'obtient tout seul auprès de Let\'s Encrypt, si la « validation » peut se faire. On peut avoir plusieurs résolveurs, un par méthode.',
    items: [
      { id: 'c-two', t: 'Deux résolveurs pour deux usages', d: 'lehttp (validation HTTP) pour les sites publics ; lednsovh (validation DNS) pour les sites locaux. Les deux partagent le même acme.json. Le nom que vous choisissez est libre, mais c\'est lui que vous écrirez dans chaque routeur.', code: CODE_RESOLVERS, where: 'command du compose', apply: 'docker compose up -d' },
      { id: 'c-http', t: 'Validation HTTP : pour les sites publics', d: 'Let\'s Encrypt appelle http://votre-site/.well-known/… : le port 80 doit être redirigé de la box vers Traefik, et le DNS du site doit pointer vers votre IP publique. Rien d\'autre à configurer.', warn: 'Ne mettez pas de restriction d\'adresse qui bloque /.well-known/acme-challenge sur le port 80 : la validation échouerait.' },
      { id: 'c-dns', t: 'Validation DNS : pour les sites locaux (aucun port ouvert)', d: 'Traefik crée lui-même un enregistrement TXT _acme-challenge chez le fournisseur DNS, que Let\'s Encrypt vérifie. Le site peut pointer vers une IP privée. Il faut que le conteneur Traefik reçoive les clés du fournisseur dans « environment » (OVH_ENDPOINT, OVH_APPLICATION_KEY, OVH_APPLICATION_SECRET, OVH_CONSUMER_KEY).', where: 'compose (environment) + .env de Traefik', apply: 'docker compose up -d', warn: 'Mettre les clés dans le .env ne suffit pas : sans la section « environment » du compose, le conteneur ne les voit pas.' },
      { id: 'c-keys', t: 'Des clés OVH limitées', d: 'Créez la clé sur api.ovh.com/createToken avec seulement GET, POST, PUT et DELETE sur /domain/zone/* (ou sur la zone concernée). Ne réutilisez pas les droits de votre compte.' },
      { id: 'c-router', t: 'Le routeur choisit son résolveur', d: 'tls.certResolver doit reprendre exactement le nom du résolveur. Pour un site local, c\'est lednsovh ; pour un site public, lehttp.', code: CODE_LOCAL, where: 'fichier du site', apply: 'Automatique' , warn: 'Sans « tls: certResolver: … » sur le routeur, aucun certificat n\'est demandé : le navigateur affiche le certificat par défaut de Traefik, non valide.' },
      { id: 'c-wild', t: 'Un wildcard pour ne pas multiplier les certificats', d: 'Avec la validation DNS, un seul certificat « *.maison.exemple.fr » couvre tous les sites locaux. Il évite aussi que chaque nom de site apparaisse dans les journaux publics de transparence des certificats.' },
      { id: 'c-acme', t: 'acme.json est persistant, en 600, et appartient à l\'utilisateur de Traefik', d: 'Il vit dans un dossier monté (ici traefik_letsencrypt). Si le fichier est perdu à chaque recréation, Traefik redemande tout et finit bloqué par les limites de Let\'s Encrypt. Faites-en une copie avant un grand ménage.' },
      { id: 'c-stale', t: 'Des certificats de sites supprimés continuent d\'être renouvelés', d: 'Traefik garde dans acme.json les noms déjà obtenus. Si le nom n\'existe plus dans le DNS, chaque renouvellement échoue (« NXDOMAIN ») et remplit les journaux. Ce n\'est pas grave, mais une entrée de acme.json peut être retirée pour nettoyer.' },
      { id: 'c-staging', t: 'Tester avec le serveur de test avant la production', d: 'caServer: https://acme-staging-v02.api.letsencrypt.org/directory pour un résolveur d\'essai : pas de limite, mais certificats non reconnus par les navigateurs.' },
      { id: 'c-manage', t: 'Redemander le certificat d\'un site qui n\'en a pas', d: 'Bouton « Redemander » : un changement de configuration sans effet (un middleware inutilisé) fait ré-évaluer à Traefik les noms qui n\'ont pas de certificat. Aucun redémarrage ; le site doit avoir un résolveur de certificats. Espacé de 30 secondes (Let\'s Encrypt limite les essais ratés à 5 par heure et par nom).', where: 'Traefik avancé → Certificats', apply: 'Immédiat' },
      { id: 'c-view', t: 'Voir, supprimer, renouveler les certificats depuis l\'interface (facultatif)', d: 'L\'interface monte le dossier qui contient acme.json (CERTS_PATH) et en lit seulement les noms, l\'émetteur et les dates : aucune clé n\'est jamais envoyée au navigateur. Pour SUPPRIMER ou RENOUVELER, elle doit aussi pouvoir relancer Traefik (socket Docker + groupe docker) : Traefik garde acme.json en mémoire et le réécrit en entier, une entrée retirée pendant qu\'il tourne serait servie quand même, puis reviendrait. L\'opération : arrêt de Traefik, sauvegarde acme.json.bak-DATE (les 5 dernières sont gardées), retrait de l\'entrée, redémarrage (quelques secondes de coupure). Traefik redemande ensuite le certificat de tout site qui l\'utilise encore : c\'est le renouvellement. Plusieurs certificats à la fois : cochez-les dans le tableau (ou « Sélectionner les expirés ») puis Renouveler / Supprimer, une seule coupure de Traefik pour tout le lot. Droit dédié : « Supprimer, renouveler et redemander des certificats » (propriétaire et administrateur).', code: CODE_CERTS, where: 'compose + .env de l\'interface', apply: 'docker compose up -d', warn: 'Monter le socket Docker donne à l\'interface la main sur Docker : une faille de l\'interface deviendrait une faille de la machine. Ne le faites que si l\'interface n\'est accessible qu\'à des personnes de confiance (chaîne privée). Sans le socket, tout fonctionne sauf Supprimer / Renouveler. Let\'s Encrypt limite à 5 certificats identiques par semaine : ne renouvelez pas en boucle.' },
    ],
  },
  {
    id: 'sites', label: 'Sites et fichiers', icon: <FolderTree size={15} />,
    intro: 'Un site = un routeur (quelle adresse, quelles protections) + un service (où est l\'application). Ils vivent dans traefik-dynamic/.',
    items: [
      { id: 'f-site', t: 'Anatomie d\'un site', d: 'Le routeur reconnaît l\'adresse (rule), choisit la porte d\'entrée (entryPoints), applique les protections (middlewares) et le certificat (tls), puis envoie vers un service. Le service donne l\'URL réelle de l\'application.', code: CODE_SITE, where: 'traefik-dynamic/apps/…', apply: 'Automatique' },
      { id: 'f-url', t: 'L\'URL du service : nom du conteneur et port interne', d: 'http://grafana:3000 : le nom du conteneur (ou du service compose) et le port sur lequel l\'application écoute À L\'INTÉRIEUR de son conteneur.', warn: 'Pas localhost (ce serait Traefik lui-même), et pas le port publié sur l\'hôte. Le conteneur doit être sur le même réseau Docker que Traefik.' },
      { id: 'f-rule', t: 'Les règles de correspondance (QUAND on visite…)', d: 'Host() pour l\'adresse, PathPrefix() pour un chemin, Method() pour GET/POST…, combinés par && (ET), || (OU), ! (NON) et des parenthèses. Dans l\'éditeur de cette interface, une ligne indentée fait partie de la ligne du dessus, comme en Python.', code: CODE_WEBHOOK },
      { id: 'f-prio', t: 'Priorité entre deux routeurs sur la même adresse', d: 'Quand plusieurs routeurs peuvent correspondre, le plus « précis » gagne, ou celui dont « priority » est le plus grand. Utile pour exposer un seul chemin publiquement alors que le reste du site est privé.' },
      { id: 'f-org', t: 'Un fichier par site, un dossier par domaine', d: 'C\'est la disposition que l\'assistant de rangement produit : un site (routeurs + service) par fichier, rangé dans le sous-dossier de son domaine, les middlewares par type (auth, chains, headers, ip). Les commentaires utiles sont supprimés au rangement : gardez vos notes ailleurs si besoin. Les fichiers dont le nom commence par « zz » (zz-webui-bans.yml, zz-webui-nudge.yml…) sont ceux que l\'interface écrit elle-même : le rangement n\'y touche jamais.' },
      { id: 'f-watch', t: 'Traefik relit tout seul', d: 'Avec providers.file.watch=true, une modification est prise en compte en quelques secondes. Une erreur de syntaxe n\'arrête pas Traefik : le fichier est simplement ignoré, et l\'erreur apparaît dans ses journaux.' },
      { id: 'f-perm', t: 'Cette interface peut écrire dans le dossier', d: 'Le dossier est monté en écriture dans l\'interface. Si elle répond « dossier non modifiable », donnez à l\'utilisateur du conteneur (PUID/PGID) le droit d\'écrire, ou lancez-la avec le même utilisateur que celui qui possède le dossier.' },
      { id: 'f-disable', t: 'Désactiver un site sans le perdre', d: 'L\'interrupteur commente le bloc (#~) au lieu de le supprimer : il se réactive tel quel. Si vous désactivez un service, les routeurs qui en dépendent sont désactivés avec lui, et réactivés ensemble.' },
    ],
  },
  {
    id: 'protect', label: 'Protections', icon: <ShieldCheck size={15} />,
    intro: 'Les middlewares sont appliqués dans l\'ordre, avant que la requête n\'atteigne l\'application. Une chaîne les regroupe sous un seul nom.',
    items: [
      { id: 'p-chains', t: 'Deux chaînes : privée et publique', d: 'chain-private pour tout ce qui reste chez vous ; chain-public pour ce qui est ouvert à tous. Chaque site n\'a qu\'un nom à écrire, et vous changez la protection de tous d\'un coup en modifiant la chaîne.', code: CODE_CHAINS, where: 'traefik-dynamic/middlewares/chains', apply: 'Automatique' },
      { id: 'p-order', t: 'L\'ordre compte', d: 'Dans la chaîne de l\'exemple, les pages d\'erreur sont en premier : placées en tête, elles entourent tout le reste et peuvent donc habiller aussi le 403 de la restriction d\'adresse. Ensuite les en-têtes, puis la restriction. Pour une identification (mot de passe), mettez-la APRÈS la restriction d\'adresse : on refuse d\'abord les inconnus, avant même de leur demander un mot de passe.' },
      { id: 'p-ip', t: 'La liste d\'adresses autorisées', d: 'Réseaux privés (10/8, 172.16/12, 192.168/16) plus, ici, une IP publique. Les réseaux Docker (172.16/12) y sont aussi, ce qui permet aux autres conteneurs d\'appeler un service privé.', code: CODE_IP, where: 'traefik-dynamic/middlewares/ip', apply: 'Automatique', warn: 'Si Traefik est derrière un autre proxy (Cloudflare…), il voit l\'adresse du proxy, pas celle du visiteur : configurez ipStrategy (profondeur ou adresses de confiance), sinon tout est refusé ou tout est accepté.' },
      { id: 'p-hair', t: 'Pourquoi votre propre IP publique est dans la liste', d: 'Depuis chez vous, un nom public peut revenir par la box (« hairpin NAT ») : Traefik voit alors votre IP publique, pas une adresse privée. Sans elle dans la liste, vos propres sites privés vous refuseraient depuis votre réseau.' },
      { id: 'p-err', t: 'Pages d\'erreur personnalisées', d: 'Le middleware « errors » demande /{status}.html à un petit service (un conteneur nginx) quand l\'application répond une erreur. Les erreurs de Traefik elles-mêmes (403 de la restriction d\'adresse, 404 de routeur introuvable) peuvent ainsi s\'afficher joliment.', code: CODE_ERR, where: 'traefik-dynamic/middlewares', apply: 'Automatique', warn: 'Le service de pages d\'erreur doit lui-même rester joignable et ne pas être derrière une chaîne qui utilise le même middleware, sinon boucle.' },
      { id: 'p-headers', t: 'En-têtes de sécurité', d: 'HSTS (le navigateur n\'essaie plus jamais le HTTP), nosniff, referrer-policy, permissions-policy. À appliquer partout, via les chaînes.', warn: 'HSTS est difficile à annuler : testez d\'abord avec une durée courte.' },
      { id: 'p-auth', t: 'Identification en plus de la restriction', d: 'basicAuth pour le dashboard de Traefik ou un outil sans identification propre. Les mots de passe y sont stockés sous forme de hachage (htpasswd -nB) : ne mettez jamais un mot de passe en clair.' },
      { id: 'p-scan', t: 'Les scans existent : vérifiez les 403/404 dans le journal', d: 'Un site public reçoit en continu des tentatives (/.env, /old…). Elles sont normales ; ce qui compte, c\'est qu\'elles se terminent en 403 ou 404 et que rien d\'intéressant ne réponde.' },
    ],
  },
  {
    id: 'local', label: 'Site local / privé', icon: <Lock size={15} />,
    intro: 'Un site accessible seulement depuis chez vous, mais en vrai HTTPS, sans rien ouvrir de plus.',
    items: [
      { id: 'l-cert', t: 'Certificat par validation DNS', d: 'Mettez certResolver: lednsovh sur le routeur du site local. Let\'s Encrypt n\'a pas besoin de joindre votre machine : il vérifie un enregistrement DNS créé par Traefik.', code: CODE_LOCAL },
      { id: 'l-dns', t: 'Le nom doit se résoudre vers l\'adresse privée de Traefik', d: 'Trois façons : (1) un compte AdGuard Home ou Pi-hole branché dans « Comptes DNS » : l\'interface crée elle-même le nom vers l\'IP privée, sans rien publier ; (2) un DNS local géré à la main (routeur, autre serveur…) : vous ajoutez l\'enregistrement vous-même ; (3) un enregistrement A public vers une IP privée (simple, mais l\'adresse devient visible).' },
      { id: 'l-steps', t: 'Pas à pas : un site local avec AdGuard Home', d: '1. Comptes DNS → Ajouter un compte → AdGuard Home (adresse de son interface, identifiant, mot de passe) et un domaine à lui confier, par exemple « local.exemple.fr ». (Pi-hole 6 : même chose avec son mot de passe, sans identifiant.) 2. Nouveau site avec une adresse sous ce domaine (app.local.exemple.fr) et le résolveur de certificats DNS. 3. Dans le bloc DNS, laissez « Créer l\'enregistrement » coché et indiquez l\'IP privée de la machine Traefik. 4. Ajoutez chain-private dans « Alors appliquer ».', warn: 'Un domaine ne peut appartenir qu\'à UN compte : un domaine déjà chez OVH ne peut pas aussi être confié à AdGuard. Prenez un sous-domaine dédié (local.exemple.fr) : c\'est le plus précis qui s\'applique, et un certificat joker *.local.exemple.fr le couvre.' },
      { id: 'l-rebind', t: 'Votre routeur ou DNS ne bloque pas la protection contre le rebinding', d: 'Certains routeurs (et dnsmasq, Unbound) refusent qu\'un nom public renvoie une IP privée. Ajoutez le domaine à la liste d\'exceptions (rebind-domain-ok, private-domain…).' },
      { id: 'l-chain', t: 'Gardez chain-private quand d\'autres sites sont publics', d: 'Les ports 80/443 sont ouverts pour vos sites publics : quelqu\'un pourrait demander le nom local directement à votre Traefik. La chaîne privée le refuse. Si AUCUN port n\'est ouvert, le site n\'est de toute façon pas joignable de l\'extérieur.' },
      { id: 'l-vpn', t: 'Depuis l\'extérieur : par VPN', d: 'WireGuard ou Tailscale : une fois connecté, le site local fonctionne comme chez vous, avec son vrai certificat. Aucun port à ouvrir pour le site lui-même.' },
    ],
  },
  {
    id: 'dns', label: 'DNS', icon: <Globe size={15} />,
    intro: 'Où se trouve le DNS de chaque domaine, et ce que cette interface peut y faire.',
    items: [
      { id: 'd-ovh', t: 'OVH : zone complète', d: 'Créez, modifiez et supprimez des enregistrements depuis l\'interface. La clé API a besoin de GET, POST, PUT, DELETE sur /domain/zone/*. Cette clé est distincte de celle de Traefik (même fournisseur, deux usages) : vous pouvez en créer deux pour limiter les dégâts.' },
      { id: 'd-duck', t: 'DuckDNS : une adresse par nom', d: 'Chaque nom xxx.duckdns.org a une seule IP, et ses sous-domaines la suivent. DuckDNS ne permet pas de lister les noms d\'un compte : collez la liste copiée depuis duckdns.org.' },
      { id: 'd-adguard', t: 'AdGuard Home : vos noms locaux créés depuis l\'interface', d: 'Les « réécritures DNS » d\'AdGuard (nom → adresse) servent d\'enregistrements A, AAAA et CNAME ; une réécriture est active dès qu\'elle existe, sans « appliquer ». Pas de TTL, pas de TXT ni de MX. Un joker (*.local.exemple.fr) est possible.', where: 'Comptes DNS → Ajouter un compte → AdGuard Home', apply: 'Immédiat', warn: 'Cette interface doit pouvoir joindre l\'interface d\'administration d\'AdGuard (http://adguard:3000 si c\'est un conteneur du même réseau Docker, sinon l\'adresse sur le réseau local). Pour un nœud distant, c\'est le serveur central qui appelle AdGuard : il doit donc le joindre aussi.' },
      { id: 'd-pihole', t: 'Pi-hole 6 : même principe, avec ses particularités', d: 'Les noms vont dans « DNS local » (A, AAAA) et « CNAME » de Pi-hole ; connexion par mot de passe seulement (pas d\'identifiant). Pas de jokers (*) : un nom par enregistrement. Pi-hole 5 n\'a pas la même API et n\'est pas pris en charge.', where: 'Comptes DNS → Ajouter un compte → Pi-hole', apply: 'Immédiat', warn: 'Si la double authentification est activée sur Pi-hole, créez un « mot de passe d\'application » (Réglages, API) et utilisez-le ici ; selon la configuration de Pi-hole, ce mot de passe peut devoir être autorisé à modifier la configuration.' },
      { id: 'd-adg-https', t: 'HTTPS avec un certificat auto-signé (AdGuard, Pi-hole)', d: 'Cochez « Certificat auto-signé » dans le compte, ou utilisez l\'adresse en http:// sur le réseau local. Les redirections ne sont jamais suivies : indiquez l\'adresse finale.' },
      { id: 'd-accounts-node', t: 'Comptes DNS : seulement ceux de ce nœud, et l\'import des autres', d: 'La page « Comptes DNS » ne montre que vos comptes dont des domaines sont rattachés au nœud courant (ceux d\'un autre de vos nœuds n\'apparaissent pas, et ceux des autres utilisateurs jamais, administrateur compris : seul le compte principal est visible des administrateurs). Un compte sans domaine, ou dont les domaines ne sont sur aucun nœud, reste visible. Les domaines que vous gérez déjà ailleurs sont proposés dans « Importer sur ce nœud » (un bouton par domaine, ou par compte) : c\'est un simple lien, vos clés ne sont jamais copiées. Cette proposition n\'existe que si vous avez le droit d\'ajouter des domaines et de les lier à ce nœud.' },
      { id: 'd-local', t: 'DNS local sans compte : rien n\'est créé automatiquement', d: 'Pour un domaine résolu par votre propre DNS (routeur, autre serveur…). L\'interface sait qu\'il existe, mais ne modifie rien : vous ajoutez les enregistrements vous-même.' },
      { id: 'd-target', t: 'Adresse proposée pour les nouveaux sous-domaines', d: 'L\'IP proposée quand la zone n\'a pas d\'enregistrement A à la racine. Elle se règle dans « Ce nœud » (DEFAULT_DNS_TARGET du .env ne sert que de valeur de départ).', where: 'Ce nœud', apply: 'Immédiat' },
      { id: 'd-ttl', t: 'Propagation et cache', d: 'Un changement n\'est pas instantané : comptez quelques minutes, plus si votre DNS local ou votre box garde l\'ancienne réponse. Pour tester : « dig +short nom @1.1.1.1 ».', code: CODE_TEST },
      { id: 'd-ip', t: 'IP publique dynamique', d: 'Si votre box change d\'IP, l\'enregistrement A devient faux et la validation HTTP échoue. DuckDNS se met à jour automatiquement ; pour OVH, utilisez un client de mise à jour dynamique.' },
      { id: 'd-ipv6', t: 'IPv6 (AAAA)', d: 'Un AAAA oublié ou périmé fait échouer des connexions et la validation. Supprimez-le si vous n\'ouvrez pas l\'IPv6.' },
      { id: 'd-share', t: 'Partager un domaine', d: 'Dans Domaines : droits (lecture, modification), types d\'enregistrements, sous-domaines autorisés, durée. Un membre ne peut donner que ce qu\'il a lui-même.' },
    ],
  },
  {
    id: 'docker', label: 'Docker et machine', icon: <Network size={15} />,
    intro: 'La machine qui héberge tout : Docker, réseau, droits, mises à jour.',
    items: [
      { id: 'n-net', t: 'Un réseau Docker partagé', d: 'Traefik et chaque application qu\'il expose sont sur le réseau « proxy », créé une fois. Sans lui, le nom du conteneur n\'est pas résolu et Traefik répond « Bad Gateway ».', code: CODE_NET, where: 'compose de chaque application', apply: 'docker compose up -d' },
      { id: 'n-sock', t: 'Le socket Docker en lecture seule', d: 'Monté en :ro, il suffit à Traefik pour lire les étiquettes. Mais il donne tout de même une vue complète de la machine : si vous n\'utilisez que des fichiers, vous pouvez vous en passer et retirer le provider Docker.' },
      { id: 'n-user', t: 'Utilisateur et groupes', d: 'Traefik tourne avec votre UID/GID, pas en root. acme.json et traefik-dynamic doivent lui appartenir. L\'interface a aussi son PUID/PGID : ils doivent pouvoir écrire dans traefik-dynamic.' },
      { id: 'n-live', t: 'live-restore : les conteneurs survivent au démon', d: 'Par défaut, quand le démon Docker s\'arrête (plantage, mise à jour), tous les conteneurs s\'arrêtent avec lui, et Docker peut les marquer « arrêtés manuellement » : ils ne repartent alors pas seuls malgré « unless-stopped ». Avec live-restore, ils continuent de tourner.', code: CODE_DAEMON, where: '/etc/docker/daemon.json', apply: 'sudo systemctl reload docker', warn: 'Une virgule en trop ou manquante empêche Docker de démarrer : validez avec « python3 -m json.tool /etc/docker/daemon.json » avant de recharger.' },
      { id: 'n-up', t: 'Mettre Docker à jour', d: 'Activez live-restore AVANT, puis mettez à jour : les conteneurs ne sont pas coupés. Vérifiez ensuite la version et que tout est revenu.', code: CODE_UPDATE, where: 'machine hôte', apply: 'apt (avec sudo)' },
      { id: 'n-restart', t: 'Politique de redémarrage', d: 'restart: unless-stopped sur chaque service important. Les conteneurs ponctuels (migrations, initialisations) restent sans politique et s\'arrêtent à la fin, c\'est normal.' },
      { id: 'n-ws', t: 'Interface derrière un autre proxy : WebSockets', d: 'Le temps réel de cette interface passe par /api/events. Traefik le laisse passer ; un autre proxy devant doit autoriser l\'en-tête Upgrade.' },
      { id: 'n-time', t: 'Horloge à l\'heure', d: 'Les codes 2FA, la validité des sessions et des certificats dépendent de l\'heure : gardez la synchronisation (NTP) active.' },
    ],
  },
  {
    id: 'armadok', label: 'Cette interface', icon: <Boxes size={15} />,
    intro: 'Les réglages de cette application et son raccordement à Traefik, y compris pour plusieurs machines.',
    items: [
      { id: 'w-install-mode', t: 'Installer : interactif ou seulement les fichiers', d: 'Au début de « make install », « make hub » ou « make worker », le script demande comment continuer : en mode interactif (questions guidées, vérifications, puis les fichiers) ou en écrivant seulement le .env et le compose, sans aucune question ni démarrage : valeurs par défaut, secrets générés. Il liste à la fin ce qu\'il reste à régler : le mot de passe (make password), le réseau Docker, le dossier de Traefik, l\'adresse du hub et le jeton pour un worker. Pour passer la question : INTERACTIVE=0 (fichiers seulement) ou INTERACTIVE=1.', code: 'make hub INTERACTIVE=0', where: 'la machine à installer', apply: 'À l\'installation' },
      { id: 'w-docker', t: 'Gérer Docker depuis l\'interface (facultatif)', d: 'Active les pages « Docker » et « Projets » : conteneurs, journaux, projets docker compose (fichiers, déploiement, Git, images) et leur liaison avec Traefik (règles, site, visiteurs). Il faut DOCKER_MANAGE=true ET le socket Docker monté (avec le groupe « docker » dans group_add), ainsi que PROJECT_ROOTS : les dossiers des projets, montés avec le MÊME chemin dans le conteneur. L\'installation guidée pose la question. Les droits « docker.* » et « projects.* » se donnent dans « Droits » ; le terminal n\'est donné à personne d\'office.', code: 'DOCKER_MANAGE=true\nPROJECT_ROOTS=/opt/stacks,/srv/apps\nDOCKER_GID=984', where: '.env et docker-compose.yml', apply: 'docker compose up -d', warn: 'Donner Docker à l\'interface équivaut à donner root sur la machine : protégez-la (chaîne privée, 2FA) et limitez les droits.' },
      { id: 'w-worker-types', t: 'Les trois types de worker', d: 'Un worker peut gérer Traefik + DNS (les sites, protections, certificats du Traefik de la machine), Docker seul (conteneurs et projets compose), ou les deux. « Nouveau nœud » génère la pile ; « make worker » pose les questions selon le type : WORKER_MODULES=traefik|docker|both pour les passer. Le menu du nœud s\'adapte : un nœud Docker seul n\'affiche ni Sites ni Certificats.', code: 'make worker HUB_URL=https://… ENROLL_TOKEN=enr_… WORKER_MODULES=both', where: 'la machine à relier' },
      { id: 'w-backup', t: 'Sauvegardes complètes du hub (Tous les nœuds → Backup et sauvegardes)', d: 'Réservé aux super-administrateurs. « Créer une sauvegarde » copie, sans arrêter le hub : la base (utilisateurs, droits, secrets chiffrés, modèles, comptes DNS…) avec le journal d\'audit, les historiques de fichiers, la configuration Docker / Git du hub et les fichiers dynamiques de Traefik, au choix. La base de géolocalisation n\'est jamais incluse (elle se retélécharge). Une sauvegarde peut être protégée par un mot de passe (chiffrement AES, sans lui elle est inutilisable). IMPORTANT : la base ne se relit qu\'avec la clé ENCRYPTION_KEY de votre .env : sauvegardez aussi ce fichier, ou cochez « Inclure la clé » (archive protégée par mot de passe obligatoire, code 2FA requis). Télécharger exige un code de double authentification ; vous pouvez aussi téléverser une sauvegarde (.zip ou .armbak) pour la restaurer, par exemple sur un nouveau hub. Restaurer : lecture et aperçu d\'abord (rien n\'est touché), choix de ce qui est remplacé, code 2FA et nom de la sauvegarde retapé ; le hub fait alors une sauvegarde « avant-restauration » de l\'état actuel, prépare les fichiers et redémarre pour les mettre en place (quelques secondes sans service) ; l\'état d\'avant reste aussi dans le dossier avant-restauration. Le conteneur doit avoir « restart: unless-stopped » pour repartir tout seul, sinon une alerte vous le dit. Si la sauvegarde vient d\'une autre clé de chiffrement, un avertissement s\'affiche et, si la clé est incluse, elle est montrée à la fin pour la mettre dans votre .env. Les sauvegardes sont dans le dossier data/sauvegardes. À ne pas confondre avec data/backups : ce sont les versions précédentes de vos fichiers Traefik, utiles pour restaurer UN fichier.', warn: 'Une sauvegarde contient tout (utilisateurs, secrets) : gardez-la à l\'abri, de préférence protégée par un mot de passe.', where: 'Tous les nœuds → Backup et sauvegardes', apply: 'Immédiat (la restauration redémarre le hub)' },
      { id: 'w-link', t: 'Lier deux hubs (Tous les nœuds → Nœuds et liaisons)', d: 'Réservé aux super-administrateurs, avec un code de double authentification à chaque action. Chaque hub garde ses utilisateurs, ses droits et ses nœuds : la liaison établit seulement QUI est l\'autre hub et si la connexion est vivante. Toujours entre DEUX hubs : si B est lié à A et à C, A et C ne se voient pas. « Inviter un hub » crée un code à usage unique (15 minutes) ; l\'autre hub clique sur « Rejoindre un hub » et le colle : les clés sont échangées. Ensuite les DEUX administrateurs comparent l\'empreinte affichée (elle doit être strictement identique) et confirment chacun : rien n\'est actif avant. Le hub qui a rejoint entretient une connexion signée vers l\'autre : un seul des deux hubs doit donc être joignable. Les versions peuvent différer : on parle le protocole commun le plus récent, avec les seules fonctions que les deux comprennent ; si elles ne se recouvrent plus, la liaison affiche « versions incompatibles » et il faut mettre à jour le plus ancien. Le délai de grâce (12 h par défaut, modifiable par liaison, 0 = tout de suite) fixe combien de temps ce que la liaison apporte reste valable quand la connexion est coupée. Révoquer coupe tout de suite et prévient l\'autre hub. Pour que l\'autre hub joigne celui-ci, ouvrez UNIQUEMENT le chemin /api/link (comme /api/worker), sans le reste de l\'interface : voir le README pour la règle Traefik.', warn: 'Comparez l\'empreinte par un canal que vous connaissez déjà (de vive voix) : c\'est ce qui protège contre quelqu\'un qui s\'interposerait pendant l\'appairage.', where: 'Tous les nœuds → Nœuds et liaisons', apply: 'Immédiat' },
      { id: 'w-node-backup', t: 'Sauvegarder un nœud (Ce nœud → Sauvegardes)', d: 'Pour les responsables d\'un nœud (droit « Sauvegarder le nœud », donné aux administrateurs ; « Restaurer une sauvegarde du nœud » est réservé au propriétaire, qui peut l\'accorder). Une sauvegarde garde les fichiers de la MACHINE du nœud : la configuration Traefik (vos sites, routeurs, middlewares), les versions précédentes de ces fichiers et, avec le module Docker, celles de vos fichiers de projets. Les mots de passe, clés DNS et la clé du worker ne sont jamais dedans. Elle se crée sur le worker (il doit être connecté) et s\'enregistre sur le hub, jusqu\'à 20 par nœud ; mot de passe facultatif (chiffrement AES) ; téléchargement et téléversement possibles. Restaurer se fait FICHIER PAR FICHIER : le hub compare la sauvegarde à l\'état actuel (modifié depuis, absent maintenant, absent de la sauvegarde), vous voyez la différence de chaque fichier (œil), vous cochez ce que vous remettez ; un fichier qui n\'est pas dans la sauvegarde n\'est retiré que si vous le cochez. Une sauvegarde « avant-restauration » de l\'état actuel est faite d\'abord ; Traefik relit ses fichiers tout seul, et un YAML invalide est refusé avant d\'être écrit. Si le propriétaire exige la double authentification pour ces droits (onglet Sécurité), ils n\'existent pas pour qui ne l\'a pas activée. Un super-administrateur n\'a pas d\'accès implicite : il passe par l\'accès d\'urgence comme pour le reste. Ces sauvegardes ne sont pas dans la sauvegarde complète du hub.', where: 'Ce nœud → Sauvegardes', apply: 'Immédiat' },
      { id: 'w-native', t: 'Un worker sans Docker (service systemd)', d: 'Pour un VPS où Docker n\'est pas voulu : « make worker-native » (tout est dans le Makefile, aucun script à côté) installe le worker comme un service systemd qui démarre au boot et redémarre tout seul. Avant de toucher à quoi que ce soit, le script vérifie la machine (systemd, Python 3.12+, pip et venv, accès au hub en HTTPS, dossier de Traefik, journal d\'accès, acme.json) et affiche une checklist ✅ / ⚠ / ❌ avec la correction exacte de chaque point ; un ❌ bloque l\'installation. « make native-check » ne fait que ces vérifications. Si vous interrompez le script pour corriger un point (installer pip, par exemple), le lancement suivant propose de reprendre avec vos réponses précédentes (gardées dans ~/.config/armadok-worker-install.env, jamais le jeton). Pour les certificats, indiquez le fichier acme.json lui-même (un dossier qui le contient est accepté). Il demande le dossier d\'installation (par défaut /opt/armadok-worker), le dossier de configuration dynamique de Traefik, et lance le service sous le propriétaire de ce dossier, ou sous un utilisateur dédié « armadok » (sans mot de passe ni shell) à qui il donne seulement les droits utiles par ACL. Le dossier contient app/ (le code), venv/ (ou lib/ sans venv), data/ (l\'identité du nœud, en 0700), .env (en 0600), VERSION et install-report.txt (la checklist). Les journaux sont ceux de systemd : « make native-logs » (ou journalctl -u armadok-worker -f). Au quotidien : make native-restart, native-stop, native-start (le redémarrage attend la connexion au hub) et make native-backup (archive datée de l\'identité du nœud : elle contient sa clé, gardez-la en lieu sûr). Les certificats peuvent être affichés par un relais isolé (sans réseau ; seuls les noms et dates sortent) plutôt que par une lecture directe d\'acme.json. Limites : Linux avec systemd, module Traefik seulement (pour Docker, utilisez « make worker »), et le hub doit être joignable en HTTPS depuis la machine.', code: 'make worker-native HUB_URL=https://… ENROLL_TOKEN=enr_…', where: 'la machine à relier (« Nouveau nœud » → « Sans Docker » affiche la commande toute prête)', apply: 'À l\'installation' },
      { id: 'w-native-venv', t: 'Worker natif : environnement Python isolé (venv) en option', d: 'Par défaut le worker natif vit dans un venv (NATIVE_VENV=true). Certains VPS n\'ont pas python3-venv ou refusent pip hors venv (Debian et Ubuntu récents, PEP 668) : le script le détecte dans la checklist et donne la commande à lancer. Avec NATIVE_VENV=false, les dépendances vont dans un dossier lib/ propre au worker : rien n\'est installé dans le Python du système. Les mises à jour (make native-update) gardent le .env et data/ ; make native-uninstall retire le service et demande avant de supprimer l\'identité du nœud.', code: 'make worker-native NATIVE_VENV=false', where: 'la machine à relier', apply: 'À l\'installation' },
      { id: 'w-install-continue', t: 'Reprendre une installation interrompue (make continue)', d: 'Les assistants d\'installation (make install, hub, worker, worker-native) gardent vos réponses au fur et à mesure dans le fichier .armadok-install.state (droits 600) : jamais un mot de passe, un jeton d\'enrôlement ni un secret OVH. Si vous coupez pour corriger un point (installer pip, créer un réseau Docker…), « make continue » reprend là où vous en étiez sans reposer les questions et redemande seulement les secrets ; relancer « make hub » ou « make worker » propose aussi la reprise. Pour le worker natif, les réponses sont dans ~/.config/armadok-worker-install.env. Le fichier est effacé quand l\'installation est terminée ; répondre « n » à la reprise repart de zéro.', code: 'make continue', where: 'le dossier d\'installation (là où se trouve le Makefile)', apply: 'Immédiat' },
      { id: 'w-hub-ip-filter', t: 'Un hub derrière un filtre IP : laisser passer les workers', d: 'Si le routeur du hub utilise une chaîne avec un filtre IP (par exemple chain-private et ip-allow-lan), un worker installé hors de votre réseau reçoit un 403 dès l\'enrôlement. Les deux solutions : un VPN (l\'adresse du worker devient locale), ou un second routeur limité au chemin /api/worker, avec une chaîne sans filtre IP. Ce chemin reste protégé : le jeton d\'enrôlement est à usage unique (15 minutes), puis chaque connexion exige une signature Ed25519 que seule la clé privée du worker, qui ne quitte jamais sa machine, peut produire. Le worker n\'a rien à ouvrir de son côté : c\'est lui qui appelle le hub, en HTTPS sortant.', code: CODE_WORKER_ROUTE, where: 'la configuration dynamique de Traefik du hub (nouveau routeur vers le même service)', apply: 'Traefik recharge seul' },
      { id: 'w-worker-log', t: 'Statistiques de visiteurs d\'un nœud distant', d: 'Le worker lit le journal d\'accès de SON Traefik et l\'envoie au hub, qui l\'analyse comme celui du nœud principal : adresses IP, pays, attaques probables, alertes, règles automatiques. Il faut monter le dossier du journal en lecture seule dans le worker et définir TRAEFIK_ACCESS_LOG (« make worker » et l\'assistant « Nouveau nœud » le font). Le fichier doit être lisible par l\'utilisateur du worker (PUID:PGID).', code: 'TRAEFIK_ACCESS_LOG=/traefik-logs/access.log', where: 'le worker (compose) ; Traefik doit écrire un journal JSON', apply: 'docker compose up -d' },
      { id: 'w-worker-exec', t: 'Autoriser le terminal sur un worker', d: 'Par défaut un worker refuse tout terminal. Pour l\'ouvrir depuis le hub, ajoutez WORKER_ALLOW_EXEC=true dans le worker, et donnez le droit « docker.exec » sur ce nœud (jamais accordé d\'office, 2FA possible). Chaque ouverture est journalisée, jamais le contenu.', code: 'WORKER_ALLOW_EXEC=true', where: 'le worker (compose)', apply: 'docker compose up -d', warn: 'Un terminal dans un conteneur de la machine équivaut à un accès à ses données : réservez-le à des personnes de confiance.' },
      { id: 'w-traefik-install', t: 'Installer Traefik avec Armadok', d: 'Si aucun Traefik n\'est détecté sur la machine du hub, la page Projets propose de créer un projet Traefik : vous choisissez l\'e-mail Let\'s Encrypt, le réseau partagé, le HTTPS automatique, la protection « réseau privé », le journal d\'accès (indispensable aux statistiques de visiteurs), l\'état en direct, le tableau de bord et les métriques. Armadok écrit le compose, le .env et la configuration dans un nouveau dossier, sans rien démarrer : relisez-les, puis démarrez le projet. Si vous avez refusé, le bouton « Créer un projet Traefik » reste en haut de la page Projets. Le Constructeur de piles de cette Aide produit des fichiers plus complets (DNS, joker, labels Docker).', where: 'Projets', apply: 'Après démarrage du projet' },
      { id: 'w-compose-files', t: 'Plusieurs fichiers compose dans un projet', d: 'Sans choix, Armadok fait comme « docker compose up » : le fichier principal et son override (docker-compose.override.yml). Si le dossier contient d\'autres variantes (prod, dev…), un clic sur « Déployer » demande quels fichiers fusionner (le dernier l\'emporte) et peut retenir ce choix ; la ligne « Fichiers » au-dessus des boutons le rappelle et le change. L\'arrêt, le démarrage et le redémarrage reprennent les fichiers du dernier lancement (lus sur les conteneurs), sauf choix explicite.', where: 'Projet → ligne « Fichiers »', apply: 'Au prochain lancement' },
      { id: 'w-project-resources', t: 'Volumes et réseaux d\'un projet', d: 'Sur la fiche d\'un projet, l\'onglet « Volumes » liste ce que ses conteneurs montent : volumes nommés, anonymes et dossiers de la machine (« montages liés »), avec le chemin dans le conteneur, le mode (lecture seule ou non) et les conteneurs qui les utilisent. L\'onglet « Réseaux » montre les réseaux auxquels ils sont reliés, l\'adresse IP de chaque conteneur et ses alias. Dans la page Docker, la liste des volumes a une colonne « Type » (nommé, anonyme, dossier monté) et un filtre par type.', where: 'Projet → Volumes / Réseaux ; Docker → Volumes', apply: 'Immédiat' },
      { id: 'w-image-redeploy', t: 'Redéployer une seule image', d: 'Dans l\'onglet Images d\'un projet, chaque ligne a un bouton « Redéployer » (mis en avant quand une nouvelle version existe) : il télécharge l\'image de CE service puis recrée son conteneur, après confirmation. Les autres services ne sont pas touchés. Le service est coupé quelques secondes. Si la nouvelle version vient d\'un autre tag, changez d\'abord le tag dans le compose ou la variable dans le .env, puis redéployez. Droit requis : déployer.', where: 'Projet → Images', apply: 'Immédiat' },
      { id: 'w-service-buttons', t: 'Boutons sur chaque service d\'un projet', d: 'Dans l\'onglet « Services » d\'un projet, chaque carte a ses boutons selon l\'état du service : Démarrer (arrêté), Redémarrer et Arrêter (en marche), Redéployer, et une corbeille qui arrête puis supprime le conteneur de CE service (docker compose rm : les volumes et les fichiers du projet sont gardés, « Déployer » le recrée). Les autres services ne sont pas touchés. Droit requis : déployer. Les anciens workers ne connaissent pas la suppression d\'un service : mettez-les à jour.', where: 'Projet → Services', apply: 'Immédiat' },
      { id: 'w-delete-with-dns', t: 'Supprimer un site ou un conteneur avec son DNS', d: 'La confirmation de suppression d\'un site (carte, liste ou bouton « Supprimer le site » en bas de son éditeur) propose, en cases à cocher, de supprimer aussi l\'enregistrement DNS de son adresse. Celle d\'un conteneur ou d\'un service de projet propose en plus ses sites Traefik (seulement ceux dont le lien est certain ; les règles définies par les labels partent avec le conteneur). Rien n\'est coché d\'office. Une case DNS n\'apparaît que si vous pouvez supprimer des enregistrements de ce domaine et qu\'aucun autre site ne sert la même adresse ; elle reste grisée tant que le site concerné n\'est pas coché. Le DNS ne part qu\'après la suppression principale ; sur OVH, la zone est ensuite appliquée (sauf si « Appliquer automatiquement » est coupé).', where: 'Sites ; éditeur d\'un site ; fiche d\'un conteneur ; Projet → Services', apply: 'Immédiat' },
      { id: 'w-project-roots', t: 'Plusieurs dossiers de projets', d: 'PROJECT_ROOTS accepte plusieurs dossiers séparés par des virgules (chemins absolus, montés au même chemin dans le conteneur). S\'il y en a plusieurs, la création d\'un projet (vide, modèle, depuis un conteneur, Traefik de base) et le clonage Git demandent dans quel dossier l\'écrire ; avec un seul, rien n\'est demandé. L\'import d\'un projet déjà présent sur le disque ne pose pas la question. Seuls les dossiers de la liste sont acceptés. Sur un worker, la variable se règle dans son compose.', where: 'Fichier .env du nœud (PROJECT_ROOTS)', apply: 'Au redémarrage' },
      { id: 'w-project-actions', t: 'Actions personnalisées d\'un projet (onglet Avancé)', d: 'Chaque action d\'un projet (lancer, arrêter, démarrer, redémarrer, redéployer, télécharger, construire, supprimer) peut être remplacée par une suite d\'étapes lancées l\'une après l\'autre ; la suite s\'arrête à la première étape en échec. Types d\'étapes : une commande docker compose, une cible du Makefile du projet, ou une commande libre. Une commande est lancée sans shell (« ; », « && », « | » et « $(…) » n\'ont aucun effet) : pour enchaîner ou scripter, passez par une cible du Makefile. Cibles et commandes demandent le droit « terminal dans les conteneurs » et, sur un worker, WORKER_ALLOW_EXEC=true ; sans ce droit, seules les étapes docker compose sont possibles, et une suite qui contient déjà une commande ne peut ni être modifiée ni être retirée. Les boutons de la barre du projet lancent alors votre suite ; une action sur un seul service, « supprimer avec les volumes » et les déploiements automatiques par webhook gardent la commande normale. Le journal note les types d\'étapes, jamais le texte d\'une commande (il peut contenir un secret). Un projet qui contient Armadok lui-même refuse une suite qui le redémarrerait.', where: 'Projet → Avancé (droit « modifier les fichiers »)', apply: 'Immédiat' },
      { id: 'w-env-right', t: 'Qui voit les valeurs du .env', d: 'Les valeurs du .env d\'un projet ne sont montrées qu\'avec le droit « variables .env » (projets.env). Sans lui, une image écrite avec une variable reste affichée telle quelle (${VERSION}) au lieu de sa valeur, le suivi des images la marque « non suivie » et la validation d\'un fichier compose n\'utilise pas le .env : un message d\'erreur ne peut donc pas en révéler le contenu. Dans le journal d\'un nœud, les adresses IP des visiteurs sont masquées sans le droit « Voir les adresses IP en clair ». Dans un modèle de projet, une adresse qui contient un identifiant (postgres://utilisateur:mot-de-passe@…) compte comme un secret et est vidée.', where: 'Équipe → Droits', apply: 'Immédiat' },
      { id: 'w-make', t: 'Lancer les commandes du Makefile d\'un projet', d: 'L\'onglet « Makefile » d\'un projet liste ses cibles avec leur description (« cible: ## texte », ou le commentaire juste au-dessus) et les lance. « Lancer » ouvre la commande dans un vrai terminal : vous voyez la sortie et pouvez répondre aux questions qu\'elle pose (confirmation, saisie, mot de passe ; Ctrl+C l\'interrompt) ; fermer la fenêtre arrête la commande. Le petit bouton à côté la lance en arrière-plan (elle continue sans fenêtre, mais ne peut pas poser de question) : à garder pour les longues commandes sans interaction. Seule l\'ouverture est notée dans le journal, jamais ce que vous tapez. Ce sont des commandes exécutées dans le conteneur d\'Armadok (make, docker, git ; pas npm ni python) : le droit « terminal dans les conteneurs » est exigé, et WORKER_ALLOW_EXEC=true sur un worker. Seules les cibles écrites dans le fichier se lancent. Une cible qui attend des arguments (ENV=prod…) affiche un badge orange (ENV *) : « Lancer » ouvre alors une fenêtre qui les demande. Les obligatoires (ifndef, $(error …), ${VAR:?}) bloquent le lancement tant qu\'ils sont vides ; les facultatifs (VAR ?= défaut) laissent le défaut du Makefile si vous ne remplissez rien. Chaque valeur est passée à make comme un seul argument, sans shell, et seuls les noms (jamais les valeurs) sont notés dans le journal ; les champs TOKEN, PASS, SECRET et KEY sont masqués.', where: 'Projet → Makefile', apply: 'Immédiat' },
      { id: 'w-traefik-via', t: 'Un nœud Docker qui passe par le Traefik du hub', d: 'Un worker « Docker seul » n\'a pas de Traefik : ses sites sont publiés par celui d\'un autre nœud (souvent le hub). Dans « Ce nœud » → Docker, choisissez « Publier les sites avec le Traefik de… » et indiquez l\'adresse de la machine (IP du réseau local ou du VPN) — ou mettez WORKER_ADDRESSES dans le worker (« make worker » la demande). Les conteneurs sont alors reliés tout seuls aux règles de ce Traefik (adresse + port publié), avec leurs statistiques ; Les pages Traefik (Sites, Middlewares, Traefik avancé, Surveillance) apparaissent dans le menu du nœud : ce sont celles du Traefik choisi, filtrées sur les conteneurs du nœud (bouton « Tout voir » pour lever le filtre) ; « Publier sur Traefik » ouvre l\'assistant de site vers l\'adresse de la machine. Il faut que ce Traefik puisse joindre la machine (réseau, pare-feu) et que le conteneur publie son port sur l\'hôte.', code: 'WORKER_ADDRESSES=192.168.1.20', where: 'Ce nœud → Docker, et le worker', apply: 'Immédiat' },
      { id: 'w-from-container', t: 'Convertir un conteneur lancé à la main en projet', d: 'Un conteneur sans projet compose (docker run…) montre, dans sa fiche, un bouton « Convertir en projet » ; « Nouveau projet → Depuis un conteneur » en liste tous les candidats. Armadok relit sa configuration et écrit docker-compose.yml + .env dans un nouveau dossier : les variables d\'environnement vont dans le .env (droits 600), le compose y renvoie par ${NOM} ; les volumes nommés sont repris en « external » pour garder les données ; tout ce qui ne se traduit pas est signalé. Rien ne démarre sans votre accord. Avec « Remplacer », l\'ancien conteneur est arrêté puis renommé « nom-avant-armadok » (jamais supprimé) et le projet est déployé sous le nom d\'origine. Droit requis : lire les .env.', where: 'Docker → conteneur, ou Projets → Nouveau projet', apply: 'Immédiat' },
      { id: 'w-labels-sync', t: 'Labels Traefik et fichiers de Traefik : passer de l\'un à l\'autre', d: 'Projet → Fichiers → « Labels Traefik ». Du compose vers Traefik : choisissez le fichier de destination (nouveau ou existant) et, si vous voulez, retirez ensuite les labels du compose pour éviter un doublon. D\'un site vers le compose : Armadok propose le service dont le nom ressemble à la cible du site, vous choisissez le fichier compose, les labels sont ajoutés sans écraser l\'existant et le site peut être mis en pause dans Traefik (réversible). Redéployez le projet ensuite. Rappel : en labels, Traefik vise le conteneur lui-même, seul le port est gardé ; le service doit partager un réseau avec Traefik.', where: 'Projet → Fichiers → Labels Traefik', apply: 'Après redéploiement' },
      { id: 'w-templates', t: 'Créer et utiliser des modèles de projet', d: 'Sur la fiche d\'un projet, « Enregistrer comme modèle » (au-dessus de « Désimporter ») garde les fichiers que vous cochez : le compose, le .env, un Makefile, de petits fichiers de config. Les secrets du .env (mots de passe, jetons, clés) sont vidés, sauf si vous cochez de les garder ; le nom du projet devient {{name}}. Ensuite « Nouveau projet → À partir d\'un modèle » liste le modèle de base, un exemple Minecraft et les vôtres : vous donnez le nom du dossier et remplissez les variables du .env (valeurs de départ, secrets masqués avec un bouton « Générer »). Rien ne démarre : relisez puis déployez. Un modèle est chiffré en base. Il est privé (vous seul) ou partagé avec ceux qui peuvent créer des projets : à l\'enregistrement, puis modifiable d\'un clic (cadenas / globe) dans la liste ; partagé, il ne garde jamais de valeur de secret. Son auteur ou un administrateur peut le supprimer.', where: 'Projet → Enregistrer comme modèle ; Projets → Nouveau projet', apply: 'Immédiat' },
      { id: 'w-git-ssh', t: 'Git en SSH et récupération automatique', d: 'Onglet Git d\'un projet : « Clé SSH de déploiement » génère une clé dont vous donnez la partie publique au dépôt (Deploy key, lecture seule) ; « Récupération automatique (webhook) » donne une adresse et un secret à coller dans le dépôt (GitHub, GitLab, Gitea) : à chaque envoi, le hub récupère (git pull --ff-only) et, si vous le demandez, redéploie. Pour cloner en SSH, la fenêtre « Cloner » propose la clé du nœud.', where: 'Projets → un projet → Git', warn: 'Le hub doit être joignable en HTTPS depuis le dépôt pour recevoir le webhook.' },
      { id: 'w-make-menu', t: 'Le menu « make » et les sauvegardes en ligne de commande', d: 'Dans le dossier d\'installation, « make » tout seul ouvre un menu interactif (service, sauvegardes, diagnostic, outils, installation, aide) ; hors terminal il affiche la liste des commandes. « make backup » crée une sauvegarde complète du hub de façon guidée : contenu au choix, nom, mot de passe (chiffrement AES), clé de chiffrement en option, copie VÉRIFIÉE vers une clé USB ou un partage, rappel de garder aussi le .env ; elle se fait sans arrêter le hub et se restaure aussi depuis l\'interface (même format). « make restore » restaure : liste numérotée ou fichier pris ailleurs sur la machine, mot de passe, aperçu (date, version, clé de chiffrement identique ou non), éléments à remplacer un par un, nom retapé ; le hub est arrêté, l\'état actuel est sauvegardé d\'abord (« avant-restauration »), puis le hub est relancé et vérifié. Cela marche MÊME SI LE HUB NE DÉMARRE PLUS : c\'est un conteneur ponctuel qui travaille sur data/. « make backups » liste, « make backup-delete » supprime. « make update » propose le canal (stable latest, développement dev, un numéro de version, ou inchangé), une sauvegarde avant, puis attend que le hub réponde ; le choix est gardé dans ARMADOK_IMAGE du .env (retour arrière : remettre l\'ancienne valeur). Sur un worker : « make backup » archive son identité (data/) et son dossier Traefik, chiffrable. Tout peut se préremplir par des variables pour un script : LABEL, COMPONENTS (database,history,home,traefik), BACKUP_PASSWORD, KEEP_KEY, COPY_TO, FILE, CONFIRM, CHANNEL, NO_BACKUP.', warn: 'Gardez aussi une copie du .env : sans ENCRYPTION_KEY, les secrets d\'une sauvegarde sont illisibles sur une autre installation.', where: 'Terminal, dans le dossier du hub', apply: 'Immédiat' },
      { id: 'w-doctor', t: 'Vérifier toute l\'installation : make doctor', d: 'Une commande contrôle le .env (secrets présents et assez longs, compte propriétaire), les droits du fichier, le dossier de Traefik et son propriétaire, le réseau Docker, le groupe docker, le journal d\'accès, les certificats, puis que le conteneur tourne et que l\'API répond. Les ✗ sont à corriger, les ⚠ sont des conseils. Elle rappelle aussi quelles valeurs du .env ne servent qu\'une fois (valeurs de départ du nœud Principal) : les modifier ensuite ne change rien, on les règle dans « Ce nœud ».', code: 'make doctor', where: 'dossier de l\'installation (là où se trouvent le .env et le docker-compose.yml)' },
      { id: 'w-env', t: 'Le .env de l\'interface : seulement une partie est « vivante »', d: 'Chemin des fichiers dynamiques, réseau et dossier des certificats sont relus à chaque démarrage. En revanche TRAEFIK_ENTRYPOINTS, TRAEFIK_CERT_RESOLVERS, TRAEFIK_API_URL et DEFAULT_DNS_TARGET ne sont que des valeurs de départ, lues une seule fois pour créer le nœud « Principal » : après, la base de l\'interface fait foi.', code: CODE_WEBUI, where: '.env pour les chemins ; « Ce nœud » pour les listes', apply: 'up -d pour le .env ; immédiat pour « Ce nœud »', warn: 'Modifier TRAEFIK_CERT_RESOLVERS (ou ENTRYPOINTS) dans le .env après le premier démarrage n\'a aucun effet, même après « up -d ». Ajouter un résolveur dans Traefik ne le fait pas non plus apparaître ici : ajoutez-le dans « Ce nœud » → Résolveurs de certificats.' },
      { id: 'w-node', t: 'Les réglages du nœud : où et qui', d: 'Page « Ce nœud » : nom, EntryPoints, résolveurs de certificats, IP par défaut, membres. L\'URL de l\'API Traefik du nœud principal se change par un administrateur de la plateforme ; pour un nœud distant, elle se règle sur sa machine (TRAEFIK_API_URL du worker).', where: 'Ce nœud', apply: 'Immédiat' },
      { id: 'w-api', t: 'L\'état des sites demande l\'API de Traefik', d: 'Une URL d\'API joignable depuis l\'interface (par exemple http://traefik:8080) pour voir si chaque site est actif ou en erreur. L\'API reste interne : elle n\'est pas publiée.' },
      { id: 'w-secrets', t: 'Secrets : JWT_SECRET et ENCRYPTION_KEY', d: 'JWT_SECRET signe les sessions ; ENCRYPTION_KEY chiffre les clés DNS et les secrets 2FA enregistrés. Sauvegardez ENCRYPTION_KEY : sans elle, les clés enregistrées ne se déchiffrent plus.' },
      { id: 'w-worker', t: 'Plusieurs machines : un worker par machine distante', d: 'Il se connecte de lui-même à ce serveur (aucun port à ouvrir sur la machine distante) ; le serveur doit seulement être joignable en HTTPS à l\'adresse indiquée dans la commande d\'installation, sinon le nœud reste « en attente ».' },
      { id: 'w-perm', t: 'Le worker écrit dans le dossier Traefik distant', d: 'Même question de droits (PUID/PGID) que sur la machine principale. Le message « dossier non modifiable » indique le propriétaire réel du dossier.' },
      { id: 'w-certs', t: 'Certificats d\'un nœud distant', d: 'Le worker a son propre relais de lecture d\'acme.json, à activer sur sa machine.' },
      { id: 'w-urgence', t: 'Accès d\'urgence', d: 'L\'administrateur de la plateforme peut ouvrir temporairement un nœud ; le propriétaire en est averti et tout est journalisé.' },
      { id: 'w-bak', t: 'Sauvegardes', d: 'Le volume /data de l\'interface (comptes, droits, clés chiffrées), acme.json et traefik-dynamic. Gardez-les ensemble, avec ENCRYPTION_KEY à part.' },
    ],
  },
  {
    id: 'secu', label: 'Sécurité', icon: <ShieldCheck size={15} />,
    intro: 'Ce que l\'on oublie le plus souvent, et qui expose le plus.',
    items: [
      { id: 's-mfa-policy', t: 'Exiger la 2FA pour les droits sensibles', d: 'Ce nœud → onglet Sécurité : le propriétaire coche les droits qui exigent la double authentification (certificats, blocage d\'adresses, automod, fichiers bruts, réglages et membres du nœud, DNS sensible…). « Sélection recommandée » en propose une base. Une personne qui n\'a pas la 2FA perd EXACTEMENT ces droits sur ce nœud jusqu\'à l\'activation (une bannière le lui explique), le reste ne change pas. Il faut avoir la 2FA soi-même pour l\'exiger ; la retirer est toujours possible.', where: 'Ce nœud → Sécurité', apply: 'Immédiat' },
      { id: 's-forwarded', t: 'Resserrer FORWARDED_ALLOW_IPS à l\'adresse de Traefik', d: 'L\'interface ne croit l\'en-tête X-Forwarded-For que des proxys listés (par défaut : 127.0.0.1 et les réseaux privés, donc Docker). Elle en tire l\'adresse du visiteur pour le verrou de connexion, les bannissements et le journal. Si vous connaissez l\'adresse de Traefik sur le réseau partagé, mettez-la seule : un autre conteneur ne pourra plus se faire passer pour un visiteur.', code: 'FORWARDED_ALLOW_IPS=172.21.0.11            # adresse (ou réseau) de Traefik sur le réseau Docker partagé', where: '.env ou environment du service armadok', apply: 'docker compose up -d', warn: 'Si vous mettez une adresse fausse, toutes les connexions paraîtront venir de Traefik : un visiteur qui se trompe de mot de passe bloquerait les autres. Vérifiez dans Surveillance → Adresses IP que vous voyez bien les vraies adresses.' },
      { id: 's-socket', t: 'Socket Docker : préférer un proxy limité', d: 'Pour supprimer ou renouveler un certificat depuis l\'interface, elle arrête puis relance Traefik via le socket Docker. Monté tel quel, il donne à une faille de l\'interface la main sur Docker, donc sur la machine. Un proxy de socket (tecnativa/docker-socket-proxy) n\'autorise que ce qu\'on lui permet ; l\'interface lui parle par DOCKER_SOCKET=docker-proxy:2375. Exigez aussi la 2FA pour le droit « Supprimer, renouveler et redemander des certificats ».', where: 'compose de l\'interface + Ce nœud → Sécurité', apply: 'docker compose up -d' },
      { id: 's-sessions', t: 'Sessions : se déconnecter partout', d: 'Changer son mot de passe, retirer sa 2FA ou une réinitialisation par un administrateur déconnecte les autres appareils ; le bouton « Me déconnecter partout ailleurs » (Mon compte) le fait à la demande. Utile si un appareil est perdu.' },
      { id: 's-webhook', t: 'Le webhook d\'alerte est chiffré en base', d: 'Les adresses de webhook Slack / Discord contiennent un jeton secret : elles sont chiffrées avec ENCRYPTION_KEY (32 caractères au moins) et ne sont jamais renvoyées au navigateur. Une ancienne adresse en clair est chiffrée au premier démarrage.' },
      { id: 's-2fa', t: '2FA sur les comptes d\'administration', d: 'Menu de votre compte. Sans elle, un mot de passe volé suffit. Gardez les codes de secours hors de la machine.' },
      { id: 's-env', t: 'Les .env sont protégés', d: 'chmod 600 et jamais dans git. Celui de Traefik contient les clés DNS, celui de l\'interface contient JWT_SECRET et ENCRYPTION_KEY.' },
      { id: 's-dash', t: 'Le dashboard et l\'API de Traefik ne sont pas exposés', d: 'api.insecure=true ouvre le port 8080 à tous : ne l\'utilisez pas. Le dashboard se publie par un routeur dédié (api@internal) derrière une chaîne privée ET une identification, comme dans l\'exemple.' },
      { id: 's-cred', t: 'Aucun mot de passe en clair dans les fichiers', d: 'Les hachages basicAuth sont acceptables dans traefik-dynamic ; une clé API ou un mot de passe ne le sont jamais. Attention à ce que vous collez dans un chat ou une capture d\'écran.' },
      { id: 's-rights', t: 'Droits au plus juste', d: 'Page Droits : rôles limités, accès temporaires, retraits quand ils ne servent plus. Tout est tracé dans l\'activité.' },
      { id: 's-admin', t: 'L\'interface est protégée comme un site sensible', d: 'Elle gère vos DNS et Traefik : uniquement en HTTPS (COOKIE_SECURE=true), idéalement derrière chain-private ou un VPN.' },
      { id: 's-pub', t: 'Un site public n\'est jamais « juste un test »', d: 'Dès qu\'un nom pointe vers votre IP publique avec un port ouvert, il est scanné en quelques minutes. Mettez au minimum chain-public (en-têtes + pages d\'erreur) et une identification.' },
    ],
  },
  {
    id: 'diag', label: 'Dépannage', icon: <Stethoscope size={15} />,
    intro: 'Un symptôme, sa cause probable, et où regarder.',
    items: [
      { id: 'x-default', t: 'Le navigateur affiche un certificat invalide (« TRAEFIK DEFAULT CERT »)', d: 'Aucun certificat n\'a été obtenu pour ce nom : le routeur n\'a pas de tls.certResolver, le nom du résolveur est faux, ou la validation a échoué. Regardez les journaux de Traefik.', code: CODE_TEST },
      { id: 'x-404', t: '404 « page not found » de Traefik', d: 'Aucun routeur ne correspond : faute dans Host(), mauvais entrypoint, fichier ignoré à cause d\'une erreur de syntaxe, ou conteneur absent pour le provider Docker.' },
      { id: 'x-502', t: '502 « Bad Gateway »', d: 'Traefik a trouvé le routeur mais n\'atteint pas l\'application : mauvais nom de conteneur, mauvais port (interne !), réseau Docker différent, ou application arrêtée.' },
      { id: 'x-403', t: '403 sur un site que vous pensez pouvoir ouvrir', d: 'La restriction d\'adresse vous refuse : votre IP (publique ou privée selon le trajet) n\'est pas dans ip-allow-lan. Voir « Protections » → hairpin NAT.' },
      { id: 'x-dnsfail', t: 'La validation DNS échoue', d: 'Clés OVH absentes du conteneur Traefik (il manque la section « environment »), droits insuffisants sur la clé, ou le DNS local masque les réponses publiques : ajoutez « --certificatesresolvers.NOM.acme.dnschallenge.resolvers=1.1.1.1:53 ».' },
      { id: 'x-httpfail', t: 'La validation HTTP échoue', d: 'Port 80 non redirigé, DNS qui ne pointe pas (encore) vers votre IP publique, IPv6 (AAAA) périmé, ou restriction d\'adresse sur le port 80.' },
      { id: 'x-nx', t: '« NXDOMAIN » dans les journaux à chaque renouvellement', d: 'Des noms supprimés du DNS restent dans acme.json. Sans conséquence ; à nettoyer si cela vous gêne.' },
      { id: 'x-conf', t: 'Mon changement de compose ou de .env n\'a rien fait', d: 'Un résolveur, un entrypoint ou une variable ne se prennent en compte qu\'après « docker compose up -d » (pas restart). Vérifiez avec « docker inspect traefik » que l\'argument ou la variable est bien présente.' },
      { id: 'x-stop', t: 'Tous les conteneurs se sont arrêtés en même temps', d: 'Le démon Docker lui-même s\'est arrêté (plantage, mise à jour, reboot). « journalctl -u docker » le dit. Activez live-restore et mettez Docker à jour.' },
      { id: 'x-nowrite', t: '« Dossier Traefik non modifiable »', d: 'L\'interface ou le worker n\'a pas le droit d\'écrire dans traefik-dynamic : voir « Sites et fichiers » (PUID/PGID).' },
      { id: 'x-nometrics', t: 'Surveillance : pas de données, ou « Prometheus injoignable »', d: 'Vérifiez dans l\'ordre : l\'adresse dans « Ce nœud » (bouton Tester), le réseau Docker commun avec Prometheus, que Prometheus lit bien Traefik (Status → Targets, « up »), et que Traefik a addEntryPointsLabels et addServicesLabels. Sans ces étiquettes, la vue d\'ensemble ou la page Sites restent vides.' },
      { id: 'x-noips', t: 'Adresses IP : liste vide', d: 'Le fichier TRAEFIK_ACCESS_LOG doit exister et être lisible par l\'interface (volume monté, droits). « Ce nœud » indique « lu en continu » ou « fichier introuvable ». Traefik doit écrire dans ce fichier (accesslog.filepath), pas sur sa sortie standard. Seules les nouvelles requêtes sont comptées : la liste se remplit avec le trafic.' },
      { id: 'x-certs-purge', t: 'Supprimer / Renouveler un certificat ne fait rien ou est grisé', d: 'La bannière de la page Certificats dit ce qui manque : TRAEFIK_CERTS_FILE absent, acme.json introuvable ou monté en lecture seule (retirez « :ro » du volume), ou socket Docker non monté. Vérifiez aussi le groupe : l\'interface doit être dans le groupe « docker » (DOCKER_GID dans le .env, valeur de getent group docker | cut -d: -f3) et le nom du conteneur de Traefik doit être exact (TRAEFIK_CONTAINER). Si Traefik n\'a pas redémarré, le résultat l\'indique : docker start traefik. Une sauvegarde acme.json.bak-DATE est toujours dans le dossier d\'acme.json.' },
      { id: 'x-proxyip', t: 'Toutes les adresses sont celles de mon proxy', d: 'Traefik est derrière un autre proxy (Cloudflare, Nginx…) : il voit le proxy. Déclarez-le dans les en-têtes de confiance de Traefik (forwardedHeaders.trustedIPs) pour que ClientHost soit l\'adresse du visiteur ; le bannissement en dépend aussi.' },
      { id: 'x-live', t: 'Un écran ne se met pas à jour', d: 'Vérifiez le point de connexion en bas du menu : s\'il est rouge, le temps réel est coupé (proxy sans WebSocket, session expirée). Rechargez la page.' },
    ],
  },
]

const KEY = 'help-checked'
const KEY_START = 'help-start'
const load = (k = KEY) => { try { return JSON.parse(localStorage.getItem(k) || '{}') } catch { return {} } }
const save = (v, k = KEY) => { try { localStorage.setItem(k, JSON.stringify(v)) } catch { /* navigation privée : on oublie simplement */ } }

// Recherche sans accents ni majuscules : tous les mots doivent se retrouver (titre, explication, exemple, piège…).
const norm = (x) => x.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
const flat = (s, i) => norm([s.label, i.t, i.d, i.code, i.where, i.apply, i.warn].filter(Boolean).join(' '))
const restOf = (t) => { const f = firstSentence(t); return f.endsWith('…') ? t : t.slice(f.length).trim() }
const firstSentence = (t) => { const m = /^.*?[.!?](?=\s|$)/.exec(t || ''); const x = (m ? m[0] : t || ''); return x.length > 150 ? x.slice(0, 147) + '…' : x }

// Ce que cette personne peut utiliser : un lien vers une page qu'elle ne verrait pas ne lui servirait à rien.
function useAvail() {
  const { user } = useAuth(); const { hasTraefik, canNode, canRights, can } = useNode(); const f = useFeatures()
  const mon = !!f && ((f.metrics.enabled && f.metrics.allowed) || (f.alerts.enabled && f.alerts.allowed) || (f.ips.enabled && f.ips.allowed) || (f.bans.enabled && f.bans.allowed))
  const ok = { traefik: hasTraefik, accounts: !!user.canAddDomains || can('node.domains.link'), monitor: mon, admin: user.role === 'admin', rights: canRights, node: canNode }
  return (need) => !need || !!ok[need]
}

// Bouton d'aide qui mène quelque part : une page, l'assistant de site, ou « Mon compte ».
function GoBtn({ to, action, label, avail, kind }) {
  const go = useGo()
  if (!avail || (!to && !action)) return null
  const run = () => (action === 'new-site' ? window.dispatchEvent(new Event('new-site')) : action === 'profile' ? window.dispatchEvent(new Event('open-profile')) : go(to))
  return <Btn small kind={kind} onClick={run}>{label || 'Ouvrir'} <ArrowRight size={13} /></Btn>
}

// Un point de l'aide : titre et première phrase toujours visibles, le reste se déplie.
function Acc({ it, done, onToggle, open }) {
  return (
    <details className={'help-acc ' + (done !== undefined && done ? 'done' : '')} open={open}>
      <summary>
        {onToggle && <button type="button" className="help-check" role="checkbox" aria-checked={!!done} aria-label={`Marquer « ${it.t} » comme fait`} title="Marquer comme fait (retenu dans ce navigateur)" onClick={(e) => { e.preventDefault(); e.stopPropagation(); onToggle() }}>{done && <Check size={13} />}</button>}
        <span className="help-acc-t"><b>{it.t}</b><span className="muted small">{firstSentence(it.d)}</span></span>
        <ChevronDown size={16} className="chev" />
      </summary>
      <div className="help-acc-body">
        {restOf(it.d) && <div>{restOf(it.d)}</div>}
        {(it.where || it.apply) && <div className="help-tags">{it.where && <span><i>Où</i> {it.where}</span>}{it.apply && <span><i>Appliquer</i> {it.apply}</span>}</div>}
        {it.warn && <div className="help-warn"><b>Piège</b> {it.warn}</div>}
        {it.code && <div className="help-code"><CopyBtn text={it.code} /><pre className="mono">{it.code}</pre></div>}
        {it.extra}
      </div>
    </details>
  )
}

function Table({ t }) {
  return (
    <div className="help-table"><table>
      <thead><tr>{t.head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>{t.rows.map((r) => <tr key={r[0]}>{r.map((c, i) => <td key={i}>{c}</td>)}</tr>)}</tbody>
    </table></div>
  )
}

const Title = ({ icon, children, sub }) => <div className="help-title">{icon}<div><h2>{children}</h2>{sub && <p className="muted">{sub}</p>}</div></div>

// ------------------------------------------------------------------ pages
function Home({ setTab, progress, startDone, startTotal }) {
  const TILES = [
    ['start', Rocket, 'Mettre un site en ligne', 'Le parcours pas à pas, du DNS au certificat.'],
    ['guide', BookOpen, 'Découvrir chaque page', 'À quoi sert chaque écran, et ce qu\'on y fait.'],
    ['diag', Stethoscope, 'Régler un problème', 'Site injoignable, certificat, DNS : où regarder.'],
    ['options', ShieldCheck, 'Surveiller et bloquer', 'Alertes, adresses IP, bannissement, automod.'],
    ['keys', Keyboard, 'Aller plus vite', 'Raccourcis clavier et astuces.'],
    ['glossary', BookMarked, 'Comprendre un mot', 'Routeur, middleware, DNS… en clair.'],
    ['faq', HelpCircle, 'Questions fréquentes', 'Les réponses courtes aux questions classiques.'],
    ['vue', Wrench, 'Installer et configurer', 'Le détail technique de l\'installation.'],
  ]
  return (
    <div className="col gap-lg">
      <div className="help-hero">
        <h2>Bienvenue ! Que voulez-vous faire ?</h2>
        <p className="muted">Cette aide est faite pour débuter : commencez par le parcours, ou choisissez ce qui vous intéresse. Vous pouvez aussi taper un mot dans la recherche, tout en haut.</p>
        <div className="row wrap" style={{ gap: 10 }}>
          <Btn kind="primary" onClick={() => setTab('start')}><Rocket size={14} /> {startDone ? 'Reprendre le parcours' : 'Commencer le parcours'}</Btn>
          <span className="muted small">{startDone}/{startTotal} étape{startTotal > 1 ? 's' : ''} faite{startDone > 1 ? 's' : ''}</span>
        </div>
      </div>
      <div className="help-tiles">{TILES.map(([id, Icon, t, d]) => (
        <button type="button" key={id} className="help-tile" onClick={() => setTab(id)}><span className="help-tile-ic"><Icon size={18} /></span><b>{t}</b><span className="muted small">{d}</span></button>))}</div>
      <div className="card row wrap" style={{ gap: 12 }}><Keyboard size={18} className="muted" /><div className="grow"><b>Astuce :</b> <Keys item={{ keys: ['Ctrl', 'K'], plus: true, alt: ['/'] }} /> ouvre la recherche partout dans l'interface ; <Keys item={{ keys: ['?'] }} /> affiche tous les raccourcis.</div>
        <Btn small kind="ghost" onClick={() => setTab('keys')}>Tous les raccourcis</Btn></div>
      {progress.total > 0 && <div className="muted small">Pense-bête de configuration : {progress.done} point{progress.done > 1 ? 's' : ''} coché{progress.done > 1 ? 's' : ''} sur {progress.total} (vos cases restent dans ce navigateur).</div>}
    </div>
  )
}

function Start({ avail, setTab }) {
  const [done, setDone] = useState(() => load(KEY_START))
  const toggle = (id) => setDone((c) => { const n = { ...c, [id]: !c[id] }; if (!n[id]) delete n[id]; save(n, KEY_START); return n })
  const steps = START.filter((x) => avail(x.need))
  const n = steps.filter((x) => done[x.id]).length
  return (
    <div className="col gap-lg">
      <Title icon={<Rocket size={20} />} sub="Dans cet ordre, c'est le chemin le plus court pour mettre un premier site en ligne et le garder en sécurité. Cochez chaque étape une fois faite.">Pour bien démarrer</Title>
      <div className="help-progress"><div style={{ width: `${steps.length ? (n / steps.length) * 100 : 0}%` }} /></div>
      <div className="muted small">{n} étape{n > 1 ? 's' : ''} sur {steps.length}</div>
      <div className="col" style={{ gap: 10 }}>{steps.map((x, i) => (
        <div key={x.id} className={'help-step ' + (done[x.id] ? 'done' : '')}>
          <button type="button" className="help-num" role="checkbox" aria-checked={!!done[x.id]} title="Marquer comme fait" onClick={() => toggle(x.id)}>{done[x.id] ? <Check size={15} /> : i + 1}</button>
          <div className="col" style={{ gap: 6, minWidth: 0, flex: 1 }}>
            <b>{x.t}</b><div className="muted">{x.d}</div>
            {x.more && <details className="help-more"><summary>En savoir plus</summary><div className="muted">{x.more}</div></details>}
            <div><GoBtn to={x.to} action={x.action} label={x.label} avail kind="primary" /></div>
          </div>
        </div>))}</div>
      {n === steps.length && steps.length > 0 && <div className="banner ok"><Check size={16} /> Parcours terminé. Pour aller plus loin : <button type="button" className="linklike" onClick={() => setTab('guide')}>découvrir chaque page</button> ou <button type="button" className="linklike" onClick={() => setTab('options')}>activer la surveillance</button>.</div>}
    </div>
  )
}

function Guide({ avail, open }) {
  return (
    <div className="col gap-lg">
      <Title icon={<BookOpen size={20} />} sub="Une fiche par page de l'interface : à quoi elle sert et ce qu'on y fait. Dépliez celle qui vous intéresse.">Découvrir l'interface</Title>
      <div className="col" style={{ gap: 10 }}>{GUIDE.filter((g) => avail(g.need) || !g.need).map((g) => { const Icon = g.icon; return (
        <details key={g.id} className="help-acc" open={open}>
          <summary><span className="help-tile-ic"><Icon size={16} /></span><span className="help-acc-t"><b>{g.t}</b><span className="muted small">{g.d}</span></span><ChevronDown size={16} className="chev" /></summary>
          <div className="help-acc-body">
            <ul className="help-list">{g.points.map((p) => <li key={p}>{p}</li>)}</ul>
            {g.tip && <div className="help-tip"><b>Astuce</b> {g.tip}</div>}
            <div><GoBtn to={g.to} action={g.action} label={g.action === 'profile' ? 'Ouvrir « Mon compte »' : `Ouvrir « ${g.t} »`} avail /></div>
          </div>
        </details>) })}</div>
    </div>
  )
}

function KeysPage() {
  return (
    <div className="col gap-lg">
      <Title icon={<Keyboard size={20} />} sub="Pour aller plus vite quand on est à l'aise. La liste complète est aussi disponible partout avec la touche ?.">Raccourcis clavier et astuces</Title>
      <div className="grid two">{SHORTCUTS.map((g) => (
        <div key={g.group} className="card col" style={{ gap: 10 }}><h3>{g.group}</h3>
          {g.items.map((it) => <div key={it.desc} className="help-key-row"><span className="help-key-k"><Keys item={it} /></span><span>{it.desc}</span></div>)}</div>))}</div>
      <div className="muted small">{SHORTCUT_NOTE} « g puis s » veut dire : appuyez sur g, relâchez, puis sur s.</div>
      <h3>À la souris</h3>
      <div className="col" style={{ gap: 8 }}>{TRICKS.map(([t, d]) => <div key={t} className="help-key-row"><b style={{ minWidth: 220 }}>{t}</b><span className="muted">{d}</span></div>)}</div>
    </div>
  )
}

function Glossary() {
  const sorted = [...GLOSSARY].sort((a, b) => a[0].localeCompare(b[0], 'fr'))
  return (
    <div className="col gap-lg">
      <Title icon={<BookMarked size={20} />} sub="Les mots qu'on croise dans l'interface, expliqués sans jargon.">Lexique</Title>
      <div className="help-gloss">{sorted.map(([t, d]) => <div key={t} className="card col" style={{ gap: 4 }}><b>{t}</b><span className="muted">{d}</span></div>)}</div>
    </div>
  )
}

function Faq({ setTab, open, items = FAQ, bare }) {
  return (
    <div className="col gap-lg">
      {!bare && <Title icon={<HelpCircle size={20} />} sub="Les questions qui reviennent le plus souvent, avec la réponse courte.">Questions fréquentes</Title>}
      <div className="col" style={{ gap: 10 }}>{items.map((f) => (
        <details key={f.q} className="help-acc" open={open}>
          <summary><span className="help-acc-t"><b>{f.q}</b></span><ChevronDown size={16} className="chev" /></summary>
          <div className="help-acc-body"><div>{f.a}</div>
            {f.code && <div className="help-code"><CopyBtn text={f.code} /><pre className="mono">{f.code}</pre></div>}
            {f.link && <div><Btn small kind="ghost" onClick={() => setTab(f.link[0])}>{f.link[1]} <ArrowRight size={13} /></Btn></div>}
          </div>
        </details>))}</div>
    </div>
  )
}

// ------------------------------------------------------------------ page
const FIXED = {
  home: ['Accueil de l\'aide', <HomeIcon size={15} />], start: ['Pour bien démarrer', <Rocket size={15} />], guide: ['Découvrir l\'interface', <BookOpen size={15} />], keys: ['Raccourcis et astuces', <Keyboard size={15} />],
  glossary: ['Lexique', <BookMarked size={15} />], faq: ['Questions fréquentes', <HelpCircle size={15} />], builder: ['Constructeur de piles', <Hammer size={15} />],
}
const GROUPS = [
  ['Pour commencer', ['home', 'start', 'guide', 'keys']],
  ['Comprendre', ['glossary', 'faq', 'diag']],
  ['Installer et configurer', ['builder', 'vue', 'options', 'traefik', 'certs', 'sites', 'protect', 'local', 'dns', 'docker', 'armadok', 'secu']],
]
const metaOf = (id) => FIXED[id] || (() => { const x = SECTIONS.find((s) => s.id === id); return x ? [x.label, x.icon] : ['', null] })()

export default function Help() {
  const [params, setParams] = useSearchParams()
  const avail = useAvail()
  const valid = (t) => FIXED[t] || SECTIONS.some((x) => x.id === t)
  const tab = valid(params.get('tab')) ? params.get('tab') : 'home'
  const setTab = (t) => { setParams({ tab: t }); document.querySelector('.main')?.scrollTo?.({ top: 0 }) }
  const [checked, setChecked] = useState(() => load())
  const [q, setQ] = useState(params.get('q') || '')
  const toggle = (id) => setChecked((c) => { const n = { ...c, [id]: !c[id] }; if (!n[id]) delete n[id]; save(n); return n })
  const count = (s) => s.items.filter((i) => checked[i.id]).length
  const progress = { done: SECTIONS.reduce((n, s) => n + count(s), 0), total: SECTIONS.reduce((n, s) => n + s.items.length, 0) }
  const startTotal = START.filter((x) => avail(x.need)).length; const startDone = START.filter((x) => avail(x.need) && load(KEY_START)[x.id]).length
  const words = norm(q).split(/\s+/).filter(Boolean)
  const searching = words.length > 0
  const has = (text) => words.every((w) => norm(text).includes(w))
  const found = searching ? SECTIONS.map((s) => ({ s, items: s.items.filter((i) => words.every((w) => flat(s, i).includes(w))) })).filter((g) => g.items.length) : []
  const fGuide = searching ? GUIDE.filter((g) => has([g.t, g.d, ...g.points, g.tip].join(' '))) : []
  const fGloss = searching ? GLOSSARY.filter(([t, d]) => has(t + ' ' + d)) : []
  const fFaq = searching ? FAQ.filter((f) => has(f.q + ' ' + f.a)) : []
  const total = found.reduce((n, g) => n + g.items.length, 0) + fGuide.length + fGloss.length + fFaq.length
  const sec = SECTIONS.find((s) => s.id === tab)
  const list = (items) => <div className="col" style={{ gap: 10 }}>{items.map((it) => <Acc key={it.id + (searching ? 's' : '')} it={it} done={!!checked[it.id]} onToggle={() => toggle(it.id)} open={searching} />)}</div>
  const navBtn = (id) => { const [label, icon] = metaOf(id); const s = SECTIONS.find((x) => x.id === id); return (
    <button type="button" key={id} className={'help-nav-item ' + (tab === id && !searching ? 'active' : '')} onClick={() => { setQ(''); setTab(id) }}>{icon}<span className="grow">{label}</span>{s && <span className="help-nav-n">{count(s)}/{s.items.length}</span>}</button>) }
  return (
    <>
      <div className="page-head"><div><h1><LifeBuoy size={20} style={{ verticalAlign: -3 }} /> Aide</h1><p>Pour débuter, comprendre l'interface, et configurer l'installation.</p></div>
        <div className="right" style={{ minWidth: 240, flex: '0 1 340px' }}><SearchBox value={q} onChange={setQ} placeholder="Rechercher : certificat, ban, DNS, 2FA…" /></div></div>
      <div className="help-layout">
        <nav className="help-nav" aria-label="Sommaire de l'aide">
          {GROUPS.map(([g, ids]) => <div key={g} className="col" style={{ gap: 2 }}><div className="help-nav-g">{g}</div>{ids.map(navBtn)}</div>)}
        </nav>
        <select className="select help-nav-sel" value={tab} onChange={(e) => { setQ(''); setTab(e.target.value) }} aria-label="Rubrique de l'aide">
          {GROUPS.map(([g, ids]) => <optgroup key={g} label={g}>{ids.map((id) => <option key={id} value={id}>{metaOf(id)[0]}</option>)}</optgroup>)}
        </select>
        <div className="help-main">
          {searching ? (
            <div className="col gap-lg">
              <div className="muted">{total ? `${total} résultat${total > 1 ? 's' : ''} pour « ${q.trim()} »` : ''}</div>
              {total === 0 && <div className="card"><div className="empty"><SearchX size={30} /><div>Rien ne correspond à « {q.trim()} ». Essayez un mot plus court (certificat, port, DNS…).</div><Btn style={{ marginTop: 10 }} onClick={() => setQ('')}>Effacer la recherche</Btn></div></div>}
              {fFaq.length > 0 && <div className="col" style={{ gap: 8 }}><span className="help-sec"><HelpCircle size={14} /> Questions fréquentes</span><Faq items={fFaq} bare setTab={(t) => { setQ(''); setTab(t) }} open /></div>}
              {fGuide.length > 0 && <div className="col" style={{ gap: 8 }}><span className="help-sec"><BookOpen size={14} /> Pages de l'interface</span>
                <div className="col" style={{ gap: 10 }}>{fGuide.map((g) => { const Icon = g.icon; return (<details key={g.id} className="help-acc" open><summary><span className="help-tile-ic"><Icon size={16} /></span><span className="help-acc-t"><b>{g.t}</b><span className="muted small">{g.d}</span></span><ChevronDown size={16} className="chev" /></summary>
                  <div className="help-acc-body"><ul className="help-list">{g.points.map((p) => <li key={p}>{p}</li>)}</ul>{g.tip && <div className="help-tip"><b>Astuce</b> {g.tip}</div>}<div><GoBtn to={g.to} action={g.action} label={`Ouvrir « ${g.t} »`} avail={avail(g.need)} /></div></div></details>) })}</div></div>}
              {fGloss.length > 0 && <div className="col" style={{ gap: 8 }}><span className="help-sec"><BookMarked size={14} /> Lexique</span><div className="help-gloss">{fGloss.map(([t, d]) => <div key={t} className="card col" style={{ gap: 4 }}><b>{t}</b><span className="muted">{d}</span></div>)}</div></div>}
              {found.map((g) => (
                <div key={g.s.id} className="col" style={{ gap: 8 }}>
                  <button type="button" className="help-sec" onClick={() => { setTab(g.s.id); setQ('') }} data-tip="Ouvrir cette catégorie">{g.s.icon}{g.s.label}</button>
                  {list(g.items)}
                </div>))}
            </div>
          ) : tab === 'home' ? <Home setTab={setTab} progress={progress} startDone={startDone} startTotal={startTotal} />
            : tab === 'start' ? <Start avail={avail} setTab={setTab} />
            : tab === 'guide' ? <Guide avail={avail} />
            : tab === 'keys' ? <KeysPage />
            : tab === 'glossary' ? <Glossary />
            : tab === 'faq' ? <Faq setTab={setTab} />
            : tab === 'builder' ? <div className="col gap-lg"><Title icon={<Hammer size={20} />} sub="Cochez les fonctions voulues : le compose et le .env de Traefik et de cette interface sont prêts à poser.">Constructeur de piles</Title><ComposeBuilder embedded /></div>
            : sec && (
              <div className="col gap-lg">
                <Title icon={sec.icon} sub={sec.intro}>{sec.label} <Badge>{count(sec)}/{sec.items.length}</Badge></Title>
                {sec.table && <Table t={sec.table} />}
                {list(sec.items)}
              </div>)}
        </div>
      </div>
    </>
  )
}
