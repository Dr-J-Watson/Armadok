import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, FilePlus2, FileText, Folder, FolderOpen, FolderPlus, History, Pencil, Power, PowerOff, Save, Search, Trash2, Undo2, FolderInput, Sparkles, Wand2, Download, Upload } from 'lucide-react'
import { api, qs } from '../api.js'
import { byDay, timeLabel } from '../lib/activity.js'
import { baseName, buildTree, countFiles, dirOf, joinPath, MAX_DEPTH, SUGGESTED_DIRS, sorted, withExt } from '../lib/tree.js'
import { fmtSize, relTime } from '../lib/util.js'
import { downloadBlob } from '../lib/csv.js'
import { useNode } from '../node.jsx'
import { onLive } from '../lib/live.js'
import { Badge, Btn, Field, Modal, useUi } from '../components/ui.jsx'
import Reorganize from '../components/Reorganize.jsx'
import { useToggler } from '../components/Toggle.jsx'
import { Info } from '../components/Tip.jsx'
import CodeEditor, { Diff } from '../components/CodeEditor.jsx'

const MAX_UPLOAD = 1_000_000
const NEW_FILE = '# Nouveau fichier\nhttp:\n  routers: {}\n'
const store = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback } catch { return fallback } }
const keep = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* */ } }

export default function Files() {
  const qc = useQueryClient(); const { toast, confirm } = useUi(); const { node, can } = useNode()
  const list = useQuery({ queryKey: ['files'], queryFn: () => api.get('/traefik/overview').then((o) => ({ files: o.files, dirs: o.dirs || [] })) })
  const files = list.data?.files || [], dirs = list.data?.dirs || []
  const [path, setPath] = useState(null)
  const [orig, setOrig] = useState(''); const [text, setText] = useState('')
  const [markers, setMarkers] = useState([]); const [modal, setModal] = useState(null)
  const [q, setQ] = useState('')
  const [collapsed, setCollapsed] = useState(() => new Set(store(`tree-collapsed:${node?.id}`, [])))
  const [hideTip, setHideTip] = useState(() => store(`tree-tip:${node?.id}`, false))
  const [dropOn, setDropOn] = useState(null)
  const [stale, setStale] = useState(false)   // le fichier ouvert a été modifié ailleurs pendant que j'ai des changements non enregistrés
  const latest = useRef({})
  const dirty = path && text !== orig
  const { toggle: setEnabled, busy: toggling } = useToggler()
  const off = files.find((f) => f.path === path)?.disabled
  latest.current = { path, text, orig, dirty }
  const seq = useRef(0)
  const picker = useRef(null)

  const refresh = () => { qc.invalidateQueries({ queryKey: ['files'] }); qc.invalidateQueries({ queryKey: ['overview'] }); qc.invalidateQueries({ queryKey: ['meta'] }) }
  const toggle = (d) => setCollapsed((c) => { const n = new Set(c); n.has(d) ? n.delete(d) : n.add(d); keep(`tree-collapsed:${node?.id}`, [...n]); return n })

  const load = async (p, force = false) => {
    if (dirty && !force && !(await confirm({ title: 'Modifications non enregistrées', message: 'Abandonner les changements en cours ?', confirmLabel: 'Abandonner' }))) return
    try { const r = await api.get('/traefik/file?' + qs({ path: p })); setPath(p); setOrig(r.content); setText(r.content); setMarkers([]); setStale(false) } catch (e) { toast(e.message, 'err') }
  }
  useEffect(() => { if (!path && files.length) load(files[0].path) }, [list.data])

  useEffect(() => {
    if (!path) return
    const id = ++seq.current
    const t = setTimeout(async () => {
      const r = await api.post('/traefik/validate', { content: text }).catch(() => null)
      if (id === seq.current && r) setMarkers(r.ok ? [] : [{ line: r.line || 1, message: r.message }])
    }, 400)
    return () => clearTimeout(t)
  }, [text, path])

  useEffect(() => { const h = (e) => { if (dirty) e.preventDefault() }; window.addEventListener('beforeunload', h); return () => window.removeEventListener('beforeunload', h) }, [dirty])

  // Temps réel : si le fichier ouvert change sur le disque (autre utilisateur, édition à la main), on le recharge, ou on prévient s'il y a des changements en cours.
  useEffect(() => onLive(async (topics) => {
    if (!topics.some((t) => t.t === 'traefik' || t.t === 'all')) return
    const cur = latest.current
    if (!cur.path) return
    try {
      const r = await api.get('/traefik/file?' + qs({ path: cur.path }))
      const now = latest.current
      if (now.path !== cur.path || r.content === now.orig) return
      if (r.content === now.text) { setOrig(r.content); setStale(false); return }      // notre propre enregistrement
      if (!now.dirty) { setOrig(r.content); setText(r.content); toast('Fichier mis à jour : il a été modifié ailleurs') } else setStale(true)
    } catch (e) {
      if (e.status === 404 && !latest.current.dirty) { setPath(null); setOrig(''); setText(''); toast('Ce fichier a été déplacé ou supprimé ailleurs') }
    }
  }), []) // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: async () => {
      if (stale && !(await confirm({ title: 'Écraser la version modifiée ailleurs ?', message: 'Ce fichier a changé sur le disque pendant que vous l\'éditiez. Enregistrer remplace cette version par la vôtre (l\'ancienne reste dans l\'historique).', confirmLabel: 'Écraser' }))) throw new Error('Enregistrement annulé')
      return api.put('/traefik/file', { path, content: text })
    },
    onSuccess: () => { setOrig(text); setStale(false); setModal(null); refresh(); toast('Fichier enregistré') },
    onError: (e) => toast(e.message, 'err'),
  })

  const needle = q.trim().toLowerCase()
  const tree = useMemo(() => buildTree(needle ? files.filter((f) => f.path.toLowerCase().includes(needle)) : files, needle ? [] : dirs), [list.data, needle])
  const allDirs = useMemo(() => ['', ...dirs], [list.data])
  const flat = !dirs.length && files.length >= 4 && !hideTip

  const switchFile = async () => {
    if (dirty && !(await confirm({ title: 'Modifications non enregistrées', message: 'Abandonner les changements en cours ?', confirmLabel: 'Abandonner' }))) return
    const res = await setEnabled({ files: [path], enable: !!off, label: `le fichier ${baseName(path)}` })
    if (!res) return
    const to = res.files[0].to; refresh()
    try { const r = await api.get('/traefik/file?' + qs({ path: to })); setPath(to); setOrig(r.content); setText(r.content); setMarkers([]) } catch (e) { toast(e.message, 'err') }
  }

  const remove = async () => {
    if (!(await confirm({ title: `Supprimer ${path} ?`, message: 'Les routeurs/services du fichier disparaîtront de Traefik. Une sauvegarde est conservée dans l\'historique.', confirmLabel: 'Supprimer' }))) return
    try { await api.del('/traefik/file?' + qs({ path })); setPath(null); setOrig(''); setText(''); refresh(); toast('Fichier supprimé') } catch (e) { toast(e.message, 'err') }
  }

  const moveFile = async (src, dst) => {
    if (src === dst) return
    try {
      await api.post('/traefik/file/move', { src, dst }); refresh(); toast(`Déplacé vers ${dirOf(dst) || 'la racine'}`)
      if (src === path) setPath(dst)
    } catch (e) { toast(e.message, 'err') }
  }

  const removeDir = async (d) => {
    if (!(await confirm({ title: `Supprimer le dossier ${d} ?`, message: 'Seul un dossier vide peut être supprimé : aucun fichier n\'est touché.', confirmLabel: 'Supprimer' }))) return
    try { await api.del('/traefik/dir?' + qs({ path: d })); refresh(); toast('Dossier supprimé') } catch (e) { toast(e.message, 'err') }
  }

  const suggest = async () => {
    try { for (const d of SUGGESTED_DIRS.filter((s) => !dirs.includes(s.name))) await api.post('/traefik/dir', { path: d.name }); refresh(); toast('Dossiers créés — glissez vos fichiers dedans') } catch (e) { toast(e.message, 'err') }
  }

  // Import : fichiers .yml/.yaml choisis ou déposés depuis l'ordinateur. Le serveur valide le YAML ; un fichier remplacé garde sa sauvegarde.
  const importFiles = async (list, dir) => {
    const all = [...list]
    const yml = all.filter((f) => /\.ya?ml$/i.test(f.name))
    const big = yml.filter((f) => f.size > MAX_UPLOAD)
    let items = []
    for (const f of yml.filter((f) => f.size <= MAX_UPLOAD)) items.push({ path: joinPath(dir, f.name.replace(/\s/g, '-')), content: await f.text() })
    if (all.length - yml.length) toast(`${all.length - yml.length} fichier(s) ignoré(s) : seuls .yml et .yaml sont acceptés`, 'err')
    if (big.length) toast(`${big.map((f) => f.name).join(', ')} : trop gros (1 Mo maximum)`, 'err')
    const clash = items.filter((i) => files.some((f) => f.path === i.path))
    if (clash.length && !(await confirm({ title: 'Remplacer des fichiers existants ?', message: `${clash.map((c) => c.path).join(', ')} existe déjà. Une sauvegarde de l'ancienne version reste dans l'historique. « Annuler » importe seulement les autres fichiers.`, confirmLabel: 'Remplacer' }))) items = items.filter((i) => !clash.includes(i))
    const done = []
    for (const i of items) {
      try { await api.put('/traefik/file', { path: i.path, content: i.content, create: !files.some((f) => f.path === i.path) }); done.push(i.path) }
      catch (e) { toast(`${i.path} : ${e.message}`, 'err') }
    }
    if (done.length) { refresh(); toast(`${done.length} fichier${done.length > 1 ? 's' : ''} importé${done.length > 1 ? 's' : ''}`); if (!dirty) load(done[0]) }
  }

  const download = () => downloadBlob(baseName(path).replace(/\.off$/, ''), text, 'text/yaml;charset=utf-8')

  const drop = (e, dir) => {
    e.preventDefault(); e.stopPropagation(); setDropOn(null)
    if (e.dataTransfer.files?.length) return importFiles(e.dataTransfer.files, dir)
    const src = e.dataTransfer.getData('text/x-traefik-file')
    if (src && dirOf(src) !== dir) moveFile(src, joinPath(dir, baseName(src)))
  }
  const dropProps = (dir) => ({
    onDragOver: (e) => { if (['text/x-traefik-file', 'Files'].some((t) => e.dataTransfer.types.includes(t))) { e.preventDefault(); e.stopPropagation(); setDropOn(dir) } },
    onDragLeave: () => setDropOn((d) => (d === dir ? null : d)),
    onDrop: (e) => drop(e, dir),
  })

  const branch = (n, depth) => {
    const { dirs: ds, files: fs } = sorted(n)
    return (
      <>
        {ds.map((d) => {
          const shut = !needle && collapsed.has(d.path)
          return (
            <div key={d.path}>
              <div className={'dir-row ' + (dropOn === d.path ? 'drop' : '')} style={{ paddingLeft: 6 + depth * 14 }} onClick={() => toggle(d.path)} {...dropProps(d.path)}>
                {shut ? <ChevronRight size={13} /> : <ChevronDown size={13} />}{shut ? <Folder size={14} /> : <FolderOpen size={14} />}
                <span className="grow ellipsis">{d.name}</span><span className="count">{countFiles(d)}</span>
                <span className="acts" onClick={(e) => e.stopPropagation()}>
                  <button title="Nouveau fichier ici" onClick={() => setModal({ type: 'new', dir: d.path })}><FilePlus2 size={13} /></button>
                  {d.path.split('/').length < MAX_DEPTH && <button title="Nouveau sous-dossier" onClick={() => setModal({ type: 'folder', parent: d.path })}><FolderPlus size={13} /></button>}
                  <button title="Renommer / déplacer" onClick={() => setModal({ type: 'folder', rename: d.path })}><Pencil size={13} /></button>
                  {countFiles(d) === 0 && <button title="Supprimer le dossier" onClick={() => removeDir(d.path)}><Trash2 size={13} /></button>}
                </span>
              </div>
              {!shut && branch(d, depth + 1)}
            </div>
          )
        })}
        {fs.map((f) => (
          <div key={f.path} className={'node ' + (f.path === path ? 'active' : '') + (f.disabled ? ' off' : '')} style={{ paddingLeft: 24 + depth * 14 }} draggable
            onDragStart={(e) => { e.dataTransfer.setData('text/x-traefik-file', f.path); e.dataTransfer.effectAllowed = 'move' }} onClick={() => load(f.path)} {...dropProps(dirOf(f.path))}>
            {f.disabled ? <PowerOff size={14} /> : <FileText size={14} />}<span className="ellipsis" title={f.disabled ? 'Fichier désactivé : Traefik ne le lit pas' : undefined}>{baseName(f.path)}</span>
          </div>
        ))}
      </>
    )
  }

  return (
    <div className="file-layout">
      <div className={'card flush tree ' + (dropOn === '' ? 'drop' : '')} {...dropProps('')}>
        <div className="row" style={{ padding: '4px 8px 8px' }}>
          <h3>Fichiers</h3><Info>{'Les fichiers YAML que Traefik lit.\nGlissez un fichier sur un dossier pour le ranger, ou déposez-y des .yml de votre ordinateur pour les importer. La baguette propose un rangement automatique. Un fichier désactivé n\'est plus lu mais reste là.'}</Info><span className="grow" />
          {files.length > 1 && can('files.reorganize') && <Btn small icon kind="ghost" title="Ranger mes fichiers (assistant)" onClick={() => setModal({ type: 'reorg' })}><Wand2 size={15} /></Btn>}
          {can('files.create') && <Btn small icon kind="ghost" title="Nouveau dossier (facultatif : sert seulement à ranger)" onClick={() => setModal({ type: 'folder', parent: '' })}><FolderPlus size={15} /></Btn>}
          {can('files.create') && <Btn small icon kind="ghost" title="Importer des fichiers .yml (ou glissez-les ici depuis votre ordinateur)" onClick={() => picker.current?.click()}><Upload size={15} /></Btn>}
          <input ref={picker} type="file" accept=".yml,.yaml" multiple hidden onChange={(e) => { importFiles(e.target.files, dirOf(path || '')); e.target.value = '' }} />
          {can('files.create') && <Btn small icon kind="ghost" title="Nouveau fichier" onClick={() => setModal({ type: 'new', dir: dirOf(path || '') })}><FilePlus2 size={15} /></Btn>}
        </div>
        {files.length > 6 && <div className="tree-search"><Search size={13} /><input placeholder="Rechercher un fichier…" value={q} onChange={(e) => setQ(e.target.value)} /></div>}
        {flat && (
          <div className="tip">
            <Sparkles size={14} />
            <div className="grow"><b>Rangez vos fichiers (facultatif)</b><div className="muted small">Des dossiers rendent la liste plus lisible. Rien ne change pour Traefik.</div>
              <div className="row" style={{ marginTop: 6 }}><Btn small kind="primary" onClick={() => setModal({ type: 'reorg' })}>Ranger pour moi</Btn><Btn small onClick={suggest}>Créer seulement les dossiers</Btn><Btn small kind="ghost" onClick={() => { setHideTip(true); keep(`tree-tip:${node?.id}`, true) }}>Non merci</Btn></div></div>
          </div>
        )}
        {branch(tree, 0)}
        {needle && !countFiles(tree) && <div className="empty small">Aucun fichier ne correspond.</div>}
      </div>
      <div className="col" style={{ minHeight: 0 }}>
        {path ? (
          <>
            <div className="row wrap">
              <code><b>{path}</b></code>{off && <Badge kind="warn">désactivé</Badge>}{dirty && <Badge kind="warn">modifié</Badge>}{markers.length > 0 && <Badge kind="err">YAML invalide</Badge>}
              <div className="row right">
                {can('files.toggle') && <Btn small busy={toggling} title={off ? 'Traefik relira ce fichier' : 'Traefik ne lira plus ce fichier (réversible)'} onClick={switchFile}>{off ? <><Power size={14} /> Réactiver</> : <><PowerOff size={14} /> Désactiver</>}</Btn>}
                <Btn small title="Télécharger ce fichier" onClick={download}><Download size={14} /> Télécharger</Btn>
                {can('history.view') && <Btn small onClick={() => setModal({ type: 'history' })}><History size={14} /> Historique</Btn>}
                {can('files.move') && <Btn small onClick={() => setModal({ type: 'move' })}><FolderInput size={14} /> Déplacer</Btn>}
                {can('files.delete') && <Btn small kind="danger" onClick={remove}><Trash2 size={14} /></Btn>}
                {can('files.edit') && <Btn small disabled={!dirty} onClick={() => setText(orig)}><Undo2 size={14} /> Annuler</Btn>}
                {can('files.edit') && <Btn small kind="primary" disabled={!dirty || markers.length > 0} onClick={() => setModal({ type: 'diff' })}><Save size={14} /> Enregistrer</Btn>}
              </div>
            </div>
            {stale && <div className="banner" style={{ margin: 0 }}><History size={15} /><span className="grow">Ce fichier a été modifié ailleurs pendant que vous l'éditiez.</span><Btn small onClick={() => load(path, true)}>Recharger (abandonner mes changements)</Btn></div>}
            <div className="editor-wrap grow"><CodeEditor readOnly={!can('files.edit')} value={text} onChange={setText} markers={markers} onSave={() => dirty && !markers.length && setModal({ type: 'diff' })} /></div>
          </>
        ) : <div className="card"><div className="empty">Sélectionnez un fichier.</div></div>}
      </div>

      {modal?.type === 'diff' && (
        <Modal wide title="Vérifier les changements" onClose={() => setModal(null)} footer={<><Btn onClick={() => setModal(null)}>Retour</Btn><Btn kind="primary" busy={save.isPending} onClick={() => save.mutate()}>Écrire {path}</Btn></>}>
          <Diff original={orig} modified={text} />
        </Modal>
      )}
      {modal?.type === 'reorg' && <Reorganize dirs={dirs} onClose={() => setModal(null)} onDone={() => { refresh(); setModal(null); setPath(null) }} />}
      {modal?.type === 'new' && <NewFile dirs={allDirs} dir={modal.dir} onClose={() => setModal(null)} onCreated={(p) => { refresh(); setModal(null); load(p) }} />}
      {modal?.type === 'move' && <MoveFile path={path} dirs={allDirs} onClose={() => setModal(null)} onMoved={(p) => { refresh(); setModal(null); setPath(p) }} />}
      {modal?.type === 'folder' && <FolderModal {...modal} onClose={() => setModal(null)} onDone={(from, to) => { refresh(); setModal(null); if (from && path?.startsWith(from + '/')) setPath(to + path.slice(from.length)) }} />}
      {modal?.type === 'history' && <HistoryModal path={path} current={text} onClose={() => setModal(null)} onRestore={(c) => { setText(c); setModal(null); toast('Version chargée dans l\'éditeur — pensez à enregistrer') }} />}
    </div>
  )
}

