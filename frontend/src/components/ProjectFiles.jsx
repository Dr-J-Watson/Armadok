import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createPortal } from 'react-dom'
import { CheckCircle2, ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown, Download, Ellipsis, FilePlus, Folder, FolderInput, FolderOpen, FolderPlus, FolderUp, History, Pencil, RefreshCw, Save, Tags, Trash2, Upload } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Loading, Modal, useUi } from './ui.jsx'
import CodeEditor, { Diff } from './CodeEditor.jsx'
import LabelsModal from './LabelsModal.jsx'
import { BulkBar, SelectBox, useBulk, useSelect } from './DockerSelect.jsx'
import { MAX_UPLOAD, collectDrop, downloadPaths, fromInput, uploadFile } from '../lib/transfer.js'
import { isCompose, kindOf, keyRank, langOf } from '../lib/files.js'
import { enc, fmtSize } from '../lib/docker.js'
import { fmtDate } from '../lib/util.js'

const MAX_IMPORT = 500   // fichiers par import, sans le droit « sans limite »
const BIG_FILE = 2 * 1024 * 1024   // au-delà, le fichier ne s'ouvre pas tout seul
const parentOf = (p) => p.split('/').slice(0, -1).join('/')
const validRel = (r) => !!r && !r.startsWith('/') && !r.endsWith('/') && !r.includes('\\') && !r.split('/').some((x) => ['', '.', '..', '.git'].includes(x)) && r.length <= 300 && r.split('/').length <= 8

// Liste plate -> arbre (dossiers d'abord, puis les fichiers clés — compose, .env, Makefile, Dockerfile — puis le reste par nom).
export function buildRows(entries, collapsed) {
  const root = { children: new Map() }
  for (const e of entries) {
    const parts = e.path.split('/'); let cur = root
    parts.forEach((name, i) => {
      const path = parts.slice(0, i + 1).join('/'); let n = cur.children.get(name)
      if (!n) { n = { name, path, type: i < parts.length - 1 ? 'dir' : e.type, children: new Map() }; cur.children.set(name, n) }
      if (i === parts.length - 1) Object.assign(n, { type: e.type, size: e.size, env: e.env })
      cur = n
    })
  }
  const out = []
  const walk = (node, depth) => {
    const kids = [...node.children.values()].sort((a, b) => (a.type === b.type ? 0 : a.type === 'dir' ? -1 : 1) || keyRank(a.path) - keyRank(b.path) || a.name.localeCompare(b.name))
    for (const n of kids) {
      out.push({ ...n, depth, count: n.children.size })
      if (n.type === 'dir' && !collapsed.has(n.path)) walk(n, depth + 1)
    }
  }
  walk(root, 0)
  return out
}

export function FileMark({ path, size = 15 }) {
  const k = kindOf(path)
  return <k.Icon size={size} className={'fk fk-' + k.id} aria-hidden />
}

