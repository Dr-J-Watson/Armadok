import { useQuery } from '@tanstack/react-query'
import { Activity, Ban, BellRing, Lock, TriangleAlert } from 'lucide-react'
import { api } from '../api.js'
import { fmtNum, fmtRate, useFeatures } from '../lib/features.js'
import { useNode } from '../node.jsx'
import { useGo } from '../lib/nav.js'

// Quelques chiffres vivants sur l'accueil ; chacun ouvre la page qui détaille.
export default function Pulse() {
  const f = useFeatures(); const go = useGo(); const { hasTraefik, can } = useNode()
  const metrics = !!(f && f.metrics.enabled && f.metrics.allowed)
  const bansOn = !!(f && f.bans.enabled && f.bans.allowed)
  const ov = useQuery({ queryKey: ['obs-overview'], queryFn: () => api.get('/obs/overview'), enabled: metrics, refetchInterval: 30000, retry: false })
  const bans = useQuery({ queryKey: ['obs-bans'], queryFn: () => api.get('/obs/bans'), enabled: bansOn, refetchInterval: 60000, retry: false })
  const certs = useQuery({ queryKey: ['certs'], queryFn: () => api.get('/traefik/certs'), enabled: hasTraefik && can('certs.view'), staleTime: 60000, retry: false })
  const i = ov.data?.instant; const cards = []
  if (metrics && i) {
    cards.push(['Requêtes / s', fmtRate(i.reqRate), '', <Activity size={15} />, '/monitoring?tab=overview', 'Trafic de tous vos sites'])
    const pct = i.reqRate ? (i.err5 / i.reqRate) * 100 : 0
    cards.push(['Erreurs serveur', i.reqRate ? `${fmtNum(pct, 1)} %` : '—', pct > 5 ? 'err' : pct > 0 ? 'warn' : 'ok', <TriangleAlert size={15} />, '/monitoring?tab=services', 'Part des réponses 5xx : voir quel site en est la cause'])
  }
  if (f?.alerts.enabled && f.alerts.allowed) cards.push(['Alertes en cours', f.firing || 0, f.firing ? 'err' : 'ok', <BellRing size={15} />, '/monitoring?tab=alerts', 'Alertes déclenchées'])
  if (bansOn && bans.data) cards.push(['Adresses bannies', bans.data.bans.length, '', <Ban size={15} />, '/monitoring?tab=bans', 'Adresses qui reçoivent une erreur 403'])
  if (certs.data?.configured) {
    const bad = certs.data.sites.filter((s) => ['expired', 'expiring', 'missing'].includes(s.worst)).length
    cards.push(['Certificats à régler', bad, bad ? 'warn' : 'ok', <Lock size={15} />, '/traefik?tab=certs', 'Sites avec un certificat expiré, bientôt expiré ou manquant'])
  }
  if (!cards.length) return null
  return <div className="grid stats" style={{ marginBottom: 18 }}>{cards.map(([l, v, kind, ic, to, tip]) => (
    <div key={l} className="card stat link" data-tip={tip} role="link" tabIndex={0} onClick={() => go(to)} onKeyDown={(e) => e.key === 'Enter' && go(to)} style={{ cursor: 'pointer' }}><div className="muted row" style={{ gap: 6 }}>{ic}{l}</div><b style={kind === 'err' ? { color: 'var(--err)' } : kind === 'warn' ? { color: 'var(--warn)' } : kind === 'ok' ? { color: 'var(--ok)' } : undefined}>{v}</b></div>))}</div>
}
