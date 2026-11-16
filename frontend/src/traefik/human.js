import { HTTP_MIDDLEWARES } from './schema.js'

// Explications en français des middlewares (les noms de fonctions Traefik ne sont jamais traduits).
export const HUMAN = {
  basicAuth: ['Demande un mot de passe', 'KeyRound', 'security'], digestAuth: ['Demande un mot de passe (digest)', 'KeyRound', 'security'],
  forwardAuth: ['Délègue la connexion à un service externe (SSO)', 'ShieldCheck', 'security'], ipAllowList: ['N\'autorise que certaines IP', 'Network', 'security'],
  ipWhiteList: ['N\'autorise que certaines IP (ancien nom)', 'Network', 'security'], passTLSClientCert: ['Transmet le certificat client', 'FileKey', 'security'],
  headers: ['En-têtes de sécurité, CORS…', 'Shield', 'security'],
  redirectScheme: ['Redirige http → https', 'CornerUpRight', 'path'], redirectRegex: ['Redirige selon un motif', 'CornerUpRight', 'path'],
  addPrefix: ['Ajoute un préfixe au chemin', 'FolderPlus', 'path'], stripPrefix: ['Retire un préfixe du chemin', 'FolderMinus', 'path'],
  stripPrefixRegex: ['Retire un préfixe (motif)', 'FolderMinus', 'path'], replacePath: ['Remplace le chemin', 'Replace', 'path'], replacePathRegex: ['Réécrit le chemin (motif)', 'Replace', 'path'],
  compress: ['Compresse les réponses', 'Package', 'content'], contentType: ['Détecte le type de contenu', 'FileType', 'content'], buffering: ['Met les requêtes en mémoire tampon', 'Database', 'content'],
  errors: ['Pages d\'erreur personnalisées', 'TriangleAlert', 'content'], chain: ['Regroupe plusieurs middlewares', 'Link2', 'group'], grpcWeb: ['Convertit gRPC-Web en gRPC', 'Plug', 'content'],
  rateLimit: ['Limite le débit par visiteur', 'Gauge', 'traffic'], inFlightReq: ['Limite les requêtes simultanées', 'Gauge', 'traffic'], retry: ['Réessaie en cas d\'échec', 'RotateCw', 'traffic'],
  circuitBreaker: ['Coupe le trafic quand le serveur est en erreur', 'Zap', 'traffic'], plugin: ['Fourni par un plugin Traefik', 'Puzzle', 'other'],
}
export const GROUP_COLOR = { security: '#e8590c', path: '#7048e8', content: '#0b7285', traffic: '#c2255c', group: '#495057', other: '#868e96' }
// humanOf(type)[0] est une EXPLICATION en français ; le nom de la fonction reste celui de Traefik (techName : « Circuit Breaker », « Rate Limit »…).
export const humanOf = (type) => HUMAN[type] || [type || 'Bloc', 'Box', 'other']
export const techName = (type) => HTTP_MIDDLEWARES[type]?.label || type || 'Middleware'

export const COND_LABELS = {
  PathPrefix: ['Chemin commence par', '/api'], Path: ['Chemin exact', '/login'], Method: ['Méthode HTTP', 'GET'], Header: ['En-tête égal à', 'X-Token', 'valeur'],
  HeaderRegexp: ['En-tête (motif)', 'X-Token', '^abc'], Query: ['Paramètre d\'URL', 'clé', 'valeur'], ClientIP: ['IP du client', '192.168.0.0/16'],
  PathRegexp: ['Chemin (motif)', '^/api/.*'], HostRegexp: ['Domaine (motif)', '^.+\\.exemple\\.fr$'],
}