function Tree({ rows, root, chk, sel, cur, onPick, onDir, collapsed, toggle, dirty, perms, onAction, over, setOver, onDropTo }) {
  // glisser-déposer : une ligne de dossier (ou la racine) est une cible ; sur un fichier, la cible est son dossier
  const dropProps = (dest) => ({ onDragOver: (ev) => { if (perms.edit && [...(ev.dataTransfer?.types || [])].includes('Files')) { ev.preventDefault(); ev.stopPropagation(); if (over !== dest) setOver(dest) } }, onDrop: (ev) => { if (perms.edit) { ev.preventDefault(); ev.stopPropagation(); onDropTo(ev, dest) } } })
  const [menu, setMenu] = useState(null)   // { path, type, x, y } : position fixe, hors de la liste qui défile (sinon elle rogne le menu)
  useEffect(() => {
    if (!menu) return
    const close = () => setMenu(null)
    window.addEventListener('click', close); window.addEventListener('keydown', close); window.addEventListener('resize', close); window.addEventListener('scroll', close, true)
    return () => { window.removeEventListener('click', close); window.removeEventListener('keydown', close); window.removeEventListener('resize', close); window.removeEventListener('scroll', close, true) }
  }, [menu])
  const openMenu = (ev, e) => {
    ev.stopPropagation()
    if (menu?.path === e.path) { setMenu(null); return }
    const r = ev.currentTarget.getBoundingClientRect(); const w = 220; const h = (e.type === 'dir' ? 80 : 40) + (perms.edit ? (e.type === 'dir' ? 130 : 90) : 0)
    const x = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)); const y = r.bottom + 4 + h > window.innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4
    setMenu({ path: e.path, type: e.type, x, y })
  }
  return (
    <div className="ft" role="tree">
      {(() => { const on = cur === '' && !sel; return (
        <div className={'ft-item ft-root ' + (on ? 'on' : '') + (over === '' ? ' drop-on' : '')} style={{ paddingLeft: 4 }} role="treeitem" aria-selected={on} {...dropProps('')}>
          <SelectBox s={chk} row={root} />
          <button type="button" className="ft-main" onClick={() => onDir('')} title="Racine du projet : cochez-la pour tout sélectionner ; cliquez pour y importer ou créer des fichiers"><span className="ft-sp" /><FolderOpen size={15} className="fk fk-dir" /><b className="ellipsis">Racine du projet</b></button>
        </div>) })()}
      {rows.map((e) => {
        const dir = e.type === 'dir'; const on = dir ? cur === e.path && !sel : sel === e.path; const k = dir ? null : kindOf(e.path)
        return (
          <div key={e.path} className={'ft-item ' + (on ? 'on' : '') + (k?.key ? ' key' : '') + (dir && over === e.path ? ' drop-on' : '')} style={{ paddingLeft: 4 + (e.depth + 1) * 14 }} role="treeitem" aria-selected={on} {...dropProps(dir ? e.path : parentOf(e.path))}>
            <SelectBox s={chk} row={e} />
            <button type="button" className="ft-main" onClick={() => (dir ? (toggle(e.path), onDir(e.path)) : onPick(e.path))} title={k?.hint || e.path}>
              {dir ? (collapsed.has(e.path) ? <ChevronRight size={14} className="muted" /> : <ChevronDown size={14} className="muted" />) : <span className="ft-sp" />}
              {dir ? (collapsed.has(e.path) ? <Folder size={15} className="fk fk-dir" /> : <FolderOpen size={15} className="fk fk-dir" />) : <FileMark path={e.path} />}
              <span className="ellipsis">{e.name}</span>
              {k?.key && <span className={'ft-tag fk-bg-' + k.id}>{k.label}</span>}
              {dirty && sel === e.path && <i className="dot warn" title="Modifié, pas encore enregistré" />}
            </button>
            <button type="button" className="ft-more" aria-label={`Actions sur ${e.name}`} aria-haspopup="menu" aria-expanded={menu?.path === e.path} onClick={(ev) => openMenu(ev, e)}><Ellipsis size={15} /></button>
          </div>
        )
      })}
      {!rows.length && <div className="muted small" style={{ padding: 8 }}>Dossier vide.</div>}
      {menu && createPortal(
        <div className="ft-menu" role="menu" style={{ position: 'fixed', left: menu.x, top: menu.y, right: 'auto', width: 220 }} onClick={(ev) => ev.stopPropagation()}>
          <button type="button" onClick={() => { setMenu(null); onAction('download', menu.path, menu.type) }}><Download size={14} /> Télécharger{menu.type === 'dir' ? ' (zip)' : ''}</button>
          {perms.edit && menu.type === 'dir' && <button type="button" onClick={() => { setMenu(null); onAction('upload', menu.path) }}><Upload size={14} /> Importer des fichiers ici</button>}
          {perms.edit && menu.type === 'dir' && <button type="button" onClick={() => { setMenu(null); onAction('new-file', menu.path) }}><FilePlus size={14} /> Nouveau fichier ici</button>}
          {perms.edit && menu.type === 'dir' && <button type="button" onClick={() => { setMenu(null); onAction('new-dir', menu.path) }}><FolderPlus size={14} /> Nouveau dossier ici</button>}
          {perms.edit && <button type="button" onClick={() => { setMenu(null); onAction('move', menu.path, menu.type) }}><Pencil size={14} /> Renommer / déplacer</button>}
          {perms.edit && <button type="button" className="danger" onClick={() => { setMenu(null); onAction('delete', menu.path, menu.type) }}><Trash2 size={14} /> Supprimer</button>}
        </div>, document.body)}
    </div>
  )
}

// Chemin d'un fichier : le NOM reste toujours lisible, c'est le début du chemin (dossiers) qui se tronque, en tête.
function PathLabel({ path }) {
  const i = path.lastIndexOf('/'); const dir = i >= 0 ? path.slice(0, i + 1) : ''; const base = i >= 0 ? path.slice(i + 1) : path
  return <span className="pf-path mono small" title={path}>{dir && <span className="pf-dir"><bdi>{dir}</bdi></span>}<b className="pf-base">{base}</b></span>
}

