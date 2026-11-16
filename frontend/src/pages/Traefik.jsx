import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AlertTriangle, ArrowLeft, ArrowRight, Copy, FileCode2, ListTree, Pause, Pencil, Plus, Power, Route, Server, ShieldCheck, Trash2, Layers, Lock } from 'lucide-react'
import { api, qs } from '../api.js'
import { Badge, Btn, Empty, Loading, Modal, SearchBox, Seg, Switch, Tabs, useUi, Field } from '../components/ui.jsx'
import { useToggler } from '../components/Toggle.jsx'
import { liveState } from '../lib/live.js'
import { verifyDeployed } from '../lib/verify.js'
import { Info } from '../components/Tip.jsx'
import CodeEditor from '../components/CodeEditor.jsx'
import SchemaForm from '../components/SchemaForm.jsx'
import { MiddlewareGallery, TypeHeader } from '../components/MiddlewareGallery.jsx'
import { presetOf } from '../traefik/presets.js'
import { HUMAN } from '../traefik/human.js'
import MissingDns from '../components/MissingDns.jsx'
import { KIND_LABELS, catalogFor, fieldsFor, MIDDLEWARE_GROUPS } from '../traefik/schema.js'
import { clean, extractHosts, fromYaml, toYaml } from '../lib/util.js'
import Files from './Files.jsx'
import Certs from './Certs.jsx'
import { useFeatures } from '../lib/features.js'
import { useAuth } from '../auth.jsx'
import { useNode } from '../node.jsx'
const FAMILY = { routers: 'sites', services: 'services', middlewares: 'middlewares', serversTransports: 'transports', options: 'tls', stores: 'tls', tls: 'tls' }   // famille de droits d'un onglet / d'un type d'élément

export function useOverview(enabled = true) {
  const { user } = useAuth(); const { hasTraefik } = useNode()
  return useQuery({ queryKey: ['overview'], queryFn: () => api.get('/traefik/overview'), enabled: enabled && !!user && hasTraefik })
}
export function useMeta() { return useQuery({ queryKey: ['meta'], queryFn: () => api.get('/meta'), staleTime: 60000 }) }

const TABS = [
  { id: 'routers', label: 'Routeurs', hint: 'Un routeur décide quelles requêtes il prend en charge (selon l\'adresse du site, le chemin…) et vers quel service les envoyer.', icon: <Route size={15} /> }, { id: 'services', label: 'Services', hint: 'Un service est la destination : l\'application (adresse:port) qui répond réellement. Il peut répartir la charge sur plusieurs serveurs.', icon: <Server size={15} /> },
  { id: 'middlewares', label: 'Middlewares', hint: 'Une protection ou une transformation placée entre le routeur et le service : mot de passe, liste d\'IP, en-têtes de sécurité, redirection…', icon: <Layers size={15} /> }, { id: 'serversTransports', label: 'Transports', hint: 'Réglages de connexion de Traefik vers vos services (certificat, délais…), par exemple pour un service en HTTPS auto-signé.', icon: <ArrowRight size={15} /> },
  { id: 'tls', label: 'TLS', hint: 'Options TLS (versions, chiffrements autorisés) et stores de certificats pour le HTTPS.', icon: <Lock size={15} /> }, { id: 'certs', label: 'Certificats', hint: 'Les certificats HTTPS obtenus par Traefik : domaines, émetteur, échéance, et les sites qui n\'en ont pas.', icon: <Lock size={15} /> }, { id: 'files', label: 'Fichiers', hint: 'Les fichiers YAML que Traefik lit. Chaque enregistrement garde une sauvegarde ; vous pouvez les ranger en dossiers.', icon: <FileCode2 size={15} /> }, { id: 'problems', label: 'Problèmes', hint: 'Incohérences détectées : références cassées, doublons, éléments jamais utilisés.', icon: <AlertTriangle size={15} /> },
]

