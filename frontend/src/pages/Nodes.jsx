import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Boxes, DoorOpen, ShieldAlert, ShieldCheck, Zap } from 'lucide-react'
import { api } from '../api.js'
import { useAuth } from '../auth.jsx'
import { useNode } from '../node.jsx'
import { Badge, Btn, Empty, Field, Info, Loading, Modal, Tabs, useUi } from '../components/ui.jsx'
import Backups from '../components/Backups.jsx'
import NodeBackups from '../components/NodeBackups.jsx'
import Links from '../components/Links.jsx'
import { CONN, NodeAvatar } from '../components/NodeSwitcher.jsx'
import { Countdown } from '../components/Countdown.jsx'
import { fmtDate } from '../lib/util.js'

// Vue de la plateforme : deux onglets — les nœuds (accès d'urgence, actions rapides) et les sauvegardes complètes du hub.
export default function Nodes() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'backups' ? 'backups' : 'nodes'
  return (
    <>
      <div className="page-head"><div><h1>Tous les nœuds</h1><p>{tab === 'backups' ? 'Sauvegardes : le hub en entier, ou les fichiers d\'un nœud au choix (selon votre accès).' : 'Vue de la plateforme. Entrer chez un utilisateur exige un motif et une double authentification, et son propriétaire en est toujours informé.'}</p></div></div>
      <div className="row" style={{ marginBottom: 14 }}><Tabs value={tab} onChange={(t) => setParams(t === 'nodes' ? {} : { tab: t })} tabs={[{ id: 'nodes', label: 'Nœuds et liaisons' }, { id: 'backups', label: 'Backup et sauvegardes' }]} /></div>
      {tab === 'backups' ? <BackupsTab /> : <><NodesList /><Links /></>}
    </>
  )
}

// Choix de la cible : le hub en entier, ou un nœud. Seuls les nœuds où la personne a le droit « Sauvegarder le nœud » sont proposés ; les autres sont grisés
// (pour un nœud distant qui n'est pas le sien, il reste l'accès d'urgence de l'onglet « Nœuds »).
function BackupsTab() {
  const { nodes: mine } = useNode()
  const q = useQuery({ queryKey: ['admin-nodes'], queryFn: () => api.get('/admin/nodes') })
  const [params, setParams] = useSearchParams(); const target = params.get('node') || 'hub'
  const set = (id) => setParams(id === 'hub' ? { tab: 'backups' } : { tab: 'backups', node: id }, { replace: true })
  if (q.isLoading) return <Loading />
  const list = (q.data || []).filter((n) => !n.deletedAt)
  const access = (n) => mine.find((m) => m.id === n.id && (m.caps || []).includes('node.backup'))
  const cur = target === 'hub' ? null : list.find((n) => n.id === target && access(n))
  return (
    <>
      <div className="row wrap" style={{ gap: 8, marginBottom: 14, alignItems: 'center' }}>
        <span className="muted small">Sauvegarder :</span>
        <Btn small kind={!cur ? 'primary' : undefined} onClick={() => set('hub')}><Boxes size={13} /> Le hub (complet)</Btn>
        {list.map((n) => {
          const a = access(n)
          return <Btn key={n.id} small kind={cur?.id === n.id ? 'primary' : undefined} disabled={!a}
            title={a ? `Fichiers de la machine du nœud ${n.name}` : n.kind === 'remote' ? 'Vous n\'avez pas le droit « Sauvegarder le nœud » ici : entrez d\'abord par l\'accès d\'urgence (onglet Nœuds).' : 'Droit « Sauvegarder le nœud » manquant'}
            onClick={() => set(n.id)}><NodeAvatar node={n} size={16} /> {n.name}</Btn>
        })}
      </div>
      {cur ? <NodeBackups key={cur.id} node={cur} /> : <Backups />}
    </>
  )
}

