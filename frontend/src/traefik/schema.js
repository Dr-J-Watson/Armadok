// Catalogue des options Traefik v3 (fichiers dynamiques).
// Types de champ : s texte · n nombre · b booléen · d durée · e enum · sl liste de textes · kv clé/valeur
//                  o objet · ol liste d'objets · raw YAML libre · ref / refl référence(s) vers un autre élément
const f = (k, t, label, extra = {}) => ({ k, t, label: label || k, ...extra })
const s = (k, label, x) => f(k, 's', label, x)
const n = (k, label, x) => f(k, 'n', label, x)
const b = (k, label, x) => f(k, 'b', label, x)
const d = (k, label, x) => f(k, 'd', label, { ph: '10s', ...x })
const e = (k, opts, label, x) => f(k, 'e', label, { opts, ...x })
const sl = (k, label, x) => f(k, 'sl', label, x)
const kv = (k, label, x) => f(k, 'kv', label, x)
const o = (k, fields, label, x) => f(k, 'o', label, { f: fields, ...x })
const ol = (k, fields, label, x) => f(k, 'ol', label, { f: fields, ...x })

const ipStrategy = o('ipStrategy', [
  n('depth', 'Profondeur (X-Forwarded-For)', { help: 'Position de l\'IP à prendre en partant de la droite.' }),
  sl('excludedIPs', 'IP exclues', { ph: '10.0.0.1' }),
  s('ipv6Subnet', 'Sous-réseau IPv6'),
], 'Stratégie IP')

const sourceCriterion = o('sourceCriterion', [
  ipStrategy,
  s('requestHeaderName', 'Nom du header'),
  b('requestHost', 'Utiliser le Host de la requête'),
], 'Critère de source')

const certInfo = (label) => o(label, [b('country'), b('province'), b('locality'), b('organization'), b('commonName'), b('serialNumber'), b('domainComponent')], label)

export const MIDDLEWARE_GROUPS = ['Sécurité & accès', 'Redirection & chemins', 'Requêtes & réponses', 'Résilience & trafic', 'Avancé']

