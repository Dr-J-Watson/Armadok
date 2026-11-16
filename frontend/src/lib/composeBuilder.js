// Constructeur de piles Docker : à partir de cases cochées, produit les fichiers de Traefik (compose, .env, configuration dynamique,
// Prometheus) et ceux de cette interface (compose, .env). Pur texte : rien n'est envoyé au serveur, les secrets sont tirés dans le navigateur.

export const DNS_PROVIDERS = {
  ovh: { label: 'OVH', provider: 'ovh', env: ['OVH_ENDPOINT=ovh-eu', 'OVH_APPLICATION_KEY', 'OVH_APPLICATION_SECRET', 'OVH_CONSUMER_KEY'], help: 'Clé créée sur api.ovh.com/createToken (GET, POST, PUT, DELETE sur /domain/zone/*).' },
  duckdns: { label: 'DuckDNS', provider: 'duckdns', env: ['DUCKDNS_TOKEN'], help: 'Jeton affiché sur duckdns.org. Un seul TXT à la fois par nom : pas de certificat joker et simple en même temps.' },
  cloudflare: { label: 'Cloudflare', provider: 'cloudflare', env: ['CF_DNS_API_TOKEN'], help: 'Jeton d\'API avec les droits « Zone : DNS : Modifier » et « Zone : Zone : Lire ».' },
}

export const DEFAULTS = {
  // Général
  webuiHost: 'armadok.exemple.fr', email: 'vous@exemple.fr', network: 'proxy', uid: '1000', gid: '1000', traefikImage: 'traefik:v3.7',
  // Traefik
  http3: true, dockerProvider: false, dockerGid: '', apiInternal: true, dashboard: false, dashboardHost: 'traefik.exemple.fr', logLevel: 'INFO',
  // Certificats
  tlsHttp: true, httpName: 'lehttp', tlsDns: false, dnsName: 'lednsovh', dnsProvider: 'ovh', staging: false,
  // Protection
  chainPrivate: true, lan: '10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16', extraIps: '',
  // Surveillance
  metrics: true, routersLabels: false, prometheus: true, prometheusUrlSeed: true, accessLog: true,
  // Cette interface
  webuiImage: 'ghcr.io/dr-j-watson/armadok:latest', certsRelay: true, dnsTarget: '', hashPlaceholder: '',
}

const csv = (s) => (s || '').split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean)
const q = (s) => `"${s}"`
export const randomHex = (bytes = 32) => { const a = new Uint8Array(bytes); crypto.getRandomValues(a); return [...a].map((b) => b.toString(16).padStart(2, '0')).join('') }

export function resolverNames(c) { return [...(c.tlsHttp ? [c.httpName] : []), ...(c.tlsDns ? [c.dnsName] : [])] }
// Résolveur des sites : le DNS si on l'a (il marche aussi pour les sites locaux), sinon le HTTP.
const mainResolver = (c) => (c.tlsDns ? c.dnsName : c.tlsHttp ? c.httpName : '')

