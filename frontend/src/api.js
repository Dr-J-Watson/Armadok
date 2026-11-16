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

async function raw(method, path, body, retry = true) {
  let res
  try {
    res = await fetch('/api' + path, {
      method,
      credentials: 'same-origin',
      headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(accessToken ? { Authorization: 'Bearer ' + accessToken } : {}), ...(nodeId ? { 'X-Node': nodeId } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError('Impossible de joindre le serveur : vérifiez votre connexion', 0)
  }
  if (res.status === 401 && retry && !path.startsWith('/auth/')) {
    try {
      await refreshSession()
      return raw(method, path, body, false)
    } catch {
      onLogout()
      throw new ApiError('Session expirée', 401)
    }
  }
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(describeError(res.status, data, res.statusText), res.status, typeof data === 'object' && data && !Array.isArray(data) ? { ...data, detail: undefined } : {})
  return data
}

export const api = {
  get: (p) => raw('GET', p),
  post: (p, b = {}) => raw('POST', p, b),
  put: (p, b) => raw('PUT', p, b),
  del: (p) => raw('DELETE', p),
}
export const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null)).toString()
