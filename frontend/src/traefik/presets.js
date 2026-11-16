// Middlewares HTTP proposés « par intention » à la création : un titre clair, un réglage de départ raisonnable, et seulement
// les champs utiles (`show`) ; le reste du formulaire est replié sous « Plus d'options ».
export const MW_PRESETS = [
  { type: 'ipAllowList', icon: 'Network', title: 'Réseau local uniquement', desc: 'Bloque tout ce qui ne vient pas de chez vous (réseau local, VPN).',
    name: 'local-only', spec: { ipAllowList: { sourceRange: ['192.168.0.0/16', '10.0.0.0/8', '172.16.0.0/12'] } }, show: ['sourceRange'],
    hint: 'Ces plages couvrent les réseaux privés courants. Ajoutez celle de votre VPN si besoin (ex. 10.8.0.0/24).' },
  { type: 'basicAuth', icon: 'KeyRound', title: 'Demander un mot de passe', desc: 'Une fenêtre identifiant / mot de passe avant d\'accéder au site.',
    name: 'auth', spec: { basicAuth: {} }, show: ['users'], hint: 'Utilisez « Générer un utilisateur » : le mot de passe est transformé en hash, jamais écrit en clair.' },
  { type: 'forwardAuth', icon: 'ShieldCheck', title: 'Connexion centralisée (SSO)', desc: 'Délègue la connexion à Authelia, Authentik, Keycloak…',
    name: 'sso', spec: { forwardAuth: { address: '', trustForwardHeader: true } }, show: ['address', 'trustForwardHeader', 'authResponseHeaders'],
    hint: 'L\'adresse est celle du point de vérification de votre service SSO (ex. http://authelia:9091/api/verify).' },
  { type: 'headers', icon: 'Shield', title: 'En-têtes de sécurité', desc: 'HSTS et protections navigateur recommandées, sans rien casser.',
    name: 'secure-headers', spec: { headers: { stsSeconds: 63072000, stsIncludeSubdomains: true, stsPreload: true, contentTypeNosniff: true } }, show: ['_sec'],
    hint: 'Forcer HTTPS pendant 2 ans (HSTS) et empêcher le navigateur de deviner les types de fichiers.' },
  { type: 'redirectScheme', icon: 'CornerUpRight', title: 'Rediriger vers HTTPS', desc: 'Envoie les visiteurs en HTTP vers la version sécurisée.',
    name: 'https-redirect', spec: { redirectScheme: { scheme: 'https', permanent: true } }, show: ['scheme', 'permanent'] },
  { type: 'stripPrefix', icon: 'FolderMinus', title: 'Retirer un préfixe du chemin', desc: 'Pour une appli servie sous /app : elle reçoit les requêtes sans ce /app.',
    name: 'strip-prefix', spec: { stripPrefix: { prefixes: [] } }, show: ['prefixes'] },
  { type: 'rateLimit', icon: 'Gauge', title: 'Limiter le débit', desc: 'Freine les abus : nombre de requêtes maximum par visiteur.',
    name: 'rate-limit', spec: { rateLimit: { average: 100, burst: 50 } }, show: ['average', 'period', 'burst'],
    hint: '100 requêtes par seconde en moyenne, avec une rafale tolérée de 50.' },
  { type: 'compress', icon: 'Package', title: 'Compresser les réponses', desc: 'Pages plus légères et plus rapides (gzip, brotli, zstd).',
    name: 'compress', spec: { compress: {} }, show: [], hint: 'Rien à régler : les valeurs par défaut conviennent.' },
  { type: 'errors', icon: 'TriangleAlert', title: 'Pages d\'erreur personnalisées', desc: 'Affiche votre propre page quand le site renvoie une erreur.',
    name: 'custom-errors', spec: { errors: { status: ['500-599'], query: '/{status}.html' } }, show: ['status', 'service', 'query'] },
  { type: 'chain', icon: 'Link2', title: 'Groupe de protections', desc: 'Réunit plusieurs middlewares sous un seul nom, appliqués dans l\'ordre.',
    name: 'chain', spec: { chain: { middlewares: [] } }, show: ['middlewares'] },
]
export const presetOf = (type) => MW_PRESETS.find((p) => p.type === type)
