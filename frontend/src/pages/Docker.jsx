import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { Box, Container, HardDrive, Image as ImageIcon, Network as NetIcon, Play, Plus, RotateCw, Sparkles, Trash2 } from 'lucide-react'
import { api } from '../api.js'
import { useNode, useNodeList } from '../node.jsx'
import { Badge, Btn, Copyable, Empty, Loading, SearchBox, Tabs, useSort, useUi } from '../components/ui.jsx'
import { NodeChip } from '../components/NodeSwitcher.jsx'
import ContainerModal from '../components/ContainerModal.jsx'
import { ImageModal, NetworkModal, NewNetworkModal, VolumeModal } from '../components/DockerDetail.jsx'
import { MiniBar } from '../components/DockerBits.jsx'
import { enc, fmtSize, portLabel, PROTECTED, stateOf } from '../lib/docker.js'
import { ago } from '../lib/features.js'
import { usePersist } from '../lib/persist.js'

const FILTERS = [['all', 'Tous'], ['running', 'En marche'], ['stopped', 'Arrêtés'], ['project', 'Projets Compose'], ['alone', 'Hors projet'], ['linked', 'Sur Traefik']]
const TABS = ['containers', 'images', 'networks', 'volumes']
const dockerNode = (n) => (n.modules || []).includes('docker') && (n.caps || []).includes('docker.view')
const nkey = (r, id) => (r._node?.id || '') + ':' + id   // clé d'une ligne : l'identifiant d'un conteneur n'est unique que dans son nœud

export default function Docker() {
  const { can, hasModule, node, setNode, fullView } = useNode(); const [params, setParams] = useSearchParams()
  const tab = TABS.includes(params.get('tab')) ? params.get('tab') : 'containers'
  const open = params.get('c'); const [q, setQ] = useState('')
  const info = useQuery({ queryKey: ['docker-info'], queryFn: () => api.get('/docker/info'), staleTime: 60000 })
  const setTab = (t) => setParams({ tab: t })
  const openC = (name, ctab, from) => { if (from && from.id !== node.id) setNode(from.id); const n = new URLSearchParams(params); n.set('c', name); if (ctab) n.set('ct', ctab); else n.delete('ct'); setParams(n) }
  const closeC = () => { const n = new URLSearchParams(params); n.delete('c'); n.delete('ct'); setParams(n, { replace: true }) }
  return (
    <>
      <div className="row" style={{ marginBottom: 14 }}>
        <div className="grow"><h1 style={{ margin: 0 }}>Docker</h1>
          <div className="muted">{fullView ? 'Affichage complet : les conteneurs, images, réseaux et volumes de tous vos nœuds. Cliquer sur une ligne ouvre son nœud.' : info.data ? `${info.data.running} en marche sur ${info.data.containers} · Docker ${info.data.version} · ${info.data.images} images` : 'Conteneurs, images, réseaux et volumes de ce nœud.'}</div></div>
      </div>
      <div className="row wrap" style={{ gap: 10, marginBottom: 14 }}>
        <Tabs value={tab} onChange={setTab} tabs={[{ id: 'containers', label: 'Conteneurs', icon: <Container size={14} /> }, { id: 'images', label: 'Images', icon: <ImageIcon size={14} /> }, { id: 'networks', label: 'Réseaux', icon: <NetIcon size={14} /> }, { id: 'volumes', label: 'Volumes', icon: <HardDrive size={14} /> }]} />
        <SearchBox value={q} onChange={setQ} placeholder="Rechercher un nom, une image, un projet, un port, un site…" />
      </div>
      {tab === 'containers' && <Containers q={q} onOpen={openC} can={can} linked={hasModule('traefik') || !!node?.traefikVia} />}
      {tab === 'images' && <Images q={q} can={can} onOpenContainer={openC} params={params} setParams={setParams} />}
      {tab === 'networks' && <Networks q={q} can={can} onOpen={openC} params={params} setParams={setParams} />}
      {tab === 'volumes' && <Volumes q={q} can={can} onOpen={openC} params={params} setParams={setParams} />}
      {open && <ContainerModal key={open} name={open} tab={params.get('ct') || undefined} onClose={closeC} />}
    </>
  )
}

