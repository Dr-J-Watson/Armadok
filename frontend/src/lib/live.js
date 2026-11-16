// Temps réel : un WebSocket (/api/events) par onglet. Le serveur ne dit pas CE QUI a changé, seulement DE QUOI il s'agit
// (« traefik », « nodes », « domains »…) : les écrans concernés se rechargent par l'API habituelle, qui applique les droits.
import { useSyncExternalStore } from 'react'
import { getToken, refreshSession } from '../api.js'

const listeners = new Set()
const stateListeners = new Set()
let state = 'off'            // 'off' | 'connecting' | 'live'
let ws = null, timer = null, watchdog = null, attempt = 0, running = false, hadConnection = false

export const onLive = (fn) => { listeners.add(fn); return () => listeners.delete(fn) }
const emit = (topics) => listeners.forEach((fn) => { try { fn(topics) } catch { /* un abonné ne casse pas les autres */ } })
const setState = (s) => { if (s !== state) { state = s; stateListeners.forEach((f) => f()) } }
export const liveState = () => state
export const useLiveState = () => useSyncExternalStore((f) => { stateListeners.add(f); return () => stateListeners.delete(f) }, () => state)

const url = () => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/events`

function arm() {           // le serveur envoie un signe de vie toutes les 25 s : sans nouvelles pendant 70 s, on reconnecte
  clearTimeout(watchdog)
  watchdog = setTimeout(() => { try { ws?.close() } catch { /* */ } }, 70000)
}

function schedule(delay) {
  clearTimeout(timer)
  if (!running) return
  attempt += 1
  timer = setTimeout(open, delay ?? Math.min(30000, 1000 * 2 ** Math.min(attempt, 5)) + Math.random() * 500)
}

function open() {
  if (!running || (ws && ws.readyState <= 1)) return
  const token = getToken()
  if (!token) return schedule(2000)
  setState('connecting')
  let w
  try { w = new WebSocket(url()) } catch { return schedule() }
  ws = w
  w.onopen = () => { w.send(JSON.stringify({ token })); arm() }
  w.onmessage = (e) => {
    arm()
    let m; try { m = JSON.parse(e.data) } catch { return }
    if (m.t === 'ready') {
      attempt = 0; setState('live')
      if (hadConnection) emit([{ t: 'all' }])   // des signaux ont pu être manqués pendant la coupure
      hadConnection = true
    } else if (m.t === 'changed') emit(m.topics || [])
  }
  w.onclose = (e) => {
    clearTimeout(watchdog)
    if (ws !== w) return
    ws = null; setState('off')
    if (!running) return
    if (e.code === 4401) refreshSession().catch(() => {}).finally(() => schedule(300))   // jeton périmé : on en prend un neuf
    else schedule()
  }
  w.onerror = () => { /* onclose suit */ }
}

export function startLive() {
  if (running) return
  running = true; attempt = 0
  open()
}
export function stopLive() {
  running = false; hadConnection = false
  clearTimeout(timer); clearTimeout(watchdog)
  try { ws?.close() } catch { /* */ }
  ws = null; setState('off')
}
if (typeof window !== 'undefined') window.addEventListener('online', () => { if (running && !ws) { attempt = 0; open() } })
// Onglet mis en arrière-plan puis revenu au premier plan : le navigateur a pu geler ses timers ou couper la connexion. On se reconnecte
// si besoin et on relit ce qui est affiché (filet de sécurité : l'écran est alors forcément à jour, même si un signal s'est perdu).
let hiddenAt = 0
if (typeof document !== 'undefined') document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') { hiddenAt = Date.now(); return }
  if (!running) return
  if (!ws || ws.readyState > 1) { attempt = 0; open() }
  else if (hiddenAt && Date.now() - hiddenAt > 3000) emit([{ t: 'all' }])
})

// Quels écrans se rechargent pour chaque sujet (clés react-query, par préfixe).
export const TOPIC_KEYS = {
  traefik: ['files', 'overview', 'history', 'placement', 'reorganize', 'impact', 'pause-domains', 'sync', 'certs', 'live', 'meta', 'features', 'obs-bans'],
  live: ['live'],
  certs: ['certs', 'features'],
  alerts: ['obs-alerts', 'features'],
  security: ['obs-ips', 'obs-bans', 'obs-ip', 'obs-trusted', 'obs-automod'],
  node: ['node', 'node-members', 'node-domains', 'node-conn', 'importable', 'meta', 'impact', 'features', 'obs-config'],
  nodes: ['node', 'meta', 'admin-nodes', 'notices', 'notices-all', 'node-members', 'rights-roles', 'rights-groups', 'rights-grants', 'rights-users', 'rights-user'],
  audit: ['audit', 'node-audit', 'audit-verify'],
  domains: ['domains', 'zones', 'credentials', 'sync', 'records', 'provider', 'credzones', 'access', 'directory', 'importable', 'pause-domains', 'overview', 'node-domains'],
  users: ['users', 'directory', 'node-members'],
  notices: ['notices', 'notices-all'],
}
export const NODE_TOPICS = new Set(['traefik', 'live', 'certs', 'node', 'audit', 'alerts', 'security'])
