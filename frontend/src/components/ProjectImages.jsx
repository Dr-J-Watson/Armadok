import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellRing, CalendarClock, Download, ExternalLink, RefreshCw, Rocket } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Empty, Field, Loading, Modal, Switch, useUi } from './ui.jsx'
import { JobOutput } from './ComposeBar.jsx'
import { ago } from '../lib/features.js'

const DAYS = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
const fmtDate = (t) => new Date(t * 1000).toLocaleString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })

// Planning de la vérification : toutes les N heures/jours, certains jours de la semaine à une heure, ou un jour du mois à une heure (heure du serveur).
function ScheduleModal({ initial, onSave, onClose, busy }) {
  const [sp, setSp] = useState(initial)
  const set = (k, v) => setSp((o) => ({ ...o, [k]: v }))
  const kind = (k) => setSp(k === 'every' ? { kind: 'every', every: 1, unit: 'days' } : k === 'weekly' ? { kind: 'weekly', days: [1], time: '16:00' } : { kind: 'monthly', day: 10, time: '09:00' })
  const toggle = (d) => set('days', sp.days.includes(d) ? sp.days.filter((x) => x !== d) : [...sp.days, d].sort())
  return (
    <Modal title="Quand vérifier les nouvelles versions" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={sp.kind === 'weekly' && !sp.days.length} onClick={() => onSave(sp)}>Enregistrer</Btn></>}>
      <div className="col" style={{ gap: 12 }}>
        <Field label="Fréquence"><select className="select" value={sp.kind} onChange={(e) => kind(e.target.value)}><option value="every">Toutes les … heures ou jours</option><option value="weekly">Certains jours de la semaine</option><option value="monthly">Un jour du mois</option></select></Field>
        {sp.kind === 'every' && <Field label="Toutes les"><div className="row" style={{ gap: 8 }}><input className="input" type="number" min="1" max="90" style={{ width: 90 }} value={sp.every} onChange={(e) => set('every', e.target.value)} /><select className="select" style={{ width: 'auto' }} value={sp.unit} onChange={(e) => set('unit', e.target.value)}><option value="hours">heures</option><option value="days">jours</option></select></div></Field>}
        {sp.kind === 'weekly' && <Field label="Les"><div className="row wrap" style={{ gap: 6 }}>{DAYS.map((n, i) => <Btn key={n} small kind={sp.days.includes(i) ? 'primary' : undefined} onClick={() => toggle(i)}>{n}</Btn>)}</div></Field>}
        {sp.kind === 'monthly' && <Field label="Le" help="Un mois plus court (ex. le 31 en février) : son dernier jour."><div className="row" style={{ gap: 8 }}><input className="input" type="number" min="1" max="31" style={{ width: 90 }} value={sp.day} onChange={(e) => set('day', e.target.value)} /><span className="muted">de chaque mois</span></div></Field>}
        {sp.kind !== 'every' && <Field label="À" help="Heure du serveur."><input className="input" type="time" style={{ width: 130 }} value={sp.time} onChange={(e) => set('time', e.target.value)} /></Field>}
      </div>
    </Modal>
  )
}

const ST = { latest: ['À jour', 'ok'], update: ['Nouvelle version', 'info'], absent: ['Non téléchargée', ''], pinned: ['Épinglée (empreinte)', ''], unknown: ['Inconnue', 'warn'], unresolved: ['Nom variable', ''] }

