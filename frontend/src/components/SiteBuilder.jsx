import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Box, Trash2, CornerUpRight, Database, FileKey, FileType, FolderMinus, FolderPlus, Gauge, GripVertical, IndentDecrease, IndentIncrease, Globe, Link2, Network, Package, Plug, Plus, Puzzle, Replace, RotateCw, Shield, ShieldCheck, KeyRound, TriangleAlert, X, Zap, Lock, Send, Pencil, Code2 } from 'lucide-react'
import { api } from '../api.js'
import { verifyDeployed } from '../lib/verify.js'
import { refreshZone } from '../lib/dns.js'
import { useSiteRemoval } from './SiteRemoval.jsx'
import { Badge, Btn, Field, Info, Modal, Switch, useUi, SearchBox } from './ui.jsx'
import { Chips } from './SchemaForm.jsx'
import CodeEditor from './CodeEditor.jsx'
import { useAuth } from '../auth.jsx'
import { useNode } from '../node.jsx'
import { useHostStatus } from './HostDns.jsx'
import { clampLevels, condRow, hostRow, rowsHosts, rowsToRule, ruleToRows } from '../lib/rule.js'
import { extractHosts, toYamlPretty } from '../lib/util.js'
import { COND_LABELS, GROUP_COLOR, humanOf, techName } from '../traefik/human.js'
import { useMeta, useOverview, ItemEditor } from '../pages/Traefik.jsx'

const ICONS = { KeyRound, ShieldCheck, Network, FileKey, Shield, CornerUpRight, FolderPlus, FolderMinus, Replace, Package, FileType, Database, TriangleAlert, Link2, Plug, Gauge, RotateCw, Zap, Puzzle, Box }
const MwIcon = ({ type, size = 16 }) => { const I = ICONS[humanOf(type)[1]] || Box; return <I size={size} /> }
const color = (type) => GROUP_COLOR[humanOf(type)[2]]
const ROUTER_OK = ['rule', 'entryPoints', 'service', 'middlewares', 'priority', 'tls']

function mwHint(spec = {}, type) {
  const b = spec[type]
  if (!b || typeof b !== 'object') return ''
  return b.sourceRange?.join(', ') || b.middlewares?.join(' → ') || b.address || b.prefix || b.scheme || b.regex || (b.average && `${b.average} req/${b.period || '1s'}`) || (b.users && `${b.users.length} utilisateur(s)`) || ''
}

function Block({ tag, c, children, icon, tip }) {
  return <div className="blk" style={{ '--bc': c }}><span className="tag">{icon}{tag}{tip && <Info>{tip}</Info>}</span>{children}</div>
}

// ------------------------------------------------------------------ bloc « QUAND »
function HostRow({ value, onChange, domains, free, lead, actions }) {
  const zone = domains.map((d) => d.name).sort((a, b) => b.length - a.length).find((z) => value === z || value.endsWith('.' + z))
  const [custom, setCustom] = useState(!zone && !!value)
  const sub = zone ? (value === zone ? '' : value.slice(0, -(zone.length + 1))) : ''
  if (custom || (!domains.length && free)) return (
    <div className="mini row" style={{ '--bc': '#1c7ed6' }}>{lead}<Globe size={15} /><input className="input mono" value={value} onChange={(e) => onChange(e.target.value.toLowerCase())} placeholder="app.exemple.fr" />
      {domains.length > 0 && <Btn small kind="ghost" onClick={() => { setCustom(false); onChange('') }}>mes domaines</Btn>}{actions}</div>
  )
  return (
    <div className="mini row" style={{ '--bc': '#1c7ed6' }}>{lead}<Globe size={15} />
      <input className="input" style={{ maxWidth: 190 }} value={sub} placeholder="sous-domaine" onChange={(e) => onChange((e.target.value.toLowerCase().trim() ? e.target.value.toLowerCase().trim() + '.' : '') + (zone || domains[0]?.name))} />
      <span className="muted">.</span>
      <select className="select" style={{ maxWidth: 250 }} value={zone || ''} onChange={(e) => e.target.value === '__x' ? setCustom(true) : onChange((sub ? sub + '.' : '') + e.target.value)}>
        {!zone && <option value="">— domaine —</option>}{domains.map((d) => <option key={d.name}>{d.name}</option>)}{free && <option value="__x">autre domaine…</option>}</select>
      {actions}</div>
  )
}

function CondRow({ c, onChange, lead, actions }) {
  const [label, p1, p2] = COND_LABELS[c.fn] || [`${c.fn}(…)`, 'valeur']
  return (
    <div className="mini row" style={{ '--bc': '#1c7ed6' }}>
      {lead}
      <select className="select" style={{ maxWidth: 110, minWidth: 90 }} value={c.neg ? '1' : ''} onChange={(e) => onChange({ ...c, neg: !!e.target.value })}><option value="">est</option><option value="1">n'est pas</option></select>
      <b style={{ whiteSpace: 'nowrap' }} title={label}>{c.fn}</b><span className="muted small">{label}</span>
      <input className="input mono" value={c.args[0] ?? ''} placeholder={p1} onChange={(e) => onChange({ ...c, args: [e.target.value, ...c.args.slice(1)] })} />
      {(p2 || c.args.length > 1) && <input className="input mono" value={c.args[1] ?? ''} placeholder={p2} onChange={(e) => onChange({ ...c, args: [c.args[0], e.target.value] })} />}
      {actions}
    </div>
  )
}

