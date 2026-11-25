import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Download, Lock, KeyRound, RotateCcw, ShieldAlert, ShieldCheck, Trash2, Upload } from 'lucide-react'
import { api } from '../api.js'
import { useAuth } from '../auth.jsx'
import { Badge, Btn, CopyBtn, Empty, Field, Loading, Modal, useUi } from './ui.jsx'
import { fmtSize } from '../lib/docker.js'
import { fmtDate } from '../lib/util.js'

const CHUNK = 2 * 1024 * 1024
const SHORT = { database: 'Base de données', history: 'Historiques', home: 'Docker / Git', traefik: 'Fichiers Traefik' }
const readB64 = (blob) => new Promise((ok, ko) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1] || ''); r.onerror = () => ko(r.error); r.readAsDataURL(blob) })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// Attend la fin d'une tâche en arrière-plan (création, restauration) : interroge toutes les secondes, rend son résultat ou lève son erreur.
async function waitJob(id) {
  for (;;) {
    const j = await api.get(`/admin/backups/jobs/${id}`)
    if (j.done) { if (j.error) throw new Error(j.error); return j.result }
    await sleep(1000)
  }
}

// Sauvegardes complètes du hub : créer, télécharger, téléverser, restaurer. Réservé aux super-administrateurs ; le téléchargement et la restauration exigent un code 2FA.
export default function Backups() {
  const { user } = useAuth(); const { toast, confirm } = useUi(); const qc = useQueryClient()
  const q = useQuery({ queryKey: ['backups'], queryFn: () => api.get('/admin/backups') })
  const [create, setCreate] = useState(false); const [dl, setDl] = useState(null); const [rs, setRs] = useState(null); const [up, setUp] = useState(null)
  const fileIn = useRef(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['backups'] })
  const del = async (b) => { if (await confirm({ title: `Supprimer « ${b.name} » ?`, message: 'Le fichier de sauvegarde est effacé du serveur. Si vous l\'avez téléchargé, votre copie reste valable.', confirmLabel: 'Supprimer' })) { try { await api.del(`/admin/backups/${encodeURIComponent(b.name)}`); toast('Sauvegarde supprimée'); refresh() } catch (e) { toast(e.message, 'err') } } }
  const upload = async (file) => {
    if (!file) return
    setUp({ name: file.name, pct: 0 })
    try {
      let off = 0
      do {
        const part = file.slice(off, off + CHUNK); const last = off + part.size >= file.size
        await api.post('/admin/backups/upload', { filename: file.name, b64: await readB64(part), offset: off, last })
        off += part.size; setUp({ name: file.name, pct: file.size ? off / file.size : 1 })
        if (last) break
      } while (true) // eslint-disable-line no-constant-condition
      toast('Sauvegarde téléversée : elle apparaît dans la liste, prête à être restaurée'); refresh()
    } catch (e) { api.post('/admin/backups/upload', { filename: file.name, abort: true }).catch(() => {}); toast(e.message, 'err') } finally { setUp(null) }
  }
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data
  return (
    <div className="col" style={{ gap: 14 }}>
      {!user.twoFactor && <div className="banner"><ShieldAlert size={18} /><span className="grow">Télécharger ou restaurer une sauvegarde exige un code de double authentification : <b>activez-la d'abord</b> sur votre compte.</span>
        <Btn small kind="primary" onClick={() => window.dispatchEvent(new Event('open-profile'))}><ShieldCheck size={13} /> Activer</Btn></div>}
      <div className="banner info"><Archive size={18} /><span className="grow">Une sauvegarde contient <b>tout le hub</b> : utilisateurs, droits, secrets chiffrés, modèles, historiques. Gardez-la à l'abri.
        Elle ne se relit qu'avec votre <b>clé de chiffrement</b> (<span className="mono">ENCRYPTION_KEY</span>, dans votre <span className="mono">.env</span>) : sauvegardez aussi ce fichier, ou incluez la clé dans une sauvegarde protégée par mot de passe.</span></div>
      {d.restartPolicy === '' && <div className="banner warn"><ShieldAlert size={18} /><span>Le conteneur du hub n'a <b>pas de redémarrage automatique</b> (<span className="mono">restart: unless-stopped</span>) : après une restauration, il faudra le relancer à la main.</span></div>}
      <div className="row wrap" style={{ gap: 8 }}>
        <span className="muted small">{d.backups.length} sauvegarde{d.backups.length > 1 ? 's' : ''} · {fmtSize(d.free)} libres sur le disque</span>
        <span className="right row wrap" style={{ gap: 8 }}>
          <input ref={fileIn} type="file" accept=".zip,.armbak" hidden onChange={(e) => { const f = e.target.files[0]; e.target.value = ''; upload(f) }} />
          <Btn onClick={() => fileIn.current?.click()} busy={!!up} title="Envoyer une sauvegarde (.zip ou .armbak) sur ce hub pour la restaurer"><Upload size={14} /> Téléverser une sauvegarde</Btn>
          <Btn kind="primary" onClick={() => setCreate(true)}><Archive size={14} /> Créer une sauvegarde</Btn>
        </span>
      </div>
      {up && <div className="card" role="status"><div className="row" style={{ gap: 8 }}><Upload size={14} /><span className="grow ellipsis">Téléversement de {up.name}</span><b>{Math.round(up.pct * 100)} %</b></div><div className="pf-bar" style={{ marginTop: 6 }}><i style={{ width: Math.round(up.pct * 100) + '%' }} /></div></div>}
      {d.backups.length === 0 ? <div className="card"><Empty><Archive size={30} /><div>Aucune sauvegarde pour l'instant.<br /><span className="muted small">« Créer une sauvegarde » copie la base et les historiques sans arrêter le hub.</span></div></Empty></div> : (
        <div className="card flush"><table className="t"><thead><tr><th>Sauvegarde</th><th>Contenu</th><th>Protection</th><th>Taille</th><th /></tr></thead><tbody>
          {d.backups.map((b) => (
            <tr key={b.name}>
              <td><b>{b.label || (b.kind && b.kind !== 'manuelle' ? b.kind : 'Sauvegarde')}</b><div className="muted small">{fmtDate(b.createdAt * 1000)}{b.createdBy ? ` · ${b.createdBy}` : ''}{b.app ? ` · ${b.app}` : ''}</div><div className="muted small mono ellipsis" style={{ maxWidth: 340 }} title={b.name}>{b.name}</div></td>
              <td><div className="row wrap" style={{ gap: 4 }}>{Object.keys(b.components).length ? Object.keys(b.components).map((k) => <Badge key={k}>{SHORT[k] || k}</Badge>) : <span className="muted small">{b.encrypted ? 'protégée : visible à l\'ouverture' : '—'}</span>}</div></td>
              <td><div className="row wrap" style={{ gap: 4 }}>{b.encrypted ? <Badge kind="ok"><Lock size={10} /> mot de passe</Badge> : <Badge kind="warn">en clair</Badge>}{b.includesKey && <Badge kind="info"><KeyRound size={10} /> clé incluse</Badge>}</div></td>
              <td className="small">{fmtSize(b.size)}</td>
              <td className="actions"><div className="row" style={{ gap: 4, justifyContent: 'flex-end' }}>
                <Btn small title="Télécharger (code de double authentification requis)" onClick={() => setDl(b)}><Download size={13} /> Télécharger</Btn>
                <Btn small title="Restaurer cette sauvegarde" onClick={() => setRs(b)}><RotateCcw size={13} /> Restaurer</Btn>
                <Btn small kind="ghost" icon title="Supprimer" aria-label="Supprimer" onClick={() => del(b)}><Trash2 size={13} /></Btn></div></td>
            </tr>))}</tbody></table></div>)}
      {create && <CreateModal d={d} onClose={() => setCreate(false)} onDone={() => { setCreate(false); refresh() }} />}
      {dl && <DownloadModal b={dl} onClose={() => setDl(null)} />}
      {rs && <RestoreModal b={rs} d={d} onClose={() => setRs(null)} />}
    </div>
  )
}

