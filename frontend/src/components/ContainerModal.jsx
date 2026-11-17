import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Boxes, ExternalLink, FileText, Globe, Info as InfoIcon, Play, RotateCw, ShieldAlert, Square, SquareTerminal, Trash2, Workflow } from 'lucide-react'
import { api } from '../api.js'
import { useNode } from '../node.jsx'
import { Badge, Btn, Copyable, Empty, Loading, Modal, Tabs, useUi } from './ui.jsx'
import { LogViewer, NameConfirm } from './DockerBits.jsx'
import { publishNow } from '../lib/publish.js'
import TerminalModal from './TerminalModal.jsx'
import { enc, fmtSize, portLabel, PROTECTED, stateOf } from '../lib/docker.js'
import { ago, fmtNum } from '../lib/features.js'
import { fmtDate } from '../lib/util.js'
import { useGo } from '../lib/nav.js'

const Row = ({ k, children }) => <div className="kv"><span className="muted">{k}</span><span>{children}</span></div>

// La fiche d'un conteneur : ce qu'il est (Aperçu), quelles règles Traefik mènent à lui (Traefik), qui y accède (Accès), ses journaux (Logs).
export default function ContainerModal({ name, tab: first, onClose }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient(); const go = useGo(); const { hasModule, can, node, setNode } = useNode()
  const [tab, setTab] = useState(first || 'overview'); const [ask, setAsk] = useState(null); const [term, setTerm] = useState(false)
  const q = useQuery({ queryKey: ['docker-c', name], queryFn: () => api.get(`/docker/containers/${enc(name)}`), refetchInterval: 10000 })
  const refresh = () => { qc.invalidateQueries({ queryKey: ['docker-c'] }); qc.invalidateQueries({ queryKey: ['docker-list'] }); qc.invalidateQueries({ queryKey: ['docker-stats'] }) }
  const act = useMutation({
    mutationFn: ({ action, confirm: c }) => api.post(`/docker/containers/${enc(name)}/action`, { action, confirm: c || '' }),
    onSuccess: (r, v) => { refresh(); toast(r.unchanged ? 'Rien à faire : déjà dans cet état' : ({ start: 'Conteneur démarré', stop: 'Conteneur arrêté', restart: 'Conteneur redémarré' })[v.action]) },
    onError: (e, v) => { if (/Retapez son nom/.test(e.message)) setAsk({ action: v.action }); else toast(e.message, 'err') },
  })
  const del = useMutation({
    mutationFn: ({ volumes, confirm: c }) => api.del(`/docker/containers/${enc(name)}?volumes=${volumes ? 'true' : 'false'}&force=true&confirm=${enc(c || '')}`),
    onSuccess: () => { refresh(); toast('Conteneur supprimé'); onClose() },
    onError: (e) => { if (/Retapez son nom/.test(e.message)) setAsk({ action: 'remove' }); else toast(e.message, 'err') },
  })
  if (q.isLoading) return <Modal title={name} onClose={onClose}><Loading /></Modal>
  if (q.error) return <Modal title={name} onClose={onClose}><div className="banner err">{q.error.message}</div></Modal>
  const c = q.data; const [stLabel, stKind] = stateOf({ state: c.state, health: c.health }); const running = c.state === 'running'
  const tf = hasModule('traefik') || !!node?.traefikVia || c.links.some((l) => l.node)   // un nœud sans Traefik montre quand même les sites servis ailleurs
  const tabs = [{ id: 'overview', label: 'Aperçu', icon: <InfoIcon size={14} /> }, ...(tf ? [{ id: 'traefik', label: 'Traefik', icon: <Workflow size={14} />, count: c.links.length || undefined }, { id: 'access', label: 'Accès', icon: <Activity size={14} /> }] : []), ...(c.canLogs ? [{ id: 'logs', label: 'Logs', icon: <FileText size={14} /> }] : [])]
  const remove = async () => { if (await confirm({ title: `Supprimer « ${c.name} » ?`, message: 'Le conteneur est supprimé définitivement (ses volumes nommés sont conservés). Ses fichiers vivent dans le dossier du projet : ils ne sont pas touchés.', confirmLabel: 'Supprimer' })) del.mutate({ volumes: false }) }
  return (
    <Modal wide title={<span className="row" style={{ gap: 8 }}>{c.name}<Badge kind={stKind}>{stLabel}</Badge>{c.protected && <Badge kind="warn" title="Un arrêt ou une suppression demande de retaper le nom"><ShieldAlert size={11} /> {PROTECTED[c.protected]}</Badge>}</span>} onClose={onClose}
      footer={<>{c.canControl && (running
        ? <><Btn title="Arrête puis relance le conteneur" busy={act.isPending} onClick={() => act.mutate({ action: 'restart' })}><RotateCw size={14} /> Redémarrer</Btn>{c.protected !== 'self' && <Btn title="Arrête le conteneur (il reste en place et se redémarre d'un clic)" busy={act.isPending} onClick={() => act.mutate({ action: 'stop' })}><Square size={14} /> Arrêter</Btn>}</>
        : <Btn title="Démarre le conteneur" kind="primary" busy={act.isPending} onClick={() => act.mutate({ action: 'start' })}><Play size={14} /> Démarrer</Btn>)}
      {c.canExec && running && c.execAvailable && <Btn onClick={() => setTerm(true)} title="Un shell dans le conteneur"><SquareTerminal size={14} /> Terminal</Btn>}
      {c.canRemove && c.protected !== 'self' && <Btn title="Supprime ce conteneur (ses volumes ne sont retirés que si vous le demandez)" kind="danger" onClick={remove}><Trash2 size={14} /> Supprimer</Btn>}<Btn onClick={onClose}>Fermer</Btn></>}>
      <div className="col">
        <div className="muted small">{c.image}{c.project && <> · projet <b>{c.project}</b></>}{c.service && <> · service <b>{c.service}</b></>}</div>
        <Tabs tabs={tabs} value={tabs.some((t) => t.id === tab) ? tab : 'overview'} onChange={setTab} />
        {tab === 'overview' && (
          <div className="grid two">
            <div className="card col" style={{ gap: 6 }}>
              <h3 style={{ margin: 0 }}>Général</h3>
              <Row k="Image"><Copyable text={c.image}>{c.image}</Copyable> <span className="muted small mono">{c.imageId}</span></Row>
              <Row k="Commande"><code className="small" style={{ wordBreak: 'break-all' }}>{c.command || '—'}</code></Row>
              <Row k="Utilisateur">{c.user || 'défaut de l\'image'}</Row>
              <Row k="Créé">{c.created ? fmtDate(Date.parse(c.created)) : '—'}</Row>
              {running && c.started && <Row k="Démarré">{ago(Date.parse(c.started) / 1000)}</Row>}
              {!running && c.finished && !c.finished.startsWith('0001') && <Row k="Arrêté">{ago(Date.parse(c.finished) / 1000)} · code {c.exitCode}{c.oom ? ' · mémoire épuisée' : ''}</Row>}
              <Row k="Redémarrage">{c.restart}{c.restartCount ? ` (${c.restartCount} fois)` : ''}</Row>
              <Row k="Ressources">{c.memoryLimit ? fmtSize(c.memoryLimit) : 'mémoire illimitée'} · {c.cpus ? `${c.cpus} CPU` : 'CPU illimité'}</Row>
              <Row k="Variables">{c.envNames.length} définie{c.envNames.length > 1 ? 's' : ''} <span className="muted small">(valeurs masquées)</span></Row>
              {c.privileged && <Row k="Privilèges"><Badge kind="err">privilégié</Badge></Row>}
            </div>
            <div className="card col" style={{ gap: 6 }}>
              <h3 style={{ margin: 0 }}>Réseau et ports</h3>
              <Row k="Mode">{c.networkMode}</Row>
              {Object.entries(c.networks).map(([n, v]) => <Row key={n} k={n}><span className="mono">{v.ip || '—'}</span> <span className="muted small">{v.aliases.filter((a) => a !== c.id).join(', ')}</span></Row>)}
              <div className="row wrap" style={{ gap: 5 }}>{c.ports.length ? c.ports.map((p, i) => <Badge key={i}>{portLabel(p)}</Badge>) : <span className="muted small">Aucun port publié</span>}</div>
              <h3 style={{ margin: '8px 0 0' }}>Volumes</h3>
              {c.mounts.length ? c.mounts.map((m, i) => <div key={i} className="small mono" style={{ wordBreak: 'break-all' }}><b>{m.name || m.source}</b> → {m.destination}{!m.rw && <span className="muted"> (lecture seule)</span>}</div>) : <span className="muted small">Aucun</span>}
            </div>
            <details className="card" style={{ gridColumn: '1 / -1' }}><summary>Labels ({Object.keys(c.labels).length})</summary>
              <div className="col" style={{ gap: 2, marginTop: 8 }}>{Object.entries(c.labels).sort().map(([k, v]) => <div key={k} className="small mono" style={{ wordBreak: 'break-all' }}><span className="muted">{k}</span> = {v}</div>)}</div></details>
          </div>)}
        {tab === 'traefik' && (c.links.length === 0
          ? <div className="card"><Empty><Workflow size={28} /><div>Aucune règle Traefik ne mène à ce conteneur.<br /><span className="muted small">On cherche ses labels <code>traefik.http.routers.*</code> et les services d'un fichier dont l'adresse est le nom de ce conteneur.</span>{can('sites.create') && <div style={{ marginTop: 10 }}><Btn title="Ouvre l'assistant de site, déjà rempli pour ce conteneur" kind="primary" onClick={() => publishNow(c.name, toast, setNode).then(onClose)}><Globe size={14} /> Publier sur Traefik</Btn></div>}</div></Empty></div>
          : <div className="col">{c.links.map((l) => (
            <div key={l.router} className="card col" style={{ gap: 6 }}>
              <div className="row" style={{ gap: 8 }}><b className="mono">{l.router}</b><Badge kind={l.sure ? 'ok' : 'warn'} title={l.sure ? 'Lien certain' : 'Lien probable : le nom ou le port ne correspond pas exactement'}>{l.sure ? 'certain' : 'probable'}</Badge><Badge>{l.provider === 'docker' ? 'labels du conteneur' : 'fichier YAML'}</Badge>{l.node && <Badge kind="info" title="Ce site est servi par le Traefik d'un autre nœud, qui vise l'adresse et le port publié de ce conteneur">sur le nœud « {l.node.name} »</Badge>}
                {l.item && <Btn small className="right" onClick={() => { if (l.node) { setNode(l.node.id) } go(`/sites?open=${enc(l.router)}`) }}><Globe size={13} /> {l.node ? `Ouvrir sur « ${l.node.name} »` : 'Ouvrir le site'}</Btn>}</div>
              <div className="small mono muted" style={{ wordBreak: 'break-all' }}>{l.rule}</div>
              <div className="row wrap" style={{ gap: 6 }}>{l.hosts.map((h) => <a key={h} className="dk-chip" href={`https://${h}`} target="_blank" rel="noreferrer"><ExternalLink size={11} /> {h}</a>)}{l.port && <Badge>port {l.port}</Badge>}</div>
            </div>))}</div>)}
        {tab === 'access' && <AccessPanel access={c.access} linked={c.links.length > 0} go={go} />}
        {tab === 'logs' && <LogViewer name={c.name} />}
      </div>
      {term && <TerminalModal container={c.name} onClose={() => setTerm(false)} />}
      {ask && <NameConfirm name={c.name} what={{ stop: 'Arrêter', restart: 'Redémarrer', remove: 'Supprimer' }[ask.action]} onCancel={() => setAsk(null)}
        onConfirm={(v) => { const a = ask.action; setAsk(null); if (a === 'remove') del.mutate({ volumes: false, confirm: v }); else act.mutate({ action: a, confirm: v }) }} />}
    </Modal>
  )
}