export const HTTP_MIDDLEWARES = {
  basicAuth: { group: 0, label: 'Basic Auth', desc: 'Authentification utilisateur / mot de passe (htpasswd bcrypt).', fields: [
    f('users', 'sl', 'Utilisateurs (user:hash)', { ph: 'user:$2a$12$…', help: 'Utilisez le bouton « générer » pour créer une entrée bcrypt.', htpasswd: true }),
    s('usersFile', 'Fichier d\'utilisateurs'), s('realm', 'Realm'), b('removeHeader', 'Retirer le header Authorization'), s('headerField', 'Header transmis au backend'),
  ] },
  digestAuth: { group: 0, label: 'Digest Auth', desc: 'Authentification digest.', fields: [sl('users', 'Utilisateurs'), s('usersFile'), s('realm'), b('removeHeader'), s('headerField')] },
  forwardAuth: { group: 0, label: 'Forward Auth', desc: 'Délègue l\'authentification à un service externe (Authelia, Authentik…).', fields: [
    s('address', 'Adresse', { required: true, ph: 'http://authelia:9091/api/verify' }), b('trustForwardHeader', 'Faire confiance aux headers X-Forwarded-*'),
    sl('authResponseHeaders', 'Headers de réponse copiés'), s('authResponseHeadersRegex', 'Regex des headers copiés'), sl('authRequestHeaders', 'Headers de requête transmis'),
    b('addAuthCookiesToResponse'),
    o('tls', [s('ca'), s('cert'), s('key'), b('insecureSkipVerify')], 'TLS'),
  ] },
  ipAllowList: { group: 0, label: 'IP Allow List', desc: 'N\'autorise que certaines IP / réseaux.', fields: [
    sl('sourceRange', 'Plages autorisées (CIDR)', { required: true, ph: '192.168.0.0/16' }), ipStrategy,
  ] },
  ipWhiteList: { group: 0, deprecated: true, label: 'IP White List (déprécié)', desc: 'Remplacé par ipAllowList.', fields: [sl('sourceRange'), ipStrategy] },
  passTLSClientCert: { group: 0, label: 'Pass TLS Client Cert', desc: 'Transmet le certificat client au backend.', fields: [
    b('pem', 'PEM complet'),
    o('info', [b('notAfter'), b('notBefore'), b('sans'), b('serialNumber'), certInfo('subject'), certInfo('issuer')], 'Informations'),
  ] },
  headers: { group: 0, label: 'Headers', desc: 'Headers personnalisés, CORS, HSTS, CSP, sécurité navigateur…', fields: [
    kv('customRequestHeaders', 'Headers de requête ajoutés', { help: 'Valeur vide = suppression du header.' }), kv('customResponseHeaders', 'Headers de réponse ajoutés'),
    o('_sec', [
      n('stsSeconds', 'HSTS (secondes)', { ph: '31536000' }), b('stsIncludeSubdomains'), b('stsPreload'), b('forceSTSHeader'),
      b('frameDeny', 'X-Frame-Options: DENY'), s('customFrameOptionsValue'), b('contentTypeNosniff'), b('browserXssFilter'), s('customBrowserXSSValue'),
      s('contentSecurityPolicy', 'Content-Security-Policy'), s('contentSecurityPolicyReportOnly'), s('referrerPolicy', 'Referrer-Policy', { ph: 'strict-origin-when-cross-origin' }),
      s('permissionsPolicy', 'Permissions-Policy'), s('publicKey', 'Public-Key-Pins'), b('isDevelopment'),
    ], 'Sécurité navigateur', { flat: true }),
    o('_cors', [
      sl('accessControlAllowOriginList', 'Origines autorisées'), sl('accessControlAllowOriginListRegex', 'Origines (regex)'), sl('accessControlAllowMethods', 'Méthodes'),
      sl('accessControlAllowHeaders', 'Headers autorisés'), sl('accessControlExposeHeaders', 'Headers exposés'), b('accessControlAllowCredentials'), n('accessControlMaxAge', 'Max-Age (s)'), b('addVaryHeader'),
    ], 'CORS', { flat: true }),
    o('_host', [sl('allowedHosts', 'Hôtes autorisés'), kv('hostsProxyHeaders', 'Headers proxy d\'hôte'), kv('sslProxyHeaders', 'Headers proxy SSL')], 'Hôtes & proxy', { flat: true }),
  ] },

  addPrefix: { group: 1, label: 'Add Prefix', desc: 'Ajoute un préfixe au chemin.', fields: [s('prefix', 'Préfixe', { required: true, ph: '/api' })] },
  stripPrefix: { group: 1, label: 'Strip Prefix', desc: 'Retire un préfixe du chemin.', fields: [sl('prefixes', 'Préfixes', { required: true, ph: '/app' }), b('forceSlash')] },
  stripPrefixRegex: { group: 1, label: 'Strip Prefix Regex', desc: 'Retire un préfixe par regex.', fields: [sl('regex', 'Regex', { required: true })] },
  replacePath: { group: 1, label: 'Replace Path', desc: 'Remplace tout le chemin.', fields: [s('path', 'Nouveau chemin', { required: true })] },
  replacePathRegex: { group: 1, label: 'Replace Path Regex', desc: 'Réécrit le chemin par regex.', fields: [s('regex', 'Regex', { required: true }), s('replacement', 'Remplacement', { required: true })] },
  redirectScheme: { group: 1, label: 'Redirect Scheme', desc: 'Redirige http → https (ou inverse).', fields: [e('scheme', ['https', 'http'], 'Schéma'), s('port', 'Port'), b('permanent', 'Permanente (301/308)')] },
  redirectRegex: { group: 1, label: 'Redirect Regex', desc: 'Redirection par regex.', fields: [s('regex', 'Regex', { required: true, ph: '^https://(.*)/old/(.*)' }), s('replacement', 'Remplacement', { required: true, ph: 'https://${1}/new/${2}' }), b('permanent')] },

  compress: { group: 2, label: 'Compress', desc: 'Compression gzip / brotli / zstd.', fields: [sl('excludedContentTypes', 'Types exclus'), sl('includedContentTypes', 'Types inclus'), n('minResponseBodyBytes', 'Taille minimale (octets)', { ph: '1024' }), sl('encodings', 'Encodages (v3.2+)'), s('defaultEncoding', 'Encodage par défaut (v3.2+)')] },
  contentType: { group: 2, label: 'Content Type', desc: 'Détection automatique du Content-Type.', fields: [b('autoDetect')] },
  buffering: { group: 2, label: 'Buffering', desc: 'Met en mémoire tampon requêtes / réponses.', fields: [n('maxRequestBodyBytes'), n('memRequestBodyBytes'), n('maxResponseBodyBytes'), n('memResponseBodyBytes'), s('retryExpression')] },
  errors: { group: 2, label: 'Custom Errors', desc: 'Pages d\'erreur personnalisées servies par un service.', fields: [
    sl('status', 'Codes HTTP', { required: true, ph: '500-599' }), f('service', 'ref', 'Service', { ref: 'services', required: true }), s('query', 'Chemin de la page', { ph: '/{status}.html' }),
  ] },
  chain: { group: 2, label: 'Chain', desc: 'Enchaîne plusieurs middlewares dans l\'ordre.', fields: [f('middlewares', 'refl', 'Middlewares (dans l\'ordre)', { ref: 'middlewares', required: true })] },
  grpcWeb: { group: 2, label: 'gRPC-Web', desc: 'Convertit gRPC-Web en gRPC.', fields: [sl('allowOrigins')] },

  rateLimit: { group: 3, label: 'Rate Limit', desc: 'Limite le nombre de requêtes par source.', fields: [n('average', 'Moyenne (req/période)', { required: true }), d('period', 'Période', { ph: '1s' }), n('burst', 'Rafale'), sourceCriterion] },
  inFlightReq: { group: 3, label: 'In-Flight Requests', desc: 'Limite les requêtes simultanées.', fields: [n('amount', 'Maximum', { required: true }), sourceCriterion] },
  retry: { group: 3, label: 'Retry', desc: 'Réessaie en cas d\'échec réseau.', fields: [n('attempts', 'Tentatives', { required: true }), d('initialInterval', 'Intervalle initial', { ph: '100ms' })] },
  circuitBreaker: { group: 3, label: 'Circuit Breaker', desc: 'Coupe le trafic quand le backend est en erreur.', fields: [
    s('expression', 'Expression', { required: true, ph: 'NetworkErrorRatio() > 0.30' }), d('checkPeriod', 'Période de contrôle', { ph: '100ms' }), d('fallbackDuration', 'Durée de repli', { ph: '10s' }), d('recoveryDuration', 'Durée de récupération', { ph: '10s' }), n('responseCode', 'Code de réponse', { ph: '503' }),
  ] },
  plugin: { group: 4, label: 'Plugin', desc: 'Middleware fourni par un plugin Traefik.', fields: [f('', 'raw', 'Configuration du plugin (YAML)', { help: 'Ex : monPlugin:\n  option: valeur' })], rawRoot: true },
}