export default function ProjectFiles({ id, perms, canTraefik, onChanged }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const tree = useQuery({ queryKey: ['project-tree', id], queryFn: () => api.get(`/projects/${id}/tree`) })
  const [sel, setSel] = useState(null); const [cur, setCur] = useState(''); const [text, setText] = useState(''); const [orig, setOrig] = useState(''); const [hash, setHash] = useState(''); const [err, setErr] = useState('')
  const [big, setBig] = useState(null); const [large, setLarge] = useState(false)   // big : fichier trop volumineux en attente d'un « charger quand même » ; large : chargé ainsi, en lecture seule
  const [modal, setModal] = useState(null); const [check, setCheck] = useState(null); const [collapsed, setCollapsed] = useState(null); const [pane, setPane] = useState(true)
  const entries = tree.data?.entries || []
  const dirs = useMemo(() => entries.filter((e) => e.type === 'dir').map((e) => e.path), [entries])
  const dirty = sel != null && text !== orig
  const seeded = useRef(false)
  const rows = useMemo(() => buildRows(entries, collapsed || new Set()), [entries, collapsed])
  // cocher un dossier coche tout son contenu (sous-dossiers compris, même repliés) ; on peut ensuite décocher un élément en particulier
  const below = useMemo(() => { const m = new Map([['', entries]]); for (const e of entries) if (e.type === 'dir') m.set(e.path, entries.filter((x) => x.path.startsWith(e.path + '/'))); return m }, [entries])
  const root = useMemo(() => ({ path: '', name: 'Racine du projet', type: 'dir' }), [])   // la racine est une ligne comme les autres : la cocher sélectionne tout
  const chk = useSelect(useMemo(() => [root, ...rows], [root, rows]), (e) => e.path, () => true, undefined, (e) => below.get(e.path) || [])
  const bulk = useBulk(['project-tree'])
  const [up, setUp] = useState(null); const [dl, setDl] = useState(false); const [over, setOver] = useState(null)   // over : dossier visé par un glisser-déposer en cours ('' = racine), null sinon   // up : import en cours { n, total, name, pct }
  const fileIn = useRef(null); const dirIn = useRef(null); const upBase = useRef('')
  // premier affichage : les dossiers profonds (2 niveaux et plus) sont repliés
  useEffect(() => { if (tree.data && !seeded.current) { seeded.current = true; setCollapsed(new Set(dirs.filter((d) => d.split('/').length >= 2))) } }, [tree.data]) // eslint-disable-line react-hooks/exhaustive-deps
  const narrow = () => window.matchMedia?.('(max-width: 900px)').matches
  const load = async (rel, force = false) => {
    if (rel === sel && !force) { if (narrow()) setPane(false); return }
    if (dirty && !(await confirm({ title: 'Modifications non enregistrées', message: 'Elles seront perdues si vous ouvrez un autre fichier.', confirmLabel: 'Abandonner' }))) return
    setErr(''); setCheck(null); setSel(rel); setCur(parentOf(rel)); setBig(null); setLarge(false); if (narrow()) setPane(false)
    if (kindOf(rel).binary) { setText(''); setOrig(''); setErr('Ce type de fichier ne s\'affiche pas dans l\'éditeur (binaire). Vous pouvez le renommer, le déplacer ou le supprimer.'); return }
    const size = entries.find((e) => e.path === rel)?.size
    if (!force && size > BIG_FILE) { setText(''); setOrig(''); setBig({ rel, size }); return }
    try { const r = await api.get(`/projects/${id}/file?rel=${enc(rel)}${force ? '&force=true' : ''}`); setText(r.content); setOrig(r.content); setHash(r.hash); setLarge(!!r.large) } catch (e) { setErr(e.message); setText(''); setOrig('') }
  }
  useEffect(() => { if (!sel && entries.length) { const c = entries.find((e) => e.type === 'file' && isCompose(e.path) && !e.path.includes('/')) || entries.find((e) => e.type === 'file' && isCompose(e.path)); if (c) load(c.path) } }, [tree.data]) // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = () => { qc.invalidateQueries({ queryKey: ['project-tree', id] }); qc.invalidateQueries({ queryKey: ['project', id] }); onChanged?.() }
  const save = useMutation({
    mutationFn: ({ create, rel, content }) => api.put(`/projects/${id}/file`, { rel: rel || sel, content: content ?? text, baseHash: create ? '' : hash, create: !!create }),
    onSuccess: (r, v) => { toast(v.create ? 'Fichier créé' : 'Fichier enregistré'); setModal(null); if (v.create) { setSel(r.path); setCur(parentOf(r.path)); setErr(''); setText(v.content ?? ''); setOrig(v.content ?? ''); setHash(r.hash); setCollapsed((c) => { const n = new Set(c); let p = parentOf(r.path); while (p) { n.delete(p); p = parentOf(p) } return n }) } else { setOrig(text); setHash(r.hash) } refresh() },
    onError: (e) => toast(e.message, 'err'),
  })
  const mkdir = useMutation({ mutationFn: (rel) => api.post(`/projects/${id}/mkdir`, { rel }), onSuccess: (r) => { toast('Dossier créé'); setModal(null); setCur(r.path); setCollapsed((c) => { const n = new Set(c); let p = r.path; while (p) { n.delete(p); p = parentOf(p) } return n }); refresh() }, onError: (e) => toast(e.message, 'err') })
  const move = useMutation({
    mutationFn: ({ src, dst }) => api.post(`/projects/${id}/move`, { src, dst }),
    onSuccess: (r, v) => { toast('Déplacé'); setModal(null); if (sel === v.src) setSel(r.path); else if (sel?.startsWith(v.src + '/')) setSel(r.path + sel.slice(v.src.length)); refresh() },
    onError: (e) => toast(e.message, 'err'),
  })
  const del = useMutation({
    mutationFn: ({ rel, type }) => (type === 'dir' ? api.del(`/projects/${id}/dir?rel=${enc(rel)}`) : api.del(`/projects/${id}/file?rel=${enc(rel)}`)),
    onSuccess: (r, v) => { toast(v.type === 'dir' ? (r?.files > (r?.history ?? r?.files) ? `Dossier supprimé (${r.files} fichiers ; seuls ${r.history} fichiers texte sont gardés dans l\'historique)` : 'Dossier supprimé (les fichiers texte restent dans l\'historique)') : 'Fichier supprimé (une copie est gardée dans l\'historique)'); if (sel === v.rel || sel?.startsWith(v.rel + '/')) { setSel(null); setText(''); setOrig(''); setErr('') } refresh() },
    onError: (e) => toast(e.message, 'err'),
  })
  const validate = async () => { try { setCheck(await api.post(`/projects/${id}/validate`, { content: text })) } catch (e) { toast(e.message, 'err') } }
  const askDelete = async (rel, type) => { if (await confirm({ title: `Supprimer ${type === 'dir' ? 'le dossier' : 'le fichier'} « ${rel} » ?`, message: type === 'dir' ? 'Le dossier et tout son contenu seront supprimés. Les fichiers texte restent dans l\'historique ; les autres sont perdus.' : 'Une copie reste dans l\'historique.', confirmLabel: 'Supprimer' })) del.mutate({ rel, type }) }
  // ── télécharger / importer / actions groupées
  const protectedPath = (p) => isCompose(p) && !p.includes('/')
  // Ce sur quoi on agit : les fichiers cochés, et les dossiers cochés DONT TOUT le contenu l'est (un dossier à moitié décoché n'est pas touché lui-même, seulement ses éléments cochés).
  // Un dossier entièrement coché englobe ce qu'il contient.
  const picked = useMemo(() => {
    const on = entries.filter((e) => chk.has(e)); const whole = new Set(on.filter((e) => e.type === 'dir' && (below.get(e.path) || []).every((x) => chk.has(x))).map((e) => e.path))
    return on.filter((e) => (e.type === 'file' || whole.has(e.path)) && !(() => { let p = parentOf(e.path); while (p) { if (whole.has(p)) return true; p = parentOf(p) } return false })())
  }, [entries, below, chk.picked]) // eslint-disable-line react-hooks/exhaustive-deps
  const download = async (rels) => {
    setDl(true)
    try { const m = await downloadPaths(id, rels); toast(rels.length === 1 && m.files === 1 && !m.name.endsWith('.zip') ? `« ${m.name} » téléchargé` : `Archive « ${m.name} » téléchargée (${m.files} fichier${m.files > 1 ? 's' : ''})`) } catch (e) { toast(e.message, 'err') } finally { setDl(false) }
  }
  const upload = async (items, base) => {
    if (!perms.edit || !items.length) return
    if (items.length > MAX_IMPORT && !perms.nolimit) { toast(`Trop de fichiers d'un coup (${MAX_IMPORT} au plus) : il faut le droit « Transferts et suppressions sans limite de taille » (à demander au gestionnaire du nœud)`, 'err'); return }
    const todo = []; const skipped = []
    for (const it of items) {
      const rel = (base ? base + '/' : '') + it.path
      if (!validRel(rel)) skipped.push(`${it.path} (nom ou profondeur non acceptés)`)
      else if (!perms.env && it.file.name.startsWith('.env')) skipped.push(`${it.path} (secrets : droit manquant)`)
      else if (it.file.size > MAX_UPLOAD && !perms.nolimit) skipped.push(`${it.path} (plus de ${MAX_UPLOAD / 1048576} Mo : droit « sans limite » requis)`)
      else todo.push({ ...it, rel })
    }
    if (!todo.length) { toast(`Rien d'importé : ${skipped.slice(0, 3).join(' · ')}`, 'err'); return }
    const taken = new Set(entries.filter((e) => e.type === 'file').map((e) => e.path)); const clash = todo.filter((t) => taken.has(t.rel))
    if (clash.length && !(await confirm({ title: `Remplacer ${clash.length} fichier${clash.length > 1 ? 's' : ''} existant${clash.length > 1 ? 's' : ''} ?`, message: `${clash.slice(0, 5).map((t) => t.rel).join(', ')}${clash.length > 5 ? '…' : ''}. Les fichiers texte remplacés restent dans l'historique.`, confirmLabel: 'Remplacer' }))) return
    let done = 0; const failed = []
    for (const t of todo) {
      setUp({ n: done + failed.length + 1, total: todo.length, name: t.path, pct: 0 })
      try { await uploadFile(id, t.file, t.rel, taken.has(t.rel), (p) => setUp((u) => u && { ...u, pct: p }), !!perms.nolimit); done++ } catch (e) { failed.push(`${t.path} : ${e.message}`) }
    }
    setUp(null); refresh()
    if (base) setCollapsed((c) => { const n = new Set(c); let p = base; while (p) { n.delete(p); p = parentOf(p) } return n })
    const bad = [...failed, ...skipped]
    if (!bad.length) toast(`${done} fichier${done > 1 ? 's' : ''} importé${done > 1 ? 's' : ''}`)
    else toast(`${done} importé${done > 1 ? 's' : ''}, ${bad.length} ignoré${bad.length > 1 ? 's' : ''} : ${bad.slice(0, 3).join(' · ')}${bad.length > 3 ? ` (+${bad.length - 3})` : ''}`, done ? 'ok' : 'err')
  }
  const pickFiles = (base) => { upBase.current = base; fileIn.current?.click() }
  const onPicked = (ev) => { const items = fromInput(ev.target.files); ev.target.value = ''; upload(items, upBase.current) }
  const onDropTo = async (ev, dest) => { setOver(null); upload(await collectDrop(ev.dataTransfer), dest) }   // dest : dossier visé (ligne survolée), sinon le dossier courant
  const delMany = () => {
    const items = picked.filter((r) => !protectedPath(r.path)); const skip = picked.length - items.length; const gone = []
    if (!items.length) return toast('Le fichier compose du projet ne se supprime pas d\'ici', 'err')
    bulk.run({
      items, label: (r) => r.path, ask: { title: `Supprimer ${items.length} élément${items.length > 1 ? 's' : ''} ?`, message: `${items.slice(0, 6).map((r) => r.path).join(', ')}${items.length > 6 ? '…' : ''}. Les dossiers partent avec leur contenu ; les fichiers texte restent dans l'historique, les autres sont perdus.${skip ? ' Le fichier compose du projet est ignoré.' : ''}`, confirmLabel: 'Supprimer' },
      fn: async (r) => { await api.del(`/projects/${id}/${r.type === 'dir' ? 'dir' : 'file'}?rel=${enc(r.path)}`); gone.push(r.path) },
      onDone: () => { chk.clear(); if (sel && gone.some((p) => sel === p || sel.startsWith(p + '/'))) { setSel(null); setText(''); setOrig(''); setErr('') } refresh() },
    })
  }
  const moveMany = (dest) => {
    const items = picked.filter((r) => !protectedPath(r.path) && parentOf(r.path) !== dest); const moved = []
    setModal(null)
    if (!items.length) return toast('Rien à déplacer : tout est déjà dans ce dossier, ou protégé', 'err')
    bulk.run({
      items, label: (r) => r.path,
      fn: async (r) => { const dst = (dest ? dest + '/' : '') + r.name; await api.post(`/projects/${id}/move`, { src: r.path, dst }); moved.push([r.path, dst]) },
      onDone: () => { chk.clear(); const m = moved.find(([s]) => sel === s || sel?.startsWith(s + '/')); if (m) setSel(m[1] + sel.slice(m[0].length)); refresh() },
    })
  }
  const act = (what, path, type) => {
    if (what === 'download') download([path])
    else if (what === 'upload') pickFiles(path)
    else if (what === 'new-file') setModal({ type: 'new-file', base: path })
    else if (what === 'new-dir') setModal({ type: 'new-dir', base: path })
    else if (what === 'move') setModal({ type: 'move', src: path, kind: type })
    else if (what === 'delete') askDelete(path, type)
  }
  const canEdit = sel && !large && (kindOf(sel).id === 'env' ? perms.env : perms.edit)
  const protectedFile = sel && isCompose(sel) && !sel.includes('/')
  const toggle = (p) => setCollapsed((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n })
  const keyFiles = entries.filter((e) => e.type === 'file' && kindOf(e.path).key && e.path.split('/').length <= 3).sort((a, b) => keyRank(a.path) - keyRank(b.path) || a.path.localeCompare(b.path))
  if (tree.isLoading || !collapsed) return <Loading />
  if (tree.error) return <div className="banner err">{tree.error.message}</div>
  const k = sel ? kindOf(sel) : null
  return (
    <div className="pf">
      <div className={'card pf-tree ' + (pane ? '' : 'folded') + (over !== null ? ' drop-on' : '')}
        onDragOver={(e) => { if (perms.edit && [...(e.dataTransfer?.types || [])].includes('Files')) { e.preventDefault(); if (over !== cur) setOver(cur) } }} onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOver(null) }} onDrop={(e) => { if (perms.edit) { e.preventDefault(); onDropTo(e, cur) } }}>
        <input ref={fileIn} type="file" multiple hidden onChange={onPicked} />
        <input ref={dirIn} type="file" multiple hidden webkitdirectory="" directory="" onChange={onPicked} />
        <div className="row" style={{ gap: 4, marginBottom: 6 }}>
          <button type="button" className="pf-fold" onClick={() => setPane((v) => !v)} aria-expanded={pane}><b>Fichiers</b><span className="muted small">{entries.filter((e) => e.type === 'file').length}</span>{pane ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>
          <span className="grow" />
          {perms.edit && <><Btn small kind="ghost" title={`Importer des fichiers de votre ordinateur dans ${cur || 'la racine du projet'} (ou glissez-les ici)`} busy={!!up} onClick={() => pickFiles(cur)}><Upload size={15} /></Btn><Btn small kind="ghost" title={`Importer un dossier entier dans ${cur || 'la racine du projet'}`} disabled={!!up} onClick={() => { upBase.current = cur; dirIn.current?.click() }}><FolderUp size={15} /></Btn></>}
          {perms.edit && <><Btn small kind="ghost" title={`Nouveau fichier dans ${cur || 'la racine du projet'}`} onClick={() => setModal({ type: 'new-file', base: cur })}><FilePlus size={15} /></Btn><Btn small kind="ghost" title={`Nouveau dossier dans ${cur || 'la racine du projet'}`} onClick={() => setModal({ type: 'new-dir', base: cur })}><FolderPlus size={15} /></Btn></>}
          <Btn small kind="ghost" title="Replier tous les dossiers" onClick={() => setCollapsed(new Set(dirs))}><ChevronsDownUp size={15} /></Btn>
          <Btn small kind="ghost" title="Déplier tous les dossiers" onClick={() => setCollapsed(new Set())}><ChevronsUpDown size={15} /></Btn>
          <Btn small kind="ghost" title="Actualiser la liste" onClick={() => tree.refetch()}><RefreshCw size={14} /></Btn>
        </div>
        {up && <div className="pf-up" role="status"><div className="row" style={{ gap: 6 }}><Upload size={13} /><span className="ellipsis grow" title={up.name}>Import {up.n}/{up.total} · {up.name}</span></div><div className="pf-bar"><i style={{ width: Math.round(up.pct * 100) + '%' }} /></div></div>}
        {over !== null && !up && <div className="pf-up" role="status">Déposer dans : <b>{over || 'la racine du projet'}</b></div>}
        <BulkBar s={{ ...chk, picked, some: picked.length > 0 }} noun="élément">
          <Btn small busy={dl} onClick={() => download(picked.map((r) => r.path))}><Download size={14} /> Télécharger</Btn>
          {perms.edit && <Btn small disabled={bulk.busy} onClick={() => setModal({ type: 'move-many' })}><FolderInput size={14} /> Déplacer</Btn>}
          {perms.edit && <Btn small kind="danger" busy={bulk.busy} onClick={delMany}><Trash2 size={14} /> Supprimer</Btn>}
        </BulkBar>
        <div className="pf-body">
          {keyFiles.length > 0 && <div className="ft-keys">{keyFiles.map((e) => { const kk = kindOf(e.path); return <button key={e.path} type="button" className={'ft-chip ' + (sel === e.path ? 'on' : '')} title={e.path} onClick={() => load(e.path)}><FileMark path={e.path} size={13} /><PathLabel path={e.path} /><span className={'ft-tag fk-bg-' + kk.id}>{kk.label}</span></button> })}</div>}
          <Tree rows={rows} root={root} over={over} setOver={setOver} onDropTo={onDropTo} chk={chk} sel={sel} cur={cur} onPick={load} onDir={setCur} collapsed={collapsed} toggle={toggle} dirty={dirty} perms={perms} onAction={act} />
          {tree.data?.truncated && <div className="muted small" style={{ padding: 6 }}>Liste tronquée : seuls les 800 premiers éléments sont affichés (5 niveaux de dossiers au plus ; node_modules, vendor, build, dist et .git sont masqués).</div>}
        </div>
      </div>
      <div className="card pf-ed">
        {!sel ? <div className="muted">Choisissez un fichier{perms.edit ? ', ou créez-en un.' : '.'}</div> : <>
          <div className="row wrap" style={{ gap: 6, marginBottom: 8 }}>
            <FileMark path={sel} size={17} />
            <PathLabel path={sel} />
            <Badge className={'fk-bg-' + k.id} title={k.hint}>{k.label}</Badge>
            {isCompose(sel) && <><Btn title="Vérifie le fichier avec « docker compose config » sans rien démarrer" small onClick={validate}><CheckCircle2 size={13} /> Valider</Btn>{canTraefik && perms.files && <Btn title="Ajoute ou règle les labels Traefik d'un service (routeur, port, protections) sans écrire le YAML" small onClick={() => setModal({ type: 'labels' })}><Tags size={13} /> Labels Traefik</Btn>}</>}
            {!k.binary && <Btn title="Les versions précédentes de ce fichier : comparer, restaurer" small kind="ghost" onClick={() => setModal({ type: 'history' })}><History size={13} /> Historique</Btn>}
            <Btn small kind="ghost" title="Télécharger ce fichier" busy={dl} onClick={() => download([sel])}><Download size={13} /></Btn>
            {canEdit && !protectedFile && <Btn small kind="ghost" title="Renommer / déplacer" onClick={() => setModal({ type: 'move', src: sel, kind: 'file' })}><Pencil size={13} /></Btn>}
            {canEdit && !protectedFile && <Btn small kind="ghost" title="Supprimer" onClick={() => askDelete(sel, 'file')}><Trash2 size={13} /></Btn>}
            {canEdit && !k.binary && <Btn title="Voir les changements avant de les enregistrer" small kind="primary" disabled={!dirty} onClick={() => setModal({ type: 'diff' })}><Save size={13} /> Enregistrer</Btn>}
          </div>
          {big ? <div className="banner"><span className="grow">Fichier trop volumineux ({fmtSize(big.size)}) : l'afficher peut ralentir le navigateur. Il s'ouvrira en lecture seule.</span><Btn small onClick={() => load(big.rel, true)}>Charger quand même</Btn><Btn small kind="ghost" onClick={() => download([big.rel])}><Download size={13} /> Télécharger</Btn></div>
          : err ? <div className="banner err">{err}</div> : <>
            {check && <div className={'banner ' + (check.ok ? 'ok' : 'err')} style={{ whiteSpace: 'pre-wrap' }}>{check.ok ? <CheckCircle2 size={14} /> : null} {check.message}</div>}
            {large && <div className="muted small" style={{ marginBottom: 6 }}>Fichier volumineux ({fmtSize(text.length)}) : ouvert en lecture seule.</div>}
            {!canEdit && !large && <div className="muted small" style={{ marginBottom: 6 }}>Lecture seule : vous n'avez pas le droit de modifier ce fichier.</div>}
            <div className="editor-wrap pf-editor"><CodeEditor key={sel} language={langOf(sel)} readOnly={!canEdit} value={text} onChange={setText} onSave={() => dirty && canEdit && setModal({ type: 'diff' })} /></div></>}
        </>}
      </div>
      {modal?.type === 'diff' && <Modal wide title={`Enregistrer ${sel}`} onClose={() => setModal(null)} footer={<><Btn onClick={() => setModal(null)}>Annuler</Btn><Btn kind="primary" busy={save.isPending} onClick={() => save.mutate({})}>Enregistrer</Btn></>}>
        <div className="col"><div className="muted small">Voici ce qui change. Le fichier est écrit sur le disque ; la version actuelle reste dans l'historique. Pour l'appliquer aux conteneurs, utilisez ensuite « Déployer ».</div><Diff original={orig} modified={text} language={langOf(sel)} /></div></Modal>}
      {modal?.type === 'new-file' && <PathModal title="Nouveau fichier" label="Chemin du fichier" initial={modal.base ? modal.base + '/' : ''} dirs={dirs} busy={save.isPending} confirmLabel="Créer" hint="Créé vide, dans le dossier du projet (jamais en dehors). Pour un dossier : « conf/nginx/site.conf » crée aussi les dossiers manquants." onClose={() => setModal(null)} onSubmit={(rel) => save.mutate({ create: true, rel, content: '' })} />}
      {modal?.type === 'new-dir' && <PathModal title="Nouveau dossier" label="Chemin du dossier" initial={modal.base ? modal.base + '/' : ''} dirs={dirs} busy={mkdir.isPending} confirmLabel="Créer" onClose={() => setModal(null)} onSubmit={(rel) => mkdir.mutate(rel)} />}
      {modal?.type === 'move' && <PathModal title={`Renommer / déplacer ${modal.kind === 'dir' ? 'le dossier' : 'le fichier'}`} label="Nouveau chemin" initial={modal.src} dirs={dirs.filter((d) => d !== modal.src && !d.startsWith(modal.src + '/'))} busy={move.isPending} confirmLabel="Appliquer" hint="Changez le nom pour renommer, le dossier pour déplacer. Les dossiers manquants sont créés." same={modal.src} onClose={() => setModal(null)} onSubmit={(dst) => move.mutate({ src: modal.src, dst })} />}
      {modal?.type === 'move-many' && <MoveMany count={picked.length} dirs={dirs.filter((d) => !picked.some((r) => r.type === 'dir' && (d === r.path || d.startsWith(r.path + '/'))))} onClose={() => setModal(null)} onSubmit={moveMany} />}
      {modal?.type === 'history' && <HistoryModal id={id} rel={sel} current={text} canEdit={canEdit} language={langOf(sel)} onClose={() => setModal(null)} onRestore={(c) => { setText(c); setModal(null); toast('Version chargée dans l\'éditeur : relisez puis enregistrez') }} />}
      {modal?.type === 'labels' && <LabelsModal id={id} onClose={() => setModal(null)} />}
    </div>
  )
}

