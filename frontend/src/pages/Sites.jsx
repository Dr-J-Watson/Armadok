import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { NavLink, useSearchParams } from 'react-router-dom'
import { useGo } from '../lib/nav.js'
import SiteMenu from '../components/SiteMenu.jsx'
import { Activity, ArrowRight, ExternalLink, Globe, LayoutGrid, List, Lock, Pause, FolderInput, FolderPlus, Pencil, Play, Plus, Trash2, Unlink } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Copyable, Empty, Loading, SearchBox, Seg, Switch, useUi } from '../components/ui.jsx'
import { usePersist } from '../lib/persist.js'
import { useToggler } from '../components/Toggle.jsx'
import { useNode, useNodeList } from '../node.jsx'
import { NodeChip } from '../components/NodeSwitcher.jsx'
import { certKind, certLabel } from './Certs.jsx'
import MissingDns from '../components/MissingDns.jsx'
import { useSiteRemoval } from '../components/SiteRemoval.jsx'
import { useMeta, useOverview } from './Traefik.jsx'
import { extractHosts } from '../lib/util.js'
import { useFeatures } from '../lib/features.js'
import { humanOf, techName } from '../traefik/human.js'
import { Breadcrumb, FolderModal, FolderTile, MoveModal, UpTile, folderStats, useFolderNav } from '../components/ProjectFolders.jsx'

// Les routeurs que l'interface écrit elle-même (bannissements : zz-webui-…) ne sont pas des sites
export const isManaged = (i) => /(^|\/)zz-webui-/.test(i.file || '')

// Où mène un routeur : le nœud dont l'adresse est visée par l'URL de son service (ou le nœud du Traefik lui-même pour un conteneur du même réseau,
// localhost…). Une adresse qu'aucun nœud ne porte (une box, un NAS…) est « externe » : on affiche l'adresse.
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', 'host.docker.internal'])
function targetOf(r, all, nodes) {
  const svc = String(r.spec?.service || '').split('@')[0]
  const prov = String(r.spec?.service || '').split('@')[1] || r.provider
  if (!svc) return null
  const s = all.find((x) => x.kind === 'services' && x.name === svc && x._node.id === r._node.id && (!prov || !x.provider || x.provider === prov)) || all.find((x) => x.kind === 'services' && x.name === svc && x._node.id === r._node.id)
  const urls = (s?.spec?.loadBalancer?.servers || []).map((v) => v?.url).filter(Boolean)
  if (!urls.length) return prov === 'docker' ? { node: r._node } : null
  const out = []
  for (const u of urls) {
    let host = ''
    try { host = new URL(u).hostname.toLowerCase().replace(/^\[|\]$/g, '') } catch { continue }
    const hit = nodes.find((n) => [...(n.addresses || []), ...(n.addressesSeen || [])].some((a) => a.toLowerCase() === host))
    if (hit) out.push({ node: hit })
    else if (LOCAL_HOSTS.has(host) || !/[.:]/.test(host)) out.push({ node: r._node })
    else out.push({ host })
  }
  const seen = new Set()
  return out.filter((t) => { const k = t.node ? 'n' + t.node.id : 'h' + t.host; if (seen.has(k)) return false; seen.add(k); return true })
}