function CreateModal({ d, onClose, onDone }) {
  const { toast } = useUi()
  const [sel, setSel] = useState(() => new Set(d.components.map((c) => c.id))); const [label, setLabel] = useState(''); const [protect, setProtect] = useState(false); const [pw, setPw] = useState(''); const [pw2, setPw2] = useState('')
  const [withKey, setWithKey] = useState(false); const [code, setCode] = useState(''); const [busy, setBusy] = useState(false); const [err, setErr] = useState('')
  const bad = !sel.size || (protect && (pw.length < 8 || pw !== pw2)) || (withKey && (!protect || code.replace(/\s/g, '').length < 6))
  const go = async () => {
    setBusy(true); setErr('')
    try {
      const { job } = await api.post('/admin/backups', { components: [...sel], label, passphrase: protect ? pw : '', includeKey: withKey, code })
      const r = await waitJob(job); toast(`Sauvegarde créée : ${r.name}`); onDone()
    } catch (e) { setErr(e.message); setBusy(false) }
  }
  return (
    <Modal title="Créer une sauvegarde" onClose={busy ? () => {} : onClose} footer={<><Btn onClick={onClose} disabled={busy}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={bad} onClick={go}><Archive size={14} /> Créer</Btn></>}>
      <div className="col">
        <div className="col" style={{ gap: 6 }}><b>Contenu</b>
          {d.components.map((c) => <label key={c.id} className="row" style={{ gap: 8 }}><input type="checkbox" checked={sel.has(c.id)} onChange={(e) => setSel((s) => { const n = new Set(s); e.target.checked ? n.add(c.id) : n.delete(c.id); return n })} /><span className="grow">{c.label}</span><span className="muted small">{fmtSize(c.bytes)}</span></label>)}
          <div className="muted small">La base est copiée pendant que le hub tourne (rien n'est arrêté). La base de géolocalisation n'est jamais incluse : elle se retélécharge.</div></div>
        <Field label="Nom (facultatif)"><input className="input" maxLength={80} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="avant la mise à jour" /></Field>
        <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={protect} onChange={(e) => { setProtect(e.target.checked); if (!e.target.checked) setWithKey(false) }} /><span><b>Protéger par un mot de passe</b> <span className="muted small">(chiffrement AES : sans lui, la sauvegarde est inutilisable)</span></span></label>
        {protect && <div className="row wrap" style={{ gap: 8 }}><input className="input grow" type="password" autoComplete="new-password" placeholder="Mot de passe (8 caractères au moins)" value={pw} onChange={(e) => setPw(e.target.value)} /><input className="input grow" type="password" autoComplete="new-password" placeholder="Le confirmer" value={pw2} onChange={(e) => setPw2(e.target.value)} />
          {pw2 && pw !== pw2 && <span className="small" style={{ color: 'var(--err)' }}>Les deux mots de passe diffèrent.</span>}</div>}
        {protect && <><label className="row" style={{ gap: 8 }}><input type="checkbox" disabled={!d.hasEnvKey} checked={withKey} onChange={(e) => setWithKey(e.target.checked)} /><span><b>Inclure la clé de chiffrement</b> <span className="muted small">{d.hasEnvKey ? '(de quoi tout relire sans votre .env ; code 2FA requis)' : '(aucune ENCRYPTION_KEY définie : rien à inclure)'}</span></span></label>
          {withKey && <Field label="Code de double authentification"><input className="input mono" style={{ maxWidth: 180, letterSpacing: 3, textAlign: 'center' }} inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} /></Field>}</>}
        {busy && <div className="muted small">Création en cours… la base fait plusieurs dizaines de Mo, cela peut prendre un moment.</div>}
        {err && <div className="banner err">{err}</div>}
      </div>
    </Modal>
  )
}