const DirSelect = ({ dirs, value, onChange }) => (
  <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
    {dirs.map((d) => <option key={d} value={d}>{d ? '📁 ' + d : '(racine)'}</option>)}
  </select>
)

function NewFile({ dirs, dir, onClose, onCreated }) {
  const [d, setD] = useState(dirs.includes(dir) ? dir : ''); const [name, setName] = useState(''); const { toast } = useUi()
  const p = joinPath(d, withExt(name.trim() || 'nouveau'))
  const go = async () => { try { await api.put('/traefik/file', { path: p, content: NEW_FILE, create: true }); onCreated(p) } catch (e) { toast(e.message, 'err') } }
  return <Modal small title="Nouveau fichier" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" disabled={!name.trim()} onClick={go}>Créer</Btn></>}>
    <div className="col">
      {dirs.length > 1 && <Field label="Dossier"><DirSelect dirs={dirs} value={d} onChange={setD} /></Field>}
      <Field label="Nom du fichier" help={<>Sera créé : <code>{p}</code></>}><input className="input" value={name} placeholder="mon-site" onChange={(e) => setName(e.target.value.replace(/\s/g, '-'))} onKeyDown={(e) => e.key === 'Enter' && name.trim() && go()} autoFocus /></Field>
    </div></Modal>
}