// ------------------------------------------------------------------ Traefik : compose
export function traefikCompose(c) {
  const dns = c.tlsDns ? DNS_PROVIDERS[c.dnsProvider] : null
  const cmd = []
  const add = (...l) => cmd.push(...l.map((x) => `      - ${q(x)}`))
  const sec = (t) => cmd.push(`\n      # ${t}`)
  sec('Journal et tableau de bord'); add(`--log.level=${c.logLevel}`)
  if (c.dashboard) add('--api.dashboard=true')
  if (c.apiInternal) add('--api.insecure=true')   // port 8080 interne : jamais publié plus bas
  if (c.accessLog) { sec('Journal d\'accès (lu par cette interface pour lister les adresses IP)'); add('--accesslog=true', '--accesslog.filepath=/logs/access.log', '--accesslog.format=json', '--accesslog.fields.headers.names.User-Agent=keep') }
  if (c.metrics) { sec('Métriques Prometheus (servies sur l\'entrypoint interne « traefik » :8080, non publié)'); add('--metrics.prometheus=true', '--metrics.prometheus.addEntryPointsLabels=true', '--metrics.prometheus.addServicesLabels=true'); if (c.routersLabels) add('--metrics.prometheus.addRoutersLabels=true') }
  sec('Sources de configuration : fichiers (surveillés)' + (c.dockerProvider ? ' et conteneurs Docker' : '')); add('--providers.file.directory=/etc/traefik/dynamic', '--providers.file.watch=true')
  if (c.dockerProvider) add('--providers.docker=true', '--providers.docker.exposedbydefault=false', '--providers.docker.endpoint=unix:///var/run/docker.sock')
  sec('Points d\'entrée'); add('--entrypoints.web.address=:80', '--entrypoints.web.http.redirections.entrypoint.to=websecure', '--entrypoints.web.http.redirections.entrypoint.scheme=https', '--entrypoints.websecure.address=:443', '--entrypoints.websecure.http.tls=true')
  if (c.http3) add('--entrypoints.websecure.http3=true')
  const ca = c.staging ? ['acme.caserver=https://acme-staging-v02.api.letsencrypt.org/directory'] : []
  if (c.tlsHttp) { sec(`Certificats Let's Encrypt, validation HTTP (sites publics) : résolveur « ${c.httpName} »`); add(`--certificatesresolvers.${c.httpName}.acme.email=${c.email}`, `--certificatesresolvers.${c.httpName}.acme.storage=/letsencrypt/acme.json`, `--certificatesresolvers.${c.httpName}.acme.httpchallenge=true`, `--certificatesresolvers.${c.httpName}.acme.httpchallenge.entrypoint=web`, ...ca.map((x) => `--certificatesresolvers.${c.httpName}.${x}`)) }
  if (c.tlsDns) { sec(`Certificats Let's Encrypt, validation DNS ${dns.label} (sites locaux, aucun port à ouvrir) : résolveur « ${c.dnsName} »`); add(`--certificatesresolvers.${c.dnsName}.acme.email=${c.email}`, `--certificatesresolvers.${c.dnsName}.acme.storage=/letsencrypt/acme.json`, `--certificatesresolvers.${c.dnsName}.acme.dnschallenge=true`, `--certificatesresolvers.${c.dnsName}.acme.dnschallenge.provider=${dns.provider}`, ...ca.map((x) => `--certificatesresolvers.${c.dnsName}.${x}`)) }
  const ports = ['      - "80:80/tcp"', '      - "443:443/tcp"', ...(c.http3 ? ['      - "443:443/udp"        # HTTP/3 (QUIC)'] : [])]
  const vols = ['      - "./traefik-dynamic:/etc/traefik/dynamic"', ...((c.tlsHttp || c.tlsDns) ? ['      - "./traefik_letsencrypt:/letsencrypt"'] : []), ...(c.accessLog ? ['      - "./logs:/logs"'] : []), ...(c.dockerProvider ? ['      - "/var/run/docker.sock:/var/run/docker.sock:ro"'] : [])]
  const env = dns ? ['    environment:', ...dns.env.map((e) => { const k = e.split('=')[0]; return `      - ${k}=\${${k}}` })] : []
  const prom = c.metrics && c.prometheus ? `
  prometheus:
    image: prom/prometheus:v2.55.1
    container_name: prometheus
    restart: unless-stopped
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.retention.time=15d"
    volumes:
      - "./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro"
      - prometheus_data:/prometheus
    networks: [proxy]
` : ''
  return `# Pile Traefik — générée par le constructeur de cette interface.
# Dossier conseillé : traefik/   (à côté : traefik-dynamic/, traefik_letsencrypt/${c.accessLog ? ', logs/' : ''}${c.metrics && c.prometheus ? ', prometheus/' : ''})
services:
  traefik:
    image: ${c.traefikImage}
    container_name: traefik
    restart: unless-stopped
    user: "\${UID}:\${GID}"${c.dockerProvider ? '\n    group_add:\n      - "${DOCKER_GID}"' : ''}
${env.join('\n')}${env.length ? '\n' : ''}    command:
${cmd.join('\n').replace(/^\n/, '')}
    ports:
${ports.join('\n')}
    volumes:
${vols.join('\n')}
    networks: [proxy]
${prom}
networks:
  proxy:
    external: true
    name: \${TRAEFIK_NETWORK:-${c.network}}
${c.metrics && c.prometheus ? '\nvolumes:\n  prometheus_data:\n' : ''}`
}

