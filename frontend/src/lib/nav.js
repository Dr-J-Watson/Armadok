import { useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useNode } from '../node.jsx'

// Navigation qui se souvient d'où l'on vient : la page d'arrivée peut afficher « ← Retour ».
export function useGo() {
  const nav = useNavigate(); const loc = useLocation()
  return useCallback((to, opts = {}) => nav(to, { ...opts, state: { back: loc.pathname + loc.search, ...(opts.state || {}) } }), [nav, loc.pathname, loc.search])
}

// État à passer à un <NavLink state=…> / <Link state=…>

const NAMES = { '/': 'à l\'accueil', '/sites': 'aux sites', '/dns': 'aux domaines', '/monitoring': 'à la surveillance', '/docker': 'à Docker', '/projects': 'aux projets', '/traefik': 'à Traefik avancé', '/node': 'à ce nœud', '/nodes': 'aux nœuds', '/middlewares': 'aux middlewares', '/rights': 'aux droits', '/accounts': 'aux comptes DNS' }
export const backLabel = (path) => NAMES[(path || '').split('?')[0]] || 'à la page précédente'

// Changer de nœud EN RESTANT sur la même page (une page qui n'existe pas sur le nouveau nœud renvoie à l'accueil toute seule). Une fiche propre à un nœud
// (un projet, un conteneur ouvert) retombe sur sa liste ; l'onglet choisi est gardé.
export function useSwitchNode() {
  const { node, nodes, setNode } = useNode(); const nav = useNavigate(); const loc = useLocation()
  const to = useCallback((id) => {
    if (!id || id === node?.id) return
    const here = loc.pathname.replace(/\/+$/, '') || '/'
    const path = /^\/projects\/\d+/.test(here) ? '/projects' : here
    const tab = path === here ? new URLSearchParams(loc.search).get('tab') : null
    setNode(id)
    nav(path + (tab ? `?tab=${encodeURIComponent(tab)}` : ''), { replace: true })
  }, [node?.id, loc.pathname, loc.search, setNode, nav])
  const step = useCallback((d) => { if (nodes.length < 2) return; const i = nodes.findIndex((n) => n.id === node?.id); to(nodes[(i + d + nodes.length) % nodes.length].id) }, [nodes, node?.id, to])
  return { to, step, nodes, node }
}
