import { useQuery } from '@tanstack/react-query'
import { Globe, Link2, Plus, Route, TriangleAlert, Users } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Empty, Loading } from '../components/ui.jsx'
import { useMeta, useOverview } from './Traefik.jsx'
import { useAuth } from '../auth.jsx'
import { useNode } from '../node.jsx'
import ActivityFeed from '../components/ActivityFeed.jsx'
import { isManaged } from './Sites.jsx'
import Pulse from '../components/Pulse.jsx'
import { useGo } from '../lib/nav.js'

const STAT_HELP = {
  Sites: 'Nombre de sites actifs (routeurs HTTP). Les sites en pause ne sont pas comptés.',
  Domaines: 'Zones DNS auxquelles vous avez accès sur ce nœud.',
  Problèmes: 'Erreurs et avertissements de configuration : références cassées, doublons, certificats ou entrypoints inconnus.',
}

// Où mène un problème : le site concerné, l'élément Traefik, ou la liste des problèmes
function issueTarget(i) {
  const [proto, kind, ...rest] = String(i.item || '').split('/'); const name = rest.join('/')
  if (!name) return i.file ? '/traefik?tab=files' : null
  if (kind === 'routers' && proto === 'http') return `/sites?open=${encodeURIComponent(name)}`
  return `/traefik?edit=${encodeURIComponent(i.item)}`
}

export default function Dashboard({ openWizard }) {
  const nav = useGo(); const { user } = useAuth(); const { node, hasTraefik, canManage, can } = useNode(); const ov = useOverview(); const meta = useMeta()
  const admin = canManage
  const zones = useQuery({ queryKey: ['zones'], queryFn: () => api.get('/dns/zones'), enabled: !!meta.data?.dnsEnabled })
  const sync = useQuery({ queryKey: ['sync'], queryFn: () => api.get('/sync'), enabled: !!meta.data?.dnsEnabled })
  const audit = useQuery({ queryKey: ['audit', node?.id], queryFn: () => api.get(`/nodes/${node.id}/audit`), enabled: admin && !!node })
  if (hasTraefik && ov.isLoading) return <Loading />
  const sites = (ov.data?.items || []).filter((i) => i.kind === 'routers' && i.proto === 'http' && !i.disabled && !isManaged(i)).length
  const issues = (ov.data?.issues || []).filter((i) => i.level !== 'info')
  const missing = sync.data?.rows.filter((r) => r.status === 'missing' && r.canEdit).length ?? 0
  const dirty = (zones.data || []).filter((z) => z.dirty)
  const stats = [...(hasTraefik ? [['Sites', sites, <Route size={16} />, '/sites']] : []), ['Domaines', zones.data?.length ?? '–', <Globe size={16} />, '/dns'], ...(admin ? [['Problèmes', issues.length, <TriangleAlert size={16} />, '/traefik?tab=problems']] : [])]
  return (
    <>
      <div className="page-head"><div><h1>Bonjour {user.username} 👋</h1><p>{node ? `${node.name} · ${node.roleLabel}` : 'Aucun nœud'}{meta.data?.ovh?.mock ? ' · OVH en mode démo' : ''}</p></div>
        {hasTraefik && can('sites.create') && <Btn kind="primary" className="right" onClick={() => openWizard({})}><Plus size={15} /> Nouveau site</Btn>}</div>
      {meta.data && !meta.data.dnsEnabled && <div className="banner info">Aucun domaine pour l'instant : ajoutez un compte (OVH, DuckDNS) ou un domaine en DNS local dans « Comptes DNS ».</div>}
      {missing > 0 && <div className="banner"><Link2 size={18} />{missing} adresse(s) de vos sites n'ont pas d'enregistrement DNS.<Btn small kind="primary" className="right" onClick={() => nav('/sites')}>Voir</Btn></div>}
      {dirty.length > 0 && <div className="banner info">Modifications DNS non appliquées : {dirty.map((z) => z.name).join(', ')}<Btn small className="right" onClick={() => nav('/dns?zone=' + dirty[0].name)}>Ouvrir</Btn></div>}
      <div className="grid stats" style={{ marginBottom: 18 }}>{stats.map(([l, v, ic, to]) => <div key={l} className="card stat" data-tip={STAT_HELP[l]} onClick={() => nav(to)}><div className="muted row" style={{ gap: 6 }}>{ic}{l}</div><b>{v}</b></div>)}</div>
      <Pulse />
      {admin && <div className="grid two">
        <div className="card flush"><div style={{ padding: '14px 16px' }}><h2>À surveiller</h2></div>
          {issues.length === 0 ? <Empty><div>Rien à signaler ✔</div><div className="row wrap" style={{ justifyContent: 'center', marginTop: 12, gap: 8 }}>{hasTraefik && can('sites.create') && <Btn small kind="primary" onClick={() => openWizard({})}><Plus size={13} /> Nouveau site</Btn>}{hasTraefik && <Btn small onClick={() => nav('/sites')}>Voir les sites</Btn>}<Btn small kind="ghost" onClick={() => nav('/help?tab=start')}>Guide de démarrage</Btn></div></Empty> : issues.slice(0, 8).map((i, k) => <div key={k} className="issue" title={issueTarget(i) ? 'Ouvrir' : undefined} onClick={() => nav(issueTarget(i) || '/traefik?tab=problems')}><Badge kind={i.level === 'error' ? 'err' : 'warn'}>{i.level}</Badge><div>{i.message}<div className="muted small">{i.item || i.file}</div></div></div>)}</div>
        <div className="card flush"><div style={{ padding: '14px 16px' }}><h2>Activité récente</h2></div>
          <div style={{ padding: '0 8px 8px' }}><ActivityFeed entries={(audit.data || []).filter((e) => !e.action.startsWith('alert.') || e.action === 'alert.rule').slice(0, 7)} compact /></div></div>
      </div>}
      {!admin && !hasTraefik && (zones.data?.length ?? 0) === 0 && <div className="card"><Empty><Users size={28} /><div>Aucun domaine ne vous est encore partagé.<br />Demandez à un administrateur ou au propriétaire d'un domaine.</div></Empty></div>}
    </>
  )
}