function NodesList() {
  const { user } = useAuth(); const { nodes: mine, setNode, reload } = useNode(); const nav = useNavigate(); const qc = useQueryClient(); const { toast } = useUi()
  const q = useQuery({ queryKey: ['admin-nodes'], queryFn: () => api.get('/admin/nodes') })
  const [em, setEm] = useState(null); const [act, setAct] = useState(null)
  const refresh = () => { qc.invalidateQueries({ queryKey: ['admin-nodes'] }); reload() }
  const open = (id, to = '/') => { setNode(id); nav(to) }
  const end = (n) => api.post(`/admin/nodes/${n.id}/emergency/end`).then(() => { toast('Accès d\'urgence terminé'); refresh() }).catch((e) => toast(e.message, 'err'))
  if (q.isLoading) return <Loading />
  return (
    <>
      {!user.twoFactor && <div className="banner"><ShieldAlert size={18} /><span className="grow">Pour accéder au nœud d'un autre utilisateur ou agir dessus, <b>activez d'abord la double authentification</b> de votre compte.</span>
        <Btn small kind="primary" onClick={() => window.dispatchEvent(new Event('open-profile'))}><ShieldCheck size={13} /> Activer</Btn></div>}
      <div className="card flush"><table className="t"><thead><tr><th>Nœud</th><th>Propriétaire</th><th>État<Info>En ligne : le worker est connecté. Hors ligne : il ne répond plus. Suspendu : bloqué par un super-admin (les membres voient un message).</Info></th><th>Membres</th><th>Domaines</th><th /></tr></thead>
        <tbody>{q.data.map((n) => {
          const member = mine.find((m) => m.id === n.id && !m.emergency)
          return (
            <tr key={n.id} style={{ opacity: n.deletedAt ? .6 : 1 }}>
              <td><div className="row"><NodeAvatar node={n} size={30} /><div><b>{n.name}</b><div className="muted small">{n.kind === 'local' ? 'cette machine' : 'nœud distant'}</div></div></div></td>
              <td>{n.owners.length ? n.owners.map((o) => <Badge key={o.id} kind={o.disabled ? 'err' : ''}>{o.username}{o.disabled ? ' · banni' : ''}</Badge>) : <span className="muted">—</span>}</td>
              <td><div className="row wrap" style={{ gap: 4 }}>
                {n.deletedAt ? <Badge kind="err">supprimé · purge le {fmtDate(n.purgeAt * 1000).split(' ')[0]}</Badge> : <>
                  {n.kind === 'remote' && <Badge kind={CONN[n.connection][0]}>{CONN[n.connection][1]}</Badge>}
                  {n.status === 'suspended' && <Badge kind="warn">suspendu</Badge>}
                  {n.emergency && <Badge kind="err"><ShieldAlert size={11} /> urgence <Countdown until={n.emergency.expires} onEnd={refresh} /></Badge>}</>}
              </div></td>
              <td>{!n.deletedAt && (mine.some((m) => m.id === n.id && !m.emergency) || n.kind === 'local' || n.emergency) ? <button type="button" className="linkcell" style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit' }} title="Voir les membres de ce nœud" onClick={() => open(n.id, '/node?tab=members')}>{n.members}</button> : n.members}</td><td>{!n.deletedAt && (mine.some((m) => m.id === n.id && !m.emergency) || n.kind === 'local' || n.emergency) ? <button type="button" className="linkcell" style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit' }} title="Voir les domaines de ce nœud" onClick={() => open(n.id, '/node?tab=domains')}>{n.domains}</button> : n.domains}</td>
              <td className="actions">
                {!n.deletedAt && (member || n.kind === 'local' || n.emergency) && <Btn small title="Ouvrir ce nœud dans l'interface" onClick={() => open(n.id)}><DoorOpen size={13} /> Ouvrir</Btn>}
                {!n.deletedAt && n.kind === 'remote' && !member && !n.emergency && <Btn small kind="danger" title="Entrer dans un nœud qui n'est pas le vôtre, en cas d'urgence : motif obligatoire, double authentification requise, 30 minutes, et son propriétaire en est averti à sa prochaine connexion." onClick={() => setEm(n)}><ShieldAlert size={13} /> Accès d'urgence</Btn>}
                {n.emergency && <Btn small kind="ghost" title="Quitter ce nœud maintenant et mettre fin à l'accès d'urgence." onClick={() => end(n)}>Terminer</Btn>}
                {n.kind === 'remote' && <Btn small kind="ghost" title="Combinaisons prêtes à l'emploi : suspendre un nœud, bannir son propriétaire, tout supprimer. Aperçu des conséquences avant de valider." onClick={() => setAct(n)}><Zap size={13} /> Actions rapides</Btn>}
              </td>
            </tr>)
        })}</tbody></table></div>
      {q.data.length === 0 && <div className="card"><Empty><Boxes size={30} /><div>Aucun nœud.</div></Empty></div>}
      {em && <EmergencyModal node={em} onClose={() => setEm(null)} onDone={(id) => { setEm(null); refresh(); open(id) }} />}
      {act && <ActionModal node={act} onClose={() => setAct(null)} onDone={() => { setAct(null); refresh() }} />}
    </>
  )
}