export function traefikEnv(c) {
  const dns = c.tlsDns ? DNS_PROVIDERS[c.dnsProvider] : null
  return `# .env de la pile Traefik (chmod 600)
UID=${c.uid}
GID=${c.gid}
${c.dockerProvider ? `# getent group docker | cut -d: -f3\nDOCKER_GID=${c.dockerGid || '999'}\n` : ''}TRAEFIK_NETWORK=${c.network}
${dns ? `\n# Clés du fournisseur DNS (${dns.label}) : celles de TRAEFIK, distinctes de celles de l'interface\n# ${dns.help}\n${dns.env.map((e) => (e.includes('=') ? e : `${e}=`)).join('\n')}\n` : ''}`
}

// ------------------------------------------------------------------ Traefik : configuration dynamique
export function dynamicFiles(c) {
  const files = {}
  const res = mainResolver(c)
  const ranges = [...csv(c.lan), ...csv(c.extraIps)]
  if (c.chainPrivate) {
    files['traefik-dynamic/middlewares/ip-allow-lan.yml'] = `http:
  middlewares:
    ip-allow-lan:
      ipAllowList:
        sourceRange:
${ranges.map((r) => `          - ${r}`).join('\n')}
`
    files['traefik-dynamic/middlewares/headers-security.yml'] = `http:
  middlewares:
    headers-security:
      headers:
        stsSeconds: 31536000
        contentTypeNosniff: true
        referrerPolicy: strict-origin-when-cross-origin
`
    files['traefik-dynamic/middlewares/chain-private.yml'] = `# Pour tout ce qui reste chez vous : en-têtes de sécurité, puis restriction d'adresse
http:
  middlewares:
    chain-private:
      chain:
        middlewares:
          - headers-security
          - ip-allow-lan
`
  }
  files['traefik-dynamic/apps/armadok.yml'] = `# Cette interface, derrière Traefik
http:
  routers:
    armadok:
      rule: "Host(\`${c.webuiHost}\`)"
      entryPoints:
        - websecure
      service: armadok
${res ? `      tls:\n        certResolver: ${res}\n` : '      tls: {}\n'}${c.chainPrivate ? '      middlewares:\n        - chain-private\n' : ''}  services:
    armadok:
      loadBalancer:
        servers:
          - url: "http://armadok:8000"
`
  if (c.dashboard) files['traefik-dynamic/apps/traefik-dashboard.yml'] = `# Tableau de bord de Traefik
http:
  routers:
    traefik:
      rule: "Host(\`${c.dashboardHost}\`)"
      entryPoints:
        - websecure
      service: "api@internal"
${res ? `      tls:\n        certResolver: ${res}\n` : '      tls: {}\n'}${c.chainPrivate ? '      middlewares:\n        - chain-private\n' : '      # ATTENTION : sans chaîne privée, ajoutez une identification (basicAuth) : ce tableau de bord montre toute votre configuration.\n'}`
  if (c.metrics && c.prometheus) files['prometheus/prometheus.yml'] = `global:
  scrape_interval: 15s
scrape_configs:
  - job_name: traefik
    static_configs:
      - targets: ["traefik:8080"]      # l'entrypoint interne « traefik » (non publié)
`
  return files
}

// ------------------------------------------------------------------ Cette interface
export function webuiCompose(c) {
  const logs = c.accessLog
  const certs = c.certsRelay && (c.tlsHttp || c.tlsDns)
  return `# Pile de cette interface — générée par le constructeur.
# Dossier conseillé : armadok/   (à côté de traefik/)
services:
  armadok:
    image: ${c.webuiImage}
    container_name: armadok
    restart: unless-stopped
    user: "\${UID:-${c.uid}}:\${GID:-${c.gid}}"          # doit pouvoir écrire dans le dossier dynamique de Traefik
    env_file: .env
    environment:
      TRAEFIK_DYNAMIC_DIR: /traefik-dynamic
      DATA_DIR: /data${certs ? '\n      TRAEFIK_CERTS_FILE: /traefik-certs/acme.json     # certificats : lus (noms et dates seulement) et retirés à la demande\n      TRAEFIK_CONTAINER: traefik                         # relancé quand on supprime / renouvelle un certificat' : ''}${logs ? '\n      TRAEFIK_ACCESS_LOG: /traefik-logs/access.log' : ''}
    volumes:
      - \${TRAEFIK_DYNAMIC_PATH}:/traefik-dynamic     # fichiers dynamiques de Traefik (écriture)
      - ./data:/data                                 # base (comptes, clés chiffrées), sauvegardes, audit${logs ? '\n      - ${TRAEFIK_ACCESS_LOG_PATH}:/traefik-logs:ro        # journal d\'accès de Traefik (lecture seule)' : ''}${certs ? '\n      - ${CERTS_PATH}:/traefik-certs                   # le dossier qui contient acme.json (écriture : retrait d\'un certificat)\n      - /var/run/docker.sock:/var/run/docker.sock      # pour arrêter puis relancer Traefik. À retirer si vous ne voulez pas supprimer / renouveler' : ''}
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]${certs ? '\n    group_add: ["${DOCKER_GID}"]                       # groupe « docker » (getent group docker | cut -d: -f3) : droit d\'utiliser le socket' : ''}
    networks: [proxy]