export const TCP_MIDDLEWARES = {
  ipAllowList: { group: 0, label: 'IP Allow List', desc: 'N\'autorise que certaines IP.', fields: [sl('sourceRange', 'Plages autorisées', { required: true })] },
  inFlightConn: { group: 3, label: 'In-Flight Connections', desc: 'Limite les connexions simultanées.', fields: [n('amount', 'Maximum', { required: true })] },
}

const healthCheck = o('healthCheck', [
  s('path', 'Chemin', { ph: '/health' }), e('scheme', ['http', 'https']), e('mode', ['http', 'grpc']), s('hostname'), n('port'),
  d('interval', 'Intervalle', { ph: '30s' }), d('timeout', 'Timeout', { ph: '5s' }), kv('headers'), b('followRedirects'), s('method', 'Méthode', { ph: 'GET' }), n('status', 'Code attendu'),
], 'Health check')

const stickyCookie = o('sticky', [o('cookie', [s('name'), b('secure'), b('httpOnly'), e('sameSite', ['none', 'lax', 'strict']), n('maxAge'), s('path')], 'Cookie')], 'Sessions persistantes (sticky)')

export const HTTP_SERVICES = {
  loadBalancer: { label: 'Load Balancer', desc: 'Répartit la charge entre un ou plusieurs serveurs.', fields: [
    ol('servers', [s('url', 'URL', { required: true, ph: 'http://conteneur:8080' }), b('preservePath'), n('weight', 'Poids (v3.3+)')], 'Serveurs', { required: true, primary: 'url' }),
    b('passHostHeader', 'Transmettre le header Host', { def: true }), f('serversTransport', 'ref', 'Servers Transport', { ref: 'serversTransports' }),
    healthCheck, stickyCookie, o('responseForwarding', [d('flushInterval', 'Flush interval', { ph: '100ms' })], 'Response forwarding'), e('strategy', ['wrr', 'p2c'], 'Stratégie'),
  ] },
  weighted: { label: 'Weighted', desc: 'Répartition pondérée entre services (canary, blue/green).', fields: [
    ol('services', [f('name', 'ref', 'Service', { ref: 'services', required: true }), n('weight', 'Poids')], 'Services', { required: true, primary: 'name' }), stickyCookie, healthCheck,
  ] },
  mirroring: { label: 'Mirroring', desc: 'Duplique une partie du trafic vers d\'autres services.', fields: [
    f('service', 'ref', 'Service principal', { ref: 'services', required: true }), b('mirrorBody'), n('maxBodySize'),
    ol('mirrors', [f('name', 'ref', 'Service', { ref: 'services', required: true }), n('percent', '% du trafic')], 'Miroirs', { primary: 'name' }), healthCheck,
  ] },
  failover: { label: 'Failover', desc: 'Bascule sur un service de secours.', fields: [f('service', 'ref', 'Service principal', { ref: 'services', required: true }), f('fallback', 'ref', 'Service de secours', { ref: 'services', required: true }), healthCheck] },
}

