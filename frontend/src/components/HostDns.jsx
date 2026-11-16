import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Globe, Plus } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn } from './ui.jsx'
import RecordModal from './RecordModal.jsx'

const useDebounced = (v, ms = 500) => { const [d, setD] = useState(v); useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t) }, [v, ms]); return d }

export function useHostStatus(hosts) {
  const key = useDebounced(JSON.stringify([...new Set(hosts)].sort()))
  return useQuery({ queryKey: ['sync', 'check', key], queryFn: () => api.post('/sync/check', { hosts: JSON.parse(key) }), enabled: JSON.parse(key).length > 0 })
}

export function suggestionToRecord(row) {
  const s = row.suggestion || {}
  return { fieldType: s.fieldType || 'A', subDomain: row.subDomain, target: s.target || '', ttl: s.ttl || 3600 }
}

// Badges "ce sous-domaine existe-t-il chez OVH ?" avec proposition d'ajout
export default function HostDns({ hosts }) {
  const q = useHostStatus(hosts)
  const [add, setAdd] = useState(null)
  if (!q.data) return null
  if (q.data.every((r) => r.status === 'disabled')) return null
  return (
    <div className="rule-preview col" style={{ gap: 6 }}>
      {q.data.map((r) => (
        <div className="row wrap" key={r.host}>
          <Globe size={14} className="muted" /><code>{r.host}</code>
          {r.status === 'ok' && <Badge kind="ok">DNS OK · {r.records[0].fieldType} → {r.records[0].target}</Badge>}
          {r.status === 'wildcard' && <Badge kind="info">couvert par un wildcard</Badge>}
          {r.status === 'external' && <Badge>zone hors de vos comptes</Badge>}
          {r.status === 'local' && <Badge kind="info">DNS local · rien à créer</Badge>}
          {r.status === 'managed' && <Badge kind="ok">DuckDNS{r.ip ? ` · ${r.ip}` : ''}</Badge>}
          {r.status === 'error' && <Badge kind="err">{r.error}</Badge>}
          {r.status === 'missing' && <><Badge kind="warn">absent chez OVH</Badge><Btn small kind="primary" onClick={() => setAdd(r)}><Plus size={12} /> Ajouter au DNS</Btn></>}
        </div>
      ))}
      {add && <RecordModal zone={add.zone} initial={suggestionToRecord(add)} onClose={() => setAdd(null)} />}
    </div>
  )
}
