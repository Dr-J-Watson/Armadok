// Client HTTP : access token JWT gardé en mémoire, refresh automatique via cookie httpOnly.
let accessToken = null
let refreshing = null
let onLogout = () => {}

export const setLogoutHandler = (fn) => (onLogout = fn)
export const setToken = (t) => (accessToken = t)
export const getToken = () => accessToken

// Nœud Traefik visé par les requêtes (en-tête X-Node) ; défini par NodeProvider.
let nodeId = null
export const setNodeHeader = (id) => (nodeId = id || null)

// Nœud sans Traefik dont les sites passent par celui d'un autre : les requêtes Traefik (sites, certificats, surveillance…) visent CE Traefik (X-Node),
// filtrées sur les conteneurs du nœud courant (X-Node-Scope). Les autres requêtes (Docker, projets…) visent le nœud courant.
let traefikRoute = null
export const setTraefikRoute = (r) => (traefikRoute = r)
const TRAEFIK_PATH = /^\/(traefik|obs|sync|meta)(\/|\?|$)/

const STATUS_TEXT = {
  400: 'Requête invalide', 401: 'Session expirée : reconnectez-vous', 403: 'Action non autorisée avec vos droits', 404: 'Introuvable',
  405: 'Action non prise en charge', 409: 'Conflit : cet élément existe déjà ou vient de changer', 413: 'Contenu trop volumineux', 422: 'Données invalides',
  423: 'Ressource verrouillée (nœud suspendu ?)', 429: 'Trop de tentatives, réessayez dans un moment', 500: 'Erreur interne du serveur',
  502: 'Le serveur ne répond pas (passerelle indisponible), réessayez dans un instant', 503: 'Service momentanément indisponible, réessayez', 504: 'Le serveur met trop de temps à répondre',
}
// Un message lisible dans TOUS les cas : `detail` du serveur (texte, liste de validation ou objet), sinon un texte selon le code HTTP.
// `statusText` est vide en HTTP/2 : s'y fier donnait des notifications vides.
export function describeError(status, data, statusText = '') {
  let d = data?.detail ?? data?.message ?? data?.error
  if (Array.isArray(d)) d = d.map((x) => (typeof x === 'string' ? x : [(x?.loc || []).slice(1).join('.'), x?.msg].filter(Boolean).join(' : '))).filter(Boolean).join(' ; ')
  else if (d && typeof d === 'object') d = d.message || d.msg || ''
  d = typeof d === 'string' ? d.trim() : ''
  if (d) return d
  return STATUS_TEXT[status] || (status >= 500 ? `Erreur du serveur (${status})` : (statusText || '').trim() || `Échec de la requête (${status || 'réseau'})`)
}

export class ApiError extends Error {
  constructor(message, status, extra) {
    super(message)
    this.status = status
    for (const [k, v] of Object.entries(extra || {})) if (!['message', 'name', 'stack', 'detail', 'status'].includes(k)) this[k] = v   // line, column, code… sans écraser le message
  }
}

export async function refreshSession() {
  refreshing ||= fetch('/api/auth/refresh', { method: 'POST', credentials: 'same-origin' })
    .then(async (r) => {
      if (!r.ok) throw new Error('refresh')
      const d = await r.json()
      accessToken = d.access_token
      return d
    })
    .finally(() => (refreshing = null))
  return refreshing
}

// Nœud d'un hub LIÉ : identifiant « L:<liaison>:<nœud distant> ». Ses requêtes de travail passent par /api/linked/<liaison>/… (le hub d'origine les relaie, l'autre hub les rejoue
// avec les droits de l'invité) ; les routes propres à CE hub n'envoient jamais l'identifiant d'un nœud lié.
const LINKED = /^L:([0-9a-f]{32}):(.+)$/
const RELAYED = /^\/(audit|docker|meta|obs|projects|traefik)(\/|\?|$)/
export function route(path, xnode) {
  const m = xnode && LINKED.exec(xnode)
  if (m) return RELAYED.test(path) && !path.startsWith('/projects/templates') ? { path: `/linked/${m[1]}${path}`, node: m[2] } : { path, node: null }
  const pm = /^\/nodes\/(L:[0-9a-f]{32}:[^/?]+)(.*)$/.exec(path)   // le détail d'un nœud lié
  if (pm) { const mm = LINKED.exec(decodeURIComponent(pm[1])); if (mm) return { path: `/linked/${mm[1]}/nodes/${mm[2]}${pm[2]}`, node: null } }
  return { path, node: xnode }
}

async function raw(method, path, body, retry = true, node = null) {
  let res
  const target = route(path, node || (traefikRoute && TRAEFIK_PATH.test(path) ? traefikRoute.via : nodeId))
  try {
    res = await fetch('/api' + target.path, {
      method,
      credentials: 'same-origin',
      headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(accessToken ? { Authorization: 'Bearer ' + accessToken } : {}), ...(target.node ? { 'X-Node': target.node } : {}), ...(!node && traefikRoute && TRAEFIK_PATH.test(path) && traefikRoute.scope && !path.startsWith('/meta') && !LINKED.test(traefikRoute.via) ? { 'X-Node-Scope': traefikRoute.scope } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError('Impossible de joindre le serveur : vérifiez votre connexion', 0)
  }
  if (res.status === 401 && retry && !path.startsWith('/auth/')) {
    try {
      await refreshSession()
      return raw(method, path, body, false, node)
    } catch {
      onLogout()
      throw new ApiError('Session expirée', 401)
    }
  }
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(describeError(res.status, data, res.statusText), res.status, typeof data === 'object' && data && !Array.isArray(data) ? { ...data, detail: undefined } : {})
  return data
}

// Le dernier argument facultatif `{ node }` vise un nœud précis (affichage complet : les mêmes listes sur tous les nœuds), sans changer de nœud courant.
export const api = {
  get: (p, o) => raw('GET', p, undefined, true, o?.node),
  post: (p, b = {}, o) => raw('POST', p, b, true, o?.node),
  put: (p, b, o) => raw('PUT', p, b, true, o?.node),
  patch: (p, b, o) => raw('PATCH', p, b, true, o?.node),
  del: (p, o) => raw('DELETE', p, undefined, true, o?.node),
}
export const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null)).toString()