function usePrune(what, label) {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const m = useMutation({ mutationFn: (all) => api.post('/docker/prune', { what, all }), onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['docker-list'] }); qc.invalidateQueries({ queryKey: ['docker-' + what] }); toast(`${r.deleted} élément${r.deleted > 1 ? 's' : ''} supprimé${r.deleted > 1 ? 's' : ''}${r.spaceReclaimed ? ` · ${fmtSize(r.spaceReclaimed)} libérés` : ''}`) }, onError: (e) => toast(e.message, 'err') })
  return async (all, message) => { if (await confirm({ title: `Nettoyer : ${label}`, message, confirmLabel: 'Nettoyer' })) m.mutate(all) }
}

function Containers({ q, onOpen, can, linked }) {
  const { toast } = useUi(); const qc = useQueryClient(); const [flt, setFlt] = usePersist('docker-filter', 'all')
  const list = useNodeList('docker-list', '/docker/containers', (d) => d.containers, { eligible: dockerNode, refetchInterval: 15000 })
  const stats = useNodeList('docker-stats', '/docker/stats', (d) => Object.entries(d.stats || {}).map(([id, v]) => ({ id, ...v })), { eligible: dockerNode, refetchInterval: 20000, enabled: !list.isLoading })
  const prune = usePrune('containers', 'conteneurs arrêtés')
  const quick = useMutation({ mutationFn: ({ name, action, node }) => api.post(`/docker/containers/${enc(name)}/action`, { action }, { node }), onSuccess: () => qc.invalidateQueries({ queryKey: ['docker-list'] }), onError: (e) => toast(e.message, 'err') })
  const rows0 = list.rows
  linked = linked || rows0.some((c) => c.links.length > 0)
  const needle = q.trim().toLowerCase()
  const hit = (c) => !needle || [c.name, c.image, c.project, c.service, ...c.ports.map(portLabel), ...c.links.flatMap((l) => l.hosts)].some((x) => String(x || '').toLowerCase().includes(needle))
  const rows = rows0.filter((c) => hit(c) && (flt === 'all' || (flt === 'running' && c.state === 'running') || (flt === 'stopped' && c.state !== 'running') || (flt === 'project' && c.project) || (flt === 'alone' && !c.project) || (flt === 'linked' && c.links.length)))
  const st = Object.fromEntries(stats.rows.map((r) => [nkey(r, r.id), r]))
  const { rows: sorted, th } = useSort(rows, { key: 'name', dir: 1 }, { cpu: (c) => st[nkey(c, c.id)]?.cpu ?? -1, mem: (c) => st[nkey(c, c.id)]?.mem ?? -1, node: (c) => c._node?.name || '' })
  const T = (k, l) => <th>{th(k, l)}</th>
  if (list.isLoading) return <Loading />
  if (list.error) return <div className="banner err">{list.error.message}</div>
  return (
    <div className="col">
      <div className="row wrap" style={{ gap: 6 }}>
        {FILTERS.filter(([k]) => linked || k !== 'linked').map(([k, l]) => <button key={k} className={'dk-flt ' + (flt === k ? 'on' : '')} onClick={() => setFlt(k)}>{l}</button>)}
        {can('docker.remove') && !list.full && <Btn small kind="ghost" className="right" title="Supprimer tous les conteneurs arrêtés (les volumes et les images ne sont pas touchés)" onClick={() => prune(false, 'Les conteneurs arrêtés sont supprimés définitivement.')}><Sparkles size={13} /> Nettoyer</Btn>}
      </div>
      {(list.errors || []).map((e) => <div key={e.node.id} className="banner warn">« {e.node.name} » ne répond pas : {e.error.message}</div>)}
      {sorted.length === 0 ? <div className="card"><Empty><Box size={28} /><div>Aucun conteneur ne correspond.</div></Empty></div> : (
        <div className="card flush"><table className="t"><thead><tr>{list.full && T('node', 'Nœud')}{T('state', 'État')}{T('name', 'Nom')}<th>Projet</th>{linked && <th>Sites Traefik</th>}{T('cpu', 'CPU')}{T('mem', 'Mémoire')}<th>Ports</th><th /></tr></thead><tbody>
          {sorted.map((c) => {
            const [lab, kind] = stateOf(c); const s = st[nkey(c, c.id)]; const hosts = [...new Set(c.links.flatMap((l) => l.hosts))]
            return (
              <tr key={nkey(c, c.id)} style={{ cursor: 'pointer' }} onClick={() => onOpen(c.name, undefined, c._node)}>
                {list.full && <td><NodeChip node={c._node} /></td>}
                <td><Badge kind={kind}>{lab}</Badge></td>
                <td><b>{c.name}</b>{c.protected && <Badge kind="warn" style={{ marginLeft: 6 }}>{PROTECTED[c.protected]}</Badge>}<div className="muted small ellipsis" style={{ maxWidth: 260 }}>{c.image}</div></td>
                <td>{c.project ? <Badge>{c.project}</Badge> : <span className="muted">—</span>}</td>
                {linked && <td>{hosts.length ? <div className="row wrap" style={{ gap: 4 }}>{hosts.slice(0, 2).map((h) => <span key={h} className="dk-chip" onClick={(e) => { e.stopPropagation(); onOpen(c.name, 'traefik', c._node) }}>{h}</span>)}{hosts.length > 2 && <span className="muted small">+{hosts.length - 2}</span>}{c.links.every((l) => !l.sure) && <Badge kind="warn" title="Lien probable">?</Badge>}</div> : <span className="muted">—</span>}</td>}
                <td style={{ minWidth: 90 }}>{s ? <><span className="small">{s.cpu.toLocaleString('fr-FR')} %</span><MiniBar pct={s.cpu} label={`${s.cpu} %`} /></> : <span className="muted">—</span>}</td>
                <td style={{ minWidth: 100 }}>{s ? <><span className="small">{fmtSize(s.mem)}</span><MiniBar pct={s.memLimit ? (s.mem / s.memLimit) * 100 : 0} label={`${fmtSize(s.mem)} / ${fmtSize(s.memLimit)}`} /></> : <span className="muted">—</span>}</td>
                <td><div className="row wrap" style={{ gap: 4 }}>{c.ports.filter((p) => p.public).slice(0, 3).map((p, i) => <Badge key={i}>{portLabel(p)}</Badge>)}{c.ports.filter((p) => p.public).length > 3 && <span className="muted small">+{c.ports.filter((p) => p.public).length - 3}</span>}</div></td>
                <td className="actions" onClick={(e) => e.stopPropagation()}>
                  <Btn small kind="ghost" title="Journaux" onClick={() => onOpen(c.name, 'logs', c._node)}>Logs</Btn>
                  {can('docker.control') && (c.state === 'running' ? (c.protected ? null : <Btn small kind="ghost" title="Redémarrer" busy={quick.isPending} onClick={() => quick.mutate({ name: c.name, action: 'restart', node: c._node?.id })}><RotateCw size={13} /></Btn>)
                    : <Btn small kind="ghost" title="Démarrer" busy={quick.isPending} onClick={() => quick.mutate({ name: c.name, action: 'start', node: c._node?.id })}><Play size={13} /></Btn>)}
                </td>
              </tr>)
          })}</tbody></table></div>)}
    </div>
  )
}

