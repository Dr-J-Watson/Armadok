import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown, Ellipsis, FilePlus, Folder, FolderOpen, FolderPlus, History, Pencil, RefreshCw, Save, Tags, Trash2 } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Loading, Modal, useUi } from './ui.jsx'
import CodeEditor, { Diff } from './CodeEditor.jsx'
import LabelsModal from './LabelsModal.jsx'
import { baseName, isCompose, kindOf, keyRank, langOf } from '../lib/files.js'
import { fmtSize } from '../lib/docker.js'
import { fmtDate } from '../lib/util.js'

const enc = encodeURIComponent
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

function Tree({ entries, sel, cur, onPick, onDir, collapsed, toggle, dirty, perms, onAction }) {
  const rows = useMemo(() => buildRows(entries, collapsed), [entries, collapsed])
  const [menu, setMenu] = useState(null)
  useEffect(() => { if (!menu) return; const close = () => setMenu(null); window.addEventListener('click', close); window.addEventListener('keydown', close); return () => { window.removeEventListener('click', close); window.removeEventListener('keydown', close) } }, [menu])
  return (
    <div className="ft" role="tree">
      {rows.map((e) => {
        const dir = e.type === 'dir'; const on = dir ? cur === e.path && !sel : sel === e.path; const k = dir ? null : kindOf(e.path)
        return (
          <div key={e.path} className={'ft-item ' + (on ? 'on' : '') + (k?.key ? ' key' : '')} style={{ paddingLeft: 4 + e.depth * 14 }} role="treeitem" aria-selected={on}>
            <button type="button" className="ft-main" onClick={() => (dir ? (toggle(e.path), onDir(e.path)) : onPick(e.path))} title={k?.hint || e.path}>
              {dir ? (collapsed.has(e.path) ? <ChevronRight size={14} className="muted" /> : <ChevronDown size={14} className="muted" />) : <span className="ft-sp" />}
              {dir ? (collapsed.has(e.path) ? <Folder size={15} className="fk fk-dir" /> : <FolderOpen size={15} className="fk fk-dir" />) : <FileMark path={e.path} />}
              <span className="ellipsis">{e.name}</span>
              {k?.key && <span className={'ft-tag fk-bg-' + k.id}>{k.label}</span>}
              {dirty && sel === e.path && <i className="dot warn" title="Modifié, pas encore enregistré" />}
            </button>
            {perms.edit && <button type="button" className="ft-more" aria-label={`Actions sur ${e.name}`} onClick={(ev) => { ev.stopPropagation(); setMenu(menu === e.path ? null : e.path) }}><Ellipsis size={15} /></button>}
            {menu === e.path && (
              <div className="ft-menu" onClick={(ev) => ev.stopPropagation()}>
                {dir && <button type="button" onClick={() => { setMenu(null); onAction('new-file', e.path) }}><FilePlus size={14} /> Nouveau fichier ici</button>}
                {dir && <button type="button" onClick={() => { setMenu(null); onAction('new-dir', e.path) }}><FolderPlus size={14} /> Nouveau dossier ici</button>}
                <button type="button" onClick={() => { setMenu(null); onAction('move', e.path, e.type) }}><Pencil size={14} /> Renommer / déplacer</button>
                <button type="button" className="danger" onClick={() => { setMenu(null); onAction('delete', e.path, e.type) }}><Trash2 size={14} /> Supprimer</button>
              </div>
            )}
          </div>
        )
      })}
      {!rows.length && <div className="muted small" style={{ padding: 8 }}>Dossier vide.</div>}
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
  const [modal, setModal] = useState(null); const [check, setCheck] = useState(null); const [collapsed, setCollapsed] = useState(null); const [pane, setPane] = useState(true)
  const entries = tree.data?.entries || []
  const dirs = useMemo(() => entries.filter((e) => e.type === 'dir').map((e) => e.path), [entries])
  const dirty = sel != null && text !== orig
  const seeded = useRef(false)
  // premier affichage : les dossiers profonds (2 niveaux et plus) sont repliés
  useEffect(() => { if (tree.data && !seeded.current) { seeded.current = true; setCollapsed(new Set(dirs.filter((d) => d.split('/').length >= 2))) } }, [tree.data]) // eslint-disable-line react-hooks/exhaustive-deps
  const narrow = () => window.matchMedia?.('(max-width: 900px)').matches
  const load = async (rel) => {
    if (rel === sel) { if (narrow()) setPane(false); return }
    if (dirty && !(await confirm({ title: 'Modifications non enregistrées', message: 'Elles seront perdues si vous ouvrez un autre fichier.', confirmLabel: 'Abandonner' }))) return
    setErr(''); setCheck(null); setSel(rel); setCur(parentOf(rel)); if (narrow()) setPane(false)
    if (kindOf(rel).binary) { setText(''); setOrig(''); setErr('Ce type de fichier ne s\'affiche pas dans l\'éditeur (binaire). Vous pouvez le renommer, le déplacer ou le supprimer.'); return }
    try { const r = await api.get(`/projects/${id}/file?rel=${enc(rel)}`); setText(r.content); setOrig(r.content); setHash(r.hash) } catch (e) { setErr(e.message); setText(''); setOrig('') }
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
    onSuccess: (r, v) => { toast(v.type === 'dir' ? 'Dossier supprimé (les fichiers texte restent dans l\'historique)' : 'Fichier supprimé (une copie est gardée dans l\'historique)'); if (sel === v.rel || sel?.startsWith(v.rel + '/')) { setSel(null); setText(''); setOrig(''); setErr('') } refresh() },
    onError: (e) => toast(e.message, 'err'),
  })
  const validate = async () => { try { setCheck(await api.post(`/projects/${id}/validate`, { content: text })) } catch (e) { toast(e.message, 'err') } }
  const askDelete = async (rel, type) => { if (await confirm({ title: `Supprimer ${type === 'dir' ? 'le dossier' : 'le fichier'} « ${rel} » ?`, message: type === 'dir' ? 'Le dossier et tout son contenu seront supprimés. Les fichiers texte restent dans l\'historique ; les autres sont perdus.' : 'Une copie reste dans l\'historique.', confirmLabel: 'Supprimer' })) del.mutate({ rel, type }) }
  const act = (what, path, type) => {
    if (what === 'new-file') setModal({ type: 'new-file', base: path })
    else if (what === 'new-dir') setModal({ type: 'new-dir', base: path })
    else if (what === 'move') setModal({ type: 'move', src: path, kind: type })
    else if (what === 'delete') askDelete(path, type)
  }
  const canEdit = sel && (kindOf(sel).id === 'env' ? perms.env : perms.edit)
  const protectedFile = sel && isCompose(sel) && !sel.includes('/')
  const toggle = (p) => setCollapsed((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n })
  const keyFiles = entries.filter((e) => e.type === 'file' && kindOf(e.path).key && e.path.split('/').length <= 3).sort((a, b) => keyRank(a.path) - keyRank(b.path) || a.path.localeCompare(b.path))
  if (tree.isLoading || !collapsed) return <Loading />
  if (tree.error) return <div className="banner err">{tree.error.message}</div>
  const k = sel ? kindOf(sel) : null
  return (
    <div className="pf">
      <div className={'card pf-tree ' + (pane ? '' : 'folded')}>
        <div className="row" style={{ gap: 4, marginBottom: 6 }}>
          <button type="button" className="pf-fold" onClick={() => setPane((v) => !v)} aria-expanded={pane}><b>Fichiers</b><span className="muted small">{entries.filter((e) => e.type === 'file').length}</span>{pane ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>
          <span className="grow" />
          {perms.edit && <><Btn small kind="ghost" title={`Nouveau fichier dans ${cur || 'la racine du projet'}`} onClick={() => setModal({ type: 'new-file', base: cur })}><FilePlus size={15} /></Btn><Btn small kind="ghost" title={`Nouveau dossier dans ${cur || 'la racine du projet'}`} onClick={() => setModal({ type: 'new-dir', base: cur })}><FolderPlus size={15} /></Btn></>}
          <Btn small kind="ghost" title="Replier tous les dossiers" onClick={() => setCollapsed(new Set(dirs))}><ChevronsDownUp size={15} /></Btn>
          <Btn small kind="ghost" title="Déplier tous les dossiers" onClick={() => setCollapsed(new Set())}><ChevronsUpDown size={15} /></Btn>
          <Btn small kind="ghost" title="Actualiser la liste" onClick={() => tree.refetch()}><RefreshCw size={14} /></Btn>
        </div>
        <div className="pf-body">
          {keyFiles.length > 0 && <div className="ft-keys">{keyFiles.map((e) => { const kk = kindOf(e.path); return <button key={e.path} type="button" className={'ft-chip ' + (sel === e.path ? 'on' : '')} title={e.path} onClick={() => load(e.path)}><FileMark path={e.path} size={13} /><PathLabel path={e.path} /><span className={'ft-tag fk-bg-' + kk.id}>{kk.label}</span></button> })}</div>}
          <Tree entries={entries} sel={sel} cur={cur} onPick={load} onDir={setCur} collapsed={collapsed} toggle={toggle} dirty={dirty} perms={perms} onAction={act} />
          {tree.data?.truncated && <div className="muted small">Liste tronquée.</div>}
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
            {canEdit && !protectedFile && <Btn small kind="ghost" title="Renommer / déplacer" onClick={() => setModal({ type: 'move', src: sel, kind: 'file' })}><Pencil size={13} /></Btn>}
            {canEdit && !protectedFile && <Btn small kind="ghost" title="Supprimer" onClick={() => askDelete(sel, 'file')}><Trash2 size={13} /></Btn>}
            {canEdit && !k.binary && <Btn title="Voir les changements avant de les enregistrer" small kind="primary" disabled={!dirty} onClick={() => setModal({ type: 'diff' })}><Save size={13} /> Enregistrer</Btn>}
          </div>
          {err ? <div className="banner err">{err}</div> : <>
            {check && <div className={'banner ' + (check.ok ? 'ok' : 'err')} style={{ whiteSpace: 'pre-wrap' }}>{check.ok ? <CheckCircle2 size={14} /> : null} {check.message}</div>}
            {!canEdit && <div className="muted small" style={{ marginBottom: 6 }}>Lecture seule : vous n'avez pas le droit de modifier ce fichier.</div>}
            <div className="editor-wrap pf-editor"><CodeEditor key={sel} language={langOf(sel)} readOnly={!canEdit} value={text} onChange={setText} onSave={() => dirty && canEdit && setModal({ type: 'diff' })} /></div></>}
        </>}
      </div>
      {modal?.type === 'diff' && <Modal wide title={`Enregistrer ${sel}`} onClose={() => setModal(null)} footer={<><Btn onClick={() => setModal(null)}>Annuler</Btn><Btn kind="primary" busy={save.isPending} onClick={() => save.mutate({})}>Enregistrer</Btn></>}>
        <div className="col"><div className="muted small">Voici ce qui change. Le fichier est écrit sur le disque ; la version actuelle reste dans l'historique. Pour l'appliquer aux conteneurs, utilisez ensuite « Déployer ».</div><Diff original={orig} modified={text} language={langOf(sel)} /></div></Modal>}
      {modal?.type === 'new-file' && <PathModal title="Nouveau fichier" label="Chemin du fichier" initial={modal.base ? modal.base + '/' : ''} dirs={dirs} busy={save.isPending} confirmLabel="Créer" hint="Créé vide, dans le dossier du projet (jamais en dehors). Pour un dossier : « conf/nginx/site.conf » crée aussi les dossiers manquants." onClose={() => setModal(null)} onSubmit={(rel) => save.mutate({ create: true, rel, content: '' })} />}
      {modal?.type === 'new-dir' && <PathModal title="Nouveau dossier" label="Chemin du dossier" initial={modal.base ? modal.base + '/' : ''} dirs={dirs} busy={mkdir.isPending} confirmLabel="Créer" onClose={() => setModal(null)} onSubmit={(rel) => mkdir.mutate(rel)} />}
      {modal?.type === 'move' && <PathModal title={`Renommer / déplacer ${modal.kind === 'dir' ? 'le dossier' : 'le fichier'}`} label="Nouveau chemin" initial={modal.src} dirs={dirs.filter((d) => d !== modal.src && !d.startsWith(modal.src + '/'))} busy={move.isPending} confirmLabel="Appliquer" hint="Changez le nom pour renommer, le dossier pour déplacer. Les dossiers manquants sont créés." same={modal.src} onClose={() => setModal(null)} onSubmit={(dst) => move.mutate({ src: modal.src, dst })} />}
      {modal?.type === 'history' && <HistoryModal id={id} rel={sel} current={text} canEdit={canEdit} language={langOf(sel)} onClose={() => setModal(null)} onRestore={(c) => { setText(c); setModal(null); toast('Version chargée dans l\'éditeur : relisez puis enregistrez') }} />}
      {modal?.type === 'labels' && <LabelsModal id={id} onClose={() => setModal(null)} />}
    </div>
  )
}

function PathModal({ title, label, initial, dirs, hint, confirmLabel, busy, same, onClose, onSubmit }) {
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
