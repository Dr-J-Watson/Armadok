import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Crown, Download, Globe, Info as InfoIcon, Pencil, Plug, RefreshCw, Settings2, ShieldAlert, ShieldCheck, Trash2, UserPlus, Users } from 'lucide-react'
import { NavLink, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api.js'
import { useAuth } from '../auth.jsx'
import { ROLES, useNode } from '../node.jsx'
import { Badge, Btn, CopyBtn, Empty, Field, Info, Loading, Modal, Seg, Tabs, useUi } from '../components/ui.jsx'
import { Chips } from '../components/SchemaForm.jsx'
import { CONN, NodeAvatar } from '../components/NodeSwitcher.jsx'
import WorkerSetup from '../components/WorkerSetup.jsx'
import ActivityFeed from '../components/ActivityFeed.jsx'
import { fmtDate } from '../lib/util.js'

const RANK = { viewer: 1, custom: 1, editor: 2, admin: 3, owner: 4 }

export default function Node() {
  const { node, role, reload, can } = useNode(); const { user } = useAuth()
  const detail = useQuery({ queryKey: ['node', node.id], queryFn: () => api.get(`/nodes/${node.id}`) })
  const members = useQuery({ queryKey: ['node-members', node.id], queryFn: () => api.get(`/nodes/${node.id}/members`), enabled: can('node.members.view') })
  const tabs = [   // chaque onglet suit un droit : on ne montre que ce que la personne peut utiliser
    ...(can('node.settings') ? [{ id: 'general', label: 'Réglages', icon: <Settings2 size={15} /> }] : []),
    ...(can('node.members.view') ? [{ id: 'members', label: 'Membres', icon: <Users size={15} />, count: members.data?.members.length }] : []),
    ...(can('node.domains.view') ? [{ id: 'domains', label: 'Domaines', icon: <Globe size={15} />, count: node.domains }] : []),
    ...(can('node.audit') ? [{ id: 'activity', label: 'Activité', icon: <Activity size={15} /> }] : []),
    ...(can('node.settings') ? [{ id: 'security', label: 'Sécurité', icon: <ShieldCheck size={15} /> }] : []),
    ...(node.kind === 'remote' && can('node.connection') ? [{ id: 'connection', label: 'Connexion', icon: <Plug size={15} /> }] : []),
  ]
  const [params, setParams] = useSearchParams(); const tab0 = params.get('tab'); const setTab = (t) => setParams({ tab: t }, { replace: true })
  const tab = tabs.some((t) => t.id === tab0) ? tab0 : tabs[0]?.id
  if (detail.isLoading) return <Loading />
  return (
    <>
      <div className="node-hero">
        <NodeAvatar node={node} size={52} />
        <div className="grow">
          <h1>{node.name} <Badge kind={ROLES[role].badge}>{role === 'owner' && <Crown size={11} />} {node.roleLabel || ROLES[role].label}</Badge>
            {node.kind === 'local' ? <Badge>cette machine</Badge> : <Badge kind={CONN[node.connection][0]}>{CONN[node.connection][1]}</Badge>}{node.status === 'suspended' && <Badge kind="err">suspendu</Badge>}</h1>
          <div className="muted">{node.description || 'Un nœud est une instance Traefik avec ses propres sites, membres et domaines.'}</div>
        </div>
      </div>
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === 'general' && <General key={detail.dataUpdatedAt} node={detail.data} user={user} onSaved={() => { reload(); detail.refetch() }} />}
      {tab === 'members' && <Members node={node} role={role} data={members.data} refresh={() => { members.refetch(); reload() }} />}
      {tab === 'domains' && <Domains node={node} user={user} onChange={reload} />}
      {tab === 'activity' && <ActivityTab node={node} platformAdmin={user.role === 'admin'} />}
      {tab === 'security' && <MfaPolicy node={node} role={role} user={user} onSaved={reload} />}
      {tab === 'connection' && <Connection node={node} role={role} reload={reload} />}
    </>
  )
}

