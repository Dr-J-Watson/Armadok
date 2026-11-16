import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { Download, EyeOff, FolderGit2, GitFork, FolderOpen, Globe, LayoutGrid, List, Undo2, TriangleAlert } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, CopyBtn, Empty, Loading, Modal, SearchBox, Seg, Tabs, useUi } from '../components/ui.jsx'
import { JobOutput } from '../components/ComposeBar.jsx'
import { MiniBar } from '../components/DockerBits.jsx'
import { fmtSize } from '../lib/docker.js'
import { fmtNum } from '../lib/features.js'
import { usePersist } from '../lib/persist.js'
import { useGo } from '../lib/nav.js'

export const STATUS = { running: ['En marche', 'ok'], partial: ['Partiel', 'warn'], stopped: ['Arrêté', ''], absent: ['Non démarré', ''] }
export const SVC_DOT = { running: 'var(--ok)', partial: 'var(--warn)', stopped: 'var(--muted)', absent: 'var(--border)' }

// Somme CPU / mémoire des conteneurs d'un projet, d'après les statistiques déjà chargées pour la page Docker.
export function useProjectStats() {
  const st = useQuery({ queryKey: ['docker-stats'], queryFn: () => api.get('/docker/stats'), refetchInterval: 20000, retry: false })
  return (ids) => { const m = st.data?.stats || {}; const rows = (ids || []).map((i) => m[i]).filter(Boolean); return rows.length ? { cpu: rows.reduce((s, r) => s + r.cpu, 0), mem: rows.reduce((s, r) => s + r.mem, 0), limit: rows.reduce((s, r) => s + (r.memLimit || 0), 0) } : null }
}