function summary(it) {
  const s = it.spec || {}
  if (it.kind === 'routers') return <div className="col" style={{ gap: 3 }}><code className="ellipsis">{s.rule || '(udp)'}</code>
    <div className="row wrap" style={{ gap: 5 }}><ArrowRight size={12} className="muted" /><Badge kind="accent" title="Service : l'application qui répond">{s.service || '—'}</Badge>{(s.middlewares || []).map((m) => <Badge key={m} kind="info" title="Protection (middleware) appliquée avant le service">{m}</Badge>)}{s.tls?.passthrough && <Badge>passthrough</Badge>}</div></div>
  if (it.kind === 'services') {
    const t = it.type; const b = s[t] || {}
    const servers = (b.servers || []).map((x) => x.url || x.address).join(', ')
    return <div className="row wrap"><Badge kind="accent">{t}</Badge><code className="ellipsis">{servers || (b.services || []).map((x) => `${x.name}(${x.weight ?? 1})`).join(', ') || b.service || ''}</code></div>
  }
  if (it.kind === 'middlewares') {
    const b = s[it.type]
    const hint = typeof b === 'object' && b ? (b.sourceRange?.join(', ') || b.middlewares?.join(' → ') || b.address || b.prefix || b.scheme || b.regex || (b.average && `${b.average}/${b.period || '1s'}`) || '') : ''
    return <div className="row wrap"><Badge kind="accent">{it.type}</Badge><code className="ellipsis">{hint}</code></div>
  }
  return <code className="ellipsis">{JSON.stringify(s).slice(0, 120)}</code>
}

