import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams, useSearchParams } from 'react-router-dom'
import { Activity, Boxes, ExternalLink, FileText, FolderCode, GitBranch, Globe, Image as ImageIcon, LayoutDashboard, Trash2, TriangleAlert, Undo2, Workflow } from 'lucide-react'
import { api } from '../api.js'
import { useNode } from '../node.jsx'
import { Badge, Btn, Empty, Loading, Modal, Tabs, useUi } from '../components/ui.jsx'
import ComposeBar from '../components/ComposeBar.jsx'
import ProjectFiles from '../components/ProjectFiles.jsx'
import ProjectGit from '../components/ProjectGit.jsx'
import ProjectImages from '../components/ProjectImages.jsx'
import PublishModal from '../components/PublishModal.jsx'
import ContainerModal, { AccessPanel } from '../components/ContainerModal.jsx'
import { LogViewer } from '../components/DockerBits.jsx'
import { SVC_DOT, STATUS, useProjectStats } from './Projects.jsx'
import { fmtSize, portLabel } from '../lib/docker.js'
import { fmtNum } from '../lib/features.js'
import { useGo } from '../lib/nav.js'

const SVC_LABEL = { running: 'En marche', partial: 'Partiel', stopped: 'Arrêté', absent: 'Non démarré' }
const enc = encodeURIComponent