function MoveMany({ count, dirs, onClose, onSubmit }) {
  const [dest, setDest] = useState('')
  return <Modal small title={`Déplacer ${count} élément${count > 1 ? 's' : ''}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" onClick={() => onSubmit(dest)}>Déplacer ici</Btn></>}>
    <div className="col">
      <label>Dossier de destination<select className="input mono" autoFocus value={dest} onChange={(e) => setDest(e.target.value)}><option value="">/ (racine du projet)</option>{dirs.map((d) => <option key={d} value={d}>{d}</option>)}</select></label>
      <div className="muted small">Les noms sont conservés. Un élément qui existe déjà à destination n'est pas écrasé : il est signalé à la fin.</div>
    </div></Modal>
}

export function PathModal({ title, label, initial, dirs, hint, confirmLabel, busy, same, onClose, onSubmit }) {
  const [rel, setRel] = useState(initial); const r = rel.trim(); const ok = validRel(r) && r !== same
  return <Modal small title={title} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={!ok} onClick={() => onSubmit(r)}>{confirmLabel}</Btn></>}>
    <form className="col" onSubmit={(e) => { e.preventDefault(); if (ok) onSubmit(r) }}>
      <label>{label}<input className="input mono" autoFocus value={rel} onChange={(e) => setRel(e.target.value)} onFocus={(e) => { const n = e.target.value.length; e.target.setSelectionRange(n, n) }} list="pf-dirs" autoCapitalize="none" autoCorrect="off" spellCheck={false} /></label>
      <datalist id="pf-dirs">{dirs.map((d) => <option key={d} value={d + '/'} />)}</datalist>
      {r && !validRel(r) && <div className="muted small" style={{ color: 'var(--err)' }}>Chemin invalide : pas de « .. », pas de « / » au début ou à la fin, pas de dossier « .git ».</div>}
      {hint && <div className="muted small">{hint}</div>}
    </form></Modal>
}

function HistoryModal({ id, rel, current, canEdit, language, onClose, onRestore }) {
  const q = useQuery({ queryKey: ['project-hist', id, rel], queryFn: () => api.get(`/projects/${id}/history?rel=${enc(rel)}`) })
  const [pick, setPick] = useState(null); const [old, setOld] = useState('')
  const open = async (v) => { setPick(v); setOld((await api.get(`/projects/${id}/history/${v.vid}?rel=${enc(rel)}`)).content) }
  return (
    <Modal wide title={`Historique · ${rel}`} onClose={onClose} footer={<>{pick && canEdit && <Btn kind="primary" onClick={() => onRestore(old)}>Charger cette version dans l'éditeur</Btn>}<Btn onClick={onClose}>Fermer</Btn></>}>
      {q.isLoading ? <Loading /> : <div className="pf-hist"><div className="col" style={{ gap: 4 }}>{(q.data.versions.length ? q.data.versions : []).map((v) => <button key={v.vid} type="button" className={'ft-row ' + (pick?.vid === v.vid ? 'on' : '')} onClick={() => open(v)}>{fmtDate(v.time * 1000)} <span className="muted small" style={{ marginLeft: 'auto' }}>{fmtSize(v.size)}</span></button>)}{!q.data.versions.length && <div className="muted">Aucune version précédente : elles sont gardées à chaque enregistrement (30 au plus).</div>}</div>
        <div>{pick ? <Diff original={old} modified={current} language={language} /> : <div className="muted">Choisissez une version pour la comparer avec l'éditeur.</div>}</div></div>}
    </Modal>
  )
}