export default function Projects() {
  const go = useGo(); const [params, setParams] = useSearchParams()
  const [q, setQ] = useState(''); const [view, setView] = usePersist('projects-view', 'cards'); const [sel, setSel] = useState([]); const [ask, setAsk] = useState(null); const [clone, setClone] = useState(false)
  const list = useQuery({ queryKey: ['projects'], queryFn: () => api.get('/projects'), refetchInterval: 20000 })
  const stat = useProjectStats()
  const { toast } = useUi(); const qc = useQueryClient()
  const tab = ['managed', 'discovered', 'ignored'].includes(params.get('tab')) ? params.get('tab') : 'managed'
  const done = (r, verb) => { qc.invalidateQueries({ queryKey: ['projects'] }); setSel([]); setAsk(null); toast(r.done.length ? `${r.done.length} projet${r.done.length > 1 ? 's' : ''} ${verb}` : 'Rien à faire') }
  const ignore = useMutation({ mutationFn: (paths) => api.post('/projects/ignore', { paths }), onSuccess: (r) => done(r, 'ignoré(s)'), onError: (e) => toast(e.message, 'err') })
  const unimport = useMutation({ mutationFn: (id) => api.del(`/projects/${id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ['projects'] }); toast('Projet retiré de la liste') }, onError: (e) => toast(e.message, 'err') })
  if (list.isLoading) return <Loading />
  if (list.error) return <div className="banner err">{list.error.message}</div>
  const d = list.data; const needle = q.trim().toLowerCase()
  const hit = (p) => !needle || [p.name, p.path, ...(p.tags || []), ...(p.hosts || []), ...p.services.map((s) => s.name), ...p.services.map((s) => s.image)].some((x) => String(x || '').toLowerCase().includes(needle))
  const rows = d[tab].filter(hit)
  return (
    <>
      <div className="row" style={{ marginBottom: 14 }}>
        <div className="grow"><h1 style={{ margin: 0 }}>Projets</h1><div className="muted">Les dossiers docker compose de ce nœud : leur état, leurs sites et leurs visiteurs.</div></div>
        {d.canGit && <Btn onClick={() => setClone(true)}><GitFork size={14} /> Cloner un dépôt</Btn>}
      </div>
      <div className="row wrap" style={{ gap: 10, marginBottom: 14 }}>
        <Tabs value={tab} onChange={(t) => { setParams({ tab: t }); setSel([]) }} tabs={[{ id: 'managed', label: 'Gérés', count: d.managed.length }, { id: 'discovered', label: 'Découverts', count: d.discovered.length }, { id: 'ignored', label: 'Ignorés', count: d.ignored.length }]} />
        <SearchBox value={q} onChange={setQ} placeholder="Rechercher un projet, un service, un site, une étiquette…" />
        {tab === 'managed' && <span className="right"><Seg options={[{ id: 'cards', label: <LayoutGrid size={14} />, hint: 'Cartes' }, { id: 'list', label: <List size={14} />, hint: 'Liste compacte' }]} value={view} onChange={setView} /></span>}
        {tab === 'discovered' && d.canImport && <span className="right row" style={{ gap: 6 }}><Btn small kind="primary" disabled={!sel.length} onClick={() => setAsk(sel)}><Download size={13} /> Importer ({sel.length})</Btn><Btn small kind="ghost" disabled={!sel.length} onClick={() => ignore.mutate(sel)}><EyeOff size={13} /> Ignorer</Btn></span>}
      </div>
      {d.roots.length === 0 && <div className="banner warn"><FolderOpen size={16} /> Aucun dossier de projets n'est configuré sur ce nœud. Indiquez-les avec la variable <code>PROJECT_ROOTS</code> (chemins absolus, montés à l'identique dans le conteneur).</div>}
      {d.truncated && <div className="banner warn">Beaucoup de projets : la liste est tronquée.</div>}
      {rows.length === 0 ? <div className="card"><Empty><FolderGit2 size={28} /><div>{tab === 'managed' ? <>Aucun projet géré.<br /><span className="muted small">Importez-en depuis l'onglet « Découverts ».</span></> : tab === 'discovered' ? 'Aucun nouveau projet trouvé dans vos dossiers.' : 'Aucun projet ignoré.'}</div></Empty></div>
        : tab === 'managed' ? (view === 'list' ? <ProjectTable rows={rows} stat={stat} go={go} onRemove={d.canImport ? (x) => unimport.mutate(x.id) : null} /> : <div className="project-grid">{rows.map((p) => <ProjectCard key={p.id} p={p} stat={stat} go={go} onRemove={d.canImport ? (x) => unimport.mutate(x.id) : null} />)}</div>)
          : <div className="card flush"><table className="t"><thead><tr>{tab === 'discovered' && d.canImport && <th style={{ width: 30 }}><input type="checkbox" checked={sel.length === rows.length && rows.length > 0} onChange={(e) => setSel(e.target.checked ? rows.map((r) => r.path) : [])} /></th>}<th>Projet</th><th>Services</th><th>État</th><th /></tr></thead><tbody>
            {rows.map((p) => <tr key={p.path}>{tab === 'discovered' && d.canImport && <td><input type="checkbox" checked={sel.includes(p.path)} onChange={(e) => setSel((s) => (e.target.checked ? [...s, p.path] : s.filter((x) => x !== p.path)))} /></td>}
              <td><b>{p.name}</b>{p.hasEnv && <Badge style={{ marginLeft: 6 }}>.env</Badge>}{p.build && <Badge style={{ marginLeft: 6 }}>build</Badge>}{p.hasGit && <Badge style={{ marginLeft: 6 }}>git</Badge>}<div className="muted small mono">{p.path}</div>{p.error && <div className="small" style={{ color: 'var(--err)' }}>{p.error}</div>}</td>
              <td className="small">{p.services.map((s) => s.name).join(', ') || '—'}</td><td>{p.status === 'absent' ? <span className="muted">—</span> : <Badge kind={STATUS[p.status][1]}>{STATUS[p.status][0]}</Badge>}</td>
              <td className="actions">{d.canImport && (tab === 'discovered' ? <Btn small onClick={() => setAsk([p.path])}><Download size={13} /> Importer</Btn> : <Btn small kind="ghost" onClick={() => unimport.mutate(p.id)}><Undo2 size={13} /> Restaurer</Btn>)}</td></tr>)}</tbody></table></div>}
      {clone && <CloneModal onClose={() => setClone(false)} onDone={() => { qc.invalidateQueries({ queryKey: ['projects'] }); setParams({ tab: 'discovered' }) }} />}
      {ask && <ImportModal paths={ask} onClose={() => setAsk(null)} onDone={(r) => done(r, 'importé(s)')} />}
    </>
  )
}

function ImportModal({ paths, onClose, onDone }) {
  const [tags, setTags] = useState(''); const { toast } = useUi()
  const m = useMutation({ mutationFn: () => api.post('/projects/import', { paths, tags }), onSuccess: onDone, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal small title={`Importer ${paths.length} projet${paths.length > 1 ? 's' : ''}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={m.isPending} onClick={() => m.mutate()}>Importer</Btn></>}>
      <div className="col"><div className="muted">Rien n'est déplacé ni réécrit : l'interface garde seulement la référence du dossier. Les conteneurs déjà lancés sont repris tels quels.</div>
        <div className="muted small mono">{paths.slice(0, 4).join('\n')}{paths.length > 4 ? `\n… +${paths.length - 4}` : ''}</div>
        <label>Étiquettes (séparées par des virgules, facultatif)<input className="input" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="prod, media" /></label></div>
    </Modal>
  )
}

function Services({ p }) {
  return <div className="row wrap" style={{ gap: 5 }}>{p.services.length ? p.services.slice(0, 8).map((s) => <span key={s.name} className="small row" style={{ gap: 4 }} title={`${s.name} : ${s.state}`}><i className="dot" style={{ background: SVC_DOT[s.state] }} />{s.name}</span>) : <span className="muted small">aucun service</span>}{p.services.length > 8 && <span className="muted small">+{p.services.length - 8}</span>}</div>
}

function ProjectCard({ p, stat, go, onRemove }) {
  const [lab, kind] = STATUS[p.status] || ['', '']; const s = stat(p.containerIds); const a = p.access
  return (
    <div className={'card project ' + (p.missing ? 'off' : '')} onClick={() => !p.missing && go(`/projects/${p.id}`)}>
      <div className="row"><FolderGit2 size={16} className="muted" /><b className="grow ellipsis">{p.name}</b>{!p.missing && <Badge kind={kind}>{lab}</Badge>}{p.missing && <Badge kind="err">introuvable</Badge>}</div>
      <div className="muted small mono ellipsis" title={p.path}>{p.path}</div>
      <Services p={p} />
      {p.hosts.length > 0 && <div className="row wrap" style={{ gap: 5 }}>{p.hosts.slice(0, 3).map((h) => <a key={h} className="dk-chip" href={`https://${h}`} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}><Globe size={11} /> {h}</a>)}{p.hosts.length > 3 && <span className="muted small">+{p.hosts.length - 3}</span>}</div>}
      <div className="project-nums">
        <div><span className="muted small">CPU</span><b>{s ? `${s.cpu.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} %` : '—'}</b>{s && <MiniBar pct={s.cpu} />}</div>
        <div><span className="muted small">Mémoire</span><b>{s ? fmtSize(s.mem) : '—'}</b>{s && s.limit > 0 && <MiniBar pct={(s.mem / s.limit) * 100} />}</div>
        <div><span className="muted small">Requêtes</span><b>{a ? fmtNum(a.hits) : '—'}</b></div>
        <div><span className="muted small">Erreurs</span><b style={a?.errors ? { color: 'var(--err)' } : undefined}>{a ? fmtNum(a.errors) : '—'}</b></div>
        <div><span className="muted small">Visiteurs</span><b>{a ? fmtNum(a.visitors) : '—'}</b></div>
      </div>
      <div className="row wrap" style={{ gap: 5 }}>
        {p.alerts.slice(0, 2).map((x, i) => <Badge key={i} kind={x.level === 'err' ? 'err' : 'warn'}><TriangleAlert size={10} /> {x.text}</Badge>)}
        {p.tags.map((t) => <Badge key={t}>{t}</Badge>)}
        {p.missing && onRemove ? <Btn small className="right" title="Retirer de la liste (rien n'est supprimé sur la machine)" onClick={(e) => { e.stopPropagation(); onRemove(p) }}><Undo2 size={13} /> Retirer de la liste</Btn> : <span className="muted small right">{p.running}/{p.total} service{p.total > 1 ? 's' : ''}</span>}
      </div>
    </div>
  )
}