function DownloadModal({ b, onClose }) {
  const { toast } = useUi(); const [code, setCode] = useState(''); const [busy, setBusy] = useState(false); const [err, setErr] = useState('')
  const go = async () => {
    setBusy(true); setErr('')
    try {
      const { ticket } = await api.post(`/admin/backups/${encodeURIComponent(b.name)}/ticket`, { code })
      const a = document.createElement('a'); a.href = `/api/admin/backups/download?ticket=${encodeURIComponent(ticket)}`; a.download = b.name; document.body.appendChild(a); a.click(); a.remove()
      toast('Téléchargement lancé'); onClose()
    } catch (e) { setErr(e.message); setBusy(false) }
  }
  return (
    <Modal small title="Télécharger la sauvegarde" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={code.replace(/\s/g, '').length < 6} onClick={go}><Download size={14} /> Télécharger</Btn></>}>
      <div className="col"><div className="mono small">{b.name} · {fmtSize(b.size)}</div>
        {!b.encrypted && <div className="banner warn">Cette sauvegarde n'est <b>pas protégée par un mot de passe</b> : quiconque obtient le fichier peut lire la base. Conservez-la en lieu sûr.</div>}
        <Field label="Code de double authentification" help="Le code à 6 chiffres de votre application d'authentification."><input className="input mono" autoFocus style={{ maxWidth: 180, letterSpacing: 3, textAlign: 'center' }} inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && code.replace(/\s/g, '').length >= 6) go() }} /></Field>
        {err && <div className="banner err">{err}</div>}</div>
    </Modal>
  )
}

