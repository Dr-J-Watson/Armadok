import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Eye, KeyRound, Pencil, Plus, Trash2, UserCog, Users, Clock } from 'lucide-react'
import { api } from '../api.js'
import { useAuth } from '../auth.jsx'
import { useNode } from '../node.jsx'
import { Badge, Btn, Empty, Field, Loading, Modal, Tabs, useUi } from '../components/ui.jsx'
import { Info } from '../components/Tip.jsx'
import { fmtDate } from '../lib/util.js'

const DURATIONS = [['', 'Sans limite'], ['3600', '1 heure'], ['86400', '24 heures'], ['604800', '7 jours'], ['2592000', '30 jours'], ['custom', 'Date précise…']]
const until = (d, custom) => (d === '' ? null : d === 'custom' ? (custom ? new Date(custom).getTime() / 1000 : null) : Math.floor(Date.now() / 1000) + Number(d))

// Pastilles de droits (comme des étiquettes) ; l'infobulle donne le libellé en clair.
function Chips({ caps, catalog, max = 18 }) {
  const [all, setAll] = useState(false)
  const shown = all ? caps : caps.slice(0, max)
  return (
    <div className="capchips">{shown.map((c) => <span key={c} className="capchip" data-tip={catalog?.[c]?.label}>{c}</span>)}
      {caps.length > max && <button type="button" className="capchip more" onClick={() => setAll(!all)}>{all ? 'Réduire' : `+ ${caps.length - max} autres`}</button>}</div>
  )
}

const useCatalog = () => {
  const q = useQuery({ queryKey: ['rights-catalog'], queryFn: () => api.get('/rights/catalog'), staleTime: 3600000 })
  const flat = useMemo(() => Object.fromEntries((q.data?.groups || []).flatMap((g) => g.caps.map((c) => [c.key, c]))), [q.data])
  return { ...q, flat }
}