function ProjectTable({ rows, stat, go, onRemove }) {
  return (
    <div className="card flush"><table className="t"><thead><tr><th>Projet</th><th>État</th><th>Services</th><th>Sites</th><th>CPU</th><th>Mémoire</th><th>Requêtes</th><th>Visiteurs</th><th /></tr></thead><tbody>
      {rows.map((p) => { const s = stat(p.containerIds); const [lab, kind] = STATUS[p.status] || ['', '']; return (
        <tr key={p.id} style={{ cursor: 'pointer' }} onClick={() => !p.missing && go(`/projects/${p.id}`)}>
          <td><b>{p.name}</b><div className="muted small mono ellipsis" style={{ maxWidth: 260 }}>{p.path}</div></td><td>{p.missing ? <Badge kind="err">introuvable</Badge> : <Badge kind={kind}>{lab}</Badge>}</td><td><Services p={p} /></td>
          <td className="small">{p.hosts.slice(0, 2).join(', ') || <span className="muted">—</span>}</td><td>{s ? `${s.cpu.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} %` : '—'}</td><td>{s ? fmtSize(s.mem) : '—'}</td>
          <td>{p.access ? fmtNum(p.access.hits) : '—'}</td><td>{p.access ? fmtNum(p.access.visitors) : '—'}</td><td className="actions">{p.missing && onRemove && <Btn small kind="ghost" title="Retirer de la liste (rien n'est supprimé sur la machine)" onClick={(e) => { e.stopPropagation(); onRemove(p) }}><Undo2 size={13} /> Retirer</Btn>}</td></tr>) })}</tbody></table></div>
  )
}


