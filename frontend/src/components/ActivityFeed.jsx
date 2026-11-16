import { useMemo, useState } from 'react'
import { Activity, ShieldAlert } from 'lucide-react'
import { CATEGORIES, byDay, describe, parseTs, targetOf, timeLabel } from '../lib/activity.js'
import { useGo } from '../lib/nav.js'
import { Empty, SearchBox, Seg } from './ui.jsx'

// Fil d'activité : texte lisible, regroupé par jour, avec recherche et filtres.
export default function ActivityFeed({ entries, compact = false }) {
  const go = useGo(); const [q, setQ] = useState(''); const [cat, setCat] = useState('all'); const [who, setWho] = useState('')
  const users = useMemo(() => [...new Set(entries.map((e) => e.user))].sort(), [entries])
  const shown = entries.filter((e) => {
    const d = describe(e)
    return (cat === 'all' || d.cat === cat) && (!who || e.user === who) && (!q || `${d.label} ${e.detail} ${e.user} ${e.action}`.toLowerCase().includes(q.toLowerCase()))
  })
  const days = byDay(shown, (e) => parseTs(e.ts))
  return (
    <div className="col">
      {!compact && (
        <div className="row wrap">
          <SearchBox value={q} onChange={setQ} placeholder="Rechercher une action, un fichier, un utilisateur…" />
          <Seg options={CATEGORIES.map(([id, label]) => ({ id, label }))} value={cat} onChange={setCat} />
          {users.length > 1 && <select className="select" style={{ width: 'auto' }} value={who} onChange={(e) => setWho(e.target.value)}><option value="">Tout le monde</option>{users.map((u) => <option key={u}>{u}</option>)}</select>}
        </div>)}
      {!days.length ? <div className="card"><Empty><Activity size={28} /><div>{entries.length ? 'Rien ne correspond à ce filtre.' : 'Aucune activité pour l\'instant.'}</div></Empty></div> : (
        <div className="feed">
          {days.map((g) => (
            <div key={g.key}>
              <div className="feed-day">{g.key}</div>
              {g.items.map((e, i) => {
                const d = describe(e); const Icon = d.icon; const t = parseTs(e.ts); const to = targetOf(e)
                return (
                  <div key={i} className={'feed-row ' + (e.session ? 'urgent ' : '') + (to ? 'go' : '')} onClick={to ? () => go(to) : undefined} title={to ? 'Ouvrir' : undefined}>
                    <span className={'feed-ic ' + d.tone}><Icon size={15} /></span>
                    <div className="feed-main">
                      <div className="feed-title">{d.label}{e.session && <span className="badge err"><ShieldAlert size={10} /> urgence</span>}</div>
                      {e.detail && <div className="feed-detail">{e.detail}</div>}
                    </div>
                    <div className="feed-who"><span className="avatar sm">{(e.user || '?').charAt(0).toUpperCase()}</span><span className="muted small">{e.user}</span></div>
                    <time className="feed-time muted small" title={t.toLocaleString('fr-FR')}>{timeLabel(t)}</time>
                  </div>)
              })}
            </div>))}
        </div>)}
    </div>
  )
}
