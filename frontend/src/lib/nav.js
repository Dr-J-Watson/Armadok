import { useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

// Navigation qui se souvient d'où l'on vient : la page d'arrivée peut afficher « ← Retour ».
export function useGo() {
  const nav = useNavigate(); const loc = useLocation()
  return useCallback((to, opts = {}) => nav(to, { ...opts, state: { back: loc.pathname + loc.search, ...(opts.state || {}) } }), [nav, loc.pathname, loc.search])
}

// État à passer à un <NavLink state=…> / <Link state=…>
export const backState = (loc) => ({ back: loc.pathname + loc.search })

const NAMES = { '/': 'à l\'accueil', '/sites': 'aux sites', '/dns': 'aux domaines', '/monitoring': 'à la surveillance', '/docker': 'à Docker', '/projects': 'aux projets', '/traefik': 'à Traefik avancé', '/node': 'à ce nœud', '/nodes': 'aux nœuds', '/middlewares': 'aux middlewares', '/rights': 'aux droits', '/accounts': 'aux comptes DNS' }
export const backLabel = (path) => NAMES[(path || '').split('?')[0]] || 'à la page précédente'
