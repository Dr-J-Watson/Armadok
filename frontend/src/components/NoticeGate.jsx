import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BellRing, ShieldAlert } from 'lucide-react'
import { api } from '../api.js'
import { Btn, Modal, useUi } from './ui.jsx'
import { fmtDate } from '../lib/util.js'

const ICON = { emergency: <ShieldAlert size={18} /> }

// Avis de l'administrateur de la plateforme : à lire (et acquitter) avant de continuer. Ils ne peuvent pas être supprimés.
export default function NoticeGate() {
  const { toast } = useUi(); const qc = useQueryClient()
  const q = useQuery({ queryKey: ['notices'], queryFn: () => api.get('/notices'), staleTime: 60000 })
  if (!q.data?.length) return null
  const ack = () => api.post('/notices/ack').then(() => qc.invalidateQueries({ queryKey: ['notices'] })).catch((e) => toast(e.message, 'err'))
  return (
    <Modal title={<span className="row"><BellRing size={18} /> {q.data.length > 1 ? `${q.data.length} avis importants` : 'Avis important'}</span>} onClose={() => {}} footer={<Btn kind="primary" onClick={ack}>J'ai pris connaissance</Btn>}>
      <div className="col gap-lg">
        <div className="muted small">L'administrateur de la plateforme est intervenu sur vos nœuds ou votre compte. Ces avis restent consultables.</div>
        {q.data.map((n) => (
          <div key={n.id} className={'notice ' + (n.kind === 'emergency' || n.kind === 'banned' || n.kind === 'deleted' ? 'danger' : '')}>
            <div className="row">{ICON[n.kind] || <BellRing size={16} />}<b className="grow">{n.nodeName}</b><span className="muted small">{fmtDate(n.createdAt * 1000)}</span></div>
            <div>{n.message}</div>
            {n.kind === 'emergency' && (n.actions.length
              ? <details open><summary className="muted small">Actions effectuées pendant l'intervention ({n.actions.length})</summary>
                <div className="col" style={{ gap: 4, marginTop: 6 }}>{n.actions.map((a, i) => <div key={i} className="small"><code>{a.action.replace(/^traefik\./, '')}</code> {a.detail} <span className="muted">· {fmtDate(a.ts)}</span></div>)}</div></details>
              : <div className="muted small">Aucune modification n'a été faite pour l'instant.</div>)}
          </div>))}
      </div>
    </Modal>
  )
}
