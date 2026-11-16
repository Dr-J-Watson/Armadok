// Ce qui est branché sur le nœud courant (certificats, Prometheus, journal d'accès…) et ce que la personne a le droit de voir.
// Une fonctionnalité non branchée est CACHÉE de l'interface ; l'aide (« Fonctionnalités facultatives ») dit comment l'activer.
import { useQuery } from '@tanstack/react-query'
import { api } from '../api.js'
import { useNode } from '../node.jsx'

export function useFeatures() {
  const { node } = useNode()
  const q = useQuery({ queryKey: ['features', node?.id], queryFn: () => api.get('/obs/features'), enabled: !!node, staleTime: 30000, retry: false })
  return q.data
}

export const fmtNum = (v, d = 1) => (v == null ? '—' : Math.abs(v) >= 100 ? Math.round(v).toLocaleString('fr-FR') : v.toLocaleString('fr-FR', { maximumFractionDigits: d }))
export const fmtRate = (v) => (v == null ? '—' : v >= 10 ? Math.round(v).toLocaleString('fr-FR') : v.toLocaleString('fr-FR', { maximumFractionDigits: v < 1 ? 2 : 1 }))
export const fmtBytes = (v) => (v == null ? '—' : v >= 1e6 ? `${(v / 1e6).toFixed(1).replace('.', ',')} Mo/s` : v >= 1e3 ? `${(v / 1e3).toFixed(1).replace('.', ',')} Ko/s` : `${Math.round(v)} o/s`)
export const fmtMs = (s) => (s == null ? '—' : s < 1 ? `${Math.round(s * 1000)} ms` : `${s.toFixed(2).replace('.', ',')} s`)
export const ago = (ts) => { if (!ts) return '—'; const s = Math.max(0, Date.now() / 1000 - ts); return s < 90 ? 'à l\'instant' : s < 5400 ? `il y a ${Math.round(s / 60)} min` : s < 129600 ? `il y a ${Math.round(s / 3600)} h` : `il y a ${Math.round(s / 86400)} j` }
