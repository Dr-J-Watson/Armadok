import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Download, Eye, Lock, RotateCcw, Trash2, Upload, WifiOff } from 'lucide-react'
import { api as rawApi } from '../api.js'
import { Badge, Btn, Empty, Field, Loading, Modal, useUi } from './ui.jsx'
import { Diff } from './CodeEditor.jsx'
import { fmtSize } from '../lib/docker.js'
import { fmtDate } from '../lib/util.js'
import { langOf } from '../lib/files.js'

const CHUNK = 2 * 1024 * 1024
const readB64 = (blob) => new Promise((ok, ko) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1] || ''); r.onerror = () => ko(r.error); r.readAsDataURL(blob) })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function waitJob(id, api) {
  for (;;) {
    const j = await api.get(`/node-backups/jobs/${id}`)
    if (j.done) { if (j.error) throw new Error(j.error); return j.result }
    await sleep(1000)
  }
}
const STATUS = { modified: ['modifié depuis', 'warn'], missing: ['absent maintenant', 'info'], extra: ['absent de la sauvegarde', ''] }

// Les sauvegardes d'UN nœud, pour ses responsables : les fichiers de sa machine (configuration Traefik, historiques). Restaurer se fait fichier par fichier, après comparaison.
// Les requêtes visent le nœud affiché (en-tête X-Node), pas forcément le nœud courant de l'interface : on peut ainsi les piloter depuis « Tous les nœuds ».
const NodeApi = createContext(rawApi)
const useApi = () => useContext(NodeApi)