const UsedBy = ({ names, onOpen }) => (names.length ? <div className="row wrap" style={{ gap: 4 }}>{names.slice(0, 4).map((n) => <Badge key={n} style={onOpen ? { cursor: 'pointer' } : undefined} onClick={onOpen ? () => onOpen(n) : undefined}>{n}</Badge>)}{names.length > 4 && <span className="muted small">+{names.length - 4}</span>}</div> : <span className="muted">inutilisé</span>)

function useRemove(url, listKey, label) {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const m = useMutation({ mutationFn: ({ id, force }) => api.del(`/docker/${url}/${enc(id)}${force ? '?force=true' : ''}`), onSuccess: () => { qc.invalidateQueries({ queryKey: [listKey] }); toast(`${label} supprimé`) }, onError: (e) => toast(e.message, 'err') })
  return async (id, message) => { if (await confirm({ title: `Supprimer « ${id} » ?`, message, confirmLabel: 'Supprimer' })) m.mutate({ id }) }
}

function Images({ q, can, onOpenContainer, params, setParams }) {
  const { node, setNode } = useNode(); const detail = params.get('im')
  const setDetail = (v, from) => { if (from && from.id !== node.id) setNode(from.id); const n = new URLSearchParams(params); if (v) n.set('im', v); else n.delete('im'); setParams(n, { replace: !v }) }
  const list = useNodeList('docker-images', '/docker/images', (d) => d.images, { eligible: dockerNode })
  const remove = useRemove('images', 'docker-images', 'Image'); const prune = usePrune('images', 'images inutilisées')
  const needle = q.trim().toLowerCase()
  const rows = list.rows.filter((i) => !needle || [...i.tags, i.id, ...i.usedBy].some((x) => x.toLowerCase().includes(needle)))
  const { rows: sorted, th } = useSort(rows, { key: 'name', dir: 1 }, { name: (i) => i.tags[0] || '~' + i.id, size: (i) => i.size, created: (i) => i.created, node: (i) => i._node?.name || '' })
  const T = (k, l) => <th>{th(k, l)}</th>
  if (list.isLoading) return <Loading />
  if (list.error) return <div className="banner err">{list.error.message}</div>
  const total = rows.reduce((s, i) => s + i.size, 0)
  return (
    <div className="col">
      <div className="row"><span className="muted grow">{rows.length} image{rows.length > 1 ? 's' : ''} · {fmtSize(total)}</span>
        {can('docker.remove') && !list.full && <><Btn small kind="ghost" title="Supprimer les images orphelines, sans nom ni conteneur (« dangling »)" onClick={() => prune(false, 'Les images sans nom (« dangling ») sont supprimées.')}><Sparkles size={13} /> Sans nom</Btn><Btn small kind="ghost" title="Supprimer toutes les images qu'aucun conteneur n'utilise, même nommées : elles seront retéléchargées au besoin" onClick={() => prune(true, 'Toutes les images qu\'aucun conteneur n\'utilise sont supprimées (elles seront retéléchargées au besoin).')}><Sparkles size={13} /> Toutes les inutilisées</Btn></>}</div>
      <div className="card flush"><table className="t"><thead><tr>{list.full && T('node', 'Nœud')}{T('name', 'Image')}{T('size', 'Taille')}<th>Utilisée par</th>{T('created', 'Créée')}<th /></tr></thead><tbody>
        {sorted.map((i) => <tr key={nkey(i, i.fullId)} style={{ cursor: 'pointer' }} title="Voir le détail de l'image" onClick={() => setDetail(i.fullId, i._node)}>{list.full && <td><NodeChip node={i._node} /></td>}<td>{i.tags.length ? i.tags.map((t) => <div key={t}><Copyable text={t}><b>{t}</b></Copyable></div>) : <span className="muted">sans nom</span>}<div className="muted small mono">{i.id}</div></td><td>{fmtSize(i.size)}</td><td><UsedBy names={i.usedBy} /></td><td className="muted small">{ago(i.created)}</td>
          <td className="actions" onClick={(e) => e.stopPropagation()}>{can('docker.remove') && !list.full && !i.usedBy.length && <Btn small kind="ghost" title="Supprimer cette image de la machine" onClick={() => remove(i.tags[0] || i.id, 'L\'image est supprimée de cette machine (elle sera retéléchargée au besoin).')}><Trash2 size={13} /></Btn>}</td></tr>)}</tbody></table></div>
      {detail && <ImageModal id={detail} onClose={() => setDetail(null)} onOpenContainer={(n) => { setDetail(null); onOpenContainer?.(n) }} />}
    </div>
  )
}