// Une ligne de la règle : poignée (glisser-déposer), lien ET / OU cliquable avec la ligne précédente, indentation (+ / −), retrait.
// Les lignes plus indentées qui se suivent forment un groupe : comme des parenthèses.
function RuleRow({ r, i, rows, setRows, domains, free, dragFrom, dropAt, setDropAt }) {
  const patch = (p) => setRows(rows.map((x) => (x.id === r.id ? { ...x, ...p } : x)))
  const maxLevel = i === 0 ? 0 : rows[i - 1].level + 1
  const lead = (
    <>
      <span className="grip" draggable title="Glisser pour changer l'ordre"
        onDragStart={(e) => { dragFrom.current = i; e.dataTransfer.effectAllowed = 'move'; const row = e.currentTarget.closest('.rrow'); if (row) e.dataTransfer.setDragImage(row, 12, 12) }} onDragEnd={() => setDropAt(null)}><GripVertical size={16} /></span>
      {i === 0
        ? <span className="rop first" title="Début de la règle">SI</span>
        : <button type="button" className={'rop ' + r.op} title={`Lien avec la ligne précédente : cliquez pour passer à ${r.op === 'or' ? 'ET (les deux doivent être vrais)' : 'OU (l’un des deux suffit)'}`} onClick={() => patch({ op: r.op === 'or' ? 'and' : 'or' })}>{r.op === 'or' ? 'OU' : 'ET'}</button>}
    </>
  )
  const actions = (
    <span className="ractions">
      <button type="button" title="Moins indenté : cette ligne ne fait plus partie de la ligne du dessus" disabled={r.level === 0} onClick={() => patch({ level: r.level - 1 })}><IndentDecrease size={14} /></button>
      <button type="button" title="Plus indenté : cette ligne fait partie de la ligne du dessus (elles forment un bloc, comme des parenthèses)" disabled={r.level >= maxLevel} onClick={() => patch({ level: r.level + 1 })}><IndentIncrease size={14} /></button>
      <button type="button" title="Retirer cette ligne" onClick={() => setRows(rows.length > 1 ? rows.filter((x) => x.id !== r.id) : [hostRow('')])}><X size={14} /></button>
    </span>
  )
  // Glisser-déposer : on dépose ENTRE deux lignes (moitié haute d'une ligne = avant, moitié basse = après), un trait indique où elle ira.
  // La ligne garde son lien ET / OU et prend l'indentation de celle qui la précède ; les flèches d'indentation permettent d'ajuster.
  const where = (e) => { const b = e.currentTarget.getBoundingClientRect(); return e.clientY < b.top + b.height / 2 ? i : i + 1 }
  const over = (e) => { if (dragFrom.current === null) return; e.preventDefault(); setDropAt(where(e)) }
  const drop = (e) => {
    const f = dragFrom.current; dragFrom.current = null; setDropAt(null)
    if (f === null) return
    let t = where(e)
    if (t === f || t === f + 1) return                          // même place
    const rest = rows.filter((_, k) => k !== f)
    if (t > f) t -= 1
    const prev = rest[t - 1] || rest[t]
    rest.splice(t, 0, { ...rows[f], level: prev ? prev.level : 0 })
    setRows(clampLevels(rest))
  }
  return (
    <div className={'rrow ' + (r.level ? 'nested ' : '') + (dropAt === i ? 'drop-before ' : '') + (dropAt === i + 1 && i === rows.length - 1 ? 'drop-after' : '')} style={{ marginLeft: r.level * 26 }} onDragOver={over} onDrop={drop}>
      {r.kind === 'host'
        ? <HostRow value={r.value} domains={domains} free={free} onChange={(v) => patch({ value: v })} lead={lead} actions={actions} />
        : <CondRow c={r} onChange={(c) => patch({ neg: c.neg, args: c.args })} lead={lead} actions={actions} />}
    </div>
  )
}

