import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useNode } from '../node.jsx'
import { Btn, Field, Modal, Seg, useUi } from './ui.jsx'
import { WORKER_TYPES } from '../lib/worker.js'
import WorkerSetup from './WorkerSetup.jsx'

// Assistant « Nouveau nœud » : 1) nom, 2) installation du worker avec détection de la connexion.
export default function NewNode({ onClose }) {
  const { toast } = useUi(); const { setNode, reload } = useNode(); const nav = useNavigate()
  const meta = useQuery({ queryKey: ['meta'], queryFn: () => api.get('/meta'), staleTime: 60000 })
  const [f, setF] = useState({ name: '', description: '', dnsTarget: '', eps: null, res: null, modules: 'traefik' }); const set = (k, v) => setF({ ...f, [k]: v })
  const eps = f.eps ?? (meta.data?.entrypoints?.length ? meta.data.entrypoints.join(', ') : 'web, websecure')
  const res = f.res ?? (meta.data?.certResolvers || []).join(', ')
  const tr = f.modules !== 'docker'; const list = (s) => s.split(/[,\s;]+/).map((x) => x.trim()).filter(Boolean)
  const [made, setMade] = useState(null); const [busy, setBusy] = useState(false)
  const create = () => { setBusy(true); api.post('/nodes', { name: f.name, description: f.description, dnsTarget: f.dnsTarget, ...(tr ? { entrypoints: list(eps), certResolvers: list(res) } : {}) }).then((r) => { setMade(r); reload() }).catch((e) => toast(e.message, 'err')).finally(() => setBusy(false)) }
  const open = () => { setNode(made.id); onClose(); nav('/node') }
  if (!made) return (
    <Modal title="Nouveau nœud" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={!f.name.trim()} onClick={create}>Créer et continuer</Btn></>}>
      <div className="col gap-lg">
        <div className="banner info" style={{ margin: 0 }}>Un nœud est une autre machine gérée depuis cette interface : son Traefik, ses conteneurs Docker, ou les deux. Un petit programme (le « worker ») sera installé dessus.</div>
        <Field label="Que doit gérer ce nœud ?" help={WORKER_TYPES.find((t) => t.id === f.modules)?.hint}><Seg value={f.modules} onChange={(m) => set('modules', m)} options={WORKER_TYPES} /></Field>
        <Field label="Nom du nœud" help="Ex : Maison, Serveur Paris, Client X."><input className="input" autoFocus value={f.name} maxLength={60} onChange={(e) => set('name', e.target.value)} onKeyDown={(e) => e.key === 'Enter' && f.name.trim() && create()} /></Field>
        <Field label="Description" help="Facultatif."><input className="input" value={f.description} maxLength={200} onChange={(e) => set('description', e.target.value)} /></Field>
        {tr && <Field label="Adresse IP publique de cette machine" help="Facultatif : proposée quand vous créez un enregistrement DNS pour un site de ce nœud."><input className="input mono" value={f.dnsTarget} onChange={(e) => set('dnsTarget', e.target.value.trim())} placeholder="203.0.113.10" /></Field>}
        {tr && <div className="fgrid">
          <Field label="Entrypoints de ce Traefik" help="Les ports d'écoute, tels que nommés dans sa configuration. Ex : web (80), websecure (443)."><input className="input mono" value={eps} onChange={(e) => set('eps', e.target.value)} /></Field>
          <Field label="Résolveur de certificats" help="Le nom (certificatesresolvers.NOM) dans le Traefik distant, pour le HTTPS des sites. Vide = sites sans certificat automatique."><input className="input mono" value={res} onChange={(e) => set('res', e.target.value)} placeholder="letsencrypt" /></Field>
        </div>}
        <div className="muted small">{tr ? 'Ces réglages sont modifiables à tout moment dans « Ce nœud → Réglages ».' : 'Le type de worker se change plus tard en réinstallant le worker.'}</div>
      </div>
    </Modal>)
  return (
    <Modal wide title={`Installer le worker de « ${made.name} »`} onClose={onClose} footer={<><Btn onClick={onClose}>Plus tard</Btn><Btn kind="primary" onClick={open}>Ouvrir ce nœud</Btn></>}>
      <WorkerSetup modules={f.modules} node={made} token={made.token} expires={made.tokenExpires} onOnline={() => { toast('Worker connecté'); reload() }} />
    </Modal>)
}