// Affichage complet : les sites de TOUS les nœuds qui portent Traefik. Cliquer sur une ligne ouvre le site sur son nœud (les actions restent propres à un nœud).
function AllSites() {
  const { node, nodes, setNode, setFullView } = useNode(); const go = useGo(); const [q, setQ] = useState('')
  const list = useNodeList('overview', '/traefik/overview', (d) => d.items, { eligible: (n) => (n.modules || []).includes('traefik') && (n.caps || []).includes('sites.view') })
  const rows = list.rows.filter((i) => i.kind === 'routers' && i.proto === 'http' && !isManaged(i)).map((i) => ({ ...i, _targets: targetOf(i, list.rows, nodes) }))
    .filter((i) => !q || (i.name + (i.spec?.rule || '') + i._node.name + (i._targets || []).map((t) => t.node?.name || t.host).join(' ')).toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => a._node.name.localeCompare(b._node.name) || a.name.localeCompare(b.name))
  const open = (r) => { setFullView(false); if (r._node.id !== node.id) setNode(r._node.id); go(`/sites?open=${encodeURIComponent(r.name)}`) }
  if (list.isLoading) return <Loading />
  return (
    <>
      <div className="row wrap" style={{ marginBottom: 14, gap: 10 }}><div className="grow"><h1 style={{ margin: 0 }}>Sites</h1><div className="muted">Affichage complet : les sites de tous vos nœuds. Cliquer sur une ligne ouvre le site sur son nœud.</div></div></div>
      {list.errors.map((e) => <div key={e.node.id} className="banner warn">« {e.node.name} » ne répond pas : {e.error.message}</div>)}
      <div className="row" style={{ marginBottom: 10 }}><SearchBox value={q} onChange={setQ} placeholder="Rechercher un site, une règle, un nœud…" /></div>
      {rows.length === 0 ? <div className="card"><Empty><Globe size={28} /><div>Aucun site.</div></Empty></div> : (
        <div className="card flush"><table className="t"><thead><tr><th title="Le nœud dont le Traefik porte ce site">Traefik</th><th>Site</th><th>Règle</th><th title="Le nœud (ou l'adresse) vers lequel le site mène">Mène vers</th><th>État</th></tr></thead><tbody>
          {rows.map((r) => <tr key={r._node.id + ':' + r.id} style={{ cursor: 'pointer' }} onClick={() => open(r)}>
            <td><NodeChip node={r._node} /></td><td><b>{r.name}</b></td><td className="mono small" style={{ wordBreak: 'break-all' }}>{r.spec?.rule}</td>
            <td>{!r._targets ? <span className="muted">—</span> : r._targets.map((t) => t.node ? <NodeChip key={t.node.id} node={t.node} /> : <span key={t.host} className="small mono muted" title="Aucun de vos nœuds n'a cette adresse (box, autre machine…)">{t.host}</span>)}</td>
            <td>{r.disabled ? <Badge kind="warn">en pause</Badge> : <Badge kind="ok">actif</Badge>}</td></tr>)}</tbody></table></div>)}
    </>
  )
}

export default function Sites(props) {
  const { fullView } = useNode()
  return fullView ? <AllSites /> : <SitesOne {...props} />
}