// ------------------------------------------------------------------ double authentification exigée
function MfaPolicy({ node, role, user, onSaved }) {
  const { toast } = useUi(); const qc = useQueryClient()
  const q = useQuery({ queryKey: ['mfa-policy', node.id], queryFn: () => api.get(`/nodes/${node.id}/mfa-policy`) })
  const cat = useQuery({ queryKey: ['rights-catalog'], queryFn: () => api.get('/rights/catalog'), staleTime: 3600000 })
  const [sel, setSel] = useState(null)
  const canEdit = role === 'owner' || user.role === 'admin'
  const save = useMutation({ mutationFn: () => api.put(`/nodes/${node.id}/mfa-policy`, { caps: [...cur] }), onSuccess: () => { qc.invalidateQueries({ queryKey: ['mfa-policy', node.id] }); toast('Exigences enregistrées'); setSel(null); onSaved?.() }, onError: (e) => toast(e.message, 'err') })
  if (q.isLoading || cat.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data; const cur = sel || new Set(d.caps); const recommended = new Set(d.recommended)
  const toggle = (k) => { const n = new Set(cur); n.has(k) ? n.delete(k) : n.add(k); setSel(n) }
  const without = d.members.filter((m) => !m.twoFactor)
  const dirty = sel && (sel.size !== d.caps.length || [...sel].some((k) => !d.caps.includes(k)))
  return (
    <div className="col gap-lg">
      <div className="card col">
        <div className="row"><h3><ShieldCheck size={14} style={{ verticalAlign: -2 }} /> Double authentification obligatoire</h3><Info>Pour chaque droit coché, une personne qui n'a PAS activé la double authentification (A2F) perd ce droit sur ce nœud, tant qu'elle ne l'a pas activée. Rien d'autre ne change pour elle. Le droit revient aussitôt la 2FA activée.</Info></div>
        <div className="muted">Cochez les droits dont l'abus ferait le plus de dégâts. Les cases entourées sont celles que nous recommandons (certificats, blocage d'adresses, fichiers bruts, réglages du nœud, membres, DNS sensible).</div>
        {!d.mine && cur.size > 0 && <div className="banner err"><ShieldAlert size={16} /><span>Vous n'avez pas activé la double authentification sur votre propre compte : activez-la d'abord (menu → votre nom), sinon vous perdriez ces droits vous-même.</span></div>}
        {without.length > 0 && cur.size > 0 && <div className="banner info"><span><b>{without.length} membre{without.length > 1 ? 's' : ''}</b> n'ont pas la 2FA ({without.slice(0, 6).map((m) => m.username).join(', ')}{without.length > 6 ? '…' : ''}) et perdraient les droits cochés tant qu'ils ne l'activent pas.</span></div>}
        <div className="row wrap"><Btn small onClick={() => setSel(new Set(d.recommended))} disabled={!canEdit}>Sélection recommandée</Btn><Btn small kind="ghost" onClick={() => setSel(new Set())} disabled={!canEdit}>Tout décocher</Btn>
          <span className="muted small">{cur.size} droit{cur.size > 1 ? 's' : ''} exige{cur.size > 1 ? 'nt' : ''} la 2FA</span></div>
        {(cat.data?.groups || []).map((g) => (
          <div key={g.id} className="col" style={{ gap: 6 }}><b>{g.label}</b>
            <div className="grid two" style={{ gap: 6 }}>{g.caps.filter((c) => !(cat.data.ownerOnly || []).includes(c.key) || recommended.has(c.key)).map((c) => (
              <label key={c.key} className="row" style={{ gap: 8, alignItems: 'flex-start', padding: '4px 8px', borderRadius: 8, border: recommended.has(c.key) ? '1px solid var(--accent)' : '1px solid transparent' }} title={c.hint}>
                <input type="checkbox" checked={cur.has(c.key)} disabled={!canEdit} onChange={() => toggle(c.key)} /><span>{c.label}</span></label>))}</div></div>))}
        {canEdit ? <div className="row"><Btn kind="primary" disabled={!dirty} busy={save.isPending} onClick={() => save.mutate()}>Enregistrer</Btn>{dirty && <Btn onClick={() => setSel(null)}>Annuler</Btn>}</div>
          : <div className="muted small">Seul le propriétaire du nœud (ou un administrateur de la plateforme) peut changer ces exigences.</div>}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ réglages
function General({ node, user, onSaved }) {
  const { toast } = useUi(); const qc = useQueryClient(); const { nodes } = useNode(); const viaNode = node.traefikVia ? nodes.find((n) => n.id === node.traefikVia) : null
  const init = { name: node.name, description: node.description, entrypoints: node.entrypoints, certResolvers: node.certResolvers, dnsTarget: node.dnsTarget, apiUrl: node.apiUrl || '', addresses: node.addresses || [], traefikVia: node.traefikVia || '' }
  const [f, setF] = useState(init); const set = (k, v) => setF({ ...f, [k]: v })
  const dirty = JSON.stringify(f) !== JSON.stringify(init)
  const platformAdmin = user.role === 'admin'
  const save = useMutation({
    mutationFn: () => api.put(`/nodes/${node.id}`, { name: f.name, description: f.description, entrypoints: f.entrypoints, certResolvers: f.certResolvers, dnsTarget: f.dnsTarget, addresses: f.addresses, ...(node.modules?.includes('docker') ? { traefikVia: f.traefikVia } : {}), ...(platformAdmin && node.kind === 'local' ? { apiUrl: f.apiUrl } : {}) }),
    onSuccess: () => { toast('Réglages enregistrés'); qc.invalidateQueries({ queryKey: ['meta'] }); onSaved() }, onError: (e) => toast(e.message, 'err'),
  })
  return (
    <div className="gen-grid">
      <div className="card col">
        <div className="row"><h3>Identité</h3>{(node.modules || []).map((m) => <Badge key={m} kind="info" title={m === 'docker' ? 'Ce nœud gère Docker' : 'Ce nœud gère Traefik'}>{m === 'docker' ? 'Docker' : 'Traefik'}</Badge>)}<Info>Comment ce nœud apparaît dans le sélecteur du menu. Rien de technique ici.</Info></div>
        <Field label="Nom du nœud" help="Affiché dans le sélecteur en haut du menu. Vous pouvez le renommer à tout moment."><input className="input" value={f.name} maxLength={60} onChange={(e) => set('name', e.target.value)} /></Field>
        <Field label="Description" help="Facultatif : à quoi sert cette instance (ex : « Serveur maison », « Production »)."><input className="input" value={f.description} maxLength={200} onChange={(e) => set('description', e.target.value)} /></Field>
      </div>
      {viaNode && !(node.modules || []).includes('traefik') ? (
        <div className="card col">
          <div className="row"><h3>Traefik</h3><Info>Ce nœud n'a pas de Traefik : ses sites passent par celui de « {viaNode.name} ». Les points d'entrée, résolveurs de certificats et l'adresse DNS viennent de lui (ils se règlent dans ses propres réglages).</Info></div>
          <div className="muted small">Publié par <b>{viaNode.name}</b></div>
          <Field label="Points d'entrée"><div className="row wrap" style={{ gap: 4 }}>{(viaNode.entrypoints || []).map((e) => <Badge key={e}>{e}</Badge>)}{!(viaNode.entrypoints || []).length && <span className="muted small">—</span>}</div></Field>
          <Field label="Résolveurs de certificats"><div className="row wrap" style={{ gap: 4 }}>{(viaNode.certResolvers || []).map((e) => <Badge key={e}>{e}</Badge>)}{!(viaNode.certResolvers || []).length && <span className="muted small">—</span>}</div></Field>
          <Field label="Adresse cible DNS par défaut"><code>{viaNode.dnsTarget || '—'}</code></Field>
        </div>
      ) : (
      <div className="card col">
          <div className="row"><h3>Traefik</h3><Info>Valeurs proposées dans les formulaires de site. Elles doivent correspondre à la configuration de Traefik lui-même.</Info></div>
          <Field label="Points d'entrée (entryPoints)" help="Proposés lors de la création d'un site. Appuyez sur Entrée pour ajouter. Ex : web, websecure."><Chips value={f.entrypoints} onChange={(v) => set('entrypoints', v)} placeholder="web, websecure…" /></Field>
          <Field label="Résolveurs de certificats" help="Noms définis dans la configuration de Traefik (certificatesResolvers). Ex : letsencrypt."><Chips value={f.certResolvers} onChange={(v) => set('certResolvers', v)} placeholder="letsencrypt…" /></Field>
        </div>
      )}
      {node.modules?.includes('docker') && <div className="card col">
        <div className="row"><h3>Docker</h3><Info>Ce nœud gère Docker. Ses adresses servent à relier ses conteneurs au Traefik d'un AUTRE nœud.</Info></div>
        <Field label="Adresses de cette machine" help="Les IP ou noms par lesquels les autres nœuds la joignent (réseau local, VPN, DNS). Quand le Traefik d'un autre nœud vise « adresse:port » et que ce conteneur publie ce port, le lien est trouvé tout seul : on le voit dans la fiche du conteneur, avec les statistiques d'accès. Sans le nom ni le port : « 10.0.0.5 », « srv-a.lan »."><Chips value={f.addresses} onChange={(v) => set('addresses', v)} placeholder="10.0.0.5, srv-a.lan…" /></Field>
        {(node.addressesSeen || []).length > 0 && <div className="muted small">Annoncées par le worker lui-même (<code>WORKER_ADDRESSES</code>) : {(node.addressesSeen || []).map((a) => <code key={a} style={{ marginRight: 6 }}>{a}</code>)}— elles s'ajoutent à celles-ci.</div>}
        <Field label="Publier les sites de cette machine avec le Traefik de…" help="Pour un nœud sans Traefik (« Docker seul ») ou dont les sites passent par un autre Traefik, par exemple celui du hub. Les conteneurs de cette machine sont alors reliés automatiquement aux règles de ce Traefik (par l'adresse de la machine et le port publié), avec leurs statistiques d'accès ; « Publier sur Traefik » crée le site sur ce Traefik, vers l'adresse de la machine. Il faut que ce Traefik puisse joindre cette machine (réseau local, VPN) et qu'au moins une adresse soit connue ci-dessus.">
          <select className="input" value={f.traefikVia} onChange={(e) => set('traefikVia', e.target.value)}><option value="">— aucun (ce nœud a son Traefik, ou pas de site) —</option>{nodes.filter((n) => n.id !== node.id && (n.modules || []).includes('traefik')).map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}</select></Field>
        {f.traefikVia && f.addresses.length === 0 && !(node.addressesSeen || []).length && <div className="banner warn" style={{ margin: 0 }}>Aucune adresse n'est connue pour cette machine : sans elle, le lien automatique ne peut pas se faire. Ajoutez-la ci-dessus (ou <code>WORKER_ADDRESSES</code> dans le worker).</div>}
      </div>}
      {!(viaNode && !(node.modules || []).includes('traefik')) && (
      <div className="card col">
          <div className="row"><h3>DNS</h3><Info>Réglage utilisé quand vous créez un enregistrement DNS pour un site de ce nœud.</Info></div>
          <Field label="Adresse cible par défaut" help="IP publique (ou nom) proposée quand un sous-domaine n'a pas encore d'enregistrement : en général l'IP de la machine qui héberge ce Traefik."><input className="input mono" value={f.dnsTarget} onChange={(e) => set('dnsTarget', e.target.value.trim())} placeholder="203.0.113.10" /></Field>
        </div>
      )}
      <div className="card col">
        <div className="row"><h3>Connexion</h3><Info>Comment cette interface atteint le Traefik de ce nœud : un dossier de fichiers, et éventuellement son API pour le statut en direct.</Info></div>
        {node.kind === 'local' && node.directory && (
          <Field label="Dossier des fichiers dynamiques" help="Défini par le volume monté dans docker-compose.yml (TRAEFIK_DYNAMIC_PATH). Pour le changer, modifiez le compose puis redémarrez : ce n'est volontairement pas modifiable depuis l'interface.">
            <div className="pathbox"><code className="grow ellipsis">{node.directory}</code><CopyBtn text={node.directory} /></div></Field>)}
        {node.kind === 'remote' && <div className="muted small"><InfoIcon size={13} style={{ verticalAlign: -2 }} /> Le dossier Traefik et l'URL de l'API se règlent sur la machine du worker (variables <code>TRAEFIK_DYNAMIC_DIR</code> et <code>TRAEFIK_API_URL</code>) : voir l'onglet « Connexion ».</div>}
        {node.kind === 'local' && (platformAdmin
          ? <Field label="URL de l'API Traefik" help="Active le statut en direct (routeurs actifs, version). Ex : http://traefik:8080. Laissez vide pour désactiver."><input className="input mono" value={f.apiUrl} onChange={(e) => set('apiUrl', e.target.value)} placeholder="http://traefik:8080" /></Field>
          : <div className="muted small"><InfoIcon size={13} style={{ verticalAlign: -2 }} /> L'URL de l'API Traefik ne peut être changée que par un administrateur de la plateforme.</div>)}
      </div>
      {node.kind === 'local' && <Observability node={node} platformAdmin={platformAdmin} />}
      {dirty && <div className="savebar"><span className="grow">Modifications non enregistrées</span><Btn onClick={() => setF(init)}>Annuler</Btn><Btn kind="primary" busy={save.isPending} disabled={!f.name.trim()} onClick={() => save.mutate()}>Enregistrer</Btn></div>}
    </div>
  )
}

// Prometheus, webhook d'alerte et journal d'accès : facultatifs. Tant qu'ils ne sont pas branchés, les écrans correspondants sont cachés.
function Observability({ node, platformAdmin }) {
  const { toast } = useUi(); const qc = useQueryClient()
  const cfg = useQuery({ queryKey: ['obs-config', node.id], queryFn: () => api.get('/obs/config'), retry: false })
  const [prom, setProm] = useState(null); const [hook, setHook] = useState('')
  if (!cfg.data) return null
  const c = cfg.data; const promVal = prom ?? c.prometheusUrl
  const done = () => { qc.invalidateQueries({ queryKey: ['obs-config'] }); qc.invalidateQueries({ queryKey: ['features'] }); qc.invalidateQueries({ queryKey: ['nodes'] }); qc.invalidateQueries({ queryKey: ['node'] }); qc.invalidateQueries({ queryKey: ['obs-alerts'] }) }
  const put = (body, msg) => api.put(`/nodes/${node.id}`, body).then(() => { toast(msg); done() }).catch((e) => toast(e.message, 'err'))
  const test = () => api.post('/obs/test').then((r) => toast(`Prometheus répond · ${r.up} / ${r.targets} cible(s) en ligne`)).catch((e) => toast(e.message, 'err'))
  const log = c.accessLog
  return (
    <div className="card col">
      <div className="row"><h3>Surveillance (facultatif)</h3><Info>Prometheus donne les métriques et les alertes ; le journal d'accès de Traefik donne les adresses IP. Sans eux, ces écrans sont simplement masqués.</Info></div>
      {platformAdmin
        ? <Field label="Adresse de Prometheus" help="Active les métriques et les alertes. Ex : http://prometheus:9090 (réseau Docker partagé).">
          <div className="row"><input className="input mono grow" value={promVal} onChange={(e) => setProm(e.target.value)} placeholder="http://prometheus:9090" />
            <Btn disabled={prom === null || prom === c.prometheusUrl} onClick={() => put({ prometheusUrl: prom.trim() }, prom.trim() ? 'Prometheus branché' : 'Prometheus débranché')}>Enregistrer</Btn>
            <Btn disabled={!c.prometheusUrl} onClick={test}>Tester</Btn></div></Field>
        : <div className="muted small"><InfoIcon size={13} style={{ verticalAlign: -2 }} /> {c.prometheusUrl ? `Prometheus : ${c.prometheusUrl}` : 'Prometheus n\'est pas branché.'} Seul un administrateur de la plateforme peut changer cette adresse.</div>}
      {platformAdmin && <Field label="Webhook des alertes" help={c.webhookSet ? 'Un webhook est défini (son adresse n\'est jamais ré-affichée). Collez-en une autre pour la remplacer.' : 'Reçoit un message JSON à chaque alerte déclenchée ou résolue (Discord, Slack, ntfy, Gotify, n8n…).'}>
        <div className="row"><input className="input mono grow" type="password" value={hook} onChange={(e) => setHook(e.target.value)} placeholder={c.webhookSet ? '•••••••• (défini)' : 'https://…'} autoComplete="off" />
          <Btn disabled={!hook.trim()} onClick={() => put({ webhookUrl: hook.trim() }, 'Webhook enregistré').then(() => setHook(''))}>Enregistrer</Btn>
          {c.webhookSet && <Btn kind="ghost" onClick={() => put({ webhookUrl: '' }, 'Webhook retiré')}>Retirer</Btn>}</div></Field>}
      <Field label="Journal d'accès de Traefik (adresses IP)" help="Fichier lu en continu (JSON ou format commun), monté en lecture seule dans cette interface. Se règle par la variable TRAEFIK_ACCESS_LOG du .env : voir l'aide.">
        <div className="row wrap">{log.path ? <code className="ellipsis">{log.path}</code> : <span className="muted">non configuré</span>}
          {log.available ? <Badge kind={log.running ? 'ok' : 'warn'}>{log.running ? 'lu en continu' : 'à l\'arrêt'}</Badge> : log.path ? <Badge kind="err">fichier introuvable</Badge> : null}
          {log.error && <span className="muted small">{log.error}</span>}</div></Field>
    </div>
  )
}

// ------------------------------------------------------------------ membres
const initial = (n) => n.trim().charAt(0).toUpperCase()

function RolePicker({ value, onChange, roles }) {
  return <div className="role-grid">{['admin', 'editor', 'viewer'].map((r) => (
    <button type="button" key={r} className={'role-card ' + (value === r ? 'sel' : '')} disabled={!roles.includes(r)} onClick={() => onChange(r)}>
      <b>{ROLES[r].label}</b><span className="muted small">{ROLES[r].help}</span></button>))}</div>
}

function Members({ node, role, data, refresh }) {
  const { toast, confirm } = useUi()
  const [edit, setEdit] = useState(null); const [add, setAdd] = useState(false)
  if (!data) return <Loading />
  const { members, assignable } = data
  const remove = async (m) => { if (await confirm({ title: `Retirer ${m.username} ?`, message: `Il n'aura plus accès au nœud « ${node.name} ».`, confirmLabel: 'Retirer' })) api.del(`/nodes/${node.id}/members/${m.userId}`).then(() => { refresh(); toast('Membre retiré') }).catch((e) => toast(e.message, 'err')) }
  const canEditRow = (m) => !m.implicit && !m.viaGrant && RANK[m.role] < RANK[role]
  return (
    <div className="col gap-lg">
      <div className="row"><div className="muted grow">Qui peut utiliser ce nœud, et avec quels droits. Vous ne pouvez jamais donner plus de droits que vous n'en avez.</div>
        <NavLink to="/rights" className="btn">Rôles personnalisés et groupes</NavLink><Btn kind="primary" onClick={() => setAdd(true)}><UserPlus size={15} /> Ajouter un membre</Btn></div>
      <div className="card flush"><table className="t"><thead><tr><th>Membre</th><th>Rôle<Info>Propriétaire : tous les droits. Administrateur : réglages et édition complète. Éditeur : crée des sites sur ses domaines. Lecteur : consulte seulement.</Info></th><th>Cibles autorisées<Info>Les serveurs vers lesquels un éditeur a le droit d'envoyer du trafic (un site ne peut pas viser n'importe quelle adresse interne). Vide = aucune.</Info></th><th /></tr></thead>
        <tbody>{members.map((m) => (
          <tr key={m.userId} style={{ opacity: m.disabled ? .5 : 1 }}>
            <td><div className="row"><div className="avatar">{initial(m.username)}</div><b>{m.username}</b>{m.disabled && <Badge kind="err">désactivé</Badge>}</div></td>
            <td><Badge kind={ROLES[m.role].badge}>{m.role === 'owner' && <Crown size={11} />} {m.viaGrant ? m.roleLabel : ROLES[m.role].label}</Badge>{m.implicit && <span className="muted small"> administrateur de la plateforme</span>}{m.viaGrant && <span className="muted small"> attribué dans « Droits »</span>}{m.expiresAt && <span className="muted small" title="Accès temporaire"> · jusqu'au {fmtDate(m.expiresAt * 1000)}</span>}</td>
            <td>{m.role === 'editor' ? (m.targetHosts ? <code>{m.targetHosts}</code> : <span className="muted small">services existants seulement</span>) : <span className="muted">—</span>}</td>
            <td className="actions">{canEditRow(m) && <><Btn small kind="ghost" onClick={() => setEdit(m)}><Pencil size={13} /> Modifier</Btn><Btn small kind="ghost" icon onClick={() => remove(m)}><Trash2 size={14} /></Btn></>}</td>
          </tr>))}</tbody></table></div>
      <div className="card"><h3 style={{ marginBottom: 10 }}>Les rôles</h3>
        <div className="role-grid">{['owner', 'admin', 'editor', 'viewer'].map((r) => <div key={r} className="col" style={{ gap: 3 }}><Badge kind={ROLES[r].badge}>{ROLES[r].label}</Badge><span className="muted small">{ROLES[r].help}</span></div>)}</div>
        <div className="muted small" style={{ marginTop: 12 }}>Les éditeurs et lecteurs ne voient que les sites de <b>leurs domaines</b> : c'est l'accès au domaine (onglet « Domaines » ou menu « Comptes OVH ») qui détermine où ils peuvent écrire.</div></div>
      {role === 'owner' && members.some((m) => !m.implicit && m.role !== 'owner') && <Transfer node={node} members={members} done={() => { refresh() }} />}
      {add && <MemberModal node={node} assignable={assignable} onClose={() => setAdd(false)} onSaved={refresh} />}
      {edit && <MemberModal node={node} member={edit} assignable={assignable} onClose={() => setEdit(null)} onSaved={refresh} />}
    </div>
  )
}

function MemberModal({ node, member, assignable, onClose, onSaved }) {
  const { toast } = useUi(); const isNew = !member
  const [f, setF] = useState({ username: '', role: member?.role || (assignable.includes('editor') ? 'editor' : assignable[0]), targetHosts: member?.targetHosts || '' })
  const [dur, setDur] = useState(member?.expiresAt ? 'keep' : ''); const [custom, setCustom] = useState('')
  const expiry = () => (dur === '' || dur === 'keep' ? undefined : dur === 'custom' ? (custom ? new Date(custom).getTime() / 1000 : undefined) : Math.floor(Date.now() / 1000) + Number(dur))
  const set = (k, v) => setF({ ...f, [k]: v })
  const save = useMutation({
    mutationFn: () => isNew ? api.post(`/nodes/${node.id}/members`, { ...f, expiresAt: expiry() }) : api.put(`/nodes/${node.id}/members/${member.userId}`, { role: f.role, targetHosts: f.targetHosts, expiresAt: expiry(), clearExpiry: dur === '' && !!member.expiresAt }),
    onSuccess: () => { toast(isNew ? 'Membre ajouté' : 'Membre modifié'); onSaved(); onClose() }, onError: (e) => toast(e.message, 'err'),
  })
  return (
    <Modal title={isNew ? 'Ajouter un membre' : `Modifier ${member.username}`} onClose={onClose}
      footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={save.isPending} disabled={isNew && !f.username.trim()} onClick={() => save.mutate()}>{isNew ? 'Ajouter' : 'Enregistrer'}</Btn></>}>
      <div className="col gap-lg">
        {isNew && <Field label="Nom d'utilisateur" help="L'identifiant exact du compte (les comptes se créent dans « Équipe »)."><input className="input" autoFocus value={f.username} onChange={(e) => set('username', e.target.value)} autoComplete="off" /></Field>}
        <Field label="Rôle"><RolePicker value={f.role} onChange={(r) => set('role', r)} roles={assignable} /></Field>
        <Field label="Durée de l'accès" tip="Accès temporaire : ce membre perd son rôle tout seul à l'échéance (journalisé)."><div className="row"><select className="select" value={dur} onChange={(e) => setDur(e.target.value)}>{member?.expiresAt && <option value="keep">Garder l'échéance actuelle</option>}<option value="">Sans limite</option><option value="3600">1 heure</option><option value="86400">24 heures</option><option value="604800">7 jours</option><option value="2592000">30 jours</option><option value="custom">Date précise…</option></select>{dur === 'custom' && <input className="input" type="datetime-local" value={custom} onChange={(e) => setCustom(e.target.value)} />}</div></Field>
        {f.role === 'editor' && <Field label="Cibles autorisées" help="Noms d'hôte ou IP vers lesquels il peut envoyer du trafic, séparés par des virgules ; * accepté. Ex : mon-app-*, 192.168.1.*. Vide = il ne peut que réutiliser des services existants."><input className="input mono" value={f.targetHosts} onChange={(e) => set('targetHosts', e.target.value)} placeholder="alice-*, 10.0.0.*" /></Field>}
      </div>
    </Modal>
  )
}

function Transfer({ node, members, done }) {
  const { toast, confirm } = useUi()
  const [to, setTo] = useState('')
  const go = async () => {
    const m = members.find((x) => String(x.userId) === to)
    if (await confirm({ title: `Transférer « ${node.name} » à ${m.username} ?`, message: `${m.username} deviendra propriétaire. Vous resterez administrateur du nœud, mais il pourra vous retirer ce rôle.`, confirmLabel: 'Transférer' }))
      api.post(`/nodes/${node.id}/transfer`, { userId: Number(to) }).then(() => { toast('Propriété transférée'); done() }).catch((e) => toast(e.message, 'err'))
  }
  return (
    <div className="card col"><h3><ShieldAlert size={13} style={{ verticalAlign: -2 }} /> Zone sensible</h3>
      <div className="muted">Transférer le nœud à un autre membre. Seul le propriétaire peut le faire.</div>
      <div className="row wrap"><select className="select" style={{ maxWidth: 260 }} value={to} onChange={(e) => setTo(e.target.value)}>
        <option value="">— choisir un membre —</option>{members.filter((m) => !m.implicit && m.role !== 'owner').map((m) => <option key={m.userId} value={m.userId}>{m.username} ({ROLES[m.role].label})</option>)}</select>
        <Btn kind="danger" disabled={!to} onClick={go}>Transférer la propriété</Btn></div></div>
  )
}

// ------------------------------------------------------------------ domaines
function Domains({ node, user, onChange }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const list = useQuery({ queryKey: ['node-domains', node.id], queryFn: () => api.get(`/nodes/${node.id}/domains`) })
  const [imp, setImp] = useState(false)
  const refresh = () => { list.refetch(); onChange(); qc.invalidateQueries({ queryKey: ['domains'] }); qc.invalidateQueries({ queryKey: ['zones'] }) }
  const unlink = async (d) => { if (await confirm({ title: `Retirer ${d.name} de ce nœud ?`, message: 'Le domaine reste intact chez OVH et sur ses autres nœuds ; il disparaît seulement d\'ici (et les sites de ce nœud qui l\'utilisent ne seront plus gérables pour les éditeurs).', confirmLabel: 'Retirer du nœud' })) api.del(`/nodes/${node.id}/domains/${d.name}`).then(() => { refresh(); toast('Domaine retiré du nœud') }).catch((e) => toast(e.message, 'err')) }
  return (
    <div className="col gap-lg">
      <div className="row"><div className="muted grow">Les domaines disponibles sur ce nœud. Un même domaine peut être rattaché à plusieurs nœuds : les clés OVH ne sont jamais copiées.{node.traefikVia && ' Ce nœud publie ses sites avec un autre Traefik : les domaines de ce Traefik apparaissent aussi ici, en lecture seule.'}</div>
        {user.canAddDomains && <Btn kind="primary" onClick={() => setImp(true)}><Download size={15} /> Importer un domaine</Btn>}</div>
      {list.isLoading ? <Loading /> : !list.data.length ? <div className="card"><Empty><Globe size={30} /><div>Aucun domaine sur ce nœud.{user.canAddDomains ? <><br />Importez un de vos domaines, ou ajoutez un compte dans « Comptes OVH ».</> : <><br />Un administrateur de la plateforme ou le propriétaire d'un domaine peut le rattacher ici.</>}</div></Empty></div> : (
        <div className="card flush">{list.data.map((d) => (
          <div className="link-card" key={d.name}><Globe size={16} className="muted" /><b className="grow">{d.name}</b>
            {d.system ? <Badge kind="accent">compte principal</Badge> : <Badge>{d.mine ? 'à vous' : d.owner}</Badge>}
            {d.inherited ? <Badge kind="info" title={`Ce nœud publie ses sites avec le Traefik de « ${d.inherited} » : ses domaines valent aussi ici. Ils se gèrent depuis ce nœud.`}>via {d.inherited}</Badge>
              : <Btn small kind="ghost" icon title="Retirer de ce nœud" onClick={() => unlink(d)}><Trash2 size={14} /></Btn>}</div>))}</div>)}
      {imp && <ImportDomains node={node} onClose={() => setImp(false)} onDone={refresh} />}
    </div>
  )
}

function ImportDomains({ node, onClose, onDone }) {
  const { toast } = useUi()
  const q = useQuery({ queryKey: ['importable', node.id], queryFn: () => api.get(`/nodes/${node.id}/importable`) })
  const [sel, setSel] = useState(new Set())
  const go = () => api.post(`/nodes/${node.id}/domains`, { names: [...sel] }).then((r) => { toast(`${r.added.length} domaine(s) rattaché(s)`); onDone(); onClose() }).catch((e) => toast(e.message, 'err'))
  const toggle = (n, on) => { const s = new Set(sel); on ? s.add(n) : s.delete(n); setSel(s) }
  return (
    <Modal title="Importer un domaine sur ce nœud" onClose={onClose} footer={<><Btn onClick={onClose}>Fermer</Btn><Btn kind="primary" disabled={!sel.size} onClick={go}>Importer {sel.size || ''}</Btn></>}>
      <div className="col">
        <div className="banner info" style={{ margin: 0 }}><InfoIcon size={16} /><div>Importer ne copie <b>pas</b> vos clés OVH : le domaine reste géré depuis votre compte et apparaît simplement aussi sur ce nœud.</div></div>
        {q.isLoading ? <Loading /> : !q.data.length ? <div className="empty">Tous vos domaines sont déjà sur ce nœud.<br /><span className="small">Pour en ajouter de nouveaux, branchez un compte dans « Comptes OVH ».</span></div> :
          q.data.map((d) => <label key={d.name} className="row"><input type="checkbox" checked={sel.has(d.name)} onChange={(e) => toggle(d.name, e.target.checked)} /><b>{d.name}</b><span className="muted small">{d.account}</span>
            <span className="right row" style={{ gap: 4 }}>{d.nodes.length ? d.nodes.map((n) => <Badge key={n}>{n}</Badge>) : <Badge kind="warn">sur aucun nœud</Badge>}</span></label>)}
      </div>
    </Modal>
  )
}

// ------------------------------------------------------------------ activité
function ActivityTab({ node, platformAdmin }) {
  const [scope, setScope] = useState('node')
  const q = useQuery({ queryKey: ['node-audit', node.id, scope], queryFn: () => api.get(`/nodes/${node.id}/audit${scope === 'platform' ? '?scope=platform' : ''}`) })
  const check = useQuery({ queryKey: ['audit-verify'], queryFn: () => api.get('/admin/audit/verify'), enabled: platformAdmin })
  if (q.isLoading) return <Loading />
  const v = check.data
  return (
    <div className="col gap-lg">
      {platformAdmin && <div className="row"><Seg value={scope} onChange={setScope} options={[{ id: 'node', label: 'Ce nœud', hint: 'Ce qui s\'est passé sur le Traefik de ce nœud' }, { id: 'platform', label: 'Plateforme', hint: 'Connexions, comptes, DNS, 2FA : ce qui ne dépend d\'aucun nœud' }]} /></div>}
      {v && (v.ok
        ? <div className="banner ok" style={{ margin: 0 }}><ShieldCheck size={16} /> Journal intact : {v.chained} ligne{v.chained > 1 ? 's' : ''} signée{v.chained > 1 ? 's' : ''}{v.legacy ? `, ${v.legacy} plus ancienne${v.legacy > 1 ? 's' : ''} non signée${v.legacy > 1 ? 's' : ''}` : ''}.</div>
        : <div className="banner err" style={{ margin: 0 }}><ShieldAlert size={16} /> Le journal a été altéré (ligne {v.problem.line}) : {v.problem.reason}.</div>)}
      <ActivityFeed entries={q.data} />
    </div>)
}

// ------------------------------------------------------------------ connexion du worker (nœuds distants)
function Connection({ node, role, reload }) {
  const { toast, confirm } = useUi(); const nav = useNavigate()
  const [setup, setSetup] = useState(null); const [del, setDel] = useState(false)
  const [state, label] = CONN[node.connection]
  const renew = async () => {
    if (node.connection !== 'pending' && !(await confirm({ title: 'Reconnecter un worker ?', message: 'Le worker actuel est révoqué immédiatement : il ne pourra plus se connecter. Un nouveau jeton est généré pour le nouveau worker ou pour réinstaller celui-ci.', confirmLabel: 'Révoquer et générer un jeton' }))) return
    api.post(`/nodes/${node.id}/worker/token`).then((r) => { setSetup(r); reload() }).catch((e) => toast(e.message, 'err'))
  }
  return (
    <div className="col gap-lg" style={{ maxWidth: 760 }}>
      <div className="card col">
        <div className="row"><h3>Worker</h3><Info>Petit programme installé sur la machine distante. Il se connecte à cette interface (aucun port à ouvrir là-bas) et lit/écrit les fichiers Traefik de sa machine.</Info><span className="grow" /><Badge kind={state}>{label}</Badge></div>
        {node.connection === 'pending'
          ? <div className="muted">Aucun worker n'a encore été installé pour ce nœud. Générez un jeton pour obtenir les instructions.</div>
          : <div className="fgrid"><Field label="Version">{node.workerVersion || '—'}</Field><Field label="Dernier signe de vie">{node.lastSeen ? fmtDate(node.lastSeen * 1000) : '—'}</Field></div>}
        {node.connection === 'offline' && <div className="banner" style={{ margin: 0 }}>Le worker ne répond plus. Vérifiez que son conteneur tourne sur la machine distante (<code>docker compose logs armadok-worker</code>). Le Traefik distant continue de fonctionner normalement ; seule l'édition depuis ici est indisponible.</div>}
        <div className="row wrap"><Btn onClick={renew}><RefreshCw size={14} /> {node.connection === 'pending' ? 'Générer un jeton' : 'Reconnecter / remplacer le worker'}</Btn></div>
      </div>
      {role === 'owner' && <div className="card col"><h3><ShieldAlert size={13} style={{ verticalAlign: -2 }} /> Zone sensible</h3>
        <div className="muted">Supprimer ce nœud retire ses membres et ses rattachements de domaines, et coupe le worker. Les fichiers restent sur la machine distante ; vos domaines OVH ne sont pas touchés.</div>
        <div><Btn kind="danger" onClick={() => setDel(true)}><Trash2 size={14} /> Supprimer ce nœud</Btn></div></div>}
      {setup && <Modal wide title="Installer le worker" onClose={() => setSetup(null)} footer={<Btn kind="primary" onClick={() => setSetup(null)}>Fermer</Btn>}>
        <WorkerSetup node={node} token={setup.token} expires={setup.tokenExpires} onOnline={() => reload()} /></Modal>}
      {del && <DeleteNode node={node} onClose={() => setDel(false)} onDone={() => { reload(); nav('/') }} />}
    </div>
  )
}

function DeleteNode({ node, onClose, onDone }) {
  const { toast } = useUi(); const [typed, setTyped] = useState('')
  const go = () => api.del(`/nodes/${node.id}`).then(() => { toast('Nœud supprimé'); onDone() }).catch((e) => toast(e.message, 'err'))
  return (
    <Modal small title={`Supprimer « ${node.name} » ?`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="danger" disabled={typed !== node.name} onClick={go}>Supprimer définitivement</Btn></>}>
      <div className="col"><div>Cette action est irréversible. Pour confirmer, tapez le nom du nœud :</div>
        <code>{node.name}</code><input className="input" autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} /></div>
    </Modal>)
}