export default function NodeBackups({ node }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const api = useMemo(() => ({ get: (p) => rawApi.get(p, { node: node.id }), post: (p, b) => rawApi.post(p, b, { node: node.id }), del: (p) => rawApi.del(p, { node: node.id }) }), [node.id])
  const q = useQuery({ queryKey: ['node-backups', node.id], queryFn: () => api.get('/node-backups'), refetchInterval: 30000 })
  const [create, setCreate] = useState(false); const [rs, setRs] = useState(null); const [up, setUp] = useState(null); const fileIn = useRef(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['node-backups', node.id] })
  const del = async (b) => { if (await confirm({ title: `Supprimer « ${b.name} » ?`, message: 'Le fichier est effacé du serveur. Si vous l\'avez téléchargé, votre copie reste valable.', confirmLabel: 'Supprimer' })) { try { await api.del(`/node-backups/${encodeURIComponent(b.name)}`); toast('Sauvegarde supprimée'); refresh() } catch (e) { toast(e.message, 'err') } } }
  const download = async (b) => {
    try {
      const { ticket } = await api.post(`/node-backups/${encodeURIComponent(b.name)}/ticket`)
      const a = document.createElement('a'); a.href = `/api/node-backups/download?ticket=${encodeURIComponent(ticket)}`; a.download = b.name; document.body.appendChild(a); a.click(); a.remove()
    } catch (e) { toast(e.message, 'err') }
  }
  const upload = async (file) => {
    if (!file) return
    setUp({ name: file.name, pct: 0 })
    try {
      let off = 0
      do {
        const part = file.slice(off, off + CHUNK); const last = off + part.size >= file.size
        await api.post('/node-backups/upload', { filename: file.name, b64: await readB64(part), offset: off, last })
        off += part.size; setUp({ name: file.name, pct: file.size ? off / file.size : 1 })
        if (last) break
      } while (true) // eslint-disable-line no-constant-condition
      toast('Sauvegarde téléversée'); refresh()
    } catch (e) { api.post('/node-backups/upload', { filename: file.name, abort: true }).catch(() => {}); toast(e.message, 'err') } finally { setUp(null) }
  }
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data
  return (
    <NodeApi.Provider value={api}>
    <div className="col" style={{ gap: 14 }}>
      <div className="banner info"><Archive size={18} /><span className="grow">Une sauvegarde de <b>{node.name}</b> garde les <b>fichiers de sa machine</b> : la configuration Traefik (vos sites, routeurs, middlewares) et les versions précédentes de vos fichiers.
        Restaurer se fait <b>fichier par fichier</b>, après comparaison avec l'état actuel : vous voyez ce qui change et vous cochez ce que vous remettez. Les mots de passe et clés ne sont pas dedans.</span></div>
      {!d.online && <div className="banner warn"><WifiOff size={18} /><span>Le worker de ce nœud n'est <b>pas connecté</b> : on ne peut pas créer de sauvegarde ni comparer. Les sauvegardes déjà faites restent téléchargeables.</span></div>}
      <div className="row wrap" style={{ gap: 8 }}>
        <span className="muted small">{d.backups.length} / {d.max} sauvegarde{d.backups.length > 1 ? 's' : ''} · {fmtSize(d.free)} libres sur le hub</span>
        <span className="right row wrap" style={{ gap: 8 }}>
          <input ref={fileIn} type="file" accept=".zip,.armbak" hidden onChange={(e) => { const f = e.target.files[0]; e.target.value = ''; upload(f) }} />
          <Btn onClick={() => fileIn.current?.click()} busy={!!up} title="Envoyer une sauvegarde de nœud (.zip ou .armbak)"><Upload size={14} /> Téléverser une sauvegarde</Btn>
          <Btn kind="primary" disabled={!d.online || !d.available.length} onClick={() => setCreate(true)}><Archive size={14} /> Créer une sauvegarde</Btn>
        </span>
      </div>
      {up && <div className="card" role="status"><div className="row" style={{ gap: 8 }}><Upload size={14} /><span className="grow ellipsis">Téléversement de {up.name}</span><b>{Math.round(up.pct * 100)} %</b></div><div className="pf-bar" style={{ marginTop: 6 }}><i style={{ width: Math.round(up.pct * 100) + '%' }} /></div></div>}
      {d.backups.length === 0 ? <div className="card"><Empty><Archive size={30} /><div>Aucune sauvegarde pour ce nœud.</div></Empty></div> : (
        <div className="card flush"><table className="t"><thead><tr><th>Sauvegarde</th><th>Protection</th><th>Taille</th><th /></tr></thead><tbody>
          {d.backups.map((b) => (
            <tr key={b.name}>
              <td><b>{b.label || (b.kind && b.kind !== 'manuelle' ? b.kind : 'Sauvegarde')}</b><div className="muted small">{fmtDate(b.createdAt * 1000)}{b.createdBy ? ` · ${b.createdBy}` : ''}{b.node && b.node.id !== node.id ? ` · faite sur « ${b.node.name} »` : ''}</div><div className="muted small mono ellipsis" style={{ maxWidth: 360 }} title={b.name}>{b.name}</div></td>
              <td>{b.encrypted ? <Badge kind="ok"><Lock size={10} /> mot de passe</Badge> : <Badge kind="warn">en clair</Badge>}</td>
              <td className="small">{fmtSize(b.size)}</td>
              <td className="actions"><div className="row" style={{ gap: 4, justifyContent: 'flex-end' }}>
                <Btn small onClick={() => download(b)}><Download size={13} /> Télécharger</Btn>
                {d.canRestore && <Btn small disabled={!d.online} title={d.online ? 'Comparer avec l\'état actuel, puis restaurer' : 'Le worker doit être connecté'} onClick={() => setRs(b)}><RotateCcw size={13} /> Restaurer</Btn>}
                <Btn small kind="ghost" icon title="Supprimer" aria-label="Supprimer" onClick={() => del(b)}><Trash2 size={13} /></Btn></div></td>
            </tr>))}</tbody></table></div>)}
      {!d.canRestore && d.backups.length > 0 && <div className="muted small">Restaurer est réservé au propriétaire du nœud (ou à qui il en donne le droit).</div>}
      {create && <CreateModal d={d} onClose={() => setCreate(false)} onDone={() => { setCreate(false); refresh() }} />}
      {rs && <RestoreModal b={rs} onClose={() => { setRs(null); refresh() }} />}
    </div>
    </NodeApi.Provider>
  )
}

function CreateModal({ d, onClose, onDone }) {
  const api = useApi()
  const { toast } = useUi()
  const [sel, setSel] = useState(() => new Set(d.available.map((c) => c.id))); const [label, setLabel] = useState(''); const [protect, setProtect] = useState(false); const [pw, setPw] = useState(''); const [pw2, setPw2] = useState('')
  const [busy, setBusy] = useState(false); const [err, setErr] = useState('')
  const bad = !sel.size || (protect && (pw.length < 8 || pw !== pw2))
  const go = async () => {
    setBusy(true); setErr('')
    try { const { job } = await api.post('/node-backups', { components: [...sel], label, passphrase: protect ? pw : '' }); const r = await waitJob(job, api); toast(`Sauvegarde créée : ${r.name}`); onDone() } catch (e) { setErr(e.message); setBusy(false) }
  }
  return (
    <Modal title="Créer une sauvegarde du nœud" onClose={busy ? () => {} : onClose} footer={<><Btn onClick={onClose} disabled={busy}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={bad} onClick={go}><Archive size={14} /> Créer</Btn></>}>
      <div className="col">
        <div className="col" style={{ gap: 6 }}><b>Contenu</b>
          {d.available.map((c) => <label key={c.id} className="row" style={{ gap: 8 }}><input type="checkbox" checked={sel.has(c.id)} onChange={(e) => setSel((s) => { const n = new Set(s); e.target.checked ? n.add(c.id) : n.delete(c.id); return n })} /><span className="grow">{c.label}</span><span className="muted small">{c.files} fichier{c.files > 1 ? 's' : ''} · {fmtSize(c.bytes)}</span></label>)}</div>
        <Field label="Nom (facultatif)"><input className="input" maxLength={80} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="avant la refonte des routeurs" /></Field>
        <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={protect} onChange={(e) => setProtect(e.target.checked)} /><span><b>Protéger par un mot de passe</b> <span className="muted small">(chiffrement AES : sans lui, la sauvegarde est inutilisable)</span></span></label>
        {protect && <div className="row wrap" style={{ gap: 8 }}><input className="input grow" type="password" autoComplete="new-password" placeholder="Mot de passe (8 caractères au moins)" value={pw} onChange={(e) => setPw(e.target.value)} /><input className="input grow" type="password" autoComplete="new-password" placeholder="Le confirmer" value={pw2} onChange={(e) => setPw2(e.target.value)} />{pw2 && pw !== pw2 && <span className="small" style={{ color: 'var(--err)' }}>Les deux mots de passe diffèrent.</span>}</div>}
        {busy && <div className="muted small">Création en cours : les fichiers sont récupérés depuis la machine du nœud…</div>}
        {err && <div className="banner err">{err}</div>}
      </div>
    </Modal>
  )
}