export default function Traefik() {
  const ov = useOverview(); const meta = useMeta()
  const [params, setParams] = useSearchParams(); const { can } = useNode()
  const feats = useFeatures()
  const allowed = TABS.filter((t) => (t.id === 'files' ? can('files.view') : t.id === 'certs' ? can('certs.view') && feats?.certs.enabled !== false : t.id === 'problems' ? can('traefik.all') : can(FAMILY[t.id] + '.view')))
  const tab = allowed.some((t) => t.id === params.get('tab')) ? params.get('tab') : (allowed[0]?.id || 'routers')
  const fam = FAMILY[tab]
  const [proto, setProto] = useState('http')
  const [q, setQ] = useState('')
  const [fileF, setFileF] = useState('')
  const [edit, setEdit] = useState(null) // {item} ou {isNew, proto, kind}
  const live = useQuery({ queryKey: ['live'], queryFn: () => api.get('/traefik/live'), enabled: !!meta.data?.traefikApi, refetchInterval: () => (liveState() === 'live' ? 120000 : 15000) })
  const qc = useQueryClient(); const { toast, confirm } = useUi(); const { toggle, busy: toggling } = useToggler()
  const setTab = (t) => setParams({ tab: t })

  useEffect(() => { const id = params.get('edit'); if (id && ov.data) { const it = ov.data.items.find((i) => i.id === id); if (it) { setProto(it.proto === 'tls' ? 'http' : it.proto); setEdit({ item: it }); setParams({ tab: it.kind === 'options' || it.kind === 'stores' ? 'tls' : it.kind }, { replace: true }) } } }, [params.get('edit'), ov.data])

  const del = useMutation({
    mutationFn: (it) => api.del(`/traefik/item?proto=${it.proto}&kind=${it.kind}&name=${encodeURIComponent(it.name)}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['overview'] }); toast('Supprimé') }, onError: (e) => toast(e.message, 'err'),
  })

  const items = ov.data?.items || []
  const issues = ov.data?.issues || []
  const list = useMemo(() => {
    const kinds = tab === 'tls' ? ['options', 'stores'] : [tab]
    return items.filter((i) => kinds.includes(i.kind) && (tab === 'tls' ? i.proto === 'tls' : i.proto === (tab === 'serversTransports' ? proto : proto) && i.proto !== 'tls'))
      .filter((i) => (!fileF || i.file === fileF) && (!q || (i.name + ' ' + JSON.stringify(i.spec)).toLowerCase().includes(q.toLowerCase())))
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [items, tab, proto, q, fileF])
  const counts = (k) => items.filter((i) => i.kind === k && i.proto === proto).length

  const askDelete = async (it) => {
    const users = it.usedBy?.length ? `\n\n⚠ Utilisé par : ${it.usedBy.join(', ')}` : ''
    if (await confirm({ title: `Supprimer « ${it.name} » ?`, message: `Retire l'élément de ${it.file}.${users}\nUne sauvegarde est conservée.`, confirmLabel: 'Supprimer' })) del.mutate(it)
  }
  const dup = (it) => setEdit({ isNew: true, proto: it.proto, kind: it.kind, template: { ...it, name: it.name + '-copie' } })

  if (ov.isLoading) return <Loading />
  const files = [...new Set(items.map((i) => i.file))]
  const files_off = new Set(ov.data.files.filter((f) => f.disabled).map((f) => f.path))
  const liveOf = (it) => it.kind === 'routers' && !it.disabled ? live.data?.routers?.[`${it.proto}/${it.name}@file`] : null

  return (
    <>
      <div className="page-head"><div><h1>Traefik</h1><p>Configuration dynamique · {ov.data.files.length} fichiers · {items.length} éléments</p></div></div>
      <Tabs value={tab} onChange={setTab} tabs={allowed.map((t) => ({ ...t, count: t.id === 'problems' ? issues.filter((i) => i.level !== 'info').length : ['routers', 'services', 'middlewares'].includes(t.id) ? counts(t.id) : undefined }))} />
      {tab === 'files' && <Files />}
      {tab === 'certs' && <Certs />}
      {tab === 'problems' && <Problems issues={issues} onOpen={(i) => i.item ? setParams({ edit: i.item }) : setTab('files')} />}
      {!['files', 'problems', 'certs'].includes(tab) && (
        <>
          <div className="row wrap" style={{ marginBottom: 14 }}>
            {tab !== 'tls' && <Seg value={proto} onChange={setProto} options={[{ id: 'http', label: 'HTTP', hint: 'Sites web et API (HTTP / HTTPS) : le cas le plus courant.' }, { id: 'tcp', label: 'TCP', hint: 'Connexions TCP brutes : base de données, SSH, courrier…' }, ...(tab === 'serversTransports' || tab === 'middlewares' ? [] : [{ id: 'udp', label: 'UDP', hint: 'Connexions UDP : jeux, DNS, VPN…' }])]} />}
            <SearchBox value={q} onChange={setQ} />
            <select className="select" style={{ width: 'auto' }} value={fileF} onChange={(e) => setFileF(e.target.value)}><option value="">Tous les fichiers</option>{files.map((f) => <option key={f}>{f}</option>)}</select>
            {can(fam + '.create') && <Btn kind="primary" className="right" title="Créer un nouvel élément de ce type" onClick={() => setEdit({ isNew: true, proto: tab === 'tls' ? 'tls' : proto, kind: tab === 'tls' ? 'options' : tab })}><Plus size={15} /> Nouveau</Btn>}
          </div>
          <div className="card flush">
            {list.length === 0 ? <Empty>Aucun élément.</Empty> : (
              <table className="t"><thead><tr><th style={{ width: 52 }}><Info>Interrupteur : désactive l'élément sans le supprimer (Traefik l'ignore). Réversible à tout moment ; ce qui en dépend est géré pour vous.</Info></th><th>Nom</th><th>Configuration</th><th>Fichier<Info>Fichier YAML dans lequel cet élément est écrit.</Info></th><th>Utilisé<Info>Combien d'éléments s'en servent. 0 = personne ne l'utilise : vous pouvez le supprimer sans risque.</Info></th><th /></tr></thead>
                <tbody>{list.map((it) => {
                  const lv = liveOf(it)
                  return (
                    <tr key={it.id} className={'click ' + (it.disabled ? 'off' : '')} onClick={() => setEdit(it.disabled ? { disabled: it } : { item: it })}>
                      <td className="sw" onClick={(e) => e.stopPropagation()}>{TOGGLEABLE.includes(it.kind) && <Switch value={!it.disabled} busy={toggling} disabled={(it.disabled && files_off.has(it.file)) || !can(fam + '.toggle')} title={it.disabled && files_off.has(it.file) ? 'Ce fichier est désactivé : réactivez-le dans l\'onglet Fichiers' : it.disabled ? 'Réactiver' : 'Désactiver'} onChange={(on) => toggle({ ids: [it.id], enable: on, label: `« ${it.name} »` })} />}</td>
                      <td><div className="row">{lv && <span className={'dot ' + (lv.status === 'enabled' ? 'ok' : 'err')} title={lv.error || lv.status} />}<b>{it.name}</b>{it.proto === 'tls' && <Badge>{it.kind}</Badge>}{it.disabled && <Badge kind="warn" className="badge-off"><Pause size={10} />{it.pausedBy ? 'en pause' : 'désactivé'}</Badge>}</div></td>
                      <td style={{ maxWidth: 520 }}>{summary(it)}</td>
                      <td className="muted small">{it.file}</td>
                      <td>{it.kind === 'routers' || it.disabled ? '' : it.usedBy?.length ? <Badge kind="ok">{it.usedBy.length}×</Badge> : <Badge kind="warn">0</Badge>}</td>
                      <td className="actions" onClick={(e) => e.stopPropagation()}>
                        {!it.disabled && can(fam + '.create') && <Btn kind="ghost" small icon title="Dupliquer" onClick={() => dup(it)}><Copy size={14} /></Btn>}
                        {!it.disabled && can(fam + '.edit') && <Btn kind="ghost" small icon title="Modifier" onClick={() => setEdit({ item: it })}><Pencil size={14} /></Btn>}
                        {can(fam + '.delete') && <Btn kind="ghost" small icon title="Supprimer" onClick={() => askDelete(it)}><Trash2 size={14} /></Btn>}
                      </td>
                    </tr>
                  )
                })}</tbody></table>
            )}
          </div>
        </>
      )}
      {edit?.disabled && <DisabledItem item={edit.disabled} offFile={files_off.has(edit.disabled.file)} onClose={() => setEdit(null)} onEnable={async () => { if (await toggle({ ids: [edit.disabled.id], enable: true, label: `« ${edit.disabled.name} »` })) setEdit(null) }} onDelete={() => { setEdit(null); askDelete(edit.disabled) }} />}
      {edit && !edit.disabled && <ItemEditor key={(edit.item?.id || edit.template?.name || 'new') + ':' + (edit.rev || 0)} {...edit} onReload={(it) => setEdit({ item: it, rev: (edit.rev || 0) + 1 })} items={items} files={ov.data.files.filter((f) => !f.disabled).map((f) => f.path)} meta={meta.data} onClose={() => { setEdit(null); if (params.get('edit')) setParams({ tab }, { replace: true }) }} onOpenItem={(it) => setEdit({ item: it })} />}
    </>
  )
}

const TOGGLEABLE = ['routers', 'services', 'middlewares', 'options']

function DisabledItem({ item, offFile, onClose, onEnable, onDelete }) {
  return (
    <Modal title={<span>{item.name} <Badge kind="warn">{item.pausedBy ? 'en pause' : 'désactivé'}</Badge></span>} onClose={onClose}
      footer={<>{!offFile && <Btn kind="danger" onClick={onDelete}><Trash2 size={14} /> Supprimer</Btn>}<span className="grow" /><Btn onClick={onClose}>Fermer</Btn>{!offFile && <Btn kind="primary" onClick={onEnable}><Power size={14} /> Réactiver</Btn>}</>}>
      <div className="col">
        <div className="muted">{offFile ? `Ce bloc est dans un fichier désactivé (${item.file}) : réactivez le fichier pour le modifier.` : `Mis en commentaire dans ${item.file}. Traefik l'ignore ; réactivez-le pour le modifier.`}</div>
        <div className="codebox"><pre>{toYaml(item.spec || {})}</pre></div>
      </div>
    </Modal>
  )
}

function Problems({ issues, onOpen }) {
  if (!issues.length) return <div className="card"><Empty><ShieldCheck size={28} /><div>Aucun problème détecté 🎉</div></Empty></div>
  const ic = { error: 'err', warning: 'warn', info: 'info' }
  return <div className="card flush">{issues.map((i, n) => <div className="issue" key={n} onClick={() => onOpen(i)}><Badge kind={ic[i.level]}>{i.level}</Badge><div className="grow">{i.message}<div className="muted small">{i.item || i.file}</div></div></div>)}</div>
}

// ------------------------------------------------------------------ éditeur d'élément
function missingRequired(fields, v, path = '') {
  const out = []
  for (const f of fields || []) {
    const val = f.flat ? v : v?.[f.k]
    if (f.t === 'o') out.push(...missingRequired(f.f, f.flat ? v : val, path + f.label + ' › '))
    else if (f.t === 'ol') (val || []).forEach((row) => out.push(...missingRequired(f.f, row, path + f.label + ' › ')))
    else if (f.required && (val === undefined || val === '' || (Array.isArray(val) && !val.length))) out.push(path + f.label)
  }
  return out
}

const groupFiles = (files) => Object.entries(files.reduce((g, f) => { const d = f.includes('/') ? f.slice(0, f.lastIndexOf('/')) : ''; (g[d] ||= []).push(f); return g }, {})).sort(([a], [b]) => a.localeCompare(b))

export function ItemEditor({ item, isNew, proto: p0, kind: k0, template, items, files, meta, onClose, onOpenItem, onSaved, onReload }) {
  const src = item || template
  const proto = item?.proto || p0, kind = item?.kind || k0
  const catalog = catalogFor(proto, kind)
  const qc = useQueryClient(); const { toast, confirm } = useUi()
  const initialSpec = useMemo(() => {
    if (src) return src.spec
    if (kind === 'routers') return proto === 'udp' ? {} : { entryPoints: [meta?.entrypoints?.includes('websecure') ? 'websecure' : (meta?.entrypoints?.[0] || 'web')], ...(proto === 'http' && meta?.certResolvers?.[0] ? { tls: { certResolver: meta.certResolvers[0] } } : {}) }
    if (kind === 'services') return { loadBalancer: { servers: [proto === 'http' ? { url: 'http://' } : { address: '' }] } }
    return {}
  }, [])
  const [name, setName] = useState(isNew ? (template?.name || '') : item.name)
  const [spec, setSpec] = useState(initialSpec)
  const [file, setFile] = useState(item?.file || '')
  const [view, setView] = useState('form')
  const [yamlTxt, setYamlTxt] = useState('')
  const [dnsRows, setDnsRows] = useState(null)
  const type = catalog ? Object.keys(spec)[0] : null
  const guided = kind === 'middlewares' && proto === 'http'          // catalogue « par intention » pour les middlewares HTTP
  const [gallery, setGallery] = useState(guided && isNew && !template)
  const [allTypes, setAllTypes] = useState(false)
  const [from, setFrom] = useState('gallery')                        // d'où l'on vient : « Retour » y ramène
  const preset = guided ? presetOf(type) : null

  const refs = useMemo(() => {
    const names = (k, pr = proto) => items.filter((i) => i.proto === pr && i.kind === k).map((i) => i.name)
    return { services: names('services'), middlewares: names('middlewares'), serversTransports: names('serversTransports'), tlsOptions: names('options', 'tls'), entrypoints: meta?.entrypoints || [], resolvers: meta?.certResolvers || [] }
  }, [items, proto, meta])
  const ctx = { refs, proto }

  // Rangement automatique d'un nouvel élément : le serveur suit l'organisation déjà en place sur le nœud (dossiers apps/, middlewares/…).
  const [typed, setTyped] = useState(name)
  useEffect(() => { const t = setTimeout(() => setTyped(name.trim()), 300); return () => clearTimeout(t) }, [name])
  const placement = useQuery({ queryKey: ['placement', proto, kind, typed], queryFn: () => api.get('/traefik/placement?' + qs({ proto, kind, name: typed })), enabled: !file && !item && !!typed })
  const autoFile = () => file || item?.file || placement.data?.file || (name.trim() ? `${name.trim()}.yml` : '…')

  const switchView = (v) => {
    if (v === 'yaml') setYamlTxt(toYaml(clean(spec) || {}))
    else { try { setSpec(fromYaml(yamlTxt)) } catch (e) { toast('YAML invalide : ' + (e.message.split('\n')[0] || 'syntaxe incorrecte'), 'err'); return } }
    setView(v)
  }

  const save = useMutation({
    mutationFn: async () => {
      const raw = view === 'yaml' ? fromYaml(yamlTxt) : spec
      let sp = clean(raw) || {}
      if (catalog && !Object.keys(sp).length && Object.keys(raw || {}).length === 1) sp = { [Object.keys(raw)[0]]: {} }   // ex. « compress » n'a aucun réglage
      if (!name.trim()) throw new Error('Le nom est obligatoire')
      if (changed && !(await confirm({ title: 'Écraser la version modifiée ailleurs ?', message: 'Cet élément a été modifié pendant que vous l\'éditiez. Enregistrer remplace cette version par la vôtre.', confirmLabel: 'Écraser' }))) throw new Error('Enregistrement annulé')
      if (catalog && Object.keys(sp).length !== 1) throw new Error('Choisissez un type')
      if (view === 'form') {
        const flds = catalog ? catalog[type]?.fields : fieldsFor(proto, kind)
        const body = catalog ? spec[type] : spec
        const miss = missingRequired(flds, body || {})
        if (miss.length) throw new Error('Champs obligatoires : ' + miss.join(', '))
      }
      await api.put('/traefik/item', { proto, kind, name: name.trim(), spec: sp, file: file ? (/\.ya?ml$/.test(file) ? file : file + '.yml') : undefined, oldName: item && item.name !== name.trim() ? item.name : undefined })
      return sp
    },
    onSuccess: async (sp) => {
      qc.invalidateQueries({ queryKey: ['overview'] }); qc.invalidateQueries({ queryKey: ['live'] })
      toast('Enregistré — Traefik recharge la configuration')
      if (kind === 'routers' && proto === 'http') verifyDeployed(api, toast, name.trim())
      if (onSaved) { onSaved(name.trim()); return }
      if (kind === 'routers' && proto === 'http' && meta?.dnsEnabled) {
        const hosts = extractHosts(sp.rule)
        if (hosts.length) {
          try { const r = (await api.post('/sync/check', { hosts })).filter((x) => x.status === 'missing'); if (r.length) { setDnsRows(r); return } } catch { /* ignore */ }
        }
      }
      onClose()
    },
    onError: (e) => toast(e.message, 'err'),
  })

  const usedBy = item?.usedBy || []
  const latest = item ? items.find((i) => i.id === item.id) : null
  const gone = !!item && !latest
  const changed = !!latest && JSON.stringify(latest.spec) !== JSON.stringify(item.spec)   // modifié ailleurs depuis l'ouverture (temps réel)
  const stateChanged = !!latest && !!latest.disabled !== !!item.disabled                   // mis en pause / réactivé ailleurs
  const uses = useMemo(() => {
    if (!item) return []
    const s = JSON.stringify(item.spec); const out = []
    items.filter((i) => i.proto === proto && ['services', 'middlewares'].includes(i.kind) && i.id !== item.id).forEach((i) => { if (s.includes(`"${i.name}"`)) out.push(i) })
    return out
  }, [item, items])

  const fields = catalog ? catalog[type]?.fields : fieldsFor(proto, kind)
  const body = catalog ? (spec[type] ?? {}) : spec
  const setBody = (v) => setSpec(catalog ? { [type]: v } : v)
  const label = { routers: 'Routeur', services: 'Service', middlewares: 'Middleware', serversTransports: 'Transport', options: 'Options TLS', stores: 'Store TLS' }[kind]

  return (
    <>
      <Modal side title={<span>{isNew ? `Nouveau ${label.toLowerCase()}` : name} <Badge>{proto.toUpperCase()}</Badge></span>} onClose={onClose}
        footer={<><span className="muted small right" style={{ marginRight: 'auto' }}>→ {autoFile()}</span><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={save.isPending} onClick={() => save.mutate()}>Enregistrer</Btn></>}>
        <div className="col gap-lg">
          {gone && <div className="banner err" style={{ margin: 0 }}><AlertTriangle size={15} /><span className="grow">Cet élément a été supprimé ou renommé ailleurs. Enregistrer le recréerait.</span></div>}
          {latest?.disabled && stateChanged && <div className="banner" style={{ margin: 0 }}><AlertTriangle size={15} /><span className="grow">Cet élément vient d'être désactivé ailleurs (par lui-même ou parce que ce dont il dépend a été coupé) : Traefik ne le lit plus.</span></div>}
          {latest && !latest.disabled && stateChanged && <div className="banner ok" style={{ margin: 0 }}><span className="grow">Cet élément vient d'être réactivé ailleurs.</span></div>}
          {changed && <div className="banner" style={{ margin: 0 }}><AlertTriangle size={15} /><span className="grow">Cet élément a été modifié ailleurs pendant que vous l'éditiez.</span>{onReload && <Btn small onClick={() => onReload(latest)}>Charger la nouvelle version</Btn>}</div>}
          <div className="fgrid">
            <Field label="Nom" required tip="Nom technique : c'est lui qu'on écrit pour y faire référence (un routeur vers un service, un site vers une protection). Sans espace, @, / ni :."><input className="input" value={name} onChange={(e) => setName(e.target.value.replace(/\s/g, '-'))} autoFocus={isNew} />
              {!isNew && item && name.trim() && name.trim() !== item.name && (usedBy.length > 0 || kind === 'middlewares' || kind === 'services') && <div className="help" style={{ color: 'var(--info)' }}>Renommer met aussi à jour {usedBy.length ? `les ${usedBy.length} élément${usedBy.length > 1 ? 's' : ''} qui l'utilisent (${usedBy.map((u) => u.split('/').pop()).join(', ')})` : 'tout ce qui y fait référence'}, y compris les blocs et fichiers désactivés.</div>}</Field>
            <Field label="Fichier" tip="Fichier YAML où l'élément est écrit. « auto » le range selon l'organisation de ce nœud (apps/, middlewares/…). Choisir un autre fichier le déplace."><select className="select" value={file} onChange={(e) => setFile(e.target.value)}><option value="">auto → {autoFile()}</option>{groupFiles(files.filter((f) => !/\.off$/.test(f))).map(([d, fs]) => <optgroup key={d} label={d || '(racine)'}>{fs.map((f) => <option key={f} value={f}>{f.split('/').pop()}</option>)}</optgroup>)}</select></Field>
            <Field label="Vue" tip="Formulaire guidé ou YAML brut : les deux décrivent la même chose, vous pouvez basculer à tout moment."><Seg value={view} onChange={switchView} options={[{ id: 'form', label: 'Formulaire' }, { id: 'yaml', label: 'YAML' }]} /></Field>
          </div>
          {!isNew && (usedBy.length > 0 || uses.length > 0) && (
            <div className="row wrap small">
              {usedBy.length > 0 && <><span className="muted">Utilisé par</span>{usedBy.map((u) => <Badge key={u} kind="ok"><span className="pill-link" onClick={() => onOpenItem(items.find((i) => i.id === u))}>{u.split('/').pop()}</span></Badge>)}</>}
              {uses.length > 0 && <><span className="muted">Utilise</span>{uses.map((u) => <Badge key={u.id} kind="info"><span className="pill-link" onClick={() => onOpenItem(u)}>{u.name}</span></Badge>)}</>}
            </div>
          )}
          {view === 'yaml' ? (
            <div className="editor-wrap" style={{ height: 420 }}><CodeEditor value={yamlTxt} onChange={setYamlTxt} onSave={() => save.mutate()} /></div>
          ) : (
            gallery ? <MiddlewareGallery onOther={() => { setGallery(false); setAllTypes(true) }}
              onPick={(p) => { setFrom('gallery'); setSpec(p.spec); if (!name.trim()) { let n = p.name, i = 2; while (refs.middlewares.includes(n)) n = `${p.name}-${i++}`; setName(n) } setGallery(false) }} /> : (
              <>
                {catalog && (guided && type && !allTypes
                  ? <TypeHeader type={type} preset={preset} desc={catalog[type]?.desc} onBack={() => { setSpec({}); if (isNew && from === 'gallery') setGallery(true); else setAllTypes(true) }} backLabel={isNew && from === 'gallery' ? 'Retour' : 'Changer de type'} />
                  : <TypePicker catalog={catalog} kind={kind} type={type} onBack={guided && isNew && !type ? () => { setAllTypes(false); setGallery(true) } : guided && type ? () => setAllTypes(false) : null}
                    onChange={(t) => { setSpec(t ? { [t]: {} } : {}); if (t) { setAllTypes(false); setFrom('list') } }} />)}
                {fields && (!catalog || type) && <SchemaForm key={type} fields={fields} value={body} onChange={setBody} ctx={ctx} show={preset?.show} />}
              </>)
          )}
        </div>
      </Modal>
      {dnsRows && <MissingDns rows={dnsRows} onClose={() => { setDnsRows(null); onClose() }} />}
    </>
  )
}

function TypePicker({ catalog, kind, type, onChange, onBack }) {
  const entries = Object.entries(catalog)
  const groups = kind === 'middlewares' ? MIDDLEWARE_GROUPS.map((g, i) => [g, entries.filter(([, v]) => v.group === i)]).filter(([, e]) => e.length) : [['Type', entries]]
  return (
    <div className="col">
      <div className="row">{onBack && <Btn small kind="ghost" onClick={onBack}><ArrowLeft size={14} /> Retour</Btn>}<h3>Type</h3></div>
      {groups.map(([g, list]) => (
        <div key={g} className="col" style={{ gap: 6 }}>
          {kind === 'middlewares' && <span className="muted small">{g}</span>}
          <div className="row wrap" style={{ gap: 6 }}>{list.map(([k, v]) => <Btn key={k} small kind={type === k ? 'primary' : ''} title={`${v.label} — ${kind === 'middlewares' && HUMAN[k] ? HUMAN[k][0] : v.desc}`} onClick={() => type !== k && onChange(k)}>{v.label}{v.deprecated ? ' ⚠' : ''}</Btn>)}</div>
        </div>
      ))}
      {type && <div className="muted small">{catalog[type].desc}</div>}
    </div>
  )
}