function Networks({ q, can, onOpen, params, setParams }) {
  const { node, setNode } = useNode(); const detail = params.get('nw'); const [adding, setAdding] = useState(false)
  const setDetail = (v, from) => { if (from && from.id !== node.id) setNode(from.id); const n = new URLSearchParams(params); if (v) n.set('nw', v); else n.delete('nw'); setParams(n, { replace: !v }) }
  const list = useNodeList('docker-networks', '/docker/networks', (d) => d.networks, { eligible: dockerNode })
  const remove = useRemove('networks', 'docker-networks', 'Réseau'); const prune = usePrune('networks', 'réseaux inutilisés')
  const needle = q.trim().toLowerCase()
  const rows = list.rows.filter((n) => !needle || [n.name, n.driver, ...n.subnets, ...n.usedBy].some((x) => x.toLowerCase().includes(needle)))
  if (list.isLoading) return <Loading />
  if (list.error) return <div className="banner err">{list.error.message}</div>
  return (
    <div className="col">
      <div className="row"><span className="muted grow">{rows.length} réseau{rows.length > 1 ? 'x' : ''}</span>{can('docker.control') && !list.full && <Btn small onClick={() => setAdding(true)} title="Créer un réseau Docker (bridge)"><Plus size={13} /> Nouveau réseau</Btn>}{can('docker.remove') && !list.full && <Btn small kind="ghost" title="Supprimer les réseaux que plus aucun conteneur n'utilise" onClick={() => prune(false, 'Les réseaux que plus aucun conteneur n\'utilise sont supprimés.')}><Sparkles size={13} /> Nettoyer</Btn>}</div>
      <div className="card flush"><table className="t"><thead><tr>{list.full && <th>Nœud</th>}<th>Nom</th><th>Pilote</th><th>Sous-réseau</th><th>Conteneurs</th><th /></tr></thead><tbody>
        {rows.map((n) => <tr key={nkey(n, n.id)} style={{ cursor: 'pointer' }} title="Voir le détail du réseau" onClick={() => setDetail(n.name, n._node)}>{list.full && <td><NodeChip node={n._node} /></td>}<td><b>{n.name}</b>{n.builtin && <Badge style={{ marginLeft: 6 }}>intégré</Badge>}{n.internal && <Badge style={{ marginLeft: 6 }} kind="warn">interne</Badge>}</td><td>{n.driver}</td><td className="mono small">{n.subnets.join(', ') || '—'}</td><td><UsedBy names={n.usedBy} onOpen={onOpen} /></td>
          <td className="actions" onClick={(e) => e.stopPropagation()}>{can('docker.remove') && !list.full && !n.builtin && !n.usedBy.length && <Btn small kind="ghost" title="Supprimer ce réseau (aucun conteneur ne l'utilise)" onClick={() => remove(n.name, 'Le réseau est supprimé.')}><Trash2 size={13} /></Btn>}</td></tr>)}</tbody></table></div>
      {detail && <NetworkModal name={detail} onClose={() => setDetail(null)} onOpenContainer={(n) => { setDetail(null); onOpen(n) }} />}
      {adding && <NewNetworkModal onClose={() => setAdding(false)} />}
    </div>
  )
}