networks:
  proxy:
    external: true
    name: \${TRAEFIK_NETWORK:-${c.network}}
`
}

export function webuiEnv(c, secrets) {
  const eps = ['web', 'websecure']
  const dns = c.tlsDns ? DNS_PROVIDERS[c.dnsProvider] : null
  return `# .env de cette interface (chmod 600)
# --- Authentification ----------------------------------------------------------------
ADMIN_USER=admin
# Hachage du mot de passe (GUILLEMETS SIMPLES obligatoires) :
#   docker run --rm ${c.webuiImage} python -m app.tools.hash_password
ADMIN_PASSWORD_HASH=${c.hashPlaceholder ? `'${c.hashPlaceholder}'` : ''}
# Tirés au hasard dans votre navigateur : gardez-les (ENCRYPTION_KEY chiffre les clés enregistrées ; la perdre les rend illisibles)
JWT_SECRET=${secrets.jwt}
ENCRYPTION_KEY=${secrets.enc}

# --- Chemins (toujours relus) --------------------------------------------------------
UID=${c.uid}
GID=${c.gid}
TRAEFIK_DYNAMIC_PATH=../traefik/traefik-dynamic
TRAEFIK_NETWORK=${c.network}
${c.certsRelay && (c.tlsHttp || c.tlsDns) ? 'CERTS_PATH=../traefik/traefik_letsencrypt\nDOCKER_GID=999   # à remplacer : getent group docker | cut -d: -f3\n' : ''}${c.accessLog ? 'TRAEFIK_ACCESS_LOG_PATH=../traefik/logs\n' : ''}
# --- VALEURS DE DÉPART du nœud Principal (lues une seule fois ; ensuite : page « Ce nœud ») ----
TRAEFIK_ENTRYPOINTS=${eps.join(',')}
TRAEFIK_CERT_RESOLVERS=${resolverNames(c).join(',')}
${c.apiInternal ? 'TRAEFIK_API_URL=http://traefik:8080\n' : 'TRAEFIK_API_URL=\n'}${c.metrics && c.prometheus && c.prometheusUrlSeed ? 'PROMETHEUS_URL=http://prometheus:9090\n' : 'PROMETHEUS_URL=\n'}DEFAULT_DNS_TARGET=${c.dnsTarget}
${dns ? `\n# Facultatif : compte ${dns.label} de l'interface (sinon, ajoutez-le ensuite dans « Comptes DNS »)\n${c.dnsProvider === 'ovh' ? 'OVH_ENDPOINT=ovh-eu\nOVH_APPLICATION_KEY=\nOVH_APPLICATION_SECRET=\nOVH_CONSUMER_KEY=\n' : '# ajoutez-le depuis « Comptes DNS »\n'}` : ''}`
}

// ------------------------------------------------------------------ Étapes (commandes à lancer)
export function steps(c) {
  const dirs = ['traefik/traefik-dynamic/apps', ...(c.chainPrivate ? ['traefik/traefik-dynamic/middlewares'] : []), ...((c.tlsHttp || c.tlsDns) ? ['traefik/traefik_letsencrypt'] : []), ...(c.accessLog ? ['traefik/logs'] : []), ...(c.metrics && c.prometheus ? ['traefik/prometheus'] : []), 'armadok/data']
  return `# 1. Les dossiers et le réseau partagé (une seule fois)
mkdir -p ${dirs.join(' ')}
docker network create ${c.network}
${(c.tlsHttp || c.tlsDns) ? `\n# 2. Le fichier des certificats : à votre UID, lisible par vous seul (Traefik refuse un acme.json trop ouvert)
touch traefik/traefik_letsencrypt/acme.json && chmod 600 traefik/traefik_letsencrypt/acme.json\n` : ''}
# ${(c.tlsHttp || c.tlsDns) ? '3' : '2'}. Posez les fichiers générés (onglets ci-dessus) aux bons endroits, puis
${c.dockerProvider ? '#    renseignez DOCKER_GID dans traefik/.env :  getent group docker | cut -d: -f3\n' : ''}#    générez le hachage du mot de passe et collez-le dans armadok/.env (ADMIN_PASSWORD_HASH) :
docker run --rm ${c.webuiImage} python -m app.tools.hash_password