// ------------------------------------------------------------------ éditeur de rôle
function RoleModal({ role, base, roles, catalog, ceiling, onClose, onSaved }) {
  const { toast } = useUi()
  const [name, setName] = useState(role?.name || '')
  const [desc, setDesc] = useState(role?.description || '')
  const [sel, setSel] = useState(new Set(role?.caps || base?.caps || []))
  const flat = catalog.flat
  const dependents = (k) => Object.values(flat).filter((c) => c.requires.includes(k)).map((c) => c.key)
  const toggle = (k) => setSel((s) => {
    const n = new Set(s)
    if (n.has(k)) { const drop = (x) => { n.delete(x); dependents(x).forEach((d) => n.has(d) && drop(d)) }; drop(k) }
    else { const add = (x) => { n.add(x); flat[x].requires.forEach((r) => !n.has(r) && add(r)) }; add(k) }
    return n
  })
  const save = useMutation({
    mutationFn: () => { const body = { name, description: desc, caps: [...sel] }; return role ? api.put(`/rights/roles/${role.id}`, body) : api.post('/rights/roles', body) },
    onSuccess: (r) => { toast(role ? 'Rôle enregistré' : 'Rôle créé'); onSaved(r) }, onError: (e) => toast(e.message, 'err'),
  })
  const setGroup = (g, on) => setSel((s) => { const n = new Set(s); g.caps.forEach((c) => { if (!on) n.delete(c.key); else if (ceiling.has(c.key)) { n.add(c.key); c.requires.forEach((r) => ceiling.has(r) && n.add(r)) } }); return n })
  return (
    <Modal wide title={role ? `Modifier « ${role.name} »` : 'Nouveau rôle'} onClose={onClose}
      footer={<><span className="muted small" style={{ marginRight: 'auto' }}>{sel.size} droit{sel.size > 1 ? 's' : ''}</span><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={save.isPending} disabled={!name.trim()} onClick={() => save.mutate()}>Enregistrer</Btn></>}>
      <div className="col gap-lg">
        <div className="fgrid">
          <Field label="Nom du rôle" required><input className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus maxLength={50} /></Field>
          <Field label="Description"><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={200} placeholder="À quoi sert ce rôle" /></Field>
          {!role && <Field label="Partir de" tip="Remplit les cases avec les droits d'un rôle existant, que vous ajustez ensuite."><select className="select" value="" onChange={(e) => { const r = roles.find((x) => String(x.id) === e.target.value); if (r) setSel(new Set(r.caps.filter((c) => ceiling.has(c)))) }}><option value="">— un rôle existant —</option>{roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Field>}
        </div>
        {catalog.data.groups.map((g) => {
          const keys = g.caps.map((c) => c.key); const n = keys.filter((k) => sel.has(k)).length
          return (
            <div className="card col" key={g.id} style={{ gap: 6 }}>
              <div className="row"><h3>{g.label}</h3><Badge>{n}/{keys.length}</Badge><span className="grow" /><Btn small kind="ghost" onClick={() => setGroup(g, true)}>Tout cocher</Btn><Btn small kind="ghost" onClick={() => setGroup(g, false)}>Tout décocher</Btn></div>
              <div className="capgrid">{g.caps.map((c) => {
                const locked = !ceiling.has(c.key)
                return (
                  <label key={c.key} className={'capopt ' + (locked ? 'locked' : '')} data-tip={locked ? 'Vous n\'avez pas ce droit : vous ne pouvez pas le donner.' : c.hint || undefined}>
                    <input type="checkbox" disabled={locked} checked={sel.has(c.key)} onChange={() => toggle(c.key)} />
                    <span><b>{c.label}</b><code>{c.key}</code>{c.requires.length > 0 && <small className="muted"> · nécessite : {c.requires.map((r) => flat[r]?.label).join(', ')}</small>}</span>
                  </label>)
              })}</div>
            </div>)
        })}
      </div>
    </Modal>
  )
}

// ------------------------------------------------------------------ onglet Rôles
function RolesTab({ catalog, ceiling }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const roles = useQuery({ queryKey: ['rights-roles'], queryFn: () => api.get('/rights/roles') })
  const [edit, setEdit] = useState(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['rights-roles'] })
  const dup = (r) => api.post(`/rights/roles/${r.id}/duplicate`).then((n) => { refresh(); toast(`« ${n.name} » créé : modifiez-le`); setEdit({ role: n }) }).catch((e) => toast(e.message, 'err'))
  const del = async (r) => { if (await confirm({ title: `Supprimer « ${r.name} » ?`, message: 'Il ne doit plus être attribué.', confirmLabel: 'Supprimer' })) api.del(`/rights/roles/${r.id}`).then(() => { refresh(); toast('Rôle supprimé') }).catch((e) => toast(e.message, 'err')) }
  if (roles.isLoading) return <Loading />
  return (
    <div className="col gap-lg">
      <div className="row"><div className="muted grow">Un rôle est un ensemble de droits, attribué ensuite à une personne ou à un groupe.</div><Btn kind="primary" onClick={() => setEdit({})}><Plus size={15} /> Nouveau rôle</Btn></div>
      <div className="card flush">
        {roles.data.map((r) => (
          <div className="rolerow" key={r.id}>
            <div className="row"><b className="grow">{r.name}</b>{r.predefined ? <Badge>Prédéfini</Badge> : <Badge kind="accent">Personnalisé</Badge>}{r.uses > 0 && <Badge kind="info">{r.uses} attribution{r.uses > 1 ? 's' : ''}</Badge>}
              <Btn small onClick={() => dup(r)}><Copy size={14} /> Dupliquer</Btn>
              {!r.predefined && <><Btn small onClick={() => setEdit({ role: r })}><Pencil size={14} /> Modifier</Btn><Btn small kind="danger" onClick={() => del(r)}><Trash2 size={14} /></Btn></>}</div>
            <div className="muted small">{r.description}</div>
            <Chips caps={r.caps} catalog={catalog.flat} />
          </div>))}
      </div>
      {edit && <RoleModal role={edit.role} roles={roles.data} catalog={catalog} ceiling={ceiling} onClose={() => setEdit(null)} onSaved={() => { refresh(); setEdit(null) }} />}
    </div>
  )
}