// Suivi des images d'un projet : comparaison de l'empreinte avec le registre (rien n'est téléchargé pour vérifier).
export default function ProjectImages({ id, perms }) {
  const { toast } = useUi(); const qc = useQueryClient(); const [job, setJob] = useState(null); const [edit, setEdit] = useState(false)
  const q = useQuery({ queryKey: ['project-images', id], queryFn: () => api.get(`/projects/${id}/images`) })
  const check = useMutation({ mutationFn: () => api.post(`/projects/${id}/images/check`), onSuccess: (r) => { qc.setQueryData(['project-images', id], (o) => ({ ...o, ...r })); const n = r.images.filter((i) => i.status === 'update').length; toast(n ? `${n} image${n > 1 ? 's' : ''} avec une nouvelle version` : 'Tout est à jour') }, onError: (e) => toast(e.message, 'err') })
  const track = useMutation({ mutationFn: (body) => api.put(`/projects/${id}/images/track`, body), onSuccess: (r) => { qc.setQueryData(['project-images', id], (o) => ({ ...o, ...r })); setEdit(false); toast(r.track ? 'Vérification automatique activée' : 'Vérification automatique désactivée') }, onError: (e) => toast(e.message, 'err') })
  const act = useMutation({ mutationFn: (body) => api.post(`/projects/${id}/compose`, body), onSuccess: (r, v) => setJob({ ...r, action: v.action }), onError: (e) => toast(e.message, 'err') })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data; const pending = d.images.some((i) => i.status === 'update' || i.needsRedeploy)
  return (
    <div className="col">
      <div className="card col" style={{ gap: 8 }}>
        <div className="row wrap" style={{ gap: 8 }}><b className="grow">Images du projet</b>
          {perms.git && <Btn title="Cherche, pour chaque image du projet, une version plus récente sur son registre (sans rien télécharger)" busy={check.isPending} onClick={() => check.mutate()}><RefreshCw size={14} /> Vérifier maintenant</Btn>}
          {perms.deploy && <Btn busy={act.isPending} onClick={() => act.mutate({ action: 'pull' })} title="Télécharge les nouvelles versions (sans redéployer)"><Download size={14} /> Télécharger</Btn>}
          {perms.deploy && <Btn kind={pending ? 'primary' : undefined} busy={act.isPending} onClick={() => act.mutate({ action: 'up' })} title="Recrée les conteneurs dont l'image a changé"><Rocket size={14} /> Redéployer</Btn>}</div>
        {perms.git && d.hours > 0 && <div className="row wrap" style={{ gap: 10 }}><Switch value={d.track} busy={track.isPending} onChange={() => track.mutate({ enabled: !d.track })} title="Vérification automatique" /><span className="small"><BellRing size={13} style={{ verticalAlign: -2 }} /> Vérifier automatiquement · <b>{d.scheduleText}</b></span>
          <Btn small onClick={() => setEdit(true)} title="Choisir la fréquence : toutes les N heures, un jour de la semaine, un jour du mois…"><CalendarClock size={13} /> Planifier</Btn>
          {d.track && d.nextRun && <span className="muted small">prochaine vérification : {fmtDate(d.nextRun)}</span>}
          <span className="muted small">{d.webhook ? 'Une notification est envoyée au webhook du nœud à chaque nouvelle version.' : 'Aucun webhook sur ce nœud : la nouvelle version apparaît ici et dans le journal, sans message (réglez-le dans « Ce nœud »).'}</span></div>}
      </div>
      {d.images.length === 0 ? <div className="card"><Empty><div>Aucun service de ce projet n'utilise d'image du registre (images construites localement).</div></Empty></div> : (
        <div className="card flush"><table className="t"><thead><tr><th>Service</th><th>Image</th><th>État</th><th>Vérifié</th></tr></thead><tbody>
          {d.images.map((i) => { const [lab, kind] = ST[i.status] || ['?', '']; return (
            <tr key={i.service}><td><b>{i.service}</b></td><td className="mono small">{i.packageUrl ? <a href={i.packageUrl} target="_blank" rel="noreferrer noopener" title="Ouvrir la page de l'image (versions, notes de publication)">{i.image} <ExternalLink size={11} style={{ verticalAlign: -1 }} /></a> : i.image}{i.local && <div className="muted">locale {i.local}{i.remote ? ` · registre ${i.remote}` : ''}</div>}</td>
              <td><Badge kind={kind}>{lab}</Badge>{i.newerTag && <Badge kind="info" style={{ marginLeft: 6 }} title={`Une version plus récente existe : ${i.newerImage}. Changez le tag dans le fichier compose pour l'utiliser.`}>version {i.newerTag}</Badge>}{i.needsRedeploy && <Badge kind="warn" style={{ marginLeft: 6 }} title="Une version plus récente est téléchargée mais les conteneurs tournent encore avec l'ancienne">à redéployer</Badge>}{i.error && <div className="muted small">{i.error}</div>}</td>
              <td className="muted small">{i.checkedAt ? ago(i.checkedAt) : '—'}</td></tr>) })}</tbody></table></div>)}
      <div className="muted small">La comparaison porte sur l'empreinte du manifeste au registre public, et, quand le tag est un numéro de version (1.27, v2.3.1-alpine…), sur les versions plus hautes publiées ; les registres privés ne sont pas interrogés. Les images construites localement n'ont pas de version au registre.</div>
      {edit && <ScheduleModal initial={d.schedule} busy={track.isPending} onClose={() => setEdit(false)} onSave={(schedule) => track.mutate({ schedule, enabled: true })} />}
      {job && <JobOutput id={id} job={job} onClose={() => { setJob(null); qc.invalidateQueries({ queryKey: ['project-images', id] }) }} />}
    </div>
  )
}