export default function Project() {
  const { id } = useParams(); const go = useGo(); const { hasModule, can } = useNode(); const [params, setParams] = useSearchParams()
  const q = useQuery({ queryKey: ['project', id], queryFn: () => api.get(`/projects/${id}`), refetchInterval: 10000 })
  const stat = useProjectStats(); const { toast, confirm } = useUi(); const qc = useQueryClient(); const [kill, setKill] = useState(false); const [pub, setPub] = useState(null)
  const unimport = useMutation({ mutationFn: () => api.del(`/projects/${id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ['projects'] }); toast('Projet retiré de la liste'); go('/projects') }, onError: (e) => toast(e.message, 'err') })
  const tab0 = params.get('tab') || 'overview'; const open = params.get('c')
  const set = (patch) => { const n = new URLSearchParams(params); Object.entries(patch).forEach(([k, v]) => (v == null ? n.delete(k) : n.set(k, v))); setParams(n, { replace: true }) }
  useEffect(() => { if (q.data?.name) document.title = `${q.data.name} · Projets · Armadok` }, [q.data?.name])
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const p = q.data; const tf = hasModule('traefik') || p.hosts.length > 0; const [lab, kind] = STATUS[p.status] || ['', '']; const s = stat(p.containerIds); const all = [...p.services.flatMap((x) => x.containers), ...p.extra]
  const links = p.services.flatMap((x) => x.links.map((l) => ({ ...l, svc: x.name }))); const uniq = [...new Map(links.map((l) => [l.router, l])).values()]
  const tabs = [{ id: 'overview', label: 'Vue d\'ensemble', icon: <LayoutDashboard size={14} /> }, { id: 'services', label: 'Services', icon: <Boxes size={14} />, count: p.services.length },
    ...(p.perms.files ? [{ id: 'files', label: 'Fichiers', icon: <FolderCode size={14} /> }] : []), ...(p.perms.git ? [{ id: 'git', label: 'Git', icon: <GitBranch size={14} /> }] : []), { id: 'images', label: 'Images', icon: <ImageIcon size={14} /> }, ...(p.perms.logs ? [{ id: 'logs', label: 'Logs', icon: <FileText size={14} /> }] : []), ...(tf ? [{ id: 'access', label: 'Accès', icon: <Activity size={14} /> }, { id: 'sites', label: 'Sites', icon: <Workflow size={14} />, count: uniq.length || undefined }] : [])]
  const tab = tabs.some((t) => t.id === tab0) ? tab0 : 'overview'
  const cards = [['Services', `${p.running} / ${p.total}`, p.status === 'running' ? 'tous en marche' : p.status === 'partial' ? 'certains ne tournent pas' : 'rien ne tourne'], ['CPU', s ? `${s.cpu.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} %` : '—', 'tous les conteneurs'], ['Mémoire', s ? fmtSize(s.mem) : '—', s?.limit ? `sur ${fmtSize(s.limit)}` : ''],
    ...(tf ? [['Requêtes', p.access ? fmtNum(p.access.hits) : '—', p.access ? `${p.access.days} derniers jours` : 'journal d\'accès non lu'], ['Erreurs', p.access ? fmtNum(p.access.errors) : '—', ''], ['Visiteurs', p.access ? fmtNum(p.access.visitors) : '—', 'adresses distinctes']] : [])]
  return (
    <>
      <div className="row wrap" style={{ marginBottom: 14, gap: 10 }}>
        <div className="grow"><div className="row" style={{ gap: 10 }}><h1 style={{ margin: 0 }}>{p.name}</h1><Badge kind={kind}>{lab}</Badge>{p.tags.map((t) => <Badge key={t}>{t}</Badge>)}</div>
          <div className="muted small mono">{p.path}</div></div>
        <ComposeBar id={id} perms={p.perms} onDone={() => q.refetch()} />
      </div>
      {p.alerts.map((x, i) => <div key={i} className={'banner ' + (x.level === 'err' ? 'err' : 'warn')}><TriangleAlert size={16} /> {x.text}</div>)}
      <Tabs tabs={tabs} value={tab} onChange={(t) => set({ tab: t, c: null })} />
      <div style={{ marginTop: 14 }}>
        {tab === 'overview' && (
          <div className="col">
            <div className="grid stats">{cards.map(([l, v, sub]) => <div key={l} className="card stat static"><div className="muted">{l}</div><b>{v}</b><div className="sub">{sub}</div></div>)}</div>
            <div className="card flush"><table className="t"><thead><tr><th>Service</th><th>État</th><th>Image</th>{tf && <th>Sites</th>}<th /></tr></thead><tbody>
              {p.services.map((x) => <tr key={x.name}><td><b>{x.name}</b>{x.profiles.length > 0 && <Badge style={{ marginLeft: 6 }} title="Démarré seulement avec ce profil">profil {x.profiles.join(', ')}</Badge>}</td>
                <td><span className="row" style={{ gap: 6 }}><i className="dot" style={{ background: SVC_DOT[x.state] }} />{SVC_LABEL[x.state]}</span></td><td className="small">{x.image || (x.build ? 'build local' : '—')}</td>
                {tf && <td>{x.hosts.length ? <div className="row wrap" style={{ gap: 4 }}>{x.hosts.slice(0, 2).map((h) => <a key={h} className="dk-chip" href={`https://${h}`} target="_blank" rel="noreferrer"><Globe size={11} /> {h}</a>)}</div> : <span className="muted">—</span>}</td>}
                <td className="actions">{x.containers[0] && <Btn small kind="ghost" onClick={() => set({ c: x.containers[0].name, ct: null })}>Détails</Btn>}{x.containers[0] && p.perms.logs && <Btn small kind="ghost" onClick={() => set({ tab: 'logs', lc: x.containers[0].name })}>Logs</Btn>}</td></tr>)}
              {p.services.length === 0 && <tr><td colSpan={5} className="muted">{p.error || 'Aucun service dans ce fichier compose.'}</td></tr>}</tbody></table></div>
            {(p.perms.import || p.perms.delete) && <div className="card flush">
              {p.perms.import && <div className="row" style={{ padding: '12px 16px', gap: 10 }}><span className="muted small grow">Retirer le projet de la liste ne touche ni aux fichiers ni aux conteneurs.</span><Btn small onClick={async () => { if (await confirm({ title: 'Retirer ce projet de la liste ?', message: 'Les fichiers et les conteneurs ne sont pas touchés ; vous pourrez le réimporter.', confirmLabel: 'Retirer', danger: false })) unimport.mutate() }}><Undo2 size={13} /> Désimporter</Btn></div>}
              {p.perms.delete && <div className="row" style={{ padding: '12px 16px', gap: 10, borderTop: '1px solid var(--border)' }}><span className="muted small grow">Supprimer le projet efface son dossier et tous ses fichiers du disque. Irréversible.</span><Btn small kind="danger" onClick={() => setKill(true)}><Trash2 size={13} /> Supprimer le projet et ses fichiers</Btn></div>}</div>}
          </div>)}
        {tab === 'services' && (
          <div className="project-grid">{p.services.map((x) => (
            <div key={x.name} className="card col" style={{ gap: 8 }}>
              <div className="row"><i className="dot" style={{ background: SVC_DOT[x.state] }} /><b className="grow">{x.name}</b><Badge kind={x.state === 'running' ? 'ok' : x.state === 'partial' ? 'warn' : ''}>{SVC_LABEL[x.state]}</Badge></div>
              <div className="muted small ellipsis">{x.image || (x.build ? 'build local' : '—')}</div>
              {x.containers.map((c) => <div key={c.id} className="row" style={{ gap: 6, flexWrap: 'wrap' }}><span className="mono small">{c.name}</span>{c.ports.slice(0, 2).map((pt, i) => <Badge key={i}>{portLabel(pt)}</Badge>)}<Btn small kind="ghost" className="right" onClick={() => set({ c: c.name, ct: null })}>Détails</Btn></div>)}
              {tf && can('sites.create') && x.containers.length > 0 && x.hosts.length === 0 && <Btn small onClick={() => setPub(x.containers[0].name)}><Globe size={13} /> Publier sur Traefik</Btn>}
              {x.containers.length === 0 && <span className="muted small">{x.profiles.length ? `Démarré avec le profil « ${x.profiles.join(', ')} »` : 'Aucun conteneur : le service n\'est pas démarré.'}</span>}
              {tf && x.hosts.length > 0 && <div className="row wrap" style={{ gap: 4 }}>{x.hosts.map((h) => <a key={h} className="dk-chip" href={`https://${h}`} target="_blank" rel="noreferrer"><Globe size={11} /> {h}</a>)}</div>}
            </div>))}
            {p.extra.length > 0 && <div className="card col" style={{ gap: 6 }}><b>Conteneurs hors du fichier</b><span className="muted small">Ils portent le nom de ce projet mais ne correspondent plus à un service du compose (service renommé ou retiré).</span>{p.extra.map((c) => <div key={c.id} className="row"><span className="mono small grow">{c.name}</span><Btn small kind="ghost" onClick={() => set({ c: c.name })}>Détails</Btn></div>)}</div>}
          </div>)}
        {tab === 'images' && <ProjectImages id={id} perms={p.perms} />}
        {tab === 'git' && <ProjectGit id={id} perms={p.perms} />}
        {tab === 'files' && <ProjectFiles id={id} perms={p.perms} canTraefik={tf} onChanged={() => q.refetch()} />}
        {tab === 'logs' && <ProjectLogs all={all} pick={params.get('lc')} onPick={(n) => set({ lc: n })} />}
        {tab === 'access' && <AccessPanel access={p.access} linked={p.hosts.length > 0} go={go} />}
        {tab === 'sites' && (uniq.length === 0
          ? <div className="card"><Empty><Workflow size={28} /><div>Aucune règle Traefik ne mène aux services de ce projet.</div></Empty></div>
          : <div className="col">{uniq.map((l) => (
            <div key={l.router} className="card col" style={{ gap: 6 }}>
              <div className="row" style={{ gap: 8 }}><b className="mono">{l.router}</b><Badge kind={l.sure ? 'ok' : 'warn'}>{l.sure ? 'certain' : 'probable'}</Badge><Badge>{l.provider === 'docker' ? 'labels' : 'fichier YAML'}</Badge><span className="muted small">service {l.svc}</span>{l.item && <Btn small className="right" onClick={() => go(`/sites?open=${enc(l.router)}`)}><Globe size={13} /> Ouvrir le site</Btn>}</div>
              <div className="small mono muted" style={{ wordBreak: 'break-all' }}>{l.rule}</div>
              <div className="row wrap" style={{ gap: 6 }}>{l.hosts.map((h) => <a key={h} className="dk-chip" href={`https://${h}`} target="_blank" rel="noreferrer"><ExternalLink size={11} /> {h}</a>)}</div>
            </div>))}</div>)}
      </div>
      {pub && <PublishModal container={pub} onClose={() => setPub(null)} />}
      {kill && <Destroy id={id} name={p.path.split('/').pop()} onClose={() => setKill(false)} onDone={() => { qc.invalidateQueries({ queryKey: ['projects'] }); go('/projects') }} />}
      {open && <ContainerModal key={open} name={open} tab={params.get('ct') || undefined} onClose={() => set({ c: null, ct: null })} />}
    </>
  )
}

