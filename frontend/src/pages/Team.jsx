import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Pencil, Plus, Trash2, UserCog } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Field, Info, Loading, Modal, Switch, useUi } from '../components/ui.jsx'
import { fmtDate } from '../lib/util.js'

const empty = { username: '', password: '', role: 'user', canAddDomains: false, maxNodes: 0 }
const QUOTAS = [[0, 'Aucun'], [1, '1'], [2, '2'], [3, '3'], [5, '5'], [10, '10'], [-1, 'Illimité']]

export default function Team() {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const users = useQuery({ queryKey: ['users'], queryFn: () => api.get('/users') })
  const [edit, setEdit] = useState(null); const [pw, setPw] = useState(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['users'] })
  const remove = async (u) => { if (await confirm({ title: `Supprimer ${u.username} ?`, message: 'Ses comptes OVH ajoutés, ses domaines et ses partages sont supprimés avec lui.', confirmLabel: 'Supprimer' })) api.del(`/users/${u.id}`).then(() => { refresh(); toast('Utilisateur supprimé') }).catch((e) => toast(e.message, 'err')) }
  const resetMfa = async (u) => { if (await confirm({ title: `Réinitialiser la 2FA de ${u.username} ?`, message: 'Il pourra se connecter avec son mot de passe seul et la réactiver ensuite.', confirmLabel: 'Réinitialiser' })) api.post(`/users/${u.id}/2fa/reset`).then(() => { refresh(); toast('2FA réinitialisée') }).catch((e) => toast(e.message, 'err')) }
  if (users.isLoading) return <Loading />
  return (
    <>
      <div className="page-head"><div><h1>Équipe</h1><p>Qui peut se connecter, et ce que chacun peut faire.</p></div><Btn kind="primary" className="right" onClick={() => setEdit({ ...empty })}><Plus size={15} /> Nouvel utilisateur</Btn></div>
      <div className="card flush"><table className="t"><thead><tr><th>Utilisateur</th><th>Rôle<Info>Administrateur : gère les comptes et voit tous les nœuds. Utilisateur : n'accède qu'à ce qu'on lui partage ou à ses propres nœuds.</Info></th><th>2FA<Info>Double authentification (code à 6 chiffres). Facultative, mais obligatoire pour l'accès d'urgence d'un administrateur au nœud d'un autre.</Info></th><th>Peut ajouter des domaines<Info>Peut brancher ses propres comptes DNS (OVH, DuckDNS) et domaines, partout. Sans ce droit, un administrateur ou propriétaire de nœud peut quand même en ajouter, mais seulement pour son nœud.</Info></th><th>Nœuds<Info>Nombre de nœuds (Traefik distants) qu'il peut créer. Il en devient propriétaire et gère lui-même ses membres.</Info></th><th>Créé</th><th /></tr></thead>
        <tbody>{users.data.map((u) => (
          <tr key={u.id} style={{ opacity: u.disabled ? .5 : 1 }}>
            <td><b>{u.username}</b> {u.source === 'env' && <Badge kind="accent">propriétaire</Badge>} {u.disabled && <Badge kind="err">désactivé</Badge>}</td>
            <td><Badge kind={u.role === 'admin' ? 'warn' : ''}>{u.role === 'admin' ? 'Administrateur' : 'Utilisateur'}</Badge></td>
            <td>{u.twoFactor && <Badge kind="ok" title="Double authentification activée">2FA</Badge>}</td>
            <td>{u.role === 'admin' || u.canAddDomains ? <Badge kind="ok">oui</Badge> : <span className="muted">non</span>}</td>
            <td>{u.role === 'admin' && <Badge kind="warn" title="Tous les droits sur le nœud de cette machine">machine locale</Badge>} {u.nodes.map((n) => <Badge key={n.id} kind={n.role === 'owner' ? 'accent' : ''} title={n.role}>{n.name}</Badge>)}{u.role !== 'admin' && !u.nodes.length && <span className="muted">aucun</span>}</td>
            <td className="muted small">{fmtDate(u.createdAt * 1000)}</td>
            <td className="actions"><Btn small kind="ghost" onClick={() => setEdit(u)}><Pencil size={13} /> Modifier</Btn>
              {u.source !== 'env' && <>{u.twoFactor && <Btn small kind="ghost" title="Réinitialiser la double authentification (appareil perdu)" onClick={() => resetMfa(u)}>Réinit. 2FA</Btn>}<Btn small kind="ghost" icon title="Nouveau mot de passe" onClick={() => setPw(u)}><KeyRound size={14} /></Btn><Btn small kind="ghost" icon onClick={() => remove(u)}><Trash2 size={14} /></Btn></>}</td>
          </tr>))}</tbody></table></div>
      <div className="card" style={{ marginTop: 18 }}><h3 style={{ marginBottom: 10 }}>Comment fonctionnent les droits</h3>
        <ul className="muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
          <li><b>Administrateur</b> : tout voir et modifier (Traefik, utilisateurs, domaines du compte principal). Il ne voit <u>pas</u> les clés ni les domaines que d'autres utilisateurs ont ajoutés, sauf s'ils les partagent avec lui.</li>
          <li><b>Utilisateur</b> : n'a accès qu'aux domaines qu'on lui partage (lecture ou modification des enregistrements).</li>
          <li><b>Peut ajouter des domaines</b> : peut brancher ses propres clés OVH, puis décider qui accède à ses domaines.</li>
          <li><b>Nœuds</b> : chaque nœud (instance Traefik) a son propriétaire et ses membres (administrateur, éditeur, lecteur). Un administrateur de la plateforme a tous les droits sur le nœud de cette machine.</li>
        </ul></div>
      {edit && <UserModal user={edit} onClose={() => setEdit(null)} onSaved={refresh} />}
      {pw && <PwModal user={pw} onClose={() => setPw(null)} />}
    </>
  )
}

