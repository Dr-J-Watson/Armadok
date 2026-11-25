import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Download, FilePlus, Globe, Lock, Pencil, Rocket, Save, Trash2 } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Loading, Modal, Seg, useUi } from './ui.jsx'
import CodeEditor from './CodeEditor.jsx'
import { FileMark, PathModal, buildRows } from './ProjectFiles.jsx'
import { isCompose, langOf } from '../lib/files.js'
import { enc, fmtSize } from '../lib/docker.js'
import { fmtDate } from '../lib/util.js'

const parentOf = (p) => p.split('/').slice(0, -1).join('/')

// Un modèle de projet, exploré et modifié fichier par fichier (comme les fichiers d'un projet). Un modèle intégré ou celui d'un autre est en lecture seule :
// « Dupliquer » en fait une copie privée à vous, modifiable.
export default function TemplateEditor({ id, canCreate, canEnv, onClose, onUse, onExport, onDuplicate }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const q = useQuery({ queryKey: ['template', id], queryFn: () => api.get(`/projects/templates/${encodeURIComponent(id)}`) })
  const t = q.data
  const [sel, setSel] = useState(null); const [text, setText] = useState(''); const [orig, setOrig] = useState(''); const [ro, setRo] = useState(true); const [err, setErr] = useState(''); const [modal, setModal] = useState(null)
  const [name, setName] = useState(''); const [desc, setDesc] = useState('')
  useEffect(() => { if (t) { setName(t.name); setDesc(t.description) } }, [t?.name, t?.description]) // eslint-disable-line react-hooks/exhaustive-deps
  const files = t?.files || []
  const rows = useMemo(() => buildRows(files.map((p) => ({ path: p, type: 'file' })), new Set()), [files.join('\n')]) // eslint-disable-line react-hooks/exhaustive-deps
  const dirs = useMemo(() => [...new Set(files.flatMap((p) => { const out = []; let d = parentOf(p); while (d) { out.push(d); d = parentOf(d) } return out }))], [files.join('\n')]) // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = sel != null && text !== orig
  const mine = !!t?.mine
  const refresh = () => { qc.invalidateQueries({ queryKey: ['template', id] }); qc.invalidateQueries({ queryKey: ['templates'] }) }

  const open = async (rel) => {
    if (rel === sel) return
    if (dirty && !(await confirm({ title: 'Modifications non enregistrées', message: 'Elles seront perdues si vous ouvrez un autre fichier.', confirmLabel: 'Abandonner' }))) return
    setErr(''); setSel(rel)
    if (rel === '.env' && !canEnv) { setText(''); setOrig(''); setErr('Le .env d\'un modèle demande le droit « voir et modifier le .env ».'); return }
    try { const r = await api.get(`/projects/templates/${encodeURIComponent(id)}/file?rel=${enc(rel)}`); setText(r.content); setOrig(r.content); setRo(r.readOnly) } catch (e) { setErr(e.message); setText(''); setOrig('') }
  }
  useEffect(() => { if (t && !sel && files.length) open(files.find((f) => isCompose(f) && !f.includes('/')) || files.find(isCompose) || files[0]) }, [t]) // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: ({ rel, content, create }) => api.put(`/projects/templates/${encodeURIComponent(id)}/file`, { rel, content, create: !!create }),
    onSuccess: (r, v) => { toast(v.create ? 'Fichier créé' : 'Fichier enregistré'); setModal(null); if (v.create) { setSel(v.rel); setText(v.content); setOrig(v.content); setRo(false); setErr('') } else setOrig(text); refresh() },
    onError: (e) => toast(e.message, 'err'),
  })
  const move = useMutation({
    mutationFn: ({ src, dst }) => api.post(`/projects/templates/${encodeURIComponent(id)}/move`, { src, dst }),
    onSuccess: (r, v) => { toast('Déplacé'); setModal(null); if (sel === v.src) setSel(v.dst); else if (sel?.startsWith(v.src + '/')) setSel(v.dst + sel.slice(v.src.length)); refresh() }, onError: (e) => toast(e.message, 'err'),
  })
  const del = useMutation({
    mutationFn: (rel) => api.del(`/projects/templates/${encodeURIComponent(id)}/file?rel=${enc(rel)}`),
    onSuccess: (r, rel) => { toast('Fichier supprimé du modèle'); if (sel === rel) { setSel(null); setText(''); setOrig('') } refresh() }, onError: (e) => toast(e.message, 'err'),
  })
  const meta = useMutation({
    mutationFn: (b) => api.put(`/projects/templates/${encodeURIComponent(id)}`, b),
    onSuccess: () => { toast('Modèle mis à jour'); refresh() }, onError: (e) => toast(e.message, 'err'),
  })
  const share = async (shared) => {
    if (shared === t.shared) return
    if (shared && !(await confirm({ title: `Partager « ${t.name} » ?`, message: 'Tous ceux qui peuvent créer des projets le verront et pourront l\'utiliser. Les valeurs des secrets du .env sont vidées pour de bon.', confirmLabel: 'Partager', danger: false }))) return
    meta.mutate({ shared })
  }
  const askDelete = async (rel) => { if (await confirm({ title: `Retirer « ${rel} » du modèle ?`, message: 'Le fichier disparaît du modèle (les projets déjà créés ne changent pas).', confirmLabel: 'Retirer' })) del.mutate(rel) }
  const metaDirty = t && (name.trim() !== t.name || desc.trim() !== t.description)

  if (q.isLoading) return <Modal wide title="Modèle" onClose={onClose}><Loading /></Modal>
  if (q.error) return <Modal wide title="Modèle" onClose={onClose} footer={<Btn onClick={onClose}>Fermer</Btn>}><div className="banner err">{q.error.message}</div></Modal>
  return (
    <Modal wide title={<span className="row" style={{ gap: 8 }}>{t.name}{t.builtin ? <Badge>intégré</Badge> : <Badge kind={t.shared ? 'info' : 'warn'}>{t.shared ? <><Globe size={10} /> partagé</> : <><Lock size={10} /> privé</>}</Badge>}</span>} onClose={async () => { if (dirty && !(await confirm({ title: 'Modifications non enregistrées', message: 'Elles seront perdues.', confirmLabel: 'Fermer quand même' }))) return; onClose() }}
      footer={<>
        {canCreate && <Btn onClick={() => onUse(t.id)}><Rocket size={14} /> Créer un projet</Btn>}
        <Btn onClick={() => onExport(t.id)}><Download size={14} /> Exporter (zip)</Btn>
        {canCreate && <Btn onClick={() => onDuplicate(t.id)} title="Une copie privée à vous, modifiable"><Copy size={14} /> Dupliquer</Btn>}
        <Btn className="right" onClick={onClose}>Fermer</Btn></>}>
      <div className="col">
        {mine ? (
          <div className="row wrap" style={{ gap: 8 }}>
            <input className="input grow" style={{ minWidth: 180 }} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} aria-label="Nom du modèle" />
            <input className="input grow" style={{ minWidth: 220 }} value={desc} maxLength={300} onChange={(e) => setDesc(e.target.value)} placeholder="Description (facultatif)" aria-label="Description" />
            <Seg value={t.shared ? 'shared' : 'private'} onChange={(v) => share(v === 'shared')} options={[{ id: 'private', label: <><Lock size={12} /> Privé</>, hint: 'Vous seul le voyez' }, { id: 'shared', label: <><Globe size={12} /> Partagé</>, hint: 'Visible de ceux qui créent des projets ; les secrets sont vidés' }]} />
            {metaDirty && <Btn kind="primary" busy={meta.isPending} disabled={!name.trim()} onClick={() => meta.mutate({ name: name.trim(), description: desc.trim() })}><Save size={13} /> Enregistrer</Btn>}
          </div>
        ) : <div className="muted small">{t.description || 'Pas de description.'}{!t.builtin && t.createdBy ? ` · par ${t.createdBy}` : ''} · {t.builtin ? 'Modèle intégré' : 'Modèle d\'un autre utilisateur'} : en lecture seule. {canCreate && 'Dupliquez-le pour le modifier.'}</div>}
        <div className="muted small">{files.length} fichier{files.length > 1 ? 's' : ''} · {fmtSize(t.size || 0)}{t.updatedAt ? ` · modifié le ${fmtDate(t.updatedAt * 1000)}` : ''} · {t.vars.length} variable{t.vars.length > 1 ? 's' : ''} à régler à la création. Dans les fichiers, <code>{'{{name}}'}</code> est remplacé par le nom du dossier du projet créé.</div>
        <div className="pf" style={{ gridTemplateColumns: 'clamp(240px, 28%, 360px) minmax(0, 1fr)' }}>
          <div className="card pf-tree">
            <div className="row" style={{ gap: 4, marginBottom: 6 }}><b>Fichiers</b><span className="muted small">{files.length}</span><span className="grow" />
              {mine && canCreate && <Btn small kind="ghost" title="Nouveau fichier dans le modèle" onClick={() => setModal({ type: 'new' })}><FilePlus size={15} /></Btn>}</div>
            <div className="ft" role="tree">
              {rows.map((e) => e.type === 'dir'
                ? <div key={e.path} className="ft-item" style={{ paddingLeft: 4 + e.depth * 14 }}><span className="ft-main muted small ellipsis" style={{ cursor: 'default' }}><span className="ft-sp" />{e.name}/</span>
                  {mine && <><button type="button" className="ft-more" style={{ opacity: 1 }} title="Renommer / déplacer ce dossier" aria-label={`Renommer ${e.name}`} onClick={() => setModal({ type: 'move', src: e.path })}><Pencil size={13} /></button></>}</div>
                : <div key={e.path} className={'ft-item ' + (sel === e.path ? 'on' : '')} style={{ paddingLeft: 4 + e.depth * 14 }} role="treeitem" aria-selected={sel === e.path}>
                  <button type="button" className="ft-main" onClick={() => open(e.path)} title={e.path}><span className="ft-sp" /><FileMark path={e.path} /><span className="ellipsis">{e.name}</span>{dirty && sel === e.path && <i className="dot warn" title="Modifié, pas encore enregistré" />}</button>
                  {mine && <button type="button" className="ft-more" style={{ opacity: 1 }} title="Renommer / déplacer" aria-label={`Renommer ${e.name}`} onClick={() => setModal({ type: 'move', src: e.path })}><Pencil size={13} /></button>}
                  {mine && !(isCompose(e.path) && files.filter(isCompose).length === 1) && <button type="button" className="ft-more" style={{ opacity: 1 }} title="Retirer du modèle" aria-label={`Retirer ${e.name}`} onClick={() => askDelete(e.path)}><Trash2 size={13} /></button>}
                </div>)}
            </div>
            {t.vars.length > 0 && <details style={{ marginTop: 10 }}><summary className="muted small" style={{ cursor: 'pointer' }}>Variables ({t.vars.length})</summary>
              <div className="col" style={{ gap: 4, marginTop: 6 }}>{t.vars.map((v) => <div key={v.key} className="small" title={v.help}><b className="mono">{v.key}</b>{v.secret && <Badge kind="warn" style={{ marginLeft: 4 }}>secret</Badge>}<div className="muted mono ellipsis">{v.secret ? (v.default ? '•••••' : '(à saisir)') : v.default || '(vide)'}</div></div>)}</div></details>}
          </div>
          <div className="card pf-ed">
            {!sel ? <div className="muted">Choisissez un fichier du modèle.</div> : <>
              <div className="row wrap" style={{ gap: 6, marginBottom: 8 }}>
                <FileMark path={sel} size={17} /><b className="mono small grow ellipsis" title={sel}>{sel}</b>
                {!ro && !err && <Btn kind="primary" busy={save.isPending} disabled={!dirty} onClick={() => save.mutate({ rel: sel, content: text })}><Save size={13} /> Enregistrer</Btn>}
              </div>
              {err ? <div className="banner err">{err}</div> : <>
                {ro && <div className="muted small" style={{ marginBottom: 6 }}>Lecture seule{t.builtin ? ' (modèle intégré)' : ''}.</div>}
                {!ro && t.shared && sel === '.env' && <div className="banner info" style={{ marginBottom: 6 }}>Modèle partagé : les valeurs des secrets du .env sont vidées à l'enregistrement.</div>}
                <div className="editor-wrap pf-editor"><CodeEditor key={sel} language={langOf(sel)} readOnly={ro} value={text} onChange={setText} onSave={() => dirty && !ro && save.mutate({ rel: sel, content: text })} /></div></>}
            </>}
          </div>
        </div>
      </div>
      {modal?.type === 'new' && <PathModal title="Nouveau fichier dans le modèle" label="Chemin du fichier" initial="" dirs={dirs} busy={save.isPending} confirmLabel="Créer" hint="Créé vide. « conf/nginx.conf » crée aussi le dossier." onClose={() => setModal(null)} onSubmit={(rel) => save.mutate({ create: true, rel, content: '' })} />}
      {modal?.type === 'move' && <PathModal title="Renommer / déplacer" label="Nouveau chemin" initial={modal.src} dirs={dirs.filter((d) => d !== modal.src && !d.startsWith(modal.src + '/'))} busy={move.isPending} confirmLabel="Appliquer" same={modal.src} onClose={() => setModal(null)} onSubmit={(dst) => move.mutate({ src: modal.src, dst })} />}
    </Modal>
  )
}