// ------------------------------------------------------------------ onglet Groupes
function GroupModal({ group, onClose, onSaved }) {
  const { toast } = useUi()
  const dir = useQuery({ queryKey: ['directory'], queryFn: () => api.get('/users-directory') })
  const [ids, setIds] = useState(new Set(group.members.map((m) => m.id)))
  const [find, setFind] = useState('')
  const users = [...(dir.data || []), ...group.members.filter((m) => !(dir.data || []).some((d) => d.id === m.id))]
  const lookup = () => api.get('/users-lookup?username=' + encodeURIComponent(find.trim())).then((u) => { setIds((s) => new Set(s).add(u.id)); group.members.push(u); setFind('') }).catch((e) => toast(e.message, 'err'))
  const save = useMutation({ mutationFn: () => api.put(`/rights/groups/${group.id}/members`, { userIds: [...ids] }), onSuccess: () => { toast('Membres enregistrés'); onSaved() }, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal title={`Membres de « ${group.name} »`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={save.isPending} onClick={() => save.mutate()}>Enregistrer</Btn></>}>
      <div className="col">
        {dir.isLoading && <Loading />}
        <div className="col" style={{ gap: 4, maxHeight: 320, overflow: 'auto' }}>{users.map((u) => <label key={u.id} className="row" style={{ gap: 8 }}><input type="checkbox" checked={ids.has(u.id)} onChange={() => setIds((s) => { const n = new Set(s); n.has(u.id) ? n.delete(u.id) : n.add(u.id); return n })} />{u.username}</label>)}</div>
        <div className="row"><input className="input" placeholder="Ajouter par nom d'utilisateur exact" value={find} onChange={(e) => setFind(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && find.trim() && lookup()} /><Btn disabled={!find.trim()} onClick={lookup}>Ajouter</Btn></div>
      </div>
    </Modal>
  )
}

function GroupsTab() {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const groups = useQuery({ queryKey: ['rights-groups'], queryFn: () => api.get('/rights/groups') })
  const [name, setName] = useState(''); const [edit, setEdit] = useState(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['rights-groups'] })
  const create = () => api.post('/rights/groups', { name }).then((g) => { setName(''); refresh(); setEdit(g) }).catch((e) => toast(e.message, 'err'))
  const del = async (g) => { if (await confirm({ title: `Supprimer le groupe « ${g.name} » ?`, message: 'Ses attributions sont retirées : ses membres perdent ces droits.', confirmLabel: 'Supprimer' })) api.del(`/rights/groups/${g.id}`).then(() => { refresh(); qc.invalidateQueries({ queryKey: ['rights-grants'] }) }).catch((e) => toast(e.message, 'err')) }
  if (groups.isLoading) return <Loading />
  return (
    <div className="col gap-lg">
      <div className="row"><input className="input grow" placeholder="Nom du nouveau groupe" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && name.trim() && create()} /><Btn kind="primary" disabled={!name.trim()} onClick={create}><Plus size={15} /> Créer</Btn></div>
      {groups.data.length === 0 ? <div className="card"><Empty><Users size={28} /><div><b>Aucun groupe</b><br />Un groupe permet d'attribuer les mêmes droits à plusieurs personnes d'un coup.</div></Empty></div> : (
        <div className="card flush">{groups.data.map((g) => (
          <div className="rolerow" key={g.id}>
            <div className="row"><b className="grow">{g.name}</b><Badge>{g.members.length} membre{g.members.length > 1 ? 's' : ''}</Badge>{g.grants > 0 && <Badge kind="info">{g.grants} attribution{g.grants > 1 ? 's' : ''}</Badge>}
              <Btn small onClick={() => setEdit(g)}><Pencil size={14} /> Membres</Btn><Btn small kind="danger" onClick={() => del(g)}><Trash2 size={14} /></Btn></div>
            <div className="capchips">{g.members.length ? g.members.map((m) => <span className="capchip" key={m.id}>{m.username}</span>) : <span className="muted small">Aucun membre</span>}</div>
          </div>))}</div>)}
      {edit && <GroupModal group={edit} onClose={() => setEdit(null)} onSaved={() => { refresh(); setEdit(null) }} />}
    </div>
  )
}