function MoveFile({ path, dirs, onClose, onMoved }) {
  const [d, setD] = useState(dirOf(path)); const [name, setName] = useState(baseName(path)); const { toast } = useUi()
  const p = joinPath(d, withExt(name.trim()))
  const go = async () => { try { await api.post('/traefik/file/move', { src: path, dst: p }); onMoved(p) } catch (e) { toast(e.message, 'err') } }
  return <Modal small title="Déplacer / renommer" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" disabled={!name.trim() || p === path} onClick={go}>Déplacer</Btn></>}>
    <div className="col">
      <Field label="Dossier"><DirSelect dirs={dirs} value={d} onChange={setD} /></Field>
      <Field label="Nom du fichier" help={<>Nouveau chemin : <code>{p}</code></>}><input className="input" value={name} onChange={(e) => setName(e.target.value.replace(/\s/g, '-'))} autoFocus /></Field>
    </div></Modal>
}

// Création d'un dossier (parent + nom) ou renommage / déplacement d'un dossier existant (chemin complet).
function FolderModal({ parent = '', rename, onClose, onDone }) {
  const { toast } = useUi(); const [val, setVal] = useState(rename || '')
  const go = async () => {
    try {
      if (rename) { await api.post('/traefik/dir/move', { src: rename, dst: val.trim() }); onDone(rename, val.trim()) }
      else { const p = joinPath(parent, val.trim()); await api.post('/traefik/dir', { path: p }); onDone() }
    } catch (e) { toast(e.message, 'err') }
  }
  return <Modal small title={rename ? `Renommer « ${rename} »` : parent ? `Nouveau sous-dossier de « ${parent} »` : 'Nouveau dossier'} onClose={onClose}
    footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" disabled={!val.trim() || val.trim() === rename} onClick={go}>{rename ? 'Renommer' : 'Créer'}</Btn></>}>
    <Field label={rename ? 'Nouveau chemin' : 'Nom du dossier'} help={rename ? 'Les fichiers suivent le dossier, avec leur historique. Écrivez un autre chemin (ex. media/series) pour le déplacer.' : `Jusqu'à ${MAX_DEPTH} niveaux. Lettres, chiffres, « - », « _ » et « . ».`}>
      <input className="input" value={val} placeholder="apps" onChange={(e) => setVal(e.target.value.replace(/\s/g, '-'))} onKeyDown={(e) => e.key === 'Enter' && val.trim() && go()} autoFocus />
    </Field></Modal>
}

