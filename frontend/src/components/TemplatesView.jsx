import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Download, FileCode2, FolderGit2, FolderInput, FolderPlus, Globe, LayoutGrid, List, Lock, Plus, Rocket, Trash2, Upload } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Empty, Field, Loading, Modal, SearchBox, Seg, useSort, useUi } from './ui.jsx'
import { BulkBar, SelectAllBox, SelectBox, useBulk, useSelect } from './DockerSelect.jsx'
import { FolderModal, FolderSelect, FolderTile, MoveModal, SectionCrumbs, UpTile, descendants, useFolderNav } from './ProjectFolders.jsx'
import TemplateEditor from './TemplateEditor.jsx'
import { FromTemplate } from './NewProject.jsx'
import { fmtSize } from '../lib/docker.js'
import { saveBlob } from '../lib/transfer.js'
import { usePersist } from '../lib/persist.js'
import { fmtDate } from '../lib/util.js'

const MAX_ZIP = 2_900_000   // l'API refuse les corps de plus de 4 Mo : l'archive voyage en base64

const b64Blob = (b64, type = 'application/zip') => { const s = atob(b64); const u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return new Blob([u], { type }) }
const readB64 = (file) => new Promise((ok, ko) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1] || ''); r.onerror = () => ko(r.error); r.readAsDataURL(file) })
const plural = (n, w) => `${n} ${w}${n > 1 ? 's' : ''}`