export function AccessPanel({ access: a, linked, go }) {
  if (!linked) return <div className="card"><Empty><Boxes size={28} /><div>Aucun site Traefik n'est relié : pas d'accès à compter.</div></Empty></div>
  if (!a) return <div className="card"><Empty><Activity size={28} /><div>Les statistiques d'accès viennent du journal d'accès de Traefik.<br /><span className="muted small">Il n'est pas lu sur ce nœud, ou vous n'avez pas le droit de voir l'activité des adresses.</span></div></Empty></div>
  return (
    <div className="col">
      <div className="grid stats">
        {[['Requêtes', fmtNum(a.hits)], ['Erreurs', fmtNum(a.errors)], ['Visiteurs', fmtNum(a.visitors)]].map(([l, v]) => <div key={l} className="card stat static"><div className="muted">{l}</div><b>{v}</b><div className="sub">{a.days} derniers jours · {a.hosts.join(', ')}</div></div>)}
      </div>
      {a.countries.length > 0 && <div className="row wrap" style={{ gap: 6 }}><span className="muted small">Pays :</span>{a.countries.map((x) => <Badge key={x.code}>{x.name} · {x.ips}</Badge>)}</div>}
      <div className="card flush"><table className="t"><thead><tr><th>Adresse</th><th>Pays</th><th>Requêtes</th><th>Erreurs</th><th>Dernière visite</th></tr></thead><tbody>
        {a.topIps.map((i) => <tr key={i.ref} style={{ cursor: 'pointer' }} onClick={() => go(`/monitoring?tab=ips&ip=${i.ref}`)}><td className="mono"><Copyable text={i.ip}>{i.ip}</Copyable> {i.banned && <Badge kind="err">bannie</Badge>}</td><td>{i.countryName || '—'}</td><td>{fmtNum(i.hits)}</td><td>{i.errors ? <span style={{ color: 'var(--err)' }}>{fmtNum(i.errors)}</span> : 0}</td><td className="muted small">{ago(i.last)}</td></tr>)}
        {a.topIps.length === 0 && <tr><td colSpan={5} className="muted">Aucune visite enregistrée sur la période.</td></tr>}</tbody></table></div>
      {a.topPaths.length > 0 && <div className="card flush"><table className="t"><thead><tr><th>Site</th><th>Chemin</th><th>Requêtes</th><th>Erreurs</th></tr></thead><tbody>
        {a.topPaths.map((p, i) => <tr key={i}><td className="small">{p.host}</td><td className="mono small">{p.path}</td><td>{fmtNum(p.hits)}</td><td>{p.errors || 0}</td></tr>)}</tbody></table></div>}
    </div>
  )
}