function SitesOne({ openSite, openWizard }) {
  const ov = useOverview(); const meta = useMeta(); const qc = useQueryClient(); const { toast, confirm } = useUi()
  const [params, setParams] = useSearchParams()
  const [q, setQ] = useState(''); const [add, setAdd] = useState(null); const [flt, setFlt] = useState('all'); const [view, setView] = usePersist('sites-view', 'cards')
  const { toggle, busy } = useToggler(); const { canManage, can } = useNode(); const go = useGo(); const removal = useSiteRemoval()
  const feat = useFeatures(); const statsOn = !!(feat && feat.metrics.enabled && feat.metrics.allowed)
  const sync = useQuery({ queryKey: ['sync'], queryFn: () => api.get('/sync'), enabled: !!meta.data?.dnsEnabled })
  const certs = useQuery({ queryKey: ['certs'], queryFn: () => api.get('/traefik/certs'), staleTime: 60000, retry: false })
  const certOf = (r) => (certs.data?.configured ? certs.data.sites.find((c) => c.id === r.id) : null)
  const items = ov.data?.items || []
  // Dossiers de sites : rangement de la liste (rien ne change dans Traefik). Vue filtrée d'un nœud qui publie par un autre Traefik : ses dossiers à lui.
  const fq = useQuery({ queryKey: ['site-folders'], queryFn: () => api.get('/traefik/site-folders'), retry: false })
  const [fmodal, setFmodal] = useState(null); const [moveS, setMoveS] = useState(null)
  const folders = fq.data?.folders || []; const assign = fq.data?.assign || {}
  const canFold = !!fq.data?.canManage
  const inv = () => qc.invalidateQueries({ queryKey: ['site-folders'] })
  const regroup = useMutation({ mutationFn: (b) => api.post('/traefik/site-folders/regroup', b), onSuccess: (r) => { inv(); setMoveS(null); if (r.moved) toast(`${r.moved} élément${r.moved > 1 ? 's' : ''} rangé${r.moved > 1 ? 's' : ''}`) }, onError: (e) => toast(e.message, 'err') })
  const saveFolder = useMutation({ mutationFn: ({ id, ...b }) => (id ? api.put(`/traefik/site-folders/${id}`, b) : api.post('/traefik/site-folders', b)), onSuccess: () => { inv(); setFmodal(null) }, onError: (e) => toast(e.message, 'err') })
  const delFolder = useMutation({ mutationFn: (id) => api.del(`/traefik/site-folders/${id}`), onSuccess: () => { inv(); toast('Dossier supprimé : son contenu est remonté d\'un niveau') }, onError: (e) => toast(e.message, 'err') })
  const nav = useFolderNav({ folders, enabled: canFold, onDrop: (x, target) => regroup.mutate({ routers: x.kind === 'site' ? [x.id] : [], folders: x.kind === 'folder' ? [x.id] : [], folderId: target }) })
  const folderOf = (r) => (nav.byId[assign[r.name]] ? assign[r.name] : null)

  useEffect(() => {
    const o = params.get('open')
    if (o && ov.data) { const it = items.find((i) => i.kind === 'routers' && i.proto === 'http' && i.name === o); if (it) openSite(it); setParams({}, { replace: true }) }
  }, [params.get('open'), ov.data])

  const dnsOf = useMemo(() => Object.fromEntries((sync.data?.rows || []).map((r) => [r.host, r])), [sync.data])
  const routers = items.filter((i) => i.kind === 'routers' && i.proto === 'http' && !isManaged(i) && (!q || (i.name + i.spec.rule).toLowerCase().includes(q.toLowerCase()))).sort((a, b) => a.name.localeCompare(b.name))
  const dnsMissing = (r) => extractHosts(r.spec.rule).map((h) => dnsOf[h]).filter(Boolean).some((x) => x.status === 'missing')
  const FILTERS = [['all', 'Tous', () => true], ['paused', 'En pause', (r) => r.disabled], ['noprot', 'Sans protection', (r) => !r.disabled && !(r.spec.middlewares || []).length], ['nocert', 'Sans certificat', (r) => !r.disabled && certOf(r)?.worst === 'missing'], ['dns', 'DNS manquant', (r) => !r.disabled && dnsMissing(r)]]
  const allSites = routers
  const counts = Object.fromEntries(FILTERS.map(([id, , fn]) => [id, allSites.filter(fn).length]))
  const shown = allSites.filter(FILTERS.find((f) => f[0] === flt)[2])
  const svcOf = (r) => items.find((i) => i.kind === 'services' && i.proto === 'http' && i.name === r.spec.service)
  const paused = routers.filter((r) => r.disabled).length
  // Un filtre ou une recherche cherche dans tous les dossiers ; sinon on ne montre que le dossier courant. Les chiffres d'un dossier ne comptent que les sites VISIBLES ici (vue filtrée comprise).
  const flat = !!q.trim() || flt !== 'all'
  const vis = flat || folders.length === 0 ? shown : shown.filter((r) => folderOf(r) === nav.cur)
  const needle = q.trim().toLowerCase()
  const subFolders = flat && !needle ? [] : folders.filter((f) => (needle ? [f.name, f.description].some((x) => String(x || '').toLowerCase().includes(needle)) : (nav.byId[f.parentId] ? f.parentId : null) === nav.cur))
  const showUp = !flat && nav.cur != null
  const siteItems = routers.map((r) => ({ folderId: folderOf(r), status: r.disabled ? 'stopped' : 'running' }))
  const askDelete = async (f) => { const st = folderStats(folders, siteItems, f.id); if (await confirm({ title: `Supprimer le dossier « ${f.name} » ?`, message: st.total || st.sub ? 'Ses sites et ses sous-dossiers remontent d\'un niveau. Aucun site n\'est supprimé.' : 'Le dossier est vide.', confirmLabel: 'Supprimer' })) delFolder.mutate(f.id) }
  const dragS = (r) => nav.dragOf('site', r.name)
  const missing = (sync.data?.rows || []).filter((r) => r.status === 'missing' && r.canEdit)

  const del = async (r) => {
    const choice = await removal.confirmRemoval({ title: `Supprimer « ${r.name} » ?`, message: 'Le site ne sera plus servi par Traefik. Son service est supprimé s\'il n\'est utilisé par personne d\'autre.\nSans case cochée ci-dessous, l\'enregistrement DNS n\'est pas touché.',
      confirmLabel: 'Supprimer', sites: [{ name: r.name, hosts: extractHosts(r.spec.rule), locked: true }] })
    if (choice) await removal.apply(choice)
  }

  if (ov.isLoading) return <Loading />
  return (
    <>
      <div className="page-head"><div><h1>Sites</h1><p>Chaque site relie une adresse, des protections{canManage && <> (<NavLink to="/middlewares">les gérer</NavLink>)</>} et un service.{paused > 0 && <> · <b>{paused}</b> en pause</>}</p></div>
        <div className="right row" style={{ gap: 8 }}>{canFold && <Btn title="Créer un dossier pour ranger les sites" onClick={() => setFmodal({ parentId: nav.cur })}><FolderPlus size={14} /> Nouveau dossier</Btn>}
          {can('sites.create') && <Btn kind="primary" onClick={() => openWizard({})}><Plus size={15} /> Nouveau site</Btn>}</div></div>
      {missing.length > 0 && <div className="banner"><Unlink size={18} />{missing.length} adresse(s) n'ont pas d'enregistrement DNS chez OVH.<Btn small kind="primary" className="right" onClick={() => setAdd(missing)}><Plus size={13} /> Les ajouter</Btn></div>}
      <div className="row wrap" style={{ marginBottom: 14 }}><SearchBox value={q} onChange={setQ} placeholder="Chercher un site…" />
        <Seg options={FILTERS.filter((f) => f[0] === 'all' || counts[f[0]] > 0 || flt === f[0]).map(([id, label]) => ({ id, label: `${label}${id === 'all' ? '' : ' · ' + counts[id]}` }))} value={flt} onChange={setFlt} />
        <span className="right"><Seg options={[{ id: 'cards', label: <LayoutGrid size={14} />, hint: 'Cartes' }, { id: 'list', label: <List size={14} />, hint: 'Liste compacte' }]} value={view} onChange={setView} /></span></div>
      {(folders.length > 0 || nav.cur != null) && !flat && <Breadcrumb path={nav.crumbs} onOpen={nav.openFolder} dropFor={nav.dropFor} />}
      {(subFolders.length > 0 || showUp) && <div className="project-grid folders">
        {showUp && <UpTile parent={nav.byId[nav.parentOfCur]} dnd={nav.dropFor(nav.parentOfCur)} onOpen={() => nav.openFolder(nav.parentOfCur)} />}
        {subFolders.map((f) => <FolderTile key={f.id} f={f} noun="site" st={folderStats(folders, siteItems, f.id)} canManage={canFold} dnd={{ ...nav.dropFor(f.id), props: { ...nav.dragOf('folder', f.id), ...nav.dropFor(f.id).props } }} onOpen={() => nav.openFolder(f.id)} onEdit={() => setFmodal({ folder: f })} onDelete={() => askDelete(f)} />)}
      </div>}
      {routers.length === 0 && !subFolders.length ? <div className="card"><Empty>Aucun site pour l'instant.{can('sites.create') && <><br /><Btn kind="primary" style={{ marginTop: 12 }} onClick={() => openWizard({})}><Plus size={14} /> Créer mon premier site</Btn></>}</Empty></div> : (
        shown.length === 0 ? <div className="card"><Empty>Aucun site ne correspond à ce filtre.<br /><Btn style={{ marginTop: 10 }} onClick={() => setFlt('all')}>Tout afficher</Btn></Empty></div>
        : vis.length === 0 ? (subFolders.length === 0 ? <div className="muted" style={{ textAlign: 'center', padding: 18 }}>Ce dossier est vide.{canFold && ' Glissez-y des sites, ou utilisez le bouton « Ranger » d\'une carte.'}</div> : null)
        : view === 'list' ? (
        <div className="card flush"><table className="t"><thead><tr><th>Adresse</th><th>Destination</th><th>Protections</th><th>État</th><th /></tr></thead><tbody>
          {vis.map((r) => { const hosts = extractHosts(r.spec.rule); const svc = svcOf(r); const targets = svc?.spec?.loadBalancer?.servers?.map((x) => x.url).filter(Boolean) || []; const c = certOf(r)
            return (
              <tr key={r.id} className="click" onClick={() => !r.disabled && openSite(r)} style={{ opacity: r.disabled ? .55 : 1 }} {...dragS(r)}>
                <td><Copyable text={hosts[0] || r.name}><b>{hosts[0] || r.name}</b></Copyable>{hosts.length > 1 && <Badge> +{hosts.length - 1}</Badge>}</td>
                <td className="mono small"><span className="ellipsis" style={{ maxWidth: 240, display: 'inline-block' }}>{targets.join(', ') || r.spec.service}</span></td>
                <td>{(r.spec.middlewares || []).length === 0 ? <Badge kind="warn"><Lock size={11} /> aucune</Badge> : <span className="small">{r.spec.middlewares.slice(0, 2).join(', ')}{r.spec.middlewares.length > 2 ? ` +${r.spec.middlewares.length - 2}` : ''}</span>}</td>
                <td><div className="row wrap" style={{ gap: 4 }}>{r.disabled && <Badge kind="warn"><Pause size={10} /> pause</Badge>}{dnsMissing(r) && <Badge kind="warn">DNS manquant</Badge>}{c && !r.disabled && c.worst === 'missing' && <Badge kind="warn">sans certificat</Badge>}{c && !r.disabled && ['expired', 'expiring'].includes(c.worst) && <Badge kind="err">certificat</Badge>}</div></td>
                <td className="actions" onClick={(e) => e.stopPropagation()}>
                  {canFold && <Btn small kind="ghost" icon title="Ranger dans un dossier" aria-label="Ranger dans un dossier" onClick={() => setMoveS([r])}><FolderInput size={14} /></Btn>}
                  {r.editable && !r.disabled && can('sites.toggle') && <Switch value title="Mettre ce site en pause" busy={busy} onChange={() => toggle({ ids: [r.id], enable: false, label: `« ${hosts[0] || r.name} »` })} />}
                  {r.editable && r.disabled && can('sites.toggle') && <Btn small kind="primary" busy={busy} onClick={() => toggle({ ids: [r.id], enable: true, label: `« ${hosts[0] || r.name} »` })}><Play size={13} /> Reprendre</Btn>}
                  {r.editable && can('sites.delete') && <Btn small kind="ghost" icon title="Supprimer ce site" aria-label="Supprimer ce site" onClick={() => del(r)}><Trash2 size={14} /></Btn>}
                  <SiteMenu site={r} host={hosts[0]} dnsRow={(hosts.map((h) => dnsOf[h]).filter(Boolean))[0]} statsService={statsOn && !r.disabled ? r.spec.service : null} canCerts={can('certs.view')} canDuplicate={can('sites.create')} onDuplicate={() => openWizard({ from: r })} />
                </td>
              </tr>) })}</tbody></table></div>
        ) : (
        <div className="site-grid">
          {vis.map((r) => {
            const hosts = extractHosts(r.spec.rule); const svc = svcOf(r)
            const targets = svc?.spec?.loadBalancer?.servers?.map((s) => s.url).filter(Boolean) || []
            const st = hosts.map((h) => dnsOf[h]).filter(Boolean)
            return (
              <div className={'card site ' + (r.disabled ? 'off' : '')} key={r.id} onClick={() => !r.disabled && openSite(r)} {...dragS(r)}>
                <div className="row"><Globe size={16} className="muted" /><Copyable text={hosts[0] || r.name} className="grow"><b className="ellipsis">{hosts[0] || r.name}</b></Copyable>{r.disabled && <Badge kind="warn"><Pause size={10} /> en pause</Badge>}{hosts.length > 1 && <Badge>+{hosts.length - 1}</Badge>}
                  {statsOn && r.spec.service && !r.disabled && <a className="stat-link" href={`/monitoring?tab=services&service=${encodeURIComponent(r.spec.service)}`} onClick={(e) => { e.preventDefault(); e.stopPropagation(); go(`/monitoring?tab=services&service=${encodeURIComponent(r.spec.service)}`) }} title="Statistiques de ce site (trafic, erreurs, lenteur)"><Activity size={14} /></a>}
                  {hosts[0] && <a href={`https://${hosts[0]}`} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} title="Ouvrir"><ExternalLink size={14} /></a>}
                  {canFold && <Btn small kind="ghost" icon title="Ranger dans un dossier (ou glissez la carte sur un dossier)" aria-label="Ranger dans un dossier" onClick={(e) => { e.stopPropagation(); setMoveS([r]) }}><FolderInput size={14} /></Btn>}
                  <SiteMenu site={r} host={hosts[0]} dnsRow={st[0]} statsService={statsOn && !r.disabled ? r.spec.service : null} canCerts={can('certs.view')} canDuplicate={can('sites.create')} onDuplicate={() => openWizard({ from: r })} /></div>
                <div className="muted small mono rule" title={r.spec.rule}>{r.spec.rule}</div>
                <div className="row wrap mws" style={{ gap: 5 }}>
                  {(r.spec.middlewares || []).length === 0 ? <Badge kind="warn" title="Aucune protection (middleware) sur ce site : toute personne qui connaît l'adresse peut y accéder."><Lock size={11} /> Sans protection</Badge>
                    : [...r.spec.middlewares.slice(0, 3).map((m) => { const mw = items.find((i) => i.kind === 'middlewares' && i.name === m); return <Badge key={m} kind="info" title={mw ? `${techName(mw.type)} · ${humanOf(mw.type)[0]}` : ''}>{m}</Badge> }), ...(r.spec.middlewares.length > 3 ? [<Badge key="more" title={r.spec.middlewares.slice(3).join(', ')}>+{r.spec.middlewares.length - 3}</Badge>] : [])]}
                </div>
                <div className="row wrap small" style={{ gap: 6 }}>
                  <ArrowRight size={13} className="muted" style={{ flex: "none" }} /><code className="ellipsis">{targets.join(', ') || r.spec.service}</code>
                  {st.some((x) => x.status === 'missing') && <Badge kind="warn" className="right" title="L'adresse de ce site n'a pas d'enregistrement DNS chez le fournisseur : on ne peut pas l'atteindre par son nom.">DNS manquant</Badge>}
                  {(() => { const c = certOf(r); if (!c || r.disabled || ['unmanaged'].includes(c.worst)) return null; const h = c.hosts.find((x) => x.status === c.worst); const d = h?.days
                    const toCert = (e) => { e.stopPropagation(); go(`/traefik?tab=certs&host=${encodeURIComponent(h?.host || hosts[0] || '')}`) }
                    return c.worst === 'missing' ? <Badge kind="warn" style={{ cursor: 'pointer' }} onClick={toCert} title="Traefik n'a pas (encore) obtenu de certificat HTTPS pour ce site. Cliquez pour voir l'onglet Certificats."><Lock size={10} /> sans certificat</Badge>
                      : <Badge kind={certKind(d)} style={{ cursor: 'pointer' }} onClick={toCert} title={`Certificat HTTPS valable jusqu'au ${new Date(h.notAfter * 1000).toLocaleDateString('fr-FR')} (${h.issuer || 'émetteur inconnu'})`}><Lock size={10} /> {certLabel(d)}</Badge> })()}
                  {st.length > 0 && st.every((x) => x.status === 'local') && <Badge className="right" title="Domaine en DNS local : c'est votre DNS qui le résout, rien à vérifier ici.">DNS local</Badge>}
                  {st.length > 0 && st.every((x) => ['ok', 'wildcard', 'managed'].includes(x.status)) && <Badge kind="ok" className="right" title="L'enregistrement DNS existe et mène bien à ce serveur.">DNS ok</Badge>}
                  {!r.editable && <Badge className="right" title="Vous pouvez voir ce site mais pas le modifier : demandez les droits d'écriture sur son domaine.">lecture seule</Badge>}
                </div>
                {r.editable && <div className="row site-actions" onClick={(e) => e.stopPropagation()}>
                  {r.disabled ? (can('sites.toggle') ? <Btn small kind="primary" busy={busy} onClick={() => toggle({ ids: [r.id], enable: true, label: `« ${hosts[0] || r.name} »` })}><Play size={13} /> Reprendre</Btn> : null)
                    : (can('sites.edit') ? <Btn small kind="ghost" onClick={() => openSite(r)}><Pencil size={13} /> Modifier</Btn> : null)}
                  <span className="right row">{!r.disabled && can('sites.toggle') && <Switch value title="Mettre ce site en pause : Traefik cesse de le servir, rien n'est supprimé. Réversible à tout moment." busy={busy} onChange={() => toggle({ ids: [r.id], enable: false, label: `« ${hosts[0] || r.name} »` })} />}{can('sites.delete') && <Btn small kind="ghost" title="Supprimer ce site" aria-label="Supprimer ce site" onClick={() => del(r)}><Trash2 size={13} /></Btn>}</span></div>}
              </div>
            )
          })}
        </div>
      ))}
      {fmodal && <FolderModal folder={fmodal.folder} parentId={fmodal.parentId} folders={folders} busy={saveFolder.isPending} onClose={() => setFmodal(null)} onSave={(b) => saveFolder.mutate({ id: fmodal.folder?.id, ...b })} />}
      {moveS && <MoveModal noun="site" projects={moveS.map((r) => ({ id: r.name, name: extractHosts(r.spec.rule)[0] || r.name }))} folders={folders} current={folderOf(moveS[0])} busy={regroup.isPending} onClose={() => setMoveS(null)} onMove={(to) => regroup.mutate({ routers: moveS.map((r) => r.name), folderId: to })} />}
      {add && <MissingDns rows={add} onClose={() => setAdd(null)} />}
      {removal.dialog}
    </>
  )
}