// La section « Modèles » de la page Projets : les modèles de projet rangés en dossiers, sélection multiple, exploration et modification fichier par fichier,
// import / export en zip. Les modèles sont communs à tous les nœuds. Même disposition que les projets et les sites : titre + actions, recherche, fil d'Ariane, dossiers, liste.
export default function TemplatesView({ onSection, tabs }) {
  const [q, setQ] = useState('')
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const [view, setView] = usePersist('templates-view', 'cards')
  const list = useQuery({ queryKey: ['templates'], queryFn: () => api.get('/projects/templates/list') })
  const [fmodal, setFmodal] = useState(null); const [moveT, setMoveT] = useState(null); const [edit, setEdit] = useState(null); const [use, setUse] = useState(null)
  const [imp, setImp] = useState(null); const [newT, setNewT] = useState(false); const [expo, setExpo] = useState(null); const [over, setOver] = useState(false)
  const fileIn = useRef(null)
  const d = list.data; const folders = d?.folders || []; const canCreate = !!d?.canCreate
  const refresh = () => qc.invalidateQueries({ queryKey: ['templates'] })
  const regroup = useMutation({ mutationFn: (b) => api.post('/projects/templates/regroup', b), onSuccess: (r) => { refresh(); setMoveT(null); if (r.moved) toast(`${plural(r.moved, 'élément')} rangé${r.moved > 1 ? 's' : ''}`) }, onError: (e) => toast(e.message, 'err') })
  const saveFolder = useMutation({ mutationFn: ({ id, ...b }) => (id ? api.put(`/projects/templates/folders/${id}`, b) : api.post('/projects/templates/folders', b)), onSuccess: () => { refresh(); setFmodal(null) }, onError: (e) => toast(e.message, 'err') })
  const delFolder = useMutation({ mutationFn: (id) => api.del(`/projects/templates/folders/${id}`), onSuccess: () => { refresh(); toast('Dossier supprimé : son contenu est remonté d\'un niveau') }, onError: (e) => toast(e.message, 'err') })
  const dup = useMutation({ mutationFn: (id) => api.post(`/projects/templates/${encodeURIComponent(id)}/duplicate`, {}), onSuccess: (r) => { refresh(); toast(`« ${r.name} » créé : une copie privée, modifiable`); setEdit(r.id) }, onError: (e) => toast(e.message, 'err') })
  const bulk = useBulk(['templates'])
  const nav = useFolderNav({ folders, enabled: canCreate, onDrop: (x, target) => regroup.mutate({ templates: x.kind === 'template' ? [x.id] : [], folders: x.kind === 'folder' ? [x.id] : [], folderId: target }) })
  const { byId, cur, crumbs, openFolder, dragOf, dropFor } = nav
  const needle = (q || '').trim().toLowerCase()
  const all = d?.templates || []
  const hit = (t) => !needle || [t.name, t.description, t.createdBy, ...t.files, ...t.vars.map((v) => v.key)].some((x) => String(x || '').toLowerCase().includes(needle))
  const fid = (t) => (byId[t.folderId] ? t.folderId : null)
  const rows0 = all.filter(hit).filter((t) => needle || fid(t) === cur)
  const { rows, th } = useSort(rows0, { key: 'name', dir: 1 }, { visibility: (t) => (t.builtin ? 0 : t.shared ? 1 : 2), files: (t) => t.files.length, vars: (t) => t.vars.length, author: (t) => t.createdBy || '', updated: (t) => t.updatedAt || 0, size: (t) => t.size || 0 })
  const sel = useSelect(rows, (t) => t.id)
  const subFolders = folders.filter((f) => (needle ? [f.name, f.description].some((x) => String(x || '').toLowerCase().includes(needle)) : (byId[f.parentId] ? f.parentId : null) === cur))
  const showUp = !needle && cur != null
  const stats = (id) => { const ids = new Set([id, ...descendants(folders, id)]); return { total: all.filter((t) => ids.has(t.folderId)).length, sub: ids.size - 1, running: 0, partial: 0, stopped: 0, missing: 0 } }
  const dragT = (t) => (t.mine ? dragOf('template', t.id) : {})

  // ── export (zip)
  const doExport = async ({ ids = [], folderIds = [], includeSecrets = false }) => {
    try {
      const r = await api.post('/projects/templates/export', { ids, folders: folderIds, includeSecrets })
      saveBlob(r.name, b64Blob(r.b64)); toast(`${plural(r.count, 'modèle')} exporté${r.count > 1 ? 's' : ''} : ${r.name}`); setExpo(null)
    } catch (e) { toast(e.message, 'err') }
  }
  const askExport = (ids, folderIds = []) => {
    const withEnv = ids.map((i) => all.find((t) => t.id === i)).filter((t) => t && t.mine && !t.shared && t.files.includes('.env'))
    if (d?.canEnv && (withEnv.length || folderIds.length)) setExpo({ ids, folderIds }); else doExport({ ids, folderIds })
  }
  // ── import (zip) : par le bouton ou en glissant l'archive sur l'onglet
  const takeFile = async (file) => {
    if (!file) return
    if (file.size > MAX_ZIP) { toast(`Archive trop grosse (${fmtSize(MAX_ZIP)} au plus)`, 'err'); return }
    try { setImp({ file, b64: await readB64(file) }) } catch { toast('Lecture du fichier impossible', 'err') }
  }

  // ── actions sur la sélection
  const picked = sel.picked; const mineSel = picked.filter((t) => t.mine)
  const delMany = () => bulk.run({
    items: mineSel, label: (t) => t.name, ask: { title: `Supprimer ${plural(mineSel.length, 'modèle')} ?`, message: `${mineSel.slice(0, 6).map((t) => t.name).join(', ')}${mineSel.length > 6 ? '…' : ''}. Les projets déjà créés avec ces modèles ne changent pas.${picked.length > mineSel.length ? ' Les modèles intégrés ou d\'autres utilisateurs sont ignorés.' : ''}`, confirmLabel: 'Supprimer' },
    fn: (t) => api.del(`/projects/templates/${encodeURIComponent(t.id)}`), onDone: () => { sel.clear(); refresh() },
  })
  const shareMany = async (shared) => {
    const items = mineSel.filter((t) => t.shared !== shared)
    if (!items.length) return toast(shared ? 'Déjà partagés' : 'Déjà privés', 'err')
    bulk.run({
      items, label: (t) => t.name, ask: shared ? { title: `Partager ${plural(items.length, 'modèle')} ?`, message: 'Tous ceux qui peuvent créer des projets les verront. Les valeurs des secrets de leur .env sont vidées pour de bon.', confirmLabel: 'Partager', danger: false } : undefined,
      fn: (t) => api.put(`/projects/templates/${encodeURIComponent(t.id)}`, { shared }), onDone: () => { sel.clear(); refresh() },
    })
  }
  const askDeleteFolder = async (f) => { const st = stats(f.id); if (await confirm({ title: `Supprimer le dossier « ${f.name} » ?`, message: st.total || st.sub ? 'Ses modèles et ses sous-dossiers remontent d\'un niveau. Aucun modèle n\'est supprimé.' : 'Ce dossier est vide.', confirmLabel: 'Supprimer' })) delFolder.mutate(f.id) }

  if (list.isLoading) return <Loading />
  if (list.error) return <div className="banner err">{list.error.message}</div>
  const Actions = ({ t }) => (
    <span className="row" style={{ gap: 2 }} onClick={(e) => e.stopPropagation()}>
      {canCreate && <Btn small kind="ghost" icon title="Créer un projet à partir de ce modèle" aria-label="Créer un projet" onClick={() => setUse(t.id)}><Rocket size={13} /></Btn>}
      <Btn small kind="ghost" icon title="Exporter ce modèle (zip)" aria-label="Exporter" onClick={() => askExport([t.id])}><Download size={13} /></Btn>
      {canCreate && <Btn small kind="ghost" icon title="Dupliquer : une copie privée à vous, modifiable" aria-label="Dupliquer" onClick={() => dup.mutate(t.id)}><Copy size={13} /></Btn>}
      {t.mine && <Btn small kind="ghost" icon title="Supprimer ce modèle" aria-label="Supprimer" onClick={async () => { if (await confirm({ title: `Supprimer le modèle « ${t.name} » ?`, message: 'Les projets déjà créés ne changent pas.', confirmLabel: 'Supprimer' })) bulk.run({ items: [t], label: (x) => x.name, fn: (x) => api.del(`/projects/templates/${encodeURIComponent(x.id)}`), onDone: refresh }) }}><Trash2 size={13} /></Btn>}
    </span>
  )
  const Vis = ({ t }) => (t.builtin ? <Badge>intégré</Badge> : <Badge kind={t.shared ? 'info' : 'warn'} title={t.shared ? `Partagé par ${t.createdBy}` : `Privé (${t.createdBy})`}>{t.shared ? <><Globe size={10} /> partagé</> : <><Lock size={10} /> privé</>}</Badge>)
  const empty = rows.length === 0 && subFolders.length === 0 && !showUp
  return (
    <div className={over ? 'drop-on' : ''} style={{ borderRadius: 12 }}
      onDragOver={(e) => { if ([...(e.dataTransfer?.types || [])].includes('Files')) { e.preventDefault(); setOver(true) } }} onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOver(false) }}
      onDrop={(e) => { if (![...(e.dataTransfer?.types || [])].includes('Files')) return; e.preventDefault(); setOver(false); if (canCreate) takeFile(e.dataTransfer.files[0]); else toast('Il faut le droit de créer des modèles', 'err') }}>
      <input ref={fileIn} type="file" accept=".zip,application/zip" hidden onChange={(e) => { const f = e.target.files[0]; e.target.value = ''; takeFile(f) }} />
      <div className="row" style={{ marginBottom: 14 }}>
        <div className="grow"><h1 style={{ margin: 0 }}>Projets</h1><div className="muted">Vos modèles de projet : un point de départ réutilisable, commun à tous les nœuds.</div></div>
        {canCreate && <Btn onClick={() => setFmodal({ parentId: cur })} title="Créer un dossier pour ranger les modèles"><FolderPlus size={14} /> Nouveau dossier</Btn>}
        {canCreate && <Btn onClick={() => fileIn.current?.click()} title="Importer un ou plusieurs modèles depuis une archive zip (ou glissez-la ici)"><Upload size={14} /> Importer (.zip)</Btn>}
        {canCreate && <Btn kind="primary" onClick={() => setNewT(true)} title="Créer un modèle vide à compléter"><Plus size={14} /> Nouveau modèle</Btn>}
      </div>
      <div className="row wrap" style={{ gap: 10, marginBottom: 14 }}>
        {tabs}
        <SearchBox value={q} onChange={setQ} placeholder="Rechercher un modèle, un fichier, une variable…" />
        <span className="right row wrap" style={{ gap: 8 }}>
          <span className="row" style={{ gap: 6 }}><SelectAllBox s={sel} /><span className="muted small">{picked.length ? plural(picked.length, 'sélectionné') : 'Tout sélectionner'}</span></span>
          <Btn small onClick={() => askExport(rows.map((t) => t.id), [])} disabled={!rows.length} title="Exporter les modèles affichés dans une archive zip"><Download size={14} /> Tout exporter</Btn>
          <Seg options={[{ id: 'cards', label: <LayoutGrid size={14} />, hint: 'Cartes' }, { id: 'list', label: <List size={14} />, hint: 'Liste compacte' }]} value={view} onChange={setView} />
        </span>
      </div>
      <SectionCrumbs section="templates" onSection={onSection} path={!needle ? crumbs : []} onOpen={openFolder} dropFor={dropFor} />
      {(subFolders.length > 0 || showUp) && <div className="project-grid folders">
        {showUp && <UpTile parent={byId[byId[cur]?.parentId]} dnd={dropFor(byId[cur]?.parentId ?? null)} onOpen={() => openFolder(byId[cur]?.parentId ?? null)} />}
        {subFolders.map((f) => <FolderTile key={f.id} f={f} st={stats(f.id)} noun="modèle" canManage={canCreate} dnd={{ ...dropFor(f.id), props: { ...dragOf('folder', f.id), ...dropFor(f.id).props } }} onOpen={() => openFolder(f.id)}
          onEdit={() => setFmodal({ folder: f })} onDelete={() => askDeleteFolder(f)} onExport={() => askExport([], [f.id])} />)}
      </div>}
      {empty ? <div className="card"><Empty><FileCode2 size={28} /><div>{needle ? 'Aucun modèle ne correspond.' : <>Aucun modèle ici.<br /><span className="muted small">{canCreate ? 'Créez-en un, importez une archive zip, ou « Enregistrer comme modèle » depuis la fiche d\'un projet.' : ''}</span></>}</div></Empty></div>
        : rows.length === 0 ? (needle ? null : <div className="muted" style={{ textAlign: 'center', padding: 18 }}>Ce dossier ne contient pas de modèle.{canCreate && ' Glissez-y des modèles, ou utilisez « Ranger ».'}</div>)
          : view === 'list' ? (
            <div className="card flush"><table className="t"><thead><tr><th style={{ width: 30 }} /><th>{th('name', 'Modèle')}</th><th>{th('visibility', 'Visibilité')}</th><th>{th('files', 'Fichiers')}</th><th>{th('vars', 'Variables')}</th><th>{th('size', 'Taille')}</th><th>{th('author', 'Auteur')}</th><th>{th('updated', 'Modifié')}</th><th /></tr></thead><tbody>
              {rows.map((t) => <tr key={t.id} style={{ cursor: 'pointer' }} onClick={() => setEdit(t.id)} {...dragT(t)}>
                <td onClick={(e) => e.stopPropagation()} title=""><SelectBox s={sel} row={t} /></td>
                <td><b>{t.name}</b>{t.description && <div className="muted small ellipsis" style={{ maxWidth: 360 }}>{t.description}</div>}</td><td><Vis t={t} /></td>
                <td>{t.files.length}</td><td>{t.vars.length}</td><td>{fmtSize(t.size || 0)}</td><td className="small">{t.createdBy || <span className="muted">—</span>}</td><td className="small">{t.updatedAt ? fmtDate(t.updatedAt * 1000) : <span className="muted">—</span>}</td>
                <td className="actions"><Actions t={t} /></td></tr>)}</tbody></table></div>
          ) : (
            <div className="project-grid">{rows.map((t) => (
              <div key={t.id} className="card project" onClick={() => setEdit(t.id)} {...dragT(t)}>
                <div className="row" style={{ gap: 8 }}><span onClick={(e) => e.stopPropagation()} title=""><SelectBox s={sel} row={t} /></span><FileCode2 size={16} className="muted" /><b className="grow ellipsis" title={t.name}>{t.name}</b><Vis t={t} /></div>
                <div className="muted small" style={{ minHeight: 34 }}>{t.description || <span className="muted">Pas de description.</span>}</div>
                <div className="row wrap" style={{ gap: 5 }}>{t.files.slice(0, 5).map((f) => <Badge key={f}>{f}</Badge>)}{t.files.length > 5 && <span className="muted small">+{t.files.length - 5}</span>}</div>
                <div className="row" style={{ gap: 6 }}><span className="muted small grow ellipsis">{plural(t.files.length, 'fichier')} · {plural(t.vars.length, 'variable')}{t.createdBy ? ` · ${t.createdBy}` : ''}</span><Actions t={t} /></div>
              </div>))}</div>)}
      <BulkBar s={sel} noun="modèle">
        <Btn small onClick={() => askExport(picked.map((t) => t.id))}><Download size={14} /> Exporter</Btn>
        {canCreate && mineSel.length > 0 && <Btn small onClick={() => setMoveT(mineSel)}><FolderInput size={14} /> Ranger</Btn>}
        {mineSel.length > 0 && <Btn small disabled={bulk.busy} onClick={() => shareMany(true)}><Globe size={14} /> Partager</Btn>}
        {mineSel.length > 0 && <Btn small disabled={bulk.busy} onClick={() => shareMany(false)}><Lock size={14} /> Rendre privés</Btn>}
        {mineSel.length > 0 && <Btn small kind="danger" busy={bulk.busy} onClick={delMany}><Trash2 size={14} /> Supprimer</Btn>}
      </BulkBar>
      {fmodal && <FolderModal folder={fmodal.folder} parentId={fmodal.parentId} folders={folders} what="modèles" busy={saveFolder.isPending} onClose={() => setFmodal(null)} onSave={(b) => saveFolder.mutate({ id: fmodal.folder?.id, ...b })} />}
      {moveT && <MoveModal projects={moveT} folders={folders} current={fid(moveT[0])} noun="modèle" busy={regroup.isPending} onClose={() => setMoveT(null)} onMove={(to) => regroup.mutate({ templates: moveT.map((x) => x.id), folderId: to }, { onSuccess: () => sel.clear() })} />}
      {edit && <TemplateEditor id={edit} canCreate={canCreate} canEnv={!!d?.canEnv} onClose={() => setEdit(null)} onUse={(id) => { setEdit(null); setUse(id) }} onExport={(id) => askExport([id])} onDuplicate={(id) => { setEdit(null); dup.mutate(id) }} />}
      {use && <FromTemplate initial={use} onClose={() => setUse(null)} />}
      {newT && <NewTemplate folderId={cur} onClose={() => setNewT(false)} onDone={(r) => { setNewT(false); refresh(); setEdit(r.id) }} />}
      {imp && <ImportModal file={imp.file} b64={imp.b64} folders={folders} cur={cur} onClose={() => setImp(null)} onDone={() => { setImp(null); refresh() }} />}
      {expo && <Modal small title="Exporter" onClose={() => setExpo(null)} footer={<><Btn onClick={() => setExpo(null)}>Annuler</Btn><Btn onClick={() => doExport({ ...expo, includeSecrets: false })}>Sans les secrets</Btn><Btn kind="primary" onClick={() => doExport({ ids: expo.ids, folderIds: expo.folderIds, includeSecrets: true })}>Avec les secrets</Btn></>}>
        <div className="col"><div>Certains de vos modèles privés gardent des valeurs de secrets dans leur <span className="mono">.env</span>.</div><div className="muted small">« Sans les secrets » (conseillé si l'archive sera partagée) les vide, comme pour un modèle partagé. « Avec les secrets » les inclut tels quels : gardez l'archive pour vous.</div></div></Modal>}
    </div>
  )
}