export const TCP_SERVICES = {
  loadBalancer: { label: 'Load Balancer', desc: 'Répartit les connexions TCP.', fields: [
    ol('servers', [s('address', 'Adresse', { required: true, ph: 'hote:5432' }), b('tls'), s('port')], 'Serveurs', { required: true, primary: 'address' }),
    o('proxyProtocol', [e('version', ['1', '2'], 'Version')], 'PROXY protocol'), d('terminationDelay', 'Délai de terminaison', { ph: '100ms' }), f('serversTransport', 'ref', 'Servers Transport', { ref: 'serversTransports' }),
  ] },
  weighted: { label: 'Weighted', desc: 'Répartition pondérée.', fields: [ol('services', [f('name', 'ref', 'Service', { ref: 'services', required: true }), n('weight')], 'Services', { primary: 'name' })] },
}
export const UDP_SERVICES = {
  loadBalancer: { label: 'Load Balancer', desc: 'Répartit les paquets UDP.', fields: [ol('servers', [s('address', 'Adresse', { required: true, ph: 'hote:51820' })], 'Serveurs', { required: true, primary: 'address' })] },
  weighted: TCP_SERVICES.weighted,
}

const tlsBlock = (tcp) => o('tls', [
  f('certResolver', 'ref', 'Cert resolver', { ref: 'resolvers', help: 'Génère le certificat (Let\'s Encrypt…).' }), f('options', 'ref', 'Options TLS', { ref: 'tlsOptions' }),
  ...(tcp ? [b('passthrough', 'Passthrough TLS')] : []),
  ol('domains', [s('main', 'Domaine principal'), sl('sans', 'SAN')], 'Domaines', { primary: 'main' }),
], 'TLS')

