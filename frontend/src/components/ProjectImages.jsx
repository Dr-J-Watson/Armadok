import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellRing, CalendarClock, Download, ExternalLink, RefreshCw, Rocket } from 'lucide-react'
import { api } from '../api.js'
import { useNode } from '../node.jsx'
import { useGo } from '../lib/nav.js'
import { ImageModal } from './DockerDetail.jsx'
import { Badge, Btn, Empty, Loading, Modal, Switch, useUi } from './ui.jsx'
import { JobOutput } from './ComposeBar.jsx'
import { ago } from '../lib/features.js'

const DAYS = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
const MONTHS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.']
const fmtDate = (t) => new Date(t * 1000).toLocaleString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', second: '2-digit' })
const KINDS = { every: 'Toutes les … (durée)', daily: 'Chaque jour à …', weekly: 'Certains jours de la semaine', monthly: 'Certains jours du mois', nth: 'Le 1er, 2e… (ou dernier) lundi du mois', once: 'Une seule fois', cron: 'Expression avancée (cron)' }
const NEW = {
  every: { kind: 'every', days: 0, hours: 6, minutes: 0, seconds: 0 }, daily: { kind: 'daily', every: 1, time: '03:00:00' }, weekly: { kind: 'weekly', days: [1], time: '16:00:00' },
  monthly: { kind: 'monthly', days: [10], months: [], time: '09:00:00' }, nth: { kind: 'nth', nth: 1, weekday: 0, months: [], time: '08:00:00' },
  once: { kind: 'once', at: new Date(Date.now() + 86400000).toISOString().slice(0, 11) + '09:00:00' }, cron: { kind: 'cron', expr: '0 0 */6 * * *' },
}
const Chips = ({ items, value, onChange, render }) => <div className="row wrap" style={{ gap: 6 }}>{items.map((x) => <Btn key={x} small kind={value.includes(x) ? 'primary' : undefined} onClick={() => onChange(value.includes(x) ? value.filter((v) => v !== x) : [...value, x])}>{render(x)}</Btn>)}</div>
const Num = ({ v, set, max, w = 70, ...p }) => <input className="input" type="number" min="0" max={max} style={{ width: w }} value={v} onChange={(e) => set(e.target.value === '' ? '' : Number(e.target.value))} {...p} />
const Time = ({ v, set }) => <input className="input" type="time" step="1" style={{ width: 140 }} value={v} onChange={(e) => set(e.target.value)} aria-label="Heure" title="Heure du serveur : heures, minutes et secondes" />