function CloneModal({ onClose, onDone }) {
  const { toast } = useUi(); const [url, setUrl] = useState(''); const [name, setName] = useState(''); const [touched, setTouched] = useState(false); const [branch, setBranch] = useState(''); const [user, setUser] = useState(''); const [token, setToken] = useState(''); const [job, setJob] = useState(null)
  const sshUrl = /^(ssh:\/\/|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:)/.test(url.trim())
  const auto = (u) => (u.replace(/\/+$/, '').replace(/\.git$/, '').split(/[/:]/).pop() || '').replace(/[^A-Za-z0-9._-]/g, '-')
  const go = useMutation({ mutationFn: () => api.post('/projects/git/clone', { url: url.trim(), name, branch, username: user, token }), onSuccess: (r) => setJob({ ...r, action: 'git-clone' }), onError: (e) => toast(e.message, 'err') })
  if (job) return <JobOutput job={job} jobUrl={(j, since) => `/projects/clone-jobs/${j}?since=${since}`} onFinished={(c) => c === 0 && onDone()} onClose={onClose} />
  return (
    <Modal title="Cloner un dépôt Git" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" disabled={!url.trim() || !name} busy={go.isPending} onClick={() => go.mutate()}>Cloner</Btn></>}>
      <div className="col">
        <div className="muted">Le dépôt est cloné dans le dossier des projets, puis apparaît dans « Découverts » : vous l'importez ensuite. Le jeton, s'il y en a un, n'est pas conservé (enregistrez-le dans l'onglet Git du projet pour les mises à jour).</div>
        <label>Adresse du dépôt<input className="input mono" autoFocus value={url} onChange={(e) => { setUrl(e.target.value); if (!touched) setName(auto(e.target.value)) }} placeholder="https://github.com/moi/mon-projet.git" /></label>
        <div className="row" style={{ gap: 10 }}><label className="grow">Dossier<input className="input mono" value={name} onChange={(e) => { setName(e.target.value); setTouched(true) }} /></label><label className="grow">Branche (facultatif)<input className="input" value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="main" /></label></div>
        {sshUrl && <NodeKey />}
        <div className="row" style={{ gap: 10 }}><label className="grow">Utilisateur (dépôt privé)<input className="input" value={user} onChange={(e) => setUser(e.target.value)} /></label><label className="grow">Jeton d'accès<input className="input" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} /></label></div>
      </div>
    </Modal>
  )
}

// Dépôt en SSH : le clonage utilise la clé SSH du NŒUD (la même pour tous ses projets qui n'ont pas de clé propre). La partie publique se donne au dépôt.
function NodeKey() {
  const { toast } = useUi(); const qc = useQueryClient()
  const q = useQuery({ queryKey: ['git-node-key'], queryFn: () => api.get('/projects/git/node-key') })
  const make = useMutation({ mutationFn: () => api.post('/projects/git/node-key'), onSuccess: () => qc.invalidateQueries({ queryKey: ['git-node-key'] }), onError: (e) => toast(e.message, 'err') })
  const k = q.data
  return (
    <div className="banner info col" style={{ gap: 6 }}>
      <div>Adresse SSH : le clonage utilise la <b>clé SSH de ce nœud</b>. Donnez sa partie publique au dépôt (« Deploy key », lecture seule), puis lancez le clonage.</div>
      {k?.set ? <><div className="codebox slim"><CopyBtn text={k.publicKey} /><pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{k.publicKey}</pre></div><div className="muted small mono">{k.fingerprint}</div></>
        : <div><Btn small kind="primary" busy={make.isPending} onClick={() => make.mutate()}>Créer la clé du nœud</Btn></div>}
    </div>
  )
}