export const ROUTER_FIELDS = {
  http: [
    f('rule', 'rule', 'Règle', { required: true }), f('entryPoints', 'refl', 'EntryPoints', { ref: 'entrypoints' }), f('service', 'ref', 'Service', { ref: 'services', required: true }),
    f('middlewares', 'refl', 'Middlewares (dans l\'ordre)', { ref: 'middlewares' }), n('priority', 'Priorité', { help: 'Par défaut : longueur de la règle.' }), e('ruleSyntax', ['v3', 'v2'], 'Syntaxe de règle'), tlsBlock(false),
  ],
  tcp: [
    f('rule', 'rule', 'Règle', { required: true, tcp: true }), f('entryPoints', 'refl', 'EntryPoints', { ref: 'entrypoints' }), f('service', 'ref', 'Service', { ref: 'services', required: true }),
    f('middlewares', 'refl', 'Middlewares', { ref: 'middlewares' }), n('priority', 'Priorité'), e('ruleSyntax', ['v3', 'v2'], 'Syntaxe de règle'), tlsBlock(true),
  ],
  udp: [f('entryPoints', 'refl', 'EntryPoints', { ref: 'entrypoints' }), f('service', 'ref', 'Service', { ref: 'services', required: true })],
}

export const TRANSPORT_FIELDS = {
  http: [
    s('serverName', 'Server name (SNI)'), b('insecureSkipVerify', 'Ignorer la vérification TLS du backend'), sl('rootCAs', 'CA racines'), n('maxIdleConnsPerHost'), b('disableHTTP2'), s('peerCertURI'),
    ol('certificates', [s('certFile'), s('keyFile')], 'Certificats clients'),
    o('forwardingTimeouts', [d('dialTimeout', 'Dial', { ph: '30s' }), d('responseHeaderTimeout', 'Réponse (headers)', { ph: '0s' }), d('idleConnTimeout', 'Inactivité', { ph: '90s' })], 'Timeouts'),
  ],
  tcp: [d('dialTimeout'), d('dialKeepAlive'), d('terminationDelay'), o('tls', [s('serverName'), b('insecureSkipVerify'), sl('rootCAs'), ol('certificates', [s('certFile'), s('keyFile')])], 'TLS')],
}

export const TLS_OPTIONS_FIELDS = [
  e('minVersion', ['VersionTLS10', 'VersionTLS11', 'VersionTLS12', 'VersionTLS13']), e('maxVersion', ['VersionTLS10', 'VersionTLS11', 'VersionTLS12', 'VersionTLS13']),
  sl('cipherSuites'), sl('curvePreferences'), b('sniStrict'), sl('alpnProtocols'),
  o('clientAuth', [sl('caFiles'), e('clientAuthType', ['NoClientCert', 'RequestClientCert', 'RequireAnyClientCert', 'VerifyClientCertIfGiven', 'RequireAndVerifyClientCert'])], 'Authentification client (mTLS)'),
]
export const TLS_STORES_FIELDS = [o('defaultCertificate', [s('certFile'), s('keyFile')], 'Certificat par défaut'), o('defaultGeneratedCert', [s('resolver'), o('domain', [s('main'), sl('sans')])], 'Certificat généré par défaut')]

export const KIND_LABELS = { routers: 'Routeurs', services: 'Services', middlewares: 'Middlewares', serversTransports: 'Servers Transports', options: 'Options TLS', stores: 'Stores TLS' }

export function catalogFor(proto, kind) {
  if (kind === 'middlewares') return proto === 'tcp' ? TCP_MIDDLEWARES : HTTP_MIDDLEWARES
  if (kind === 'services') return { http: HTTP_SERVICES, tcp: TCP_SERVICES, udp: UDP_SERVICES }[proto]
  return null
}
export function fieldsFor(proto, kind) {
  if (kind === 'routers') return ROUTER_FIELDS[proto]
  if (kind === 'serversTransports') return TRANSPORT_FIELDS[proto]
  if (proto === 'tls') return kind === 'options' ? TLS_OPTIONS_FIELDS : TLS_STORES_FIELDS
  return null
}