function UserModal({ user, onClose, onSaved }) {
  const { toast } = useUi(); const isNew = !user.id
  const [f, setF] = useState({ ...user }); const set = (k, v) => setF({ ...f, [k]: v })
  const isAdmin = f.role === 'admin'; const locked = user.source === 'env'
  const save = useMutation({
    mutationFn: () => isNew ? api.post('/users', f) : api.put(`/users/${user.id}`, { role: f.role, canAddDomains: f.canAddDomains, maxNodes: f.maxNodes, disabled: f.disabled }),
    onSuccess: () => { toast(isNew ? 'Utilisateur créé' : 'Modifications enregistrées'); onSaved(); onClose() }, onError: (e) => toast(e.message, 'err'),
  })
  return (
    <Modal title={isNew ? 'Nouvel utilisateur' : `Modifier ${user.username}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={save.isPending} onClick={() => save.mutate()}>Enregistrer</Btn></>}>
      <div className="col gap-lg">
        {isNew && <div className="fgrid"><Field label="Identifiant"><input className="input" value={f.username} onChange={(e) => set('username', e.target.value)} autoFocus /></Field>
          <Field label="Mot de passe" help="12 caractères minimum"><input className="input" type="password" value={f.password} onChange={(e) => set('password', e.target.value)} autoComplete="new-password" /></Field></div>}
        <Field label="Rôle" tip="Administrateur : gère les comptes et voit tous les nœuds. Utilisateur : n'accède qu'à ce qu'on lui partage ou à ses propres nœuds."><div className="row wrap">{[['user', 'Utilisateur'], ['admin', 'Administrateur']].map(([v, l]) => <Btn key={v} kind={f.role === v ? 'primary' : ''} disabled={locked} onClick={() => set('role', v)}>{l}</Btn>)}</div></Field>
        {!isAdmin && <>
          <Field label="Peut ajouter ses propres comptes / domaines DNS" tip="Droit global : ajouter des comptes (OVH, DuckDNS) et des domaines, sur n'importe quel nœud où il est membre. Sans lui, un propriétaire ou administrateur de nœud peut quand même en ajouter pour SON nœud."><div className="row"><Switch value={f.canAddDomains} onChange={(v) => set('canAddDomains', v)} /><span className="muted small">Il choisira lui-même qui accède à ses domaines.</span></div></Field>
          <Field label="Nœuds qu'il peut créer" help="Un nœud est un Traefik sur une autre machine. Il en devient propriétaire et gère lui-même ses membres."><div className="row wrap">{(QUOTAS.some(([v]) => v === f.maxNodes) ? QUOTAS : [...QUOTAS, [f.maxNodes, String(f.maxNodes)]]).map(([v, l]) => <Btn key={v} kind={f.maxNodes === v ? 'primary' : ''} onClick={() => set('maxNodes', v)}>{l}</Btn>)}</div></Field>
          <div className="muted small">Son accès à Traefik sur un nœud existant se règle <b>par nœud</b> : ajoutez-le comme membre dans « Ce nœud → Membres ».</div>
        </>}
        {!isNew && !locked && <Field label="Compte désactivé" tip="Bloque la connexion immédiatement (même s'il est déjà connecté), sans supprimer le compte ni ses données. Réversible."><div className="row"><Switch value={f.disabled} onChange={(v) => set('disabled', v)} /><span className="muted small">Il ne peut plus se connecter.</span></div></Field>}
        {locked && <div className="banner info" style={{ margin: 0 }}>Compte propriétaire défini dans le .env : toujours administrateur.</div>}
      </div>
    </Modal>
  )
}

function PwModal({ user, onClose }) {
  const { toast } = useUi(); const [p, setP] = useState('')
  const go = () => api.post(`/users/${user.id}/password`, { password: p }).then(() => { toast('Mot de passe changé'); onClose() }).catch((e) => toast(e.message, 'err'))
  return <Modal small title={`Mot de passe de ${user.username}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" disabled={p.length < 12} onClick={go}>Changer</Btn></>}>
    <Field label="Nouveau mot de passe" help="12 caractères minimum"><input className="input" type="password" value={p} onChange={(e) => setP(e.target.value)} autoFocus autoComplete="new-password" /></Field></Modal>
}
