import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { api } from '../api.js'
import { Btn, Field, Modal, useUi } from './ui.jsx'

const LEVELS = [['view', 'Lecture seule'], ['edit', 'Modifier les enregistrements']]

export default function ShareModal({ domain, onClose }) {
  const { toast } = useUi(); const qc = useQueryClient()
  const acc = useQuery({ queryKey: ['access', domain], queryFn: () => api.get(`/domains/${domain}/access`) })
  const dir = useQuery({ queryKey: ['directory'], queryFn: () => api.get('/users-directory') })
  const [grants, setGrants] = useState(null)
  const [busy, setBusy] = useState(false)
  const [found, setFound] = useState([]); const [typed, setTyped] = useState('')
  useEffect(() => { if (acc.data) setGrants(acc.data.grants.map((g) => ({ userId: g.userId, level: g.level, types: g.types || '', subs: g.subs || '', noDelete: !!g.noDelete, expiresAt: g.expiresAt || null, keep: !!g.expiresAt }))) }, [acc.data])
  const name = (id) => dir.data?.find((u) => u.id === id)?.username || found.find((u) => u.id === id)?.username || '?'
  const free = (dir.data || []).filter((u) => !grants?.some((g) => g.userId === u.id))
  const lookup = () => api.get('/users-lookup?username=' + encodeURIComponent(typed.trim())).then((u) => { if (!grants.some((g) => g.userId === u.id)) { setFound([...found, u]); setGrants([...grants, { userId: u.id, level: 'view' }]) } setTyped('') }).catch((e) => toast(e.message, 'err'))
  const save = async () => {
    setBusy(true)
    try { await api.put(`/domains/${domain}/access`, { grants: grants.map(({ keep, dur, custom, ...g }) => ({ ...g, expiresAt: dur === undefined || dur === 'keep' ? (g.expiresAt || null) : dur === '' ? null : dur === 'custom' ? (custom ? new Date(custom).getTime() / 1000 : null) : Math.floor(Date.now() / 1000) + Number(dur) })) }); qc.invalidateQueries({ queryKey: ['domains'] }); toast('Accès mis à jour'); onClose() } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  return (
    <Modal title={`Qui a accès à ${domain} ?`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} onClick={save}>Enregistrer</Btn></>}>
      <div className="col">
        <div className="muted small">Vous et les administrateurs concernés gardent l'accès de gestion. Les droits sur les sites Traefik se règlent séparément dans « Équipe ».</div>
        {grants?.length === 0 && <div className="empty" style={{ padding: 20 }}>Personne d'autre n'a accès pour l'instant.</div>}
        {grants?.map((g, i) => (
          <div className="col" key={g.userId} style={{ gap: 6, paddingBottom: 8, borderBottom: '1px solid var(--border)' }}>
          <div className="row"><b className="grow">{name(g.userId)}</b>
            <select className="select" style={{ width: 'auto' }} title="Lecture seule : voit les enregistrements et les sites du domaine. Modifier : ajoute, change et supprime les enregistrements, et crée des sites sur ce domaine." value={g.level} onChange={(e) => setGrants(grants.map((x, j) => (j === i ? { ...x, level: e.target.value } : x)))}>{LEVELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
            <Btn kind="ghost" icon onClick={() => setGrants(grants.filter((_, j) => j !== i))}><X size={14} /></Btn></div>
          {g.level === 'edit' && <details className="group" open={!!(g.types || g.subs || g.noDelete || g.expiresAt)}><summary>Restrictions et durée</summary>
            <div className="inner"><div className="fgrid">
              <Field label="Types d'enregistrements autorisés" help="Ex. A, CNAME, TXT. Vide = tous."><input className="input mono" value={g.types} onChange={(e) => setGrants(grants.map((x, j) => (j === i ? { ...x, types: e.target.value } : x)))} placeholder="A, CNAME" /></Field>
              <Field label="Sous-domaines autorisés" help="Ex. app, *.dev (le motif * est accepté). Vide = tous."><input className="input mono" value={g.subs} onChange={(e) => setGrants(grants.map((x, j) => (j === i ? { ...x, subs: e.target.value } : x)))} placeholder="app, *.dev" /></Field>
              <Field label="Durée de l'accès"><div className="row"><select className="select" value={g.dur ?? (g.expiresAt ? 'keep' : '')} onChange={(e) => setGrants(grants.map((x, j) => (j === i ? { ...x, dur: e.target.value } : x)))}>{g.expiresAt && <option value="keep">Garder l'échéance actuelle</option>}<option value="">Sans limite</option><option value="3600">1 heure</option><option value="86400">24 heures</option><option value="604800">7 jours</option><option value="2592000">30 jours</option><option value="custom">Date précise…</option></select>{g.dur === 'custom' && <input className="input" type="datetime-local" value={g.custom || ''} onChange={(e) => setGrants(grants.map((x, j) => (j === i ? { ...x, custom: e.target.value } : x)))} />}</div></Field>
              <label className="row small" style={{ gap: 8, alignSelf: 'end' }}><input type="checkbox" checked={g.noDelete} onChange={(e) => setGrants(grants.map((x, j) => (j === i ? { ...x, noDelete: e.target.checked } : x)))} /> Ne peut pas supprimer d'enregistrements</label>
            </div></div></details>}
          </div>))}
        <Field label="Ajouter quelqu'un" help="Choisissez parmi les personnes que vous connaissez, ou tapez le nom d'utilisateur exact.">
          <select className="select" value="" onChange={(e) => e.target.value && setGrants([...grants, { userId: Number(e.target.value), level: 'view' }])}>
            <option value="">{free.length ? '— choisir un utilisateur —' : 'Aucun autre utilisateur'}</option>{free.map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}</select>
          <div className="row" style={{ marginTop: 6 }}><input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && typed.trim() && lookup()} placeholder="Nom d'utilisateur exact" /><Btn disabled={!typed.trim()} onClick={lookup}>Ajouter</Btn></div>
        </Field>
      </div>
    </Modal>
  )
}