function MwPicker({ items, taken, onPick, onCreate, onClose, canCreate }) {
  const [q, setQ] = useState('')
  const list = items.filter((i) => !taken.includes(i.name) && (!q || `${i.name} ${techName(i.type)} ${humanOf(i.type)[0]}`.toLowerCase().includes(q.toLowerCase())))
  return (
    <Modal wide title="Ajouter un bloc" onClose={onClose}>
      <div className="col">
        <div className="row"><SearchBox value={q} onChange={setQ} placeholder="Chercher un comportement…" />{canCreate && <Btn className="right" kind="primary" onClick={onCreate}><Plus size={14} /> Créer un nouveau bloc</Btn>}</div>
        {list.length === 0 && <div className="empty">Aucun bloc disponible.{canCreate ? ' Créez-en un !' : ''}</div>}
        <div className="pick-grid">{list.map((i) => (
          <button key={i.name} className="pick" title={humanOf(i.type)[0]} style={{ '--bc': color(i.type) }} onClick={() => onPick(i.name)}>
            <span className="pick-ic"><MwIcon type={i.type} size={20} /></span>
            <b>{i.name}</b><span>{techName(i.type)}</span><small className="muted ellipsis">{mwHint(i.spec, i.type)}</small>
          </button>))}</div>
      </div>
    </Modal>
  )
}

function MwRow({ name, items, idx, total, move, onRemove, onEdit, dragProps, canEdit }) {
  const it = items.find((i) => i.name === name)
  const chain = it?.type === 'chain' ? it.spec.chain?.middlewares : null
  return (
    <div className="mini row" style={{ '--bc': color(it?.type) }} {...dragProps}>
      <span className="grip"><GripVertical size={16} /></span>
      <span className="num">{idx + 1}</span>
      <span className="ic"><MwIcon type={it?.type} /></span>
      <div className="grow"><b>{name}</b> <span className="muted" title={it ? humanOf(it.type)[0] : ''}>{it ? techName(it.type) : name.includes('@') ? 'défini ailleurs' : 'introuvable'}</span>
        {chain ? <div className="row wrap" style={{ gap: 4, marginTop: 4 }}>{chain.map((c) => <Badge key={c}>{c}</Badge>)}</div> : it && <div className="muted small ellipsis">{mwHint(it.spec, it.type)}</div>}</div>
      {!it && !name.includes('@') && <Badge kind="err">manquant</Badge>}
      <Btn small kind="ghost" icon disabled={idx === 0} onClick={() => move(idx, -1)} title="Monter"><ArrowUp size={14} /></Btn>
      <Btn small kind="ghost" icon disabled={idx === total - 1} onClick={() => move(idx, 1)} title="Descendre"><ArrowDown size={14} /></Btn>
      {canEdit && it && <Btn small kind="ghost" icon onClick={() => onEdit(it)} title="Modifier ce bloc"><Pencil size={14} /></Btn>}
      <Btn small kind="ghost" icon onClick={onRemove} title="Retirer"><X size={14} /></Btn>
    </div>
  )
}