# ${(c.tlsHttp || c.tlsDns) ? '4' : '3'}. Démarrez Traefik, puis l'interface
cd traefik && docker compose up -d && cd ..
cd armadok && docker compose up -d && cd ..

# ${(c.tlsHttp || c.tlsDns) ? '5' : '4'}. Ouvrez https://${c.webuiHost}  (le DNS de ce nom doit pointer vers cette machine)
`
}

// ------------------------------------------------------------------ Vérifications avant génération
export function problems(c) {
  const out = []
  if (!c.tlsHttp && !c.tlsDns) out.push(['warn', 'Aucun résolveur de certificats : les sites ne seront pas en HTTPS valide (certificat par défaut de Traefik).'])
  if (c.tlsDns && !/^[a-z][a-z0-9-]*$/i.test(c.dnsName)) out.push(['err', 'Nom du résolveur DNS invalide (lettres, chiffres, tirets).'])
  if (c.tlsHttp && !/^[a-z][a-z0-9-]*$/i.test(c.httpName)) out.push(['err', 'Nom du résolveur HTTP invalide (lettres, chiffres, tirets).'])
  if (c.tlsHttp && c.tlsDns && c.httpName === c.dnsName) out.push(['err', 'Les deux résolveurs ont le même nom.'])
  if ((c.tlsHttp || c.tlsDns) && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(c.email)) out.push(['err', 'Une adresse e-mail valide est demandée par Let\'s Encrypt.'])
  if (!/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(c.webuiHost)) out.push(['err', 'Adresse de l\'interface invalide (ex. armadok.exemple.fr).'])
  if (c.dashboard && !/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(c.dashboardHost)) out.push(['err', 'Adresse du tableau de bord invalide.'])
  if (c.dashboard && !c.chainPrivate) out.push(['warn', 'Tableau de bord sans chaîne privée : il sera public. Cochez la chaîne privée ou ajoutez une identification.'])
  if (!c.chainPrivate) out.push(['warn', 'Sans chaîne privée, cette interface sera accessible à tout Internet (elle gère vos DNS et Traefik) : gardez la protection ou placez-la derrière un VPN.'])
  if (c.dockerProvider && !c.dockerGid) out.push(['info', 'Docker : DOCKER_GID est à renseigner (getent group docker | cut -d: -f3).'])
  if (c.apiInternal) out.push(['info', 'API interne : « api.insecure » ouvre le port 8080 à tous les conteneurs du réseau partagé (il n\'est pas publié sur l\'hôte). Décochez si vous ne voulez ni l\'état en direct des sites ni Prometheus.'])
  if (c.metrics && !c.apiInternal) out.push(['info', 'Les métriques sont servies sur l\'entrypoint interne « traefik » (:8080) : cochez « API interne » si Prometheus ne les lit pas.'])
  if (c.tlsDns && c.dnsProvider === 'duckdns') out.push(['info', DNS_PROVIDERS.duckdns.help])
  const bad = [...csv(c.lan), ...csv(c.extraIps)].filter((r) => !/^(\d{1,3}(\.\d{1,3}){3}(\/\d{1,2})?|[0-9a-f:]+(\/\d{1,3})?)$/i.test(r))
  if (c.chainPrivate && bad.length) out.push(['err', `Adresse ou réseau invalide : ${bad.join(', ')}`])
  if (c.chainPrivate && ![...csv(c.lan), ...csv(c.extraIps)].length) out.push(['err', 'Indiquez au moins une adresse ou un réseau autorisé pour la chaîne privée.'])
  return out
}

export function allFiles(c, secrets) {
  return {
    'traefik/docker-compose.yml': traefikCompose(c),
    'traefik/.env': traefikEnv(c),
    ...Object.fromEntries(Object.entries(dynamicFiles(c)).map(([k, v]) => ['traefik/' + k, v])),
    'armadok/docker-compose.yml': webuiCompose(c),
    'armadok/.env': webuiEnv(c, secrets),
  }
}
