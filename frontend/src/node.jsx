import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, setNodeHeader, setTraefikRoute } from './api.js'
import { useAuth } from './auth.jsx'
import { Loading } from './components/ui.jsx'
import { TOPIC_KEYS, NODE_TOPICS, liveState, onLive, startLive, stopLive } from './lib/live.js'

const Ctx = createContext(null)
export const useNode = () => useContext(Ctx)

export const ROLES = {
  owner: { label: 'Propriétaire', badge: 'warn', help: 'Tous les droits, dont gérer les membres et transférer le nœud.' },
  admin: { label: 'Administrateur', badge: 'accent', help: 'Réglages, membres (éditeurs et lecteurs), domaines et édition complète de Traefik.' },
  editor: { label: 'Éditeur', badge: 'info', help: 'Crée et modifie des sites sur les domaines où il peut écrire, vers les cibles autorisées.' },
  viewer: { label: 'Lecteur', badge: '', help: 'Voit les sites de ses domaines, sans rien modifier.' },
  dns: { label: 'Domaines', badge: '', help: 'Accède seulement aux domaines (DNS) qui lui sont partagés.' },
  custom: { label: 'Personnalisé', badge: 'info', help: 'Droits choisis un par un dans un rôle personnalisé (page Droits).' },
}

const makeClient = () => new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 15000 } } })
const saved = () => { try { return localStorage.getItem('node') } catch { return null } }

// Fournit le nœud courant. Changer de nœud repart d'un cache vide : on ne voit jamais les données d'un autre nœud.
export function NodeProvider({ children }) {
  const { user } = useAuth()
  const nodes = useQuery({ queryKey: ['nodes'], queryFn: () => api.get('/nodes'), enabled: !!user, staleTime: 30000,
    refetchInterval: (q) => (liveState() === 'live' ? 300000 : q.state.data?.some((n) => n.kind === 'remote' && n.connection !== 'online') ? 10000 : 60000) })
  const root = useQueryClient()
  useEffect(() => { if (user) startLive(); else stopLive() }, [user?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => onLive((topics) => { // la liste des nœuds vit hors du cache par nœud : on la recharge ici
    if (topics.some((t) => t.t === 'all' || t.t === 'nodes' || t.t === 'node' || t.t === 'notices')) root.invalidateQueries({ queryKey: ['nodes'] })
  }), [root])
  const [pref, setPref] = useState(saved)
  const list = nodes.data || []
  const node = list.find((n) => n.id === pref) || list[0] || null
  setNodeHeader(node?.id) // avant le rendu des enfants : leurs premières requêtes visent le bon nœud
  // Nœud sans Traefik dont les sites passent par un autre : ses pages Traefik sont celles de cet autre nœud, filtrées (ou non) sur ses conteneurs.
  const viaNode = node?.traefikVia && !(node.modules || []).includes('traefik') ? list.find((n) => n.id === node.traefikVia && (n.modules || []).includes('traefik')) || null : null
  const [scopeOn, setScopeOn] = useState(() => { try { return localStorage.getItem('traefik-scope') !== 'off' } catch { return true } })
  const setScope = (v) => { try { localStorage.setItem('traefik-scope', v ? 'on' : 'off') } catch { /* */ } setScopeOn(v) }
  setTraefikRoute(viaNode ? { via: viaNode.id, scope: scopeOn ? node.id : null } : null)
  const client = useMemo(makeClient, [node?.id, viaNode?.id, scopeOn]) // eslint-disable-line react-hooks/exhaustive-deps
  const value = useMemo(() => {
    const role = node?.role || null
    const caps = new Set(node?.caps || [])
    if (viaNode) for (const c of viaNode.caps || []) if (/^(sites|certs|traefik|alerts|metrics|files|history|security)\./.test(c)) caps.add(c)   // les droits Traefik sont ceux du nœud qui porte Traefik
    const can = (cap) => (node ? caps.has(cap) : cap.startsWith('dns.'))   // sans nœud, seuls les droits DNS existent (selon le domaine)
    const modules = [...(node?.modules || ['traefik']), ...(viaNode ? ['traefik'] : [])]   // ce que le nœud sait faire (annoncé par son worker) : on ne montre que ce qui existe
    const hasModule = (m) => modules.includes(m)
    const NODE_ADMIN = ['node.settings', 'node.members.view', 'node.connection', 'node.audit', 'node.domains.view']
    return {
      nodes: list, node, role, loading: nodes.isLoading, can, caps, modules, hasModule, traefikVia: viaNode ? { node: viaNode, scope: scopeOn, setScope } : null,
      hasTraefik: !!role && can('sites.view') && hasModule('traefik'),   // voit les sites de ce nœud (et le nœud a Traefik)
      hasDocker: !!role && hasModule('docker'),                // le nœud gère Docker (les droits fins viennent avec les pages Docker)
      canManage: can('traefik.all'),                           // voit et agit sur tout Traefik (pas seulement ses domaines)
      canNode: NODE_ADMIN.some(can),                           // accède à la page « Ce nœud »
      canRights: list.some((n) => (n.caps || []).includes('node.members.edit')),   // peut gérer des droits quelque part
      setNode: (id) => { try { localStorage.setItem('node', id) } catch { /* */ } setPref(id) },
      reload: () => nodes.refetch(),
    }
  }, [nodes.data, node?.id, nodes.isLoading, viaNode?.id, scopeOn]) // eslint-disable-line react-hooks/exhaustive-deps
  if (user && nodes.isLoading) return <Loading />
  return <Ctx.Provider value={value}><QueryClientProvider key={`${viaNode?.id || ''}:${scopeOn}`} client={client}>{children}</QueryClientProvider></Ctx.Provider>
}

// À placer sous le client du nœud : recharge les écrans concernés par chaque signal. Un signal d'un AUTRE nœud est ignoré.
export function useLiveInvalidate() {
  const qc = useQueryClient(); const { node } = useNode()
  const cur = useRef({}); cur.current = { qc, id: node?.id }
  useEffect(() => onLive((topics) => {
    const { qc: c, id } = cur.current
    if (topics.some((t) => t.t === 'all')) { c.invalidateQueries(); return }
    const keys = new Set()
    topics.forEach((t) => { if (NODE_TOPICS.has(t.t) && t.node && t.node !== id) return; (TOPIC_KEYS[t.t] || []).forEach((k) => keys.add(k)) })
    keys.forEach((k) => c.invalidateQueries({ queryKey: [k] }))
  }), [])
}