// Une règle du planning, selon son type.
function RuleEditor({ r, set }) {
  const kind = r.kind
  return (
    <div className="col" style={{ gap: 8 }}>
      <select className="select" value={kind} onChange={(e) => set(NEW[e.target.value])}>{Object.entries(KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      {kind === 'every' && <div className="row wrap" style={{ gap: 6 }}>Toutes les <Num v={r.days} set={(v) => set({ ...r, days: v })} max={90} /> j <Num v={r.hours} set={(v) => set({ ...r, hours: v })} max={2160} /> h <Num v={r.minutes} set={(v) => set({ ...r, minutes: v })} max={59} /> min <Num v={r.seconds} set={(v) => set({ ...r, seconds: v })} max={59} /> s</div>}
      {kind === 'daily' && <div className="row wrap" style={{ gap: 6 }}>Tous les <Num v={r.every} set={(v) => set({ ...r, every: v })} max={365} w={70} /> jour(s), à <Time v={r.time} set={(v) => set({ ...r, time: v })} /></div>}
      {kind === 'weekly' && <><Chips items={[0, 1, 2, 3, 4, 5, 6]} value={r.days} onChange={(v) => set({ ...r, days: v })} render={(i) => DAYS[i]} /><div className="row" style={{ gap: 6 }}>à <Time v={r.time} set={(v) => set({ ...r, time: v })} /></div></>}
      {kind === 'monthly' && <><div className="muted small">Jours du mois (le 31 d'un mois plus court tombe sur son dernier jour)</div>
        <div className="row wrap" style={{ gap: 4 }}>{Array.from({ length: 31 }, (_, i) => i + 1).map((d) => <Btn key={d} small kind={r.days.includes(d) ? 'primary' : undefined} onClick={() => set({ ...r, days: r.days.includes(d) ? r.days.filter((x) => x !== d) : [...r.days, d] })}>{d}</Btn>)}
          <Btn small kind={r.days.includes('last') ? 'primary' : undefined} onClick={() => set({ ...r, days: r.days.includes('last') ? r.days.filter((x) => x !== 'last') : [...r.days, 'last'] })}>dernier jour</Btn></div>
        <div className="muted small">Mois (aucun = tous)</div><Chips items={[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]} value={r.months || []} onChange={(v) => set({ ...r, months: v })} render={(m) => MONTHS[m - 1]} />
        <div className="row" style={{ gap: 6 }}>à <Time v={r.time} set={(v) => set({ ...r, time: v })} /></div></>}
      {kind === 'nth' && <><div className="row wrap" style={{ gap: 6 }}>Le <select className="select" style={{ width: 'auto' }} value={r.nth} onChange={(e) => set({ ...r, nth: Number(e.target.value) })}><option value={1}>premier</option><option value={2}>deuxième</option><option value={3}>troisième</option><option value={4}>quatrième</option><option value={-1}>dernier</option></select>
        <select className="select" style={{ width: 'auto' }} value={r.weekday} onChange={(e) => set({ ...r, weekday: Number(e.target.value) })}>{DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</select> du mois, à <Time v={r.time} set={(v) => set({ ...r, time: v })} /></div>
        <div className="muted small">Mois (aucun = tous)</div><Chips items={[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]} value={r.months || []} onChange={(v) => set({ ...r, months: v })} render={(m) => MONTHS[m - 1]} /></>}
      {kind === 'once' && <input className="input" type="datetime-local" step="1" style={{ width: 230 }} value={r.at} onChange={(e) => set({ ...r, at: e.target.value })} />}
      {kind === 'cron' && <><input className="input mono" value={r.expr} onChange={(e) => set({ ...r, expr: e.target.value })} placeholder="secondes minutes heures jour mois jour-semaine" spellCheck={false} />
        <div className="muted small">5 champs (minute heure jour mois jour-semaine) ou 6 avec les secondes en premier. <code>*</code> tout, <code>*/15</code> tous les 15, <code>8-18</code> de 8 à 18, <code>1,15</code> liste, <code>L</code> dernier jour du mois. Exemple : <code>0 30 8 * * 1-5</code> = 8 h 30 min 00 s en semaine.</div></>}
    </div>
  )
}

// Planning de la vérification : une ou plusieurs règles ; la prochaine vérification est la plus proche de toutes (heure du serveur).
function ScheduleModal({ id, initial, onSave, onClose, busy }) {
  const [rules, setRules] = useState(initial.map((r) => (r.kind === 'once' ? { ...r, at: r.at.slice(0, 19) } : r)))
  const [pv, setPv] = useState(null)
  useEffect(() => {
    const t = setTimeout(() => api.post(`/projects/${id}/images/schedule/preview`, { schedule: rules }).then(setPv).catch((e) => setPv({ ok: false, error: e.message })), 350)
    return () => clearTimeout(t)
  }, [rules]) // eslint-disable-line react-hooks/exhaustive-deps
  const setAt = (i, r) => setRules(rules.map((x, j) => (j === i ? r : x)))
  return (
    <Modal wide title="Quand vérifier les nouvelles versions" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={!pv?.ok} onClick={() => onSave(rules)}>Enregistrer</Btn></>}>
      <div className="col" style={{ gap: 12 }}>
        <div className="muted small">Ajoutez autant de règles que vous voulez : la vérification a lieu dès que l'une d'elles tombe. Ex. « chaque mardi à 16 h » <b>et</b> « le 10 de chaque mois à 9 h ».</div>
        {rules.map((r, i) => <div key={i} className="card col" style={{ gap: 8 }}>
          <div className="row"><b className="grow small">Règle {i + 1}</b>{rules.length > 1 && <Btn small kind="danger" onClick={() => setRules(rules.filter((_, j) => j !== i))}>Retirer</Btn>}</div>
          <RuleEditor r={r} set={(x) => setAt(i, x)} /></div>)}
        {rules.length < 10 && <div><Btn small onClick={() => setRules([...rules, NEW.weekly])}>+ Ajouter une règle</Btn></div>}
        <div className="card col" style={{ gap: 4 }}>
          {!pv ? <span className="muted small">…</span> : !pv.ok ? <span className="small" style={{ color: 'var(--err, #f87171)' }}>{pv.error}</span> : <>
            <b className="small">{pv.text.join(' · ')}</b>
            <div className="muted small">Prochaines vérifications :</div>{pv.next.map((t) => <div key={t} className="small">{fmtDate(t)}</div>)}</>}
        </div>
      </div>
    </Modal>
  )
}
const ST = { latest: ['À jour', 'ok'], update: ['Nouvelle version', 'info'], absent: ['Non téléchargée', ''], pinned: ['Épinglée (empreinte)', ''], unknown: ['Inconnue', 'warn'], unresolved: ['Nom variable', ''] }

// Suivi des images d'un projet : comparaison de l'empreinte avec le registre (rien n'est téléchargé pour vérifier).
export default function ProjectImages({ id, perms }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient(); const [job, setJob] = useState(null); const [edit, setEdit] = useState(false); const [detail, setDetail] = useState(null); const { can } = useNode(); const go = useGo(); const canView = can('docker.view')
  const q = useQuery({ queryKey: ['project-images', id], queryFn: () => api.get(`/projects/${id}/images`) })
  const check = useMutation({ mutationFn: () => api.post(`/projects/${id}/images/check`), onSuccess: (r) => { qc.setQueryData(['project-images', id], (o) => ({ ...o, ...r })); const n = r.images.filter((i) => i.status === 'update').length; toast(n ? `${n} image${n > 1 ? 's' : ''} avec une nouvelle version` : 'Tout est à jour') }, onError: (e) => toast(e.message, 'err') })
  const track = useMutation({ mutationFn: (body) => api.put(`/projects/${id}/images/track`, body), onSuccess: (r) => { qc.setQueryData(['project-images', id], (o) => ({ ...o, ...r })); setEdit(false); toast(r.track ? 'Vérification automatique activée' : 'Vérification automatique désactivée') }, onError: (e) => toast(e.message, 'err') })
  const act = useMutation({ mutationFn: (body) => api.post(`/projects/${id}/compose`, body), onSuccess: (r, v) => setJob({ ...r, action: v.action }), onError: (e) => toast(e.message, 'err') })
  const one = async (i) => { if (await confirm({ title: `Redéployer ${i.service} ?`, message: `L'image ${i.image} est téléchargée à nouveau puis le conteneur du service « ${i.service} » est recréé (les autres services ne sont pas touchés, sauf ceux qui en dépendent et manquent). Le service est coupé quelques secondes.`, confirmLabel: 'Redéployer', danger: false })) act.mutate({ action: 'up', pull: true, services: [i.service] }) }
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
        {perms.git && d.hours > 0 && <div className="row wrap" style={{ gap: 10 }}><Switch value={d.track} busy={track.isPending} onChange={() => track.mutate({ enabled: !d.track })} title="Vérification automatique" /><span className="small"><BellRing size={13} style={{ verticalAlign: -2 }} /> Vérifier automatiquement · <b>{d.scheduleText.join(' · ')}</b></span>
          <Btn small onClick={() => setEdit(true)} title="Choisir quand : toutes les N heures/minutes/secondes, certains jours, un jour du mois, plusieurs règles…"><CalendarClock size={13} /> Planifier</Btn>
          {d.track && d.nextRun && <span className="muted small">prochaine vérification : {fmtDate(d.nextRun)}</span>}
          <span className="muted small">{d.webhook ? 'Une notification est envoyée au webhook du nœud à chaque nouvelle version.' : 'Aucun webhook sur ce nœud : la nouvelle version apparaît ici et dans le journal, sans message (réglez-le dans « Ce nœud »).'}</span></div>}
      </div>
      {d.images.length === 0 ? <div className="card"><Empty><div>Aucun service de ce projet n'utilise d'image du registre (images construites localement).</div></Empty></div> : (
        <div className="card flush"><table className="t"><thead><tr><th>Service</th><th>Image</th><th>État</th><th>Vérifié</th>{perms.deploy && <th></th>}</tr></thead><tbody>
          {d.images.map((i) => { const [lab, kind] = ST[i.status] || ['?', '']; return (
            <tr key={i.service} style={i.local && canView ? { cursor: 'pointer' } : undefined} title={i.local ? (canView ? "Voir le détail de l'image (couches, variables, ports, conteneurs qui l'utilisent)" : undefined) : "Image pas encore téléchargée : pas de détail local"} onClick={() => i.local && canView && setDetail(i.local)}><td><b>{i.service}</b></td><td className="mono small">{i.packageUrl ? <a href={i.packageUrl} onClick={(e) => e.stopPropagation()} target="_blank" rel="noreferrer noopener" title="Ouvrir la page de l'image (versions, notes de publication)">{i.image} <ExternalLink size={11} style={{ verticalAlign: -1 }} /></a> : i.image}{i.imageRaw && <div className="muted" title="Valeur lue dans le .env du projet">{i.imageRaw}</div>}{i.local && <div className="muted">locale {i.local}{i.remote ? ` · registre ${i.remote}` : ''}</div>}</td>
              <td><Badge kind={kind}>{lab}</Badge>{i.newerTag && <Badge kind="info" style={{ marginLeft: 6 }} title={`Une version plus récente existe : ${i.newerImage}. ${i.imageRaw ? `Changez la variable dans le .env (${i.imageRaw}) pour l'utiliser.` : "Changez le tag dans le fichier compose pour l'utiliser."}`}>version {i.newerTag}</Badge>}{i.needsRedeploy && <Badge kind="warn" style={{ marginLeft: 6 }} title="Une version plus récente est téléchargée mais les conteneurs tournent encore avec l'ancienne">à redéployer</Badge>}{i.error && <div className="muted small">{i.error}</div>}</td>
              <td className="muted small">{i.checkedAt ? ago(i.checkedAt) : '—'}</td>
              {perms.deploy && <td style={{ textAlign: 'right' }}><Btn small kind={i.status === 'update' || i.needsRedeploy ? 'primary' : undefined} busy={act.isPending} onClick={(e) => { e.stopPropagation(); one(i) }} title={`Télécharge l'image de « ${i.service} » et recrée ce seul conteneur`}><Rocket size={13} /> Redéployer</Btn></td>}</tr>) })}</tbody></table></div>)}
      <div className="muted small">La comparaison porte sur l'empreinte du manifeste au registre public, et, quand le tag est un numéro de version (1.27, v2.3.1-alpine…), sur les versions plus hautes publiées ; les registres privés ne sont pas interrogés. Les images construites localement n'ont pas de version au registre.</div>
      {edit && <ScheduleModal id={id} initial={d.schedule} busy={track.isPending} onClose={() => setEdit(false)} onSave={(schedule) => track.mutate({ schedule, enabled: true })} />}
      {detail && <ImageModal id={detail} onClose={() => setDetail(null)} onOpenContainer={(n) => { setDetail(null); go(`/docker?c=${encodeURIComponent(n)}`) }} />}
      {job && <JobOutput id={id} job={job} onClose={() => { setJob(null); qc.invalidateQueries({ queryKey: ['project-images', id] }) }} />}
    </div>
  )
}