// ------------------------------------------------------------------ onglet Attributions
function GrantsTab() {
  const { toast, confirm } = useUi(); const qc = useQueryClient(); const { nodes } = useNode()
  const roles = useQuery({ queryKey: ['rights-roles'], queryFn: () => api.get('/rights/roles') })
  const groups = useQuery({ queryKey: ['rights-groups'], queryFn: () => api.get('/rights/groups') })
  const dir = useQuery({ queryKey: ['directory'], queryFn: () => api.get('/users-directory') })
  const grants = useQuery({ queryKey: ['rights-grants'], queryFn: () => api.get('/rights/grants') })
  const [f, setF] = useState({ type: 'user', subject: '', role: '', node: '', dur: '', custom: '' })
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value, ...(k === 'type' ? { subject: '' } : {}) }))
  const refresh = () => { qc.invalidateQueries({ queryKey: ['rights-grants'] }); qc.invalidateQueries({ queryKey: ['rights-users'] }); qc.invalidateQueries({ queryKey: ['nodes'] }) }
  const add = useMutation({
    mutationFn: () => api.post('/rights/grants', { subjectType: f.type, subjectId: Number(f.subject), roleId: Number(f.role), nodeId: f.node || null, expiresAt: until(f.dur, f.custom) }),
    onSuccess: () => { toast('Attribution enregistrée'); setF((x) => ({ ...x, subject: '', role: '' })); refresh() }, onError: (e) => toast(e.message, 'err'),
  })
  const del = async (g) => { if (await confirm({ title: 'Retirer cette attribution ?', message: `${g.subject} perd le rôle « ${g.role} »${g.node ? ` sur ${g.node}` : ''}.`, confirmLabel: 'Retirer' })) api.del(`/rights/grants/${g.id}`).then(() => { refresh(); toast('Attribution retirée') }).catch((e) => toast(e.message, 'err')) }
  if (roles.isLoading || grants.isLoading) return <Loading />
  const mine = nodes.filter((n) => (n.caps || []).includes('node.members.edit'))
  const grantable = roles.data.filter((r) => r.builtin !== 'owner')
  return (
    <div className="col gap-lg">
      <div className="card col">
        <div className="row"><UserCog size={16} /><h3>Nouvelle attribution</h3></div>
        <div className="fgrid">
          <Field label="Pour"><div className="row"><select className="select" style={{ maxWidth: 140 }} value={f.type} onChange={set('type')}><option value="user">un utilisateur</option><option value="group">un groupe</option></select>
            <select className="select" value={f.subject} onChange={set('subject')}><option value="">Choisir…</option>{(f.type === 'user' ? dir.data || [] : groups.data || []).map((x) => <option key={x.id} value={x.id}>{x.username || x.name}</option>)}</select></div></Field>
          <Field label="Rôle"><select className="select" value={f.role} onChange={set('role')}><option value="">Choisir…</option>{grantable.map((r) => <option key={r.id} value={r.id}>{r.name}{r.predefined ? '' : ' (perso)'}</option>)}</select></Field>
          <Field label="Sur" tip="Un nœud précis, ou « tous mes nœuds » : ceux dont vous êtes propriétaire, y compris ceux que vous créerez plus tard."><select className="select" value={f.node} onChange={set('node')}><option value="">tous mes nœuds</option>{mine.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}</select></Field>
          <Field label="Jusqu'à" tip="Droits temporaires : l'attribution disparaît toute seule à l'échéance."><div className="row"><select className="select" value={f.dur} onChange={set('dur')}>{DURATIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>{f.dur === 'custom' && <input className="input" type="datetime-local" value={f.custom} onChange={set('custom')} />}</div></Field>
        </div>
        <div><Btn kind="primary" busy={add.isPending} disabled={!f.subject || !f.role || (f.dur === 'custom' && !f.custom)} onClick={() => add.mutate()}>Attribuer</Btn></div>
      </div>
      {grants.data.length === 0 ? <div className="card"><div className="muted">Aucune attribution : seul le propriétaire a des droits.</div></div> : (
        <div className="card flush"><table className="t"><thead><tr><th>Pour</th><th>Rôle</th><th>Sur</th><th>Fin</th><th /></tr></thead><tbody>
          {grants.data.map((g) => (
            <tr key={g.id} className={g.expired ? 'off' : ''}>
              <td>{g.subjectType === 'group' ? <Users size={13} style={{ verticalAlign: -2 }} /> : null} <b>{g.subject}</b></td>
              <td>{g.role}{g.membership && <Badge className="mw-proto" title="Rôle historique du nœud : se modifie aussi depuis la page du nœud">nœud</Badge>}</td>
              <td>{g.node || <Badge kind="info">tous mes nœuds</Badge>}</td>
              <td>{g.expiresAt ? <span className="row" style={{ gap: 4 }}><Clock size={12} />{fmtDate(g.expiresAt * 1000)}{g.expired && <Badge kind="err">expirée</Badge>}</span> : <span className="muted">—</span>}</td>
              <td className="right"><Btn small kind="ghost" icon title="Retirer" onClick={() => del(g)}><Trash2 size={14} /></Btn></td>
            </tr>))}
        </tbody></table></div>)}
    </div>
  )
}