function NewTemplate({ folderId, onClose, onDone }) {
  const { toast } = useUi(); const [name, setName] = useState(''); const [desc, setDesc] = useState('')
  const m = useMutation({ mutationFn: () => api.post('/projects/templates/new', { name: name.trim(), description: desc.trim(), folderId }), onSuccess: onDone, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal small title="Nouveau modèle" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" disabled={!name.trim()} busy={m.isPending} onClick={() => m.mutate()}>Créer et ouvrir</Btn></>}>
      <div className="col"><div className="muted small">Un modèle de départ (un compose et un .env minimaux), privé : vous le complétez ensuite fichier par fichier.</div>
        <Field label="Nom"><input className="input" autoFocus maxLength={80} value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && name.trim()) m.mutate() }} placeholder="Mon application web" /></Field>
        <Field label="Description (facultatif)"><input className="input" maxLength={300} value={desc} onChange={(e) => setDesc(e.target.value)} /></Field></div>
    </Modal>
  )
}

// Importer une archive : on LIT d'abord (aperçu, rien n'est écrit), on choisit quoi faire des doublons et où ranger, puis on applique.
function ImportModal({ file, b64, folders, cur, onClose, onDone }) {
  const { toast } = useUi()
  const [onConflict, setOnConflict] = useState('rename'); const [shared, setShared] = useState(false); const [dest, setDest] = useState(cur ?? null)
  const body = (apply) => ({ b64, filename: file.name, apply, onConflict, shared, folderId: dest })
  const prev = useMutation({ mutationFn: () => api.post('/projects/templates/import', body(false)) })
  useEffect(() => { prev.mutate() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const go = useMutation({
    mutationFn: () => api.post('/projects/templates/import', body(true)),
    onSuccess: (r) => { const bits = [r.created && `${r.created} créé${r.created > 1 ? 's' : ''}`, r.replaced && `${r.replaced} remplacé${r.replaced > 1 ? 's' : ''}`, r.renamed && `${r.renamed} renommé${r.renamed > 1 ? 's' : ''}`, r.skipped && `${r.skipped} ignoré${r.skipped > 1 ? 's' : ''}`, r.errors && `${r.errors} en erreur`].filter(Boolean); toast(`Import : ${bits.join(', ') || 'rien à faire'}`, r.errors && !r.created ? 'err' : 'ok'); onDone() },
    onError: (e) => toast(e.message, 'err'),
  })
  const list = prev.data?.templates || []; const ok = list.filter((t) => !t.error)
  return (
    <Modal wide title={`Importer · ${file.name}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" disabled={!ok.length} busy={go.isPending} onClick={() => go.mutate()}><Upload size={14} /> Importer {ok.length ? plural(ok.length, 'modèle') : ''}</Btn></>}>
      {prev.isPending ? <Loading /> : prev.error ? <div className="banner err">{prev.error.message}</div> : (
        <div className="col" style={{ gap: 12 }}>
          <div className="banner info">L'archive n'est jamais exécutée : seuls ses fichiers texte sont lus. Relisez-les avant de créer un projet à partir d'un modèle venu d'ailleurs.</div>
          <div className="card flush"><table className="t"><thead><tr><th>Modèle</th><th>Rangé dans</th><th>Fichiers</th><th /></tr></thead><tbody>
            {list.map((t) => <tr key={t.index}>
              <td><b>{t.name}</b>{t.description && <div className="muted small">{t.description}</div>}</td>
              <td className="small">{t.folders.length ? t.folders.join(' / ') : <span className="muted">—</span>}</td>
              <td className="small">{plural(t.files.length, 'fichier')}{t.vars ? ` · ${plural(t.vars, 'variable')}` : ''}</td>
              <td>{t.error ? <Badge kind="err" title={t.error}>{t.error.length > 60 ? t.error.slice(0, 58) + '…' : t.error}</Badge> : t.conflict ? <Badge kind="warn">{t.conflict.builtin ? 'même nom qu\'un modèle intégré' : 'nom déjà pris'}</Badge> : <Badge kind="ok">nouveau</Badge>}</td></tr>)}</tbody></table></div>
          {list.some((t) => t.conflict) && <Field label="Si un modèle du même nom existe déjà"><Seg value={onConflict} onChange={setOnConflict} options={[{ id: 'rename', label: 'Garder les deux (renommer)' }, { id: 'replace', label: 'Remplacer le mien' }, { id: 'skip', label: 'Ignorer' }]} /></Field>}
          <Field label="Visibilité des modèles importés" help="Partagé : visible de tous ceux qui créent des projets, valeurs des secrets du .env vidées. Privé : vous seul, .env gardé tel quel."><Seg value={shared ? 'shared' : 'private'} onChange={(v) => setShared(v === 'shared')} options={[{ id: 'private', label: <><Lock size={12} /> Privés</> }, { id: 'shared', label: <><Globe size={12} /> Partagés</> }]} /></Field>
          <Field label="Ranger dans" help="Les dossiers de l'archive sont recréés à partir de ce dossier."><FolderSelect folders={folders} value={dest} onChange={setDest} /></Field>
        </div>)}
    </Modal>
  )
}
