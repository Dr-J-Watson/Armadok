import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellRing, Download, RefreshCw, Rocket } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Empty, Loading, Switch, useUi } from './ui.jsx'
import { JobOutput } from './ComposeBar.jsx'
import { ago } from '../lib/features.js'

const ST = { latest: ['À jour', 'ok'], update: ['Nouvelle version', 'info'], absent: ['Non téléchargée', ''], pinned: ['Épinglée (empreinte)', ''], unknown: ['Inconnue', 'warn'], unresolved: ['Nom variable', ''] }

// Suivi des images d'un projet : comparaison de l'empreinte avec le registre (rien n'est téléchargé pour vérifier).
export default function ProjectImages({ id, perms }) {
  const { toast } = useUi(); const qc = useQueryClient(); const [job, setJob] = useState(null)
  const q = useQuery({ queryKey: ['project-images', id], queryFn: () => api.get(`/projects/${id}/images`) })
  const check = useMutation({ mutationFn: () => api.post(`/projects/${id}/images/check`), onSuccess: (r) => { qc.setQueryData(['project-images', id], (o) => ({ ...o, ...r })); const n = r.images.filter((i) => i.status === 'update').length; toast(n ? `${n} image${n > 1 ? 's' : ''} avec une nouvelle version` : 'Tout est à jour') }, onError: (e) => toast(e.message, 'err') })
  const track = useMutation({ mutationFn: (enabled) => api.put(`/projects/${id}/images/track`, { enabled }), onSuccess: (r) => { qc.setQueryData(['project-images', id], (o) => ({ ...o, track: r.track })); toast(r.track ? 'Vérification quotidienne activée' : 'Vérification quotidienne désactivée') }, onError: (e) => toast(e.message, 'err') })
  const act = useMutation({ mutationFn: (body) => api.post(`/projects/${id}/compose`, body), onSuccess: (r, v) => setJob({ ...r, action: v.action }), onError: (e) => toast(e.message, 'err') })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data; const pending = d.images.some((i) => i.status === 'update' || i.needsRedeploy)
  return (
    <div className="col">
      <div className="card col" style={{ gap: 8 }}>
        <div className="row wrap" style={{ gap: 8 }}><b className="grow">Images du projet</b>
          {perms.git && <Btn busy={check.isPending} onClick={() => check.mutate()}><RefreshCw size={14} /> Vérifier maintenant</Btn>}
          {perms.deploy && <Btn busy={act.isPending} onClick={() => act.mutate({ action: 'pull' })} title="Télécharge les nouvelles versions (sans redéployer)"><Download size={14} /> Télécharger</Btn>}
          {perms.deploy && <Btn kind={pending ? 'primary' : undefined} busy={act.isPending} onClick={() => act.mutate({ action: 'up' })} title="Recrée les conteneurs dont l'image a changé"><Rocket size={14} /> Redéployer</Btn>}</div>
        {perms.git && d.hours > 0 && <div className="row" style={{ gap: 10 }}><Switch value={d.track} busy={track.isPending} onChange={() => track.mutate(!d.track)} title="Vérification quotidienne" /><span className="small"><BellRing size={13} style={{ verticalAlign: -2 }} /> Vérifier chaque jour{d.track ? ' (activé)' : ''}</span>
          <span className="muted small">{d.webhook ? 'Une notification est envoyée au webhook du nœud à chaque nouvelle version.' : 'Aucun webhook sur ce nœud : la nouvelle version apparaît ici et dans le journal, sans message (réglez-le dans « Ce nœud »).'}</span></div>}
      </div>
      {d.images.length === 0 ? <div className="card"><Empty><div>Aucun service de ce projet n'utilise d'image du registre (images construites localement).</div></Empty></div> : (
        <div className="card flush"><table className="t"><thead><tr><th>Service</th><th>Image</th><th>État</th><th>Vérifié</th></tr></thead><tbody>
          {d.images.map((i) => { const [lab, kind] = ST[i.status] || ['?', '']; return (
            <tr key={i.service}><td><b>{i.service}</b></td><td className="mono small">{i.image}{i.local && <div className="muted">locale {i.local}{i.remote ? ` · registre ${i.remote}` : ''}</div>}</td>
              <td><Badge kind={kind}>{lab}</Badge>{i.needsRedeploy && <Badge kind="warn" style={{ marginLeft: 6 }} title="Une version plus récente est téléchargée mais les conteneurs tournent encore avec l'ancienne">à redéployer</Badge>}{i.error && <div className="muted small">{i.error}</div>}</td>
              <td className="muted small">{i.checkedAt ? ago(i.checkedAt) : '—'}</td></tr>) })}</tbody></table></div>)}
      <div className="muted small">La comparaison porte sur l'empreinte du manifeste au registre public ; les registres privés ne sont pas interrogés. Les images construites localement n'ont pas de version au registre.</div>
      {job && <JobOutput id={id} job={job} onClose={() => { setJob(null); qc.invalidateQueries({ queryKey: ['project-images', id] }) }} />}
    </div>
  )
}
