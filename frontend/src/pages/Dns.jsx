import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { CloudUpload, Download, History, Pause, Pencil, Play, Plus, RefreshCw, Route, Share2, ShieldCheck, Trash2, Upload, Wand2 } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, CopyBtn, Copyable, Empty, Loading, Modal, SearchBox, Switch, useSort, useUi } from '../components/ui.jsx'
import CodeEditor from '../components/CodeEditor.jsx'
import RecordModal from '../components/RecordModal.jsx'
import { useOverview } from './Traefik.jsx'
import { extractHosts, fmtDate } from '../lib/util.js'
import ShareModal from '../components/ShareModal.jsx'
import { useToggler } from '../components/Toggle.jsx'
import { CertHelp, ProviderPanel } from '../components/ProviderPanel.jsx'
import { AddAccount, ImportZones } from './Accounts.jsx'
import { Info } from '../components/Tip.jsx'
import { useAuth } from '../auth.jsx'
import { useNode } from '../node.jsx'

const TYPE_COLORS = { A: 'ok', AAAA: 'ok', CNAME: 'accent', MX: 'warn', TXT: 'info', NS: '', CAA: 'info', SRV: 'warn' }

export default function Dns({ openWizard }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient(); const nav = useNavigate(); const { user } = useAuth(); const { hasTraefik, canManage, can } = useNode()
  const [adding, setAdding] = useState(false); const [imp, setImp] = useState(null)
  const canAddDns = !!user.canAddDomains || can('node.domains.link')
  const [params, setParams] = useSearchParams()
  const zones = useQuery({ queryKey: ['zones'], queryFn: () => api.get('/dns/zones') })
  const zone = params.get('zone') || zones.data?.[0]?.name
  const ov = useOverview()
  const [q, setQ] = useState(params.get('q') || ''); const [type, setType] = useState('')
  const [sel, setSel] = useState(new Set()); const [modal, setModal] = useState(null)
  const [auto, setAuto] = useState(localStorage.getItem('autoRefresh') !== '0')
  useEffect(() => setSel(new Set()), [zone])
  useEffect(() => localStorage.setItem('autoRefresh', auto ? '1' : '0'), [auto])

  const zinfo = zones.data?.find((z) => z.name === zone)
  const records = zinfo?.capabilities?.records !== false  // DNS local / DuckDNS : pas d'enregistrements libres
  const recs = useQuery({ queryKey: ['records', zone], queryFn: () => api.get(`/dns/zones/${zone}/records`), enabled: !!zone && !!zinfo && records })
  const canEdit = zinfo?.level !== 'view'; const isManage = zinfo?.level === 'manage'
  const cap = zinfo?.capabilities || {}
  const publishes = cap.publish !== false      // AdGuard Home : une réécriture est active dès qu'elle existe, rien à « appliquer »
  const showTtl = cap.ttl !== false
  const { pauseDomain, busy: pausing } = useToggler()
  const pstate = useQuery({ queryKey: ['pause-domains'], queryFn: () => api.get('/traefik/toggle/domains'), enabled: hasTraefik }).data?.find((d) => d.domain === zone?.toLowerCase())
  const routerByHost = useMemo(() => {
    const m = {}
    ov.data?.items.filter((i) => i.kind === 'routers').forEach((r) => extractHosts(r.spec.rule).forEach((h) => (m[h] ||= []).push(r)))
    return m
  }, [ov.data])

  const filtered = (recs.data || []).filter((r) => (!type || r.fieldType === type) && (!q || `${r.subDomain} ${r.fieldType} ${r.target}`.toLowerCase().includes(q.toLowerCase())))
  const { rows, th } = useSort(filtered, null, { name: (r) => r.subDomain || '', type: (r) => r.fieldType, target: (r) => r.target })
  const types = [...new Set((recs.data || []).map((r) => r.fieldType))]
  const fqdn = (r) => (r.subDomain ? `${r.subDomain}.${zone}` : zone)

  const invalidate = () => { qc.invalidateQueries({ queryKey: ['records'] }); qc.invalidateQueries({ queryKey: ['zones'] }); qc.invalidateQueries({ queryKey: ['sync'] }) }
  const refresh = useMutation({ mutationFn: () => api.post(`/dns/zones/${zone}/refresh`), onSuccess: () => { invalidate(); toast('Zone appliquée chez OVH') }, onError: (e) => toast(e.message, 'err') })
  const remove = async (ids) => {
    const label = ids.length === 1 ? (() => { const r = recs.data.find((x) => x.id === ids[0]); return `${r.fieldType} ${fqdn(r)}` })() : `${ids.length} enregistrements`
    if (!(await confirm({ title: 'Supprimer ?', message: `${label}\n\n${cap.zoneTools === false ? `La réécriture est supprimée de ${zinfo?.providerLabel}.` : 'La suppression est définitive côté OVH (restaurable via l\'historique de la zone).'}`, confirmLabel: 'Supprimer' }))) return
    try { await api.post(`/dns/zones/${zone}/bulk`, { delete: ids }); if (auto) await api.post(`/dns/zones/${zone}/refresh`); setSel(new Set()); invalidate(); toast('Supprimé') } catch (e) { toast(e.message, 'err') }
  }

  if (zones.isLoading) return <Loading />
  if (zones.error) return <div className="banner err">{zones.error.message}</div>
  const toggle = (id) => { const n = new Set(sel); n.has(id) ? n.delete(id) : n.add(id); setSel(n) }

  return (
    <>
      <div className="page-head">
        <div><h1>Domaines DNS</h1><p>{zones.data.length} domaine(s){records ? <> · {recs.data?.length ?? '…'} enregistrements dans {zone}</> : zinfo && <> · {zone} : {zinfo.providerLabel}</>}</p></div>
        {canAddDns && <Btn kind="primary" className="right" title="Ajouter un domaine : chez OVH, chez DuckDNS, ou en DNS local (sans fournisseur)." onClick={() => setAdding(true)}><Plus size={15} /> Ajouter un domaine</Btn>}
        {zones.data.length === 0 && <div className="banner info">Aucun domaine accessible pour l'instant : demandez un accès, ou ajoutez un compte (OVH, DuckDNS) ou un domaine en DNS local.</div>}
        <select className={'select ' + (canAddDns ? '' : 'right')} style={{ width: 'auto' }} value={zone} onChange={(e) => setParams({ zone: e.target.value })}>{zones.data.map((z) => <option key={z.name} value={z.name}>{z.name}{z.provider && z.provider !== 'ovh' ? ` · ${z.providerLabel}` : ''}</option>)}</select>
      </div>
      {publishes && zinfo?.dirty && !auto && canEdit && <div className="banner"><CloudUpload size={18} /> Modifications en attente : elles ne sont pas encore actives chez OVH.<Btn small kind="primary" className="right" busy={refresh.isPending} onClick={() => refresh.mutate()} disabled={!can('dns.publish')}>Appliquer maintenant</Btn></div>}
      <div className="row wrap" style={{ marginBottom: 14 }}>
        {records && <>
        <SearchBox value={q} onChange={setQ} placeholder="Filtrer (sous-domaine, valeur…)" />
        <select className="select" style={{ width: 'auto' }} value={type} onChange={(e) => setType(e.target.value)}><option value="">Tous types</option>{types.map((t) => <option key={t}>{t}</option>)}</select>
        {publishes && <label className="row small muted" title="Applique la zone chez OVH après chaque modification"><Switch value={auto} onChange={setAuto} /> Appliquer automatiquement</label>}</>}
        <div className="row right">
          {records && <Btn icon title="Recharger" onClick={() => { qc.invalidateQueries({ queryKey: ['records'] }); api.get(`/dns/zones/${zone}/records?force=true`).then(() => qc.invalidateQueries({ queryKey: ['records'] })) }}><RefreshCw size={15} /></Btn>}
          {hasTraefik && canEdit && pstate && pstate.sites > 0 && (pstate.paused > 0
            ? <Btn busy={pausing} title="Remet en service les sites que cette pause avait coupés" onClick={() => pauseDomain(zone, false)}><Play size={15} /> Reprendre les sites ({pstate.paused})</Btn>
            : <Btn busy={pausing} title="Coupe d'un coup tous les sites de ce domaine, sans toucher au DNS" onClick={() => pauseDomain(zone, true)}><Pause size={15} /> Mettre en pause les sites</Btn>)}
          {isManage && <Btn title="Choisir qui peut voir ou modifier les enregistrements de ce domaine." onClick={() => setModal({ t: 'share' })}><Share2 size={15} /> Accès</Btn>}
          {zinfo && <Btn title="Ce que Traefik doit avoir pour obtenir un certificat HTTPS sur ce domaine." onClick={() => setModal({ t: 'cert' })}><ShieldCheck size={15} /> Certificats</Btn>}
          {records && zinfo && cap.zoneTools !== false && <Btn title="Outils de la zone : import / export, historique et restauration, SOA, DNSSEC." onClick={() => setModal({ t: 'tools' })}><Wand2 size={15} /> Zone</Btn>}
          {records && zinfo && canEdit && can('dns.records.create') && <Btn kind="primary" onClick={() => setModal({ t: 'rec' })}><Plus size={15} /> Enregistrement</Btn>}
        </div>
      </div>
      {!records && zinfo && <ProviderPanel zone={zone} zinfo={zinfo} canEdit={canEdit} onChanged={invalidate} />}
      {records && <div className="card flush">
        {recs.isLoading ? <Loading /> : rows.length === 0 ? <Empty>Aucun enregistrement.</Empty> : (
          <table className="t"><thead><tr><th style={{ width: 30 }}><input type="checkbox" checked={sel.size === rows.length} onChange={(e) => setSel(e.target.checked ? new Set(rows.map((r) => r.id)) : new Set())} /></th><th>{th('name', 'Nom')}</th><th>{th('type', 'Type')}<Info>{'A / AAAA : pointe vers une adresse IP (IPv4 / IPv6).\nCNAME : alias vers un autre nom.\nMX : serveur de messagerie. TXT : texte (SPF, vérifications).\nSRV, CAA… : usages techniques.'}</Info></th><th>{th('target', 'Cible')}</th>{showTtl && <th>TTL<Info>Durée de mémorisation (en secondes) par les serveurs DNS. Plus court = un changement se propage plus vite, mais génère plus de requêtes.</Info></th>}<th>Traefik<Info>Si cette adresse est déjà servie par un site Traefik de ce nœud, ou un raccourci pour en créer un.</Info></th><th /></tr></thead>
            <tbody>{rows.map((r) => {
              const routers = routerByHost[fqdn(r)] || []
              const web = ['A', 'AAAA', 'CNAME'].includes(r.fieldType) && r.subDomain
              return (
                <tr key={r.id}>
                  <td><input type="checkbox" checked={sel.has(r.id)} onChange={() => toggle(r.id)} /></td>
                  <td><Copyable text={fqdn(r)}><b>{r.subDomain || '@'}</b><span className="muted">.{zone}</span></Copyable></td>
                  <td><Badge kind={TYPE_COLORS[r.fieldType]}>{r.fieldType}</Badge></td>
                  <td><div className="row"><code className="ellipsis" title={r.target}>{r.target}</code><CopyBtn text={r.target} /></div></td>
                  {showTtl && <td className="muted">{r.ttl || 'défaut'}</td>}
                  <td>{routers.length ? routers.map((x) => <Badge key={x.id} kind="ok"><span className="pill-link" onClick={() => nav(x.proto === 'http' ? `/sites?open=${encodeURIComponent(x.name)}` : `/traefik?edit=${encodeURIComponent(x.id)}`)}><Route size={11} /> {x.name}</span></Badge>)
                    : web && canEdit && hasTraefik && can('sites.create') ? <Btn small kind="ghost" title="Créer un routeur Traefik pour ce sous-domaine" onClick={() => openWizard({ sub: r.subDomain, zone, dns: false })}><Plus size={12} /> route</Btn> : ''}</td>
                  <td className="actions">{canEdit && <>{can('dns.records.edit') && <Btn kind="ghost" small icon onClick={() => setModal({ t: 'rec', record: r })}><Pencil size={14} /></Btn>}{can('dns.records.delete') && <Btn kind="ghost" small icon onClick={() => remove([r.id])}><Trash2 size={14} /></Btn>}</>}</td>
                </tr>
              )
            })}</tbody></table>
        )}
      </div>}
      {sel.size > 0 && <div className="sel-bar"><b>{sel.size} sélectionné(s)</b><Btn small kind="danger" className="right" onClick={() => remove([...sel])}><Trash2 size={14} /> Supprimer</Btn><Btn small onClick={() => setSel(new Set())}>Désélectionner</Btn></div>}
      {modal?.t === 'rec' && <RecordModal zone={zone} record={modal.record} onClose={() => setModal(null)} />}
      {adding && <AddAccount mode="domain" currentCred={zinfo?.credentialId} currentName={zone} onImport={(c) => setImp(c)} canKeys={canAddDns} onClose={() => setAdding(false)} onDone={(c) => { invalidate(); qc.invalidateQueries({ queryKey: ['domains'] }); qc.invalidateQueries({ queryKey: ['credentials'] }); qc.invalidateQueries({ queryKey: ['meta'] }); setAdding(false); if (c?.provider === 'ovh') setImp(c) }} />}
      {imp && <ImportZones cred={imp} onClose={() => setImp(null)} onDone={() => { invalidate(); qc.invalidateQueries({ queryKey: ['domains'] }) }} />}
      {modal?.t === 'cert' && <CertHelp zone={zone} provider={zinfo?.provider || 'ovh'} onClose={() => setModal(null)} />}
      {modal?.t === 'share' && <ShareModal domain={zone} onClose={() => setModal(null)} />}
      {modal?.t === 'tools' && <ZoneTools zone={zone} info={zinfo} level={zinfo?.level} onClose={() => setModal(null)} onChanged={invalidate} />}
    </>
  )
}

const tabs0 = (can) => [...(can('dns.export') ? [['export']] : []), ...(can('dns.import') ? [['import']] : []), ...(can('dns.history.view') ? [['history']] : []), ['info']]

function ZoneTools({ zone, info, level, onClose, onChanged }) {
  const { toast, confirm } = useUi(); const { can } = useNode()
  const [tab0, setTab] = useState('export')
  const tab = tabs0(can).some(([id]) => id === tab0) ? tab0 : tabs0(can)[0]?.[0]
  const exp = useQuery({ queryKey: ['export', zone], queryFn: () => api.get(`/dns/zones/${zone}/export`) })
  const hist = useQuery({ queryKey: ['dnshist', zone], queryFn: () => api.get(`/dns/zones/${zone}/history`) })
  const soa = useQuery({ queryKey: ['soa', zone], queryFn: () => api.get(`/dns/zones/${zone}/soa`) })
  const sec = useQuery({ queryKey: ['dnssec', zone], queryFn: () => api.get(`/dns/zones/${zone}/dnssec`) })
  const [imp, setImp] = useState('')
  const run = async (fn, ok) => { try { await fn(); toast(ok); onChanged(); sec.refetch() } catch (e) { toast(e.message, 'err') } }
  const tabs = [...(can('dns.export') ? [['export', 'Export']] : []), ...(level === 'manage' && can('dns.import') ? [['import', 'Import']] : []), ...(can('dns.history.view') ? [['history', 'Historique']] : []), ['info', 'SOA & DNSSEC']]
  return (
    <Modal wide title={`Zone ${zone}`} onClose={onClose}>
      <div className="tabs">{tabs.map(([id, l]) => <button key={id} className={'tab ' + (tab === id ? 'active' : '')} onClick={() => setTab(id)}>{l}</button>)}</div>
      {tab === 'export' && <div className="col"><div className="editor-wrap" style={{ height: 380 }}><CodeEditor language="plaintext" value={exp.data?.zoneFile || ''} readOnly /></div>
        <div><Btn onClick={() => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([exp.data.zoneFile])); a.download = `${zone}.zone`; a.click() }}><Download size={14} /> Télécharger</Btn></div></div>}
      {tab === 'import' && <div className="col"><div className="banner">Attention : l'import remplace TOUT le contenu de la zone.</div>
        <div className="editor-wrap" style={{ height: 300 }}><CodeEditor language="plaintext" value={imp} onChange={setImp} /></div>
        <div><Btn kind="danger" disabled={!imp.trim()} onClick={async () => (await confirm({ message: `Remplacer toute la zone ${zone} ?`, confirmLabel: 'Importer' })) && run(() => api.post(`/dns/zones/${zone}/import`, { zoneFile: imp }), 'Zone importée')}><Upload size={14} /> Importer</Btn></div></div>}
      {tab === 'history' && <div className="card flush">{!hist.data?.length ? <Empty>Aucun historique.</Empty> : <table className="t"><tbody>{hist.data.map((d) => <tr key={d}><td>{fmtDate(d)}</td><td className="actions"><Btn small disabled={level !== 'manage'} onClick={async () => (await confirm({ title: 'Restaurer cette version ?', message: fmtDate(d), danger: false, confirmLabel: 'Restaurer' })) && run(() => api.post(`/dns/zones/${zone}/history/restore`, { date: d }), 'Zone restaurée')}><History size={13} /> Restaurer</Btn></td></tr>)}</tbody></table>}</div>}
      {tab === 'info' && <div className="col">
        <div className="row wrap"><b>Serveurs de noms</b>{info?.nameServers?.map((n) => <Badge key={n}>{n}</Badge>)}</div>
        <div className="row"><ShieldCheck size={16} /><b>DNSSEC</b><Badge kind={sec.data?.status === 'enabled' ? 'ok' : ''}>{sec.data?.status || '…'}</Badge>
          {level === 'manage' && can('dns.dnssec.edit') && sec.data?.status === 'disabled' && <Btn small onClick={() => run(() => api.post(`/dns/zones/${zone}/dnssec/enable`), 'DNSSEC activé')}>Activer</Btn>}
          {level === 'manage' && can('dns.dnssec.edit') && sec.data?.status === 'enabled' && <Btn small kind="danger" onClick={async () => (await confirm('Désactiver DNSSEC ?')) && run(() => api.post(`/dns/zones/${zone}/dnssec/disable`), 'DNSSEC désactivé')}>Désactiver</Btn>}</div>
        {soa.data && <table className="t card"><tbody>{Object.entries(soa.data).map(([k, v]) => <tr key={k}><td className="muted">{k}</td><td className="mono">{String(v)}</td></tr>)}</tbody></table>}
      </div>}
    </Modal>
  )
}