function HistoryModal({ path, current, onClose, onRestore }) {
  const h = useQuery({ queryKey: ['history', path], queryFn: () => api.get('/traefik/history?' + qs({ path })) })
  const [sel, setSel] = useState(null); const [content, setContent] = useState('')
  const pick = async (id) => { setSel(id); setContent((await api.get('/traefik/history/content?' + qs({ path, id }))).content) }
  const days = byDay(h.data || [], (b) => new Date(b.mtime * 1000))
  return (
    <Modal wide title={`Historique · ${path}`} onClose={onClose} footer={sel && <Btn kind="primary" onClick={() => onRestore(content)}>Charger cette version dans l'éditeur</Btn>}>
      <div className="row" style={{ alignItems: 'stretch', gap: 14 }}>
        <div className="feed" style={{ width: 250, maxHeight: '60vh', overflow: 'auto', flex: 'none' }}>
          {h.data?.length === 0 && <div className="muted">Aucune sauvegarde pour l'instant (une version est gardée à chaque enregistrement).</div>}
          {days.map((g) => <div key={g.key}><div className="feed-day">{g.key}</div>
            {g.items.map((b) => { const d = new Date(b.mtime * 1000); return (
              <button key={b.id} className={'feed-row pick ' + (sel === b.id ? 'sel' : '')} onClick={() => pick(b.id)}>
                <span className={'feed-ic ' + (b.deleted ? 'err' : 'info')}>{b.deleted ? <Trash2 size={14} /> : <History size={14} />}</span>
                <div className="feed-main"><div className="feed-title">{timeLabel(d)}{b.deleted && <span className="badge err">avant suppression</span>}</div><div className="feed-detail">{relTime(d)} · {fmtSize(b.size)}</div></div>
              </button>) })}</div>)}
        </div>
        <div className="grow">{sel ? <Diff original={content} modified={current} height="55vh" /> : <div className="empty">Choisissez une version pour la comparer (gauche : sauvegarde · droite : éditeur).</div>}</div>
      </div>
    </Modal>
  )
}