function ProjectLogs({ all, pick, onPick }) {
  const names = all.map((c) => c.name); const [local, setLocal] = useState(null)
  const cur = names.includes(pick) ? pick : names.includes(local) ? local : names.find((n) => all.find((c) => c.name === n)?.state === 'running') || names[0]
  if (!cur) return <div className="card"><Empty><FileText size={28} /><div>Aucun conteneur : rien à lire pour l'instant.</div></Empty></div>
  return (
    <div className="col">
      <div className="row" style={{ gap: 8 }}><span className="muted small">Conteneur</span><select className="input" style={{ maxWidth: 320 }} value={cur} onChange={(e) => { setLocal(e.target.value); onPick(e.target.value) }}>{names.map((n) => <option key={n} value={n}>{n}</option>)}</select></div>
      <LogViewer key={cur} name={cur} />
    </div>
  )
}

function Destroy({ id, name, onClose, onDone }) {
  const [v, setV] = useState(''); const { toast } = useUi()
  const m = useMutation({ mutationFn: () => api.post(`/projects/${id}/destroy`, { confirm: v }), onSuccess: () => { toast('Projet supprimé'); onDone() }, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal small title="Supprimer le projet et ses fichiers" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="danger" disabled={v !== name} busy={m.isPending} onClick={() => m.mutate()}>Supprimer définitivement</Btn></>}>
      <div className="col"><div>Le dossier <b>{name}</b> et tout ce qu'il contient (compose, .env, données dans le dossier) seront effacés du disque. Les conteneurs doivent être arrêtés (« Down »). Retapez le nom du dossier pour confirmer.</div>
        <input className="input mono" autoFocus value={v} onChange={(e) => setV(e.target.value)} placeholder={name} /></div>
    </Modal>
  )
}