// Restaurer : on COMPARE d'abord (rien n'est touché), on coche les fichiers à remettre, on confirme avec le nom. Une sauvegarde « avant-restauration » est faite avant d'écrire quoi que ce soit.
function RestoreModal({ b, onClose }) {
  const api = useApi()
  const { toast } = useUi()
  const [pw, setPw] = useState(''); const [data, setData] = useState(null); const [err, setErr] = useState(''); const [busy, setBusy] = useState(false)
  const [sel, setSel] = useState(new Set()); const [typed, setTyped] = useState(''); const [view, setView] = useState(null); const [done, setDone] = useState(null)
  const key = (e) => `${e.component}\u0000${e.rel}`
  const read = async () => {
    setBusy(true); setErr('')
    try {
      const r = await api.post(`/node-backups/${encodeURIComponent(b.name)}/diff`, { passphrase: pw })
      setData(r); setSel(new Set(r.entries.filter((e) => e.status !== 'extra').map(key)))   // par défaut : on remet ce qui a changé ou disparu ; on ne retire rien sans que ce soit coché
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  useEffect(() => { if (!b.encrypted) read() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (e) => setSel((s) => { const n = new Set(s); n.has(key(e)) ? n.delete(key(e)) : n.add(key(e)); return n })
  const group = (st, on) => setSel((s) => { const n = new Set(s); data.entries.filter((e) => e.status === st).forEach((e) => (on ? n.add(key(e)) : n.delete(key(e)))); return n })
  const chosen = data ? data.entries.filter((e) => sel.has(key(e))) : []
  const go = async () => {
    setBusy(true); setErr('')
    try {
      const { job } = await api.post(`/node-backups/${encodeURIComponent(b.name)}/restore`, { passphrase: pw, confirm: typed, items: chosen.map((e) => ({ component: e.component, rel: e.rel, action: e.status === 'extra' ? 'delete' : 'restore' })) })
      setDone(await waitJob(job, api))
    } catch (e) { setErr(e.message); setBusy(false) }
  }
  if (done) return (
    <Modal small title="Restauration terminée" onClose={onClose} footer={<Btn kind="primary" onClick={onClose}>Fermer</Btn>}>
      <div className="col"><div className={'banner ' + (done.errors.length ? 'warn' : 'ok')}>{done.restored} fichier{done.restored > 1 ? 's' : ''} restauré{done.restored > 1 ? 's' : ''}{done.deleted ? `, ${done.deleted} retiré${done.deleted > 1 ? 's' : ''}` : ''}{done.errors.length ? `, ${done.errors.length} en erreur` : ''}.</div>
        {done.errors.map((e) => <div key={e} className="small" style={{ color: 'var(--err)' }}>{e}</div>)}
        <div className="muted small">L'état d'avant a été gardé dans la sauvegarde <span className="mono">{done.safety}</span> : vous pouvez revenir en arrière avec elle. Traefik relit ses fichiers tout seul.</div></div>
    </Modal>)
  return (
    <Modal wide title={`Restaurer · ${b.label || b.name}`} onClose={busy ? () => {} : onClose} footer={<><Btn onClick={onClose} disabled={busy}>Annuler</Btn>{data && <Btn kind="danger" busy={busy} disabled={!chosen.length || typed !== b.name} onClick={go}><RotateCcw size={14} /> Restaurer {chosen.length} fichier{chosen.length > 1 ? 's' : ''}</Btn>}</>}>
      <div className="col" style={{ gap: 12 }}>
        <div className="mono small">{b.name}</div>
        {!data && b.encrypted && <div className="col"><Field label="Mot de passe de la sauvegarde"><input className="input" type="password" autoFocus autoComplete="off" value={pw} onChange={(e) => setPw(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && pw) read() }} /></Field><div><Btn kind="primary" busy={busy} disabled={!pw} onClick={read}>Comparer avec le nœud</Btn></div></div>}
        {!data && !b.encrypted && busy && <Loading />}
        {data && <>
          <div className="small muted">{data.entries.length ? `${data.entries.length} fichier${data.entries.length > 1 ? 's' : ''} diffère${data.entries.length > 1 ? 'nt' : ''} de l'état actuel` : 'Aucune différence'} · {data.same} identique{data.same > 1 ? 's' : ''}.{data.unavailable.length > 0 && ` Éléments de la sauvegarde absents de ce nœud (module manquant) : ${data.unavailable.join(', ')}.`}{data.truncated && ' Liste tronquée.'}</div>
          {['modified', 'missing', 'extra'].map((st) => { const rows = data.entries.filter((e) => e.status === st); if (!rows.length) return null; const all = rows.every((e) => sel.has(key(e)))
            return (
              <div key={st} className="col" style={{ gap: 4 }}>
                <div className="row" style={{ gap: 8 }}><input type="checkbox" checked={all} onChange={(e) => group(st, e.target.checked)} aria-label="Tout cocher" /><Badge kind={STATUS[st][1]}>{STATUS[st][0]}</Badge><span className="muted small">{rows.length} fichier{rows.length > 1 ? 's' : ''}{st === 'extra' ? ' — cocher les RETIRERA du nœud' : st === 'missing' ? ' — cocher les remettra' : ' — cocher remet la version de la sauvegarde'}</span></div>
                <div className="card flush" style={{ maxHeight: 220, overflow: 'auto' }}>
                  {rows.map((e) => <div key={key(e)} className="row" style={{ gap: 8, padding: '5px 10px', borderBottom: '1px solid var(--border)' }}>
                    <input type="checkbox" checked={sel.has(key(e))} onChange={() => toggle(e)} />
                    <span className="mono small grow ellipsis" title={`${e.component}/${e.rel}`}><span className="muted">{e.component === 'traefik' ? '' : e.component + '/'}</span>{e.rel}</span>
                    <span className="muted small">{e.sizeNow != null ? fmtSize(e.sizeNow) : '—'} → {e.sizeBackup != null ? fmtSize(e.sizeBackup) : '—'}</span>
                    <Btn small kind="ghost" icon title="Voir les différences" aria-label="Voir les différences" onClick={() => setView(e)}><Eye size={13} /></Btn></div>)}
                </div></div>) })}
          <div className="banner warn">Une sauvegarde de l'état actuel est faite avant d'écrire quoi que ce soit. Seuls les fichiers cochés sont touchés.</div>
          <Field label="Pour confirmer, retapez le nom de la sauvegarde"><input className="input mono" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={b.name} autoComplete="off" spellCheck={false} /></Field>
        </>}
        {err && <div className="banner err">{err}</div>}
      </div>
      {view && <FileDiff b={b} pw={pw} e={view} onClose={() => setView(null)} />}
    </Modal>
  )
}

function FileDiff({ b, pw, e, onClose }) {
  const api = useApi()
  const q = useQuery({ queryKey: ['node-file-diff', b.name, e.component, e.rel], queryFn: () => api.post(`/node-backups/${encodeURIComponent(b.name)}/file-diff`, { passphrase: pw, component: e.component, rel: e.rel }) })
  const side = (x) => (x == null ? '' : x.content ?? '')
  const note = (x) => (x == null ? null : x.large ? 'fichier trop gros pour être comparé' : x.binary ? 'fichier binaire' : null)
  return (
    <Modal wide title={`${e.rel}`} onClose={onClose} footer={<Btn onClick={onClose}>Fermer</Btn>}>
      {q.isLoading ? <Loading /> : q.error ? <div className="banner err">{q.error.message}</div> : (
        <div className="col"><div className="muted small">À gauche : <b>le nœud maintenant</b>{q.data.before == null ? ' (le fichier n\'existe pas)' : ''} · à droite : <b>la sauvegarde</b>{q.data.after == null ? ' (le fichier n\'y est pas)' : ''}.</div>
          {note(q.data.before) || note(q.data.after) ? <div className="banner">{note(q.data.before) || note(q.data.after)}</div> : <Diff original={side(q.data.before)} modified={side(q.data.after)} language={langOf(e.rel)} />}</div>)}
    </Modal>
  )
}