function EmergencyModal({ node, onClose, onDone }) {
  const { toast } = useUi(); const [reason, setReason] = useState(''); const [code, setCode] = useState(''); const [busy, setBusy] = useState(false); const [err, setErr] = useState('')
  const go = () => { setBusy(true); setErr(''); api.post(`/admin/nodes/${node.id}/emergency`, { reason, code }).then(() => { toast('Accès d\'urgence ouvert pour 30 minutes'); onDone(node.id) }).catch((e) => setErr(e.message)).finally(() => setBusy(false)) }
  return (
    <Modal title={`Accès d'urgence · ${node.name}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="danger" busy={busy} disabled={reason.trim().length < 10 || code.replace(/\s/g, '').length < 6} onClick={go}>Entrer sur le nœud</Btn></>}>
      <div className="col gap-lg">
        <div className="banner err" style={{ margin: 0 }}><ShieldAlert size={18} /><div>Vous allez intervenir sur le nœud de {node.owners.map((o) => o.username).join(', ') || 'un utilisateur'}. <b>Il en sera informé</b> à sa prochaine connexion, avec votre motif et la liste de vos actions. La session dure <b>30 minutes</b>.</div></div>
        <Field label="Motif" help="Obligatoire (10 caractères minimum). Il sera montré au propriétaire."><textarea className="textarea" style={{ fontFamily: 'inherit' }} rows={3} autoFocus value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} placeholder="Ex : le propriétaire signale une panne et m'a demandé d'intervenir" /></Field>
        <Field label="Code de double authentification" help="Le code à 6 chiffres de votre application d'authentification."><input className="input mono" style={{ maxWidth: 180, letterSpacing: 3, textAlign: 'center' }} value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" placeholder="000000" /></Field>
        {err && <div className="banner err" style={{ margin: 0 }}>{err}</div>}
      </div>
    </Modal>)
}

const PRESETS = [
  ['suspend', 'Suspendre', 'Coupe le worker et ferme le nœud à ses membres. Réversible.', false, (n) => n.status === 'active'],
  ['resume', 'Reprendre', 'Rouvre le nœud suspendu.', false, (n) => n.status === 'suspended'],
  ['revoke_worker', 'Couper le worker', 'Déconnecte et révoque le worker. Le Traefik distant continue de tourner ; le propriétaire devra réinstaller un worker.', true, () => true],
  ['ban_owner', 'Bannir le propriétaire', 'Désactive son compte immédiatement et suspend ses nœuds.', true, (n) => n.owners.length > 0],
  ['delete', 'Supprimer le nœud', 'Le retire pour tous ; récupérable pendant 30 jours.', true, () => true],
  ['delete_and_ban', 'Supprimer et bannir', 'Supprime le nœud et bannit son propriétaire.', true, (n) => n.owners.length > 0],
  ['remove_quota', 'Retirer le droit de créer des nœuds', 'Ses nœuds existants ne sont pas touchés.', false, (n) => n.owners.length > 0],
  ['take_ownership', 'Reprendre la propriété', 'Vous devenez propriétaire ; l\'ancien propriétaire devient lecteur.', true, () => true],
]
const DELETED = [['restore', 'Restaurer', 'Remet le nœud en service.', false], ['purge', 'Purger définitivement', 'Irréversible : le nœud et ses rattachements disparaissent.', true]]

function ActionModal({ node, onClose, onDone }) {
  const { toast } = useUi()
  const imp = useQuery({ queryKey: ['impact', node.id], queryFn: () => api.get(`/admin/nodes/${node.id}/impact`) })
  const [action, setAction] = useState(''); const [note, setNote] = useState(''); const [typed, setTyped] = useState(''); const [code, setCode] = useState(''); const [busy, setBusy] = useState(false); const [err, setErr] = useState('')
  const presets = node.deletedAt ? DELETED : PRESETS.filter((p) => p[4](node))
  const chosen = presets.find((p) => p[0] === action)
  const go = () => { setBusy(true); setErr(''); api.post(`/admin/nodes/${node.id}/actions`, { action, code, note }).then(() => { toast(`${chosen[1]} : fait`); onDone() }).catch((e) => setErr(e.message)).finally(() => setBusy(false)) }
  const i = imp.data
  return (
    <Modal wide title={`Actions rapides · ${node.name}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind={chosen?.[3] ? 'danger' : 'primary'} busy={busy} disabled={!chosen || typed !== node.name || code.replace(/\s/g, '').length < 6} onClick={go}>{chosen ? chosen[1] : 'Choisir une action'}</Btn></>}>
      <div className="col gap-lg">
        {imp.isLoading ? <Loading /> : i && <div className="card" style={{ background: 'var(--panel2)' }}><h3 style={{ marginBottom: 8 }}>Ce qui est concerné</h3>
          <div className="small col" style={{ gap: 3 }}>
            <div><b>Propriétaire</b> : {i.owners.map((o) => o.username).join(', ') || '—'} {i.owners.some((o) => o.disabled) && <Badge kind="err">banni</Badge>}</div>
            <div><b>Membres</b> ({i.memberNames.length}) : {i.memberNames.join(', ') || '—'}</div>
            <div><b>Domaines</b> ({i.domainNames.length}) : {i.domainNames.join(', ') || '—'}</div>
            {i.otherNodes.length > 0 && <div><b>Autres nœuds de son propriétaire</b> : {i.otherNodes.join(', ')}</div>}
            {i.domainNames.length > 0 && <div className="muted">Les enregistrements DNS de ces domaines ne sont jamais modifiés par ces actions : vérifiez-les ensuite si le nœud ne doit plus répondre.</div>}
          </div></div>}
        <div className="role-grid">{presets.map(([id, label, help, danger]) => (
          <button type="button" key={id} className={'role-card ' + (action === id ? 'sel' : '')} onClick={() => setAction(id)}><b style={{ color: danger ? 'var(--err)' : undefined }}>{label}</b><span className="muted small">{help}</span></button>))}</div>
        {chosen && <>
          <Field label="Message au propriétaire (facultatif)" help="Ajouté à l'avis qu'il verra à sa prochaine connexion."><input className="input" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} /></Field>
          <div className="fgrid">
            <Field label={`Pour confirmer, tapez « ${node.name} »`}><input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} /></Field>
            <Field label="Code de double authentification"><input className="input mono" style={{ letterSpacing: 3, textAlign: 'center' }} value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" placeholder="000000" /></Field></div>
          {err && <div className="banner err" style={{ margin: 0 }}>{err}</div>}
        </>}
      </div>
    </Modal>)
}
