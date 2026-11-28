import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { TriangleAlert } from 'lucide-react'
import { api } from '../api.js'
import { Btn, Field, Modal, useUi } from './ui.jsx'

const FOLDER_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$/

// Renommer un projet : le nom affiché (dans Armadok seulement) et/ou le dossier sur le disque (qui peut changer le nom du projet pour Compose).
export default function ProjectRename({ id, project: p, onClose }) {
  const { toast } = useUi(); const qc = useQueryClient()
  const [shown, setShown] = useState(p.displayName || ''); const [folder, setFolder] = useState(p.folder)
  const [plan, setPlan] = useState(null); const [checking, setChecking] = useState(false); const [accept, setAccept] = useState(false); const [busy, setBusy] = useState(false); const [err, setErr] = useState('')
  const canFolder = p.perms.folder; const canName = p.perms.rename
  const folderChanged = canFolder && folder.trim() !== p.folder
  const validFolder = FOLDER_RE.test(folder.trim())

  useEffect(() => {   // ce que changerait le nouveau dossier : vérifié dès que la saisie se stabilise
    setPlan(null); setAccept(false)
    if (!folderChanged || !validFolder) return
    let gone = false; setChecking(true)
    const t = setTimeout(() => api.get(`/projects/${id}/rename?folder=${encodeURIComponent(folder.trim())}`).then((r) => { if (!gone) setPlan(r) }).catch((e) => { if (!gone) setErr(e.message) }).finally(() => { if (!gone) setChecking(false) }), 400)
    return () => { gone = true; clearTimeout(t) }
  }, [folder]) // eslint-disable-line react-hooks/exhaustive-deps

  const blocked = folderChanged && (!validFolder || checking || !plan || plan.containers.length > 0 || (plan.volumes.length > 0 && !accept))
  const nameChanged = canName && shown.trim() !== (p.displayName || '')
  const changed = folderChanged || nameChanged
  const go = async () => {
    setBusy(true); setErr('')
    try {
      await api.put(`/projects/${id}/rename`, { ...(nameChanged ? { displayName: shown.trim() } : {}), ...(folderChanged ? { folder: folder.trim(), acceptVolumes: accept } : {}) })
      qc.invalidateQueries({ queryKey: ['projects'] }); qc.invalidateQueries({ queryKey: ['project', String(id)] })
      toast('Projet renommé'); onClose()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <Modal small title="Renommer le projet" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={!changed || blocked} onClick={go}>Renommer</Btn></>}>
      <div className="col">
        <Field label="Nom affiché" help={canName ? 'Le nom montré dans Armadok. Vide : le nom du dossier. Rien ne change sur le disque.' : 'Droit « Renommer un projet » requis.'}>
          <input className="input" autoFocus disabled={!canName} maxLength={80} value={shown} placeholder={folder.trim() || p.folder} onChange={(e) => setShown(e.target.value)} /></Field>
        {canFolder && <Field label="Dossier" help={`Le dossier sur le disque, au même endroit : ${p.path.slice(0, p.path.length - p.folder.length)}…`}>
          <input className="input mono" maxLength={61} value={folder} onChange={(e) => { setErr(''); setFolder(e.target.value) }} spellCheck={false} autoComplete="off" /></Field>}
        {folderChanged && !validFolder && <div className="banner err" style={{ margin: 0 }}>Nom de dossier invalide : lettres, chiffres, « - », « _ » et « . », sans espace.</div>}
        {plan && plan.containers.length > 0 && <div className="banner err" style={{ margin: 0 }}><TriangleAlert size={16} /><span>Des conteneurs de ce projet existent encore ({plan.containers.slice(0, 4).join(', ')}{plan.containers.length > 4 ? '…' : ''}). Faites d'abord <b>Down</b>, puis renommez et relancez.</span></div>}
        {plan && !plan.containers.length && plan.composeBefore !== plan.composeAfter && <div className="banner info" style={{ margin: 0 }}>Le nom du projet pour Compose suit le dossier : il passera de <b className="mono">{plan.composeBefore}</b> à <b className="mono">{plan.composeAfter}</b>. Les conteneurs et réseaux prendront ce nouveau nom au prochain lancement.</div>}
        {plan && !plan.containers.length && plan.composeBefore === plan.composeAfter && <div className="muted small">Le nom Compose (<span className="mono">{plan.composeBefore}</span>) est fixé par le fichier compose : il ne change pas.</div>}
        {plan && !plan.containers.length && plan.volumes.length > 0 && <div className="banner warn col" style={{ margin: 0, gap: 6 }}>
          <div><TriangleAlert size={14} /> Ces volumes portent l'ancien nom et <b>ne seront plus repris</b> : le projet repartira avec des volumes neufs et vides.</div>
          <div className="mono small">{plan.volumes.join(', ')}</div>
          <div className="muted small">Pour les garder, ajoutez plutôt <span className="mono">name: {plan.composeBefore}</span> en tête du fichier compose avant de renommer.</div>
          <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} /><span>J'ai compris, renommer quand même</span></label></div>}
        {err && <div className="banner err" style={{ margin: 0 }}>{err}</div>}
      </div>
    </Modal>)
}