function Volumes({ q, can, onOpen, params, setParams }) {
  const { node, setNode } = useNode(); const detail = params.get('vo')
  const setDetail = (v, from) => { if (from && from.id !== node.id) setNode(from.id); const n = new URLSearchParams(params); if (v) n.set('vo', v); else n.delete('vo'); setParams(n, { replace: !v }) }
  const list = useNodeList('docker-volumes', '/docker/volumes', (d) => d.volumes, { eligible: dockerNode })
  const remove = useRemove('volumes', 'docker-volumes', 'Volume'); const prune = usePrune('volumes', 'volumes inutilisés')
  const needle = q.trim().toLowerCase()
  const rows = list.rows.filter((v) => !needle || [v.name, v.driver, ...v.usedBy, v.labels['com.docker.compose.project']].some((x) => String(x || '').toLowerCase().includes(needle)))
  if (list.isLoading) return <Loading />
  if (list.error) return <div className="banner err">{list.error.message}</div>
  return (
    <div className="col">
      <div className="row"><span className="muted grow">{rows.length} volume{rows.length > 1 ? 's' : ''}</span>{can('docker.remove') && !list.full && <Btn small kind="ghost" title="Supprimer les volumes qu'aucun conteneur n'utilise : leurs DONNÉES sont perdues" onClick={() => prune(false, 'Tous les volumes qu\'aucun conteneur n\'utilise sont supprimés AVEC LEURS DONNÉES. Irréversible.')}><Sparkles size={13} /> Nettoyer</Btn>}</div>
      <div className="card flush"><table className="t"><thead><tr>{list.full && <th>Nœud</th>}<th>Nom</th><th>Projet</th><th>Utilisé par</th><th>Créé</th><th /></tr></thead><tbody>
        {rows.map((v) => <tr key={nkey(v, v.name)} style={{ cursor: 'pointer' }} title="Voir le détail du volume et son contenu" onClick={() => setDetail(v.name, v._node)}>{list.full && <td><NodeChip node={v._node} /></td>}<td><Copyable text={v.name}><b className="mono small">{v.name.length > 40 ? v.name.slice(0, 38) + '…' : v.name}</b></Copyable></td><td>{v.labels['com.docker.compose.project'] ? <Badge>{v.labels['com.docker.compose.project']}</Badge> : <span className="muted">—</span>}</td><td><UsedBy names={v.usedBy} onOpen={onOpen} /></td><td className="muted small">{v.created ? v.created.slice(0, 10) : '—'}</td>
          <td className="actions" onClick={(e) => e.stopPropagation()}>{can('docker.remove') && !list.full && !v.usedBy.length && <Btn small kind="ghost" title="Supprimer ce volume et ses données" onClick={() => remove(v.name, 'Le volume est supprimé AVEC SES DONNÉES. Irréversible.')}><Trash2 size={13} /></Btn>}</td></tr>)}</tbody></table></div>
      {detail && <VolumeModal name={detail} onClose={() => setDetail(null)} onOpenContainer={(n) => { setDetail(null); onOpen(n) }} />}
    </div>
  )
}