// Restaurer : lire d'abord (aperçu, rien n'est touché), choisir quoi remettre, confirmer avec le nom et un code 2FA. Le hub fait une sauvegarde de l'état actuel, puis redémarre.
function RestoreModal({ b, d, onClose }) {
  const [pw, setPw] = useState(''); const [info, setInfo] = useState(null); const [err, setErr] = useState(''); const [busy, setBusy] = useState(false)
  const [sel, setSel] = useState(new Set()); const [code, setCode] = useState(''); const [typed, setTyped] = useState(''); const [phase, setPhase] = useState('form'); const [done, setDone] = useState(null)
  const read = async () => {
    setBusy(true); setErr('')
    try { const r = await api.post(`/admin/backups/${encodeURIComponent(b.name)}/inspect`, { passphrase: pw }); setInfo(r); setSel(new Set(Object.keys(r.manifest.components))) } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  useEffect(() => { if (!b.encrypted) read() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const go = async () => {
    setBusy(true); setErr('')
    try {
      const { job } = await api.post(`/admin/backups/${encodeURIComponent(b.name)}/restore`, { code, passphrase: pw, components: [...sel], confirm: typed })
      const r = await waitJob(job); setDone(r); setPhase('restart')
      // le hub redémarre : on attend qu'il disparaisse puis revienne, et on recharge la page
      for (let i = 0; i < 90; i++) { await sleep(2000); try { const h = await fetch('/api/health', { cache: 'no-store' }); if (h.ok && i > 1) { if (!r.archiveKey) location.reload(); return } } catch { /* hub arrêté : normal */ } }
    } catch (e) { setErr(e.message); setBusy(false) }
  }
  const ready = info && sel.size && code.replace(/\s/g, '').length >= 6 && typed === b.name
  if (phase === 'restart') return (
    <Modal small title="Restauration préparée" onClose={() => location.reload()} footer={<Btn kind="primary" onClick={() => location.reload()}>Recharger la page</Btn>}>
      <div className="col"><div className="banner ok"><ShieldCheck size={16} /> Les fichiers sont prêts : le hub redémarre et les met en place. Cette page se recharge toute seule quand il est de retour.</div>
        {done?.archiveKey && <div className="banner warn col" style={{ gap: 6 }}><div><b>Cette sauvegarde vient d'une installation avec une autre clé de chiffrement.</b> Tant que votre <span className="mono">.env</span> n'a pas cette valeur, les secrets de la base resteront illisibles. Mettez-la dans <span className="mono">ENCRYPTION_KEY</span>, puis relancez avec <span className="mono">docker compose up -d</span> :</div>
          <div className="codebox slim"><CopyBtn text={done.archiveKey} /><pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{done.archiveKey}</pre></div></div>}
        <div className="muted small">L'état d'avant est gardé dans le dossier <span className="mono">avant-restauration</span> du hub, et une sauvegarde « avant-restauration » vient d'être ajoutée à la liste : vous pouvez revenir en arrière.</div></div>
    </Modal>)
  return (
    <Modal wide title={`Restaurer · ${b.label || b.name}`} onClose={busy ? () => {} : onClose} footer={<><Btn onClick={onClose} disabled={busy}>Annuler</Btn>{info && <Btn kind="danger" busy={busy} disabled={!ready} onClick={go}><RotateCcw size={14} /> Restaurer et redémarrer</Btn>}</>}>
      <div className="col" style={{ gap: 12 }}>
        <div className="mono small">{b.name} · {fmtSize(b.size)}</div>
        {!info && b.encrypted && <div className="col"><Field label="Mot de passe de la sauvegarde"><input className="input" type="password" autoFocus autoComplete="off" value={pw} onChange={(e) => setPw(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && pw) read() }} /></Field><div><Btn kind="primary" busy={busy} disabled={!pw} onClick={read}>Lire la sauvegarde</Btn></div></div>}
        {!info && !b.encrypted && busy && <Loading />}
        {info && <>
          <div className="small muted">Créée le {fmtDate(info.manifest.createdAt * 1000)}{info.manifest.createdBy ? ` par ${info.manifest.createdBy}` : ''} · version {info.manifest.app || '?'} (ce hub : {info.currentApp}).</div>
          <div className="col" style={{ gap: 6 }}><b>Ce qui sera remplacé</b>
            {Object.entries(info.manifest.components).map(([k, v]) => <label key={k} className="row" style={{ gap: 8 }}><input type="checkbox" checked={sel.has(k)} onChange={(e) => setSel((s) => { const n = new Set(s); e.target.checked ? n.add(k) : n.delete(k); return n })} /><span className="grow">{d.components.find((c) => c.id === k)?.label || k}</span><span className="muted small">{v.files} fichier{v.files > 1 ? 's' : ''} · {fmtSize(v.bytes)}</span></label>)}</div>
          {info.keyStatus === 'different' && <div className="banner warn"><ShieldAlert size={16} /><span>La clé de chiffrement de cette sauvegarde est <b>différente</b> de celle de ce hub. {info.manifest.includesKey ? 'Elle est incluse : elle vous sera montrée à la fin.' : 'Sans la clé d\'origine, les secrets de la base resteront illisibles.'}</span></div>}
          {info.keyStatus === 'unknown' && <div className="banner info">La clé d'origine n'est pas connue pour cette sauvegarde (ancienne version). Si elle venait d'un autre hub, ses secrets pourraient rester illisibles.</div>}
          <div className="banner warn col" style={{ gap: 4 }}><div><b>Ce que cela fait :</b> une sauvegarde de l'état actuel est faite d'abord, puis les éléments cochés sont remplacés au prochain démarrage et <b>le hub redémarre</b> (quelques secondes sans service). Tout ce qui a changé depuis cette sauvegarde, utilisateurs et réglages compris, sera perdu.</div></div>
          <Field label="Code de double authentification"><input className="input mono" style={{ maxWidth: 180, letterSpacing: 3, textAlign: 'center' }} inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} /></Field>
          <Field label="Pour confirmer, retapez le nom de la sauvegarde"><input className="input mono" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={b.name} autoComplete="off" spellCheck={false} /></Field>
        </>}
        {err && <div className="banner err">{err}</div>}
      </div>
    </Modal>
  )
}