// ------------------------------------------------------------------ constructeur
export default function SiteBuilder({ item, prefill = {}, onClose, onReload }) {
  const { user } = useAuth(); const { canManage, can } = useNode(); const { toast, confirm } = useUi(); const qc = useQueryClient()
  const ov = useOverview(); const meta = useMeta(); const removal = useSiteRemoval()
  const items = ov.data?.items || []
  const isAdmin = canManage // droits complets sur ce nœud (propriétaire / administrateur du nœud)
  const domainsQ = useQuery({ queryKey: ['domains'], queryFn: () => api.get('/domains') })
  const domains = (domainsQ.data || []).filter((d) => d.level !== 'view')
  const isNew = !item
  const editable = isNew || item.editable
  const dup = !item ? prefill.from || null : null      // « Dupliquer » : on repart des réglages d'un site existant, avec une adresse vide
  const base = item?.spec || dup?.spec || {}
  const svcItem = items.find((i) => i.proto === 'http' && i.kind === 'services' && i.name === base.service)

  // --- état initial
  const parsed = useMemo(() => ruleToRows(base.rule), [])
  const [name, setName] = useState(item?.name || prefill.name || '')
  const [nameTouched, setNameTouched] = useState(!isNew || !!prefill.name)
  const [rows, setRows] = useState(() => {   // les lignes de la règle ; il y a TOUJOURS au moins une adresse au départ
    if (!dup && parsed && parsed.length) return parsed
    return [hostRow(prefill.sub !== undefined && prefill.zone ? (prefill.sub ? `${prefill.sub}.${prefill.zone}` : prefill.zone) : '')]
  })
  const ruleDrag = useRef(null); const [dropAt, setDropAt] = useState(null)
  const [rawRule, setRawRule] = useState(dup || parsed ? null : base.rule)
  const [mws, setMws] = useState(base.middlewares || null)
  const [eps, setEps] = useState(base.entryPoints || null)
  const [resolver, setResolver] = useState(base.tls?.certResolver ?? null)
  const [priority, setPriority] = useState(base.priority ?? '')
  const lb = svcItem?.spec?.loadBalancer
  const svcOk = !svcItem || (svcItem.type === 'loadBalancer' && lb?.servers?.every((s) => s.url))
  const [svcMode, setSvcMode] = useState(isNew || svcOk ? 'inline' : 'ref')
  const [urls, setUrls] = useState(lb?.servers?.map((s) => s.url) || [prefill.url || ''])
  const [refName, setRefName] = useState(base.service || '')
  const [dnsAdd, setDnsAdd] = useState(isNew && prefill.dns !== false)
  const [dnsIp, setDnsIp] = useState('')   // adresse à créer quand aucune n'est connue (typiquement : IP privée de Traefik pour AdGuard Home)
  const [dnsType, setDnsType] = useState('A')
  const [picker, setPicker] = useState(false); const [creating, setCreating] = useState(false); const [editMw, setEditMw] = useState(null)
  const [showYaml, setShowYaml] = useState(false); const [busy, setBusy] = useState(false)
  const dragFrom = useRef(null)

  useEffect(() => { // valeurs par défaut quand les métadonnées arrivent
    if (!meta.data) return
    if (eps === null) setEps([meta.data.entrypoints.includes('websecure') ? 'websecure' : meta.data.entrypoints[0]].filter(Boolean))
    if (resolver === null) setResolver(isNew ? (meta.data.certResolvers[0] || '') : '')
  }, [meta.data])
  useEffect(() => { if (mws === null && ov.data) setMws(isNew && items.some((i) => i.kind === 'middlewares' && i.name === 'private-site') ? ['private-site'] : []) }, [ov.data])
  useEffect(() => { // nouveau site : première adresse vide -> premier domaine
    const first = rows.find((r) => r.kind === 'host')
    if (isNew && first && !first.value && domains[0]) setRows(rows.map((r) => (r === first ? { ...r, value: domains[0].name } : r)))
  }, [domainsQ.data])

  const hostsClean = rowsHosts(rows)
  const rule = rawRule !== null ? rawRule : rowsToRule(rows)
  useEffect(() => { if (!nameTouched) { const h = hostsClean[0] || ''; const z = domains.map((d) => d.name).find((d) => h === d || h.endsWith('.' + d)); setName((z && h !== z ? h.slice(0, -(z.length + 1)) : h).replace(/\./g, '-')) } }, [hostsClean.join()])

  const dns = useHostStatus(hostsClean)
  const mwItems = items.filter((i) => i.proto === 'http' && i.kind === 'middlewares' && !i.disabled)
  const sharedBy = dup ? [] : (svcItem?.usedBy || []).filter((u) => u !== `http/routers/${item?.name}`)
  const sname = svcMode === 'inline' ? (svcItem && item ? svcItem.name : name) : refName

  // --- spec générée
  const router = useMemo(() => {
    const keep = isAdmin ? base : Object.fromEntries(Object.entries(base).filter(([k]) => ROUTER_OK.includes(k)))
    const tlsBase = isAdmin ? base.tls || {} : Object.fromEntries(Object.entries(base.tls || {}).filter(([k]) => ['certResolver', 'options'].includes(k)))
    const tls = { ...tlsBase }; if (resolver) tls.certResolver = resolver; else delete tls.certResolver
    const spec = { ...keep, rule, entryPoints: eps || [], service: sname, tls }
    if (mws?.length) spec.middlewares = mws; else delete spec.middlewares
    if (priority !== '' && priority !== null) spec.priority = Number(priority); else delete spec.priority
    if (!Object.keys(tls).length) delete spec.tls
    return spec
  }, [rule, eps, sname, mws, resolver, priority])
  const service = useMemo(() => {
    if (svcMode !== 'inline') return null
    const old = lb?.servers || []
    const servers = urls.filter(Boolean).map((u, i) => ({ ...(old[i] || {}), url: u }))
    return { ...(svcItem?.spec || {}), loadBalancer: { ...(lb || {}), servers } }
  }, [svcMode, urls])
  const serviceChanged = svcMode === 'inline' && (!svcItem || JSON.stringify(service) !== JSON.stringify(svcItem.spec))

  // --- validation
  const problems = []
  if (!hostsClean.length && rawRule === null) problems.push('Ajoutez au moins un domaine')
  const nameClash = name && name !== item?.name ? items.find((i) => i.proto === 'http' && i.kind === 'routers' && i.name === name) : null
  if (!name) problems.push('Donnez un nom au site')
  if (svcMode === 'inline' ? !urls.some(Boolean) : !refName) problems.push('Indiquez où envoyer le trafic')
  if (!eps?.length) problems.push('Choisissez au moins un point d\'entrée')

  const latest = item ? items.find((i) => i.id === item.id) : null
  const gone = !!item && !!ov.data && !latest && !isNew
  const changed = !!latest && JSON.stringify(latest.spec) !== JSON.stringify(item.spec)   // modifié ailleurs depuis l'ouverture (temps réel)
  const stateChanged = !!latest && !!latest.disabled !== !!item.disabled                   // mis en pause / réactivé ailleurs (ex. son service a été coupé)
  const svcNow = items.find((i) => i.proto === 'http' && i.kind === 'services' && i.name === (svcMode === 'inline' ? sname : refName))
  const svcOff = !!svcNow?.disabled && !latest?.disabled   // le service de ce site est coupé : il ne reçoit plus de trafic
  const save = async () => {
    if (changed && !(await confirm({ title: 'Écraser la version modifiée ailleurs ?', message: 'Ce site a été modifié pendant que vous l\'éditiez. Enregistrer remplace cette version par la vôtre (l\'ancienne reste dans l\'historique du fichier).', confirmLabel: 'Écraser' }))) return
    if (nameClash && !(await confirm({ title: `Remplacer le site « ${name} » ?`, message: `Un site porte déjà ce nom${nameClash.file ? ` (${nameClash.file})` : ''}. Il sera remplacé par celui-ci. Une sauvegarde de l'ancien fichier reste dans l'historique.`, confirmLabel: 'Remplacer' }))) return
    if (dnsAdd && dnsMissing.some((r) => r.canEdit && !r.suggestion.target) && dnsType !== 'CNAME' && !dnsIp.trim()) { toast('Indiquez l\'adresse (IP) à associer au nom, ou décochez la création de l\'enregistrement DNS', 'err'); return }
    setBusy(true)
    try {
      await api.put('/traefik/site', { name, oldName: item?.name, router, serviceName: svcMode === 'inline' ? sname : undefined, service: serviceChanged ? service : undefined, file: undefined })
      if (dnsAdd) {
        const rows = (await api.post('/sync/check', { hosts: hostsClean })).filter((r) => r.status === 'missing' && r.canEdit)
        const byZone = {}
        rows.forEach((r) => { const t = dnsType === 'CNAME' ? r.suggestion.cnameTarget : ((['adguard', 'pihole'].includes(r.provider) && dnsIp.trim()) || r.suggestion.target || dnsIp.trim()); if (t) (byZone[r.zone] ||= []).push({ fieldType: dnsType === 'CNAME' && r.suggestion.cnameTarget ? 'CNAME' : 'A', subDomain: r.subDomain, target: t, ttl: r.suggestion.ttl }) })
        for (const [zone, create] of Object.entries(byZone)) {
          const res = await api.post(`/dns/zones/${zone}/bulk`, { create })
          if (res.errors.length) toast(res.errors.join('\n'), 'err')
          await refreshZone(api, toast, zone)
        }
      }
      qc.invalidateQueries(); toast('Site enregistré — Traefik recharge la configuration'); onClose()
      verifyDeployed(api, toast, name)   // second message : Traefik confirme (ou signale une erreur) une seconde plus tard
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }

  const move = (i, d) => { const a = [...mws]; [a[i], a[i + d]] = [a[i + d], a[i]]; setMws(a) }
  const dragProps = (i) => ({ draggable: true, onDragStart: () => (dragFrom.current = i), onDragOver: (e) => e.preventDefault(), onDrop: () => { const f = dragFrom.current; if (f === null || f === i) return; const a = [...mws]; const [x] = a.splice(f, 1); a.splice(i, 0, x); setMws(a); dragFrom.current = null } })
  const dnsMissing = dns.data?.filter((r) => r.status === 'missing') || []
  const askIp = dnsMissing.some((r) => r.canEdit && (!r.suggestion.target || ['adguard', 'pihole'].includes(r.provider)))   // DNS local (AdGuard, Pi-hole) : l'adresse est celle de Traefik, on la laisse choisir
  const knownEps = meta.data?.entrypoints || []

  const del = async () => {
    const choice = await removal.confirmRemoval({ title: `Supprimer « ${item.name} » ?`, message: 'Le site ne sera plus servi par Traefik. Son service est supprimé s\'il n\'est utilisé par personne d\'autre.\nSans case cochée ci-dessous, l\'enregistrement DNS n\'est pas touché.',
      confirmLabel: 'Supprimer', sites: [{ name: item.name, hosts: extractHosts(item.spec?.rule || ''), locked: true }] })
    if (!choice) return
    await removal.apply(choice); onClose()
  }
  return (
    <>
      <Modal side title={<span>{isNew ? 'Nouveau site' : name} {!isNew && !editable && <Badge>lecture seule</Badge>}</span>} onClose={onClose}
        footer={<>{!isNew && item.editable && !gone && can('sites.delete') && <Btn kind="danger" title="Supprime ce site : Traefik ne le sert plus (rien d'autre n'est touché sans votre accord)" onClick={del}><Trash2 size={15} /> Supprimer le site</Btn>}
          {problems.length > 0 && <span className="small" style={{ color: 'var(--warn)', marginRight: 'auto' }}>{problems[0]}</span>}
          <Btn kind="ghost" onClick={() => setShowYaml(!showYaml)}><Code2 size={15} /> YAML</Btn><Btn onClick={onClose}>Annuler</Btn>
          <Btn kind="primary" busy={busy} disabled={!editable || problems.length > 0 || !can(isNew ? 'sites.create' : 'sites.edit')} title={!can(isNew ? 'sites.create' : 'sites.edit') ? 'Vous n\'avez pas ce droit' : undefined} onClick={save}>{isNew ? 'Créer le site' : 'Enregistrer'}</Btn></>}>
        <fieldset disabled={!editable} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          {gone && <div className="banner err" style={{ marginBottom: 12 }}><TriangleAlert size={15} /><span className="grow">Ce site a été supprimé ou renommé ailleurs. Enregistrer le recréerait.</span></div>}
          {latest?.disabled && stateChanged && <div className="banner" style={{ marginBottom: 12 }}><TriangleAlert size={15} /><span className="grow">Ce site vient d'être désactivé ailleurs{svcNow?.disabled ? ` (son service « ${svcNow.name} » est coupé)` : ''} : Traefik ne le sert plus.</span></div>}
          {latest && !latest.disabled && stateChanged && <div className="banner ok" style={{ marginBottom: 12 }}><span className="grow">Ce site vient d'être réactivé ailleurs.</span></div>}
          {svcOff && <div className="banner" style={{ marginBottom: 12 }}><TriangleAlert size={15} /><span className="grow">Le service « {svcNow.name} » est désactivé : ce site ne reçoit plus de trafic tant qu'il l'est.</span></div>}
          {changed && <div className="banner" style={{ marginBottom: 12 }}><TriangleAlert size={15} /><span className="grow">Ce site a été modifié ailleurs pendant que vous l'éditiez.</span>{onReload && <Btn small onClick={() => onReload(latest)}>Charger la nouvelle version</Btn>}</div>}
          <div className="stack">
            <Block tag="NOM & PRIORITÉ" c="#868e96" icon={<Pencil size={12} />} tip={'Le nom identifie le site dans les fichiers Traefik. La priorité départage deux sites qui correspondent à la même requête.'}>
              <div className="fgrid">
                <Field label="Nom du site" tip={'Identifiant unique du site dans la configuration de Traefik (il ne change pas l\'adresse visible). Deux sites ne peuvent pas porter le même nom : en enregistrer un avec un nom déjà pris REMPLACE le site existant.'}>
                  <input className="input mono" value={name} disabled={!isNew && !isAdmin} onChange={(e) => { setName(e.target.value.replace(/[^A-Za-z0-9_.-]/g, '-')); setNameTouched(true) }} /></Field>
                {isAdmin && <Field label="Priorité" tip={'Quand plusieurs sites correspondent à la même requête (par exemple un site entier et un autre sur un chemin précis du même domaine), celui dont la priorité est la plus haute gagne. Vide = automatique : la règle la plus précise passe avant la plus large. À régler seulement si deux sites se chevauchent.'} help="Vide = automatique"><input className="input" type="number" value={priority} onChange={(e) => setPriority(e.target.value)} /></Field>}
              </div>
              {nameClash && <div className="banner" style={{ margin: '8px 0 0' }}><TriangleAlert size={15} /> Un site nommé « {name} » existe déjà{nameClash.file ? ` (${nameClash.file})` : ''} : l'enregistrer le remplacera. Choisissez un autre nom pour garder les deux.</div>}
            </Block>

            <Block tag="QUAND on visite…" c="#1c7ed6" icon={<Globe size={12} />} tip={'Quand une requête arrive pour cette adresse (et ces conditions), ce site la prend en charge.\nCliquez sur ET / OU devant une ligne pour le basculer. Glissez la poignée pour changer l\'ordre. Une ligne indentée fait partie de la ligne du dessus, comme dans du code : elles forment un bloc entre parenthèses.'}>
              {rawRule !== null ? (
                <div className="col"><div className="banner info" style={{ margin: 0 }}>Cette règle est trop complexe pour des blocs : édition en texte.</div>
                  <textarea className="textarea" value={rawRule} onChange={(e) => setRawRule(e.target.value)} /></div>
              ) : (
                <div className="col" style={{ gap: 8 }}>
                  {rows.map((r, i) => <RuleRow key={r.id} r={r} i={i} rows={rows} setRows={setRows} domains={domains} free={isAdmin} dragFrom={ruleDrag} dropAt={dropAt} setDropAt={setDropAt} />)}
                  <div className="row wrap">
                    <Btn small onClick={() => setRows([...rows, hostRow(domains[0]?.name || '', 'or', rows[rows.length - 1]?.level || 0)])}><Plus size={13} /> Autre adresse (OU)</Btn>
                    <select className="select" style={{ width: 'auto' }} value="" onChange={(e) => e.target.value && setRows([...rows, condRow(e.target.value, [''], 'and', rows[rows.length - 1]?.level || 0)])}>
                      <option value="">+ Condition (ET)…</option>{Object.entries(COND_LABELS).filter(([k]) => isAdmin || ['PathPrefix', 'Path', 'Method', 'Header', 'Query', 'ClientIP'].includes(k)).map(([k, [l]]) => <option key={k} value={k}>{k} — {l}</option>)}</select>
                  </div>
                  {rows.length > 1 && <div className="muted small mono ellipsis" title={rule}>{rule}</div>}
                </div>
              )}
            </Block>

            <Block tag="ALORS appliquer…" c="#e8590c" icon={<Shield size={12} />} tip={'Les protections et transformations passées dans l\'ordre avant d\'envoyer la requête : mot de passe, liste d\'IP, en-têtes de sécurité… Glissez-les pour changer l\'ordre.'}>
              <div className="col" style={{ gap: 8 }}>
                {(mws || []).length === 0 && <div className="muted">Aucun bloc : le site est accessible à tout le monde, sans protection.</div>}
                {(mws || []).map((m, i) => <MwRow key={m} name={m} idx={i} total={mws.length} items={mwItems} move={move} dragProps={dragProps(i)} canEdit={isAdmin} onEdit={setEditMw} onRemove={() => setMws(mws.filter((x) => x !== m))} />)}
                <div><Btn small onClick={() => setPicker(true)}><Plus size={13} /> Ajouter un bloc</Btn></div>
              </div>
            </Block>

            <Block tag="PUIS envoyer vers…" c="#2f9e44" icon={<Send size={12} />} tip={'L\'application qui répond vraiment : l\'adresse de votre conteneur ou serveur (ex. http://nom-du-conteneur:8080).\nPlusieurs serveurs = Traefik répartit les requêtes entre eux.'}>
              <div className="col" style={{ gap: 8 }}>
                {svcMode === 'inline' ? (<>
                  {urls.map((u, i) => (
                    <div className="mini row" style={{ '--bc': '#2f9e44' }} key={i}><Send size={15} />
                      <input className="input mono" value={u} placeholder="http://nom-du-conteneur:8080" onChange={(e) => setUrls(urls.map((x, j) => (j === i ? e.target.value.trim() : x)))} />
                      {urls.length > 1 && <Btn small kind="ghost" icon onClick={() => setUrls(urls.filter((_, j) => j !== i))}><X size={14} /></Btn>}</div>))}
                  <div className="row wrap"><Btn small onClick={() => setUrls([...urls, ''])}><Plus size={13} /> Serveur supplémentaire (répartition)</Btn>
                    {(isAdmin || (svcItem ? svcItem.editable : true)) && items.some((i) => i.kind === 'services') && <Btn small kind="ghost" onClick={() => { setSvcMode('ref'); setRefName(base.service || '') }}>Utiliser un service existant</Btn>}</div>
                  {sharedBy.length > 0 && <div className="banner" style={{ margin: 0 }}>Ce service est aussi utilisé par : {sharedBy.map((s) => s.split('/').pop()).join(', ')}. Le modifier les modifie aussi.</div>}
                </>) : (
                  <div className="mini row" style={{ '--bc': '#2f9e44' }}><Link2 size={15} />
                    <select className="select" value={refName} onChange={(e) => setRefName(e.target.value)}><option value="">— choisir un service —</option>{items.filter((i) => i.kind === 'services' && i.proto === 'http' && !i.disabled).map((i) => <option key={i.name} value={i.name}>{i.name} ({i.type})</option>)}</select>
                    <Btn small kind="ghost" onClick={() => setSvcMode('inline')}>Saisir une URL</Btn></div>)}
              </div>
            </Block>

            <Block tag="HTTPS" c="#7048e8" icon={<Lock size={12} />} tip={'Sur quel port Traefik écoute ce site et d\'où vient son certificat. Par défaut : « websecure » (HTTPS, port 443) avec un certificat Let\'s Encrypt automatique.'}>
              <div className="fgrid">
                <Field label="Point d'entrée" tip="Le port d'écoute de Traefik pour ce site. « websecure » = HTTPS (443), « web » = HTTP (80). Les noms viennent de la configuration de Traefik."><Chips value={eps || []} onChange={setEps} listId="sb-eps" placeholder="websecure" /><datalist id="sb-eps">{knownEps.map((e) => <option key={e} value={e} />)}</datalist></Field>
                <Field label="Certificat automatique" tip="Le « résolveur » est la configuration Traefik qui obtient et renouvelle seule le certificat HTTPS (Let's Encrypt). Sans résolveur, il faut fournir un certificat vous-même." help="Résolveur Let's Encrypt qui crée le certificat."><select className="select" value={resolver || ''} onChange={(e) => setResolver(e.target.value)}><option value="">(aucun)</option>{(meta.data?.certResolvers || []).map((r) => <option key={r}>{r}</option>)}</select></Field>
              </div>
            </Block>

            {meta.data?.dnsEnabled && hostsClean.length > 0 && (
              <Block tag="DNS" c="#0c8599" icon={<Globe size={12} />} tip={'Pour qu\'on puisse atteindre le site par son nom, l\'adresse doit être résolue vers ce serveur. Chez OVH ou dans votre AdGuard Home / Pi-hole, on la vérifie et on la crée si besoin (dans AdGuard ou Pi-hole, vers l\'adresse privée de Traefik : rien n\'est publié) ; en DNS local ou chez DuckDNS, rien n\'est à créer ici.'}>
                <div className="col" style={{ gap: 8 }}>
                  {(dns.data || []).map((r) => (
                    <div className="row wrap" key={r.host}><code>{r.host}</code>
                      {r.status === 'ok' && <Badge kind="ok">déjà chez {{ adguard: 'AdGuard', pihole: 'Pi-hole' }[r.provider] || 'OVH'} · {r.records[0].fieldType} → {r.records[0].target}</Badge>}
                      {r.status === 'wildcard' && <Badge kind="info">couvert par un wildcard</Badge>}
                      {r.status === 'external' && <Badge>domaine hors de vos comptes</Badge>}
                      {r.status === 'local' && <Badge kind="info" title="Ce domaine est résolu par votre DNS local : rien à créer chez un fournisseur. Vérifiez que votre DNS local mène à ce serveur.">DNS local · rien à créer</Badge>}
                      {r.status === 'managed' && <Badge kind="ok" title="DuckDNS : tous les sous-domaines suivent l'adresse du nom.">DuckDNS{r.ip ? ` · ${r.ip}` : ''}</Badge>}
                      {r.status === 'missing' && <Badge kind="warn">n'existe pas encore{r.canEdit ? '' : ' (pas de droit d\'écriture)'}</Badge>}
                      {r.status === 'error' && <Badge kind="err">{r.error}</Badge>}</div>))}
                  {dnsMissing.some((r) => r.canEdit) && (
                    <div className="row wrap rule-preview"><Switch value={dnsAdd} onChange={setDnsAdd} /> <span>Créer automatiquement l'enregistrement</span>
                      {dnsAdd && <select className="select" style={{ width: 'auto' }} value={dnsType} onChange={(e) => setDnsType(e.target.value)}><option value="A">A → {dnsMissing[0].suggestion.target || dnsIp.trim() || '?'}</option>{dnsMissing[0].suggestion.cnameTarget && <option value="CNAME">CNAME → racine</option>}</select>}
                      {dnsAdd && dnsType !== 'CNAME' && askIp && <input className="input mono" style={{ width: 190 }} value={dnsIp} onChange={(e) => setDnsIp(e.target.value)} placeholder={dnsMissing.find((r) => r.suggestion.target)?.suggestion.target ? `autre IP (défaut ${dnsMissing.find((r) => r.suggestion.target).suggestion.target})` : 'IP, ex. 192.168.1.10'} title="Adresse à associer au nom (pour un DNS local : l'adresse privée de la machine qui fait tourner Traefik)" />}</div>)}
                </div>
              </Block>
            )}
          </div>

          {isAdmin && (
            <details className="group" style={{ marginTop: 20 }}>
              <summary>Règle Traefik (avancé)</summary>
              <div className="inner col" style={{ gap: 8 }}>
                {rawRule === null
                  ? <><div className="muted small">La règle construite à partir de vos choix, telle qu'elle sera écrite dans le fichier :</div>
                    <code style={{ display: 'block', whiteSpace: 'pre-wrap', wordBreak: 'break-all', padding: '8px 10px', background: 'var(--panel2)', borderRadius: 8 }}>{rule}</code>
                    <div><Btn small onClick={() => setRawRule(rule)}>Éditer en texte</Btn></div></>
                  : <div><Btn small onClick={() => { const r = ruleToRows(rawRule); if (r) { setRows(r.length ? r : [hostRow('')]); setRawRule(null) } else toast('Cette règle ne peut pas être lue en lignes : corrigez-la d\'abord', 'err') }}>Revenir aux blocs</Btn></div>}
              </div>
            </details>)}
          {showYaml && <div className="editor-wrap" style={{ height: 260, marginTop: 14 }}><CodeEditor readOnly value={toYamlPretty({ http: { routers: { [name]: router }, ...(service ? { services: { [sname]: service } } : {}) } })} /></div>}
        </fieldset>
      </Modal>
      {picker && <MwPicker items={mwItems} taken={mws || []} canCreate={isAdmin} onClose={() => setPicker(false)} onPick={(n) => { setMws([...(mws || []), n]); setPicker(false) }} onCreate={() => { setPicker(false); setCreating(true) }} />}
      {creating && <ItemEditor isNew proto="http" kind="middlewares" items={items} files={ov.data.files.map((f) => f.path)} meta={meta.data} onClose={() => setCreating(false)} onSaved={(n) => { setMws((m) => [...(m || []), n]); setCreating(false) }} />}
      {removal.dialog}
      {editMw && <ItemEditor item={editMw} items={items} files={ov.data.files.map((f) => f.path)} meta={meta.data} onClose={() => setEditMw(null)} onOpenItem={setEditMw} />}
    </>
  )
}