// ------------------------------------------------------------------ onglet Utilisateurs
function UserRights({ user, catalog, onClose }) {
  const d = useQuery({ queryKey: ['rights-user', user.id], queryFn: () => api.get(`/rights/users/${user.id}`) })
  return (
    <Modal wide title={`Droits de ${user.username}`} onClose={onClose}>
      {d.isLoading ? <Loading /> : d.data.nodes.length === 0 ? <div className="muted">Aucun accès sur les nœuds que vous administrez.</div> : (
        <div className="col gap-lg">{d.data.nodes.map((n) => (
          <div className="card col" key={n.nodeId} style={{ gap: 8 }}>
            <div className="row"><h3>{n.node}</h3><Badge>{n.caps.length} droits</Badge></div>
            <div className="row wrap small" style={{ gap: 6 }}><span className="muted">Origine :</span>{n.sources.map((s, i) => <Badge key={i} kind={s.kind === 'grant' ? 'accent' : ''}>{s.role}{s.via ? ` (groupe ${s.via})` : ''}{s.expiresAt ? ` · jusqu'au ${fmtDate(s.expiresAt * 1000)}` : ''}</Badge>)}</div>
            {catalog.data.groups.map((g) => { const has = g.caps.filter((c) => n.caps.includes(c.key)); return has.length ? <div key={g.id}><div className="muted small">{g.label}</div><Chips caps={has.map((c) => c.key)} catalog={catalog.flat} /></div> : null })}
          </div>))}</div>)}
    </Modal>
  )
}

function UsersTab({ catalog }) {
  const users = useQuery({ queryKey: ['rights-users'], queryFn: () => api.get('/rights/users') })
  const [sel, setSel] = useState(null)
  if (users.isLoading) return <Loading />
  return (
    <div className="col gap-lg">
      <div className="muted">Les droits s'attribuent dans l'onglet « Attributions ». Ici : qui a accès à quoi sur vos nœuds.</div>
      <div className="card flush">{users.data.map((u) => (
        <div className="rolerow" key={u.id}>
          <div className="row"><b className="grow">{u.username}</b>{u.platformRole === 'admin' && <Badge kind="warn">admin plateforme</Badge>}<Btn small onClick={() => setSel(u)}><Eye size={14} /> Droits</Btn></div>
          <div className="capchips">{u.access.map((a, i) => <span className="capchip" key={i}>{a.node} · {a.source}</span>)}</div>
        </div>))}</div>
      {sel && <UserRights user={sel} catalog={catalog} onClose={() => setSel(null)} />}
    </div>
  )
}

// ------------------------------------------------------------------ page
export default function Rights() {
  const { nodes } = useNode(); const { user } = useAuth()
  const catalog = useCatalog()
  const [tab, setTab] = useState('users')
  const ceiling = useMemo(() => {
    const s = new Set(); nodes.filter((n) => (n.caps || []).includes('node.members.edit')).forEach((n) => n.caps.forEach((c) => s.add(c)))
    if (catalog.data) catalog.data.ownerOnly.forEach((c) => s.delete(c))
    return s
  }, [nodes, catalog.data])
  if (catalog.isLoading) return <Loading />
  return (
    <div className="col gap-lg">
      <div className="page-head"><div><h1>Droits <Info>{'Qui peut faire quoi sur vos nœuds.\nUn rôle = un ensemble de droits ; un groupe = plusieurs personnes ; une attribution donne un rôle à une personne ou un groupe, sur un nœud ou sur tous vos nœuds, pour une durée limitée si besoin.'}</Info></h1><p>Rôles, groupes et attributions</p></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'users', label: 'Utilisateurs', icon: <Users size={15} /> }, { id: 'groups', label: 'Groupes', icon: <Users size={15} /> }, { id: 'roles', label: 'Rôles', icon: <KeyRound size={15} /> }, { id: 'grants', label: 'Attributions', icon: <UserCog size={15} /> }]} />
      {tab === 'users' && <UsersTab catalog={catalog} />}
      {tab === 'groups' && <GroupsTab />}
      {tab === 'roles' && <RolesTab catalog={catalog} ceiling={ceiling} />}
      {tab === 'grants' && <GrantsTab />}
    </div>
  )
}
