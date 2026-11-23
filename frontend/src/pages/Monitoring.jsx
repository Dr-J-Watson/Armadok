import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { Activity, Pencil, Bot, Ban, BellRing, EyeOff, Siren, Gauge, Download, Globe, ShieldCheck, LineChart as LineIcon, List, Lock, MapPin, Play, ShieldAlert, Trash2, BellOff, FileDown, Flag } from 'lucide-react'
import { api, qs } from '../api.js'
import { Badge, Btn, Empty, Field, Info, Loading, Modal, SearchBox, Seg, Switch, Tabs, Copyable, useSort, useUi } from '../components/ui.jsx'
import { Bars, LineChart } from '../components/Charts.jsx'
import RangePicker from '../components/RangePicker.jsx'
import Explorer from '../components/Explorer.jsx'
import Automod from '../components/Automod.jsx'
import SiteStats from '../components/SiteStats.jsx'
import { PromBreakdown, Visitors } from '../components/OverviewStats.jsx'
import WorldMap, { countryColor } from '../components/WorldMap.jsx'
import { ago, fmtBytes, fmtMs, fmtNum, fmtRate, useFeatures } from '../lib/features.js'
import { extractHosts, fmtDate } from '../lib/util.js'
import Fresh from '../components/Fresh.jsx'
import { usePersist } from '../lib/persist.js'
import { downloadCsv } from '../lib/csv.js'
import { useGo } from '../lib/nav.js'
import { useOverview } from './Traefik.jsx'

const CLASS_COLOR = { '2xx': 'var(--ok)', '3xx': 'var(--info)', '4xx': 'var(--warn)', '5xx': 'var(--err)' }
const SEV = { info: ['info', 'Info'], warning: ['warn', 'Avertissement'], critical: ['err', 'Critique'] }
const ATK_KIND = { critical: 'err', high: 'err', medium: 'warn', low: '' }   // gravité -> couleur de pastille
// nom d'un site (adresse) -> service Traefik correspondant, pour ouvrir ses statistiques
function useHostService() {
  const ov = useOverview()
  return (host) => (host ? (ov.data?.items || []).find((i) => i.kind === 'routers' && i.proto === 'http' && extractHosts(i.spec?.rule || '').includes(String(host).toLowerCase()))?.spec?.service : null)
}
// « dans 23 h », « dans 3 j »
const inFuture = (ts) => { const s = ts - Date.now() / 1000; if (s <= 0) return 'terminé'; const m = s / 60; return m < 90 ? `dans ${Math.max(1, Math.round(m))} min` : m < 60 * 36 ? `dans ${Math.round(m / 60)} h` : `dans ${Math.round(m / 1440)} j` }
const metricsHelp = <>Pas de données ? Vérifiez que Traefik expose ses métriques (<code>--metrics.prometheus=true</code>, avec <code>addEntryPointsLabels</code> et <code>addServicesLabels</code>) et que Prometheus les lit : voir l'aide, « Fonctionnalités facultatives ».</>

export default function Monitoring() {
  const f = useFeatures(); const [params, setParams] = useSearchParams()
  if (!f) return <Loading />
  const tabs = [
    ...((f.metrics.enabled && f.metrics.allowed) || (f.ips.enabled && f.ips.allowed) ? [{ id: 'overview', label: 'Vue d\'ensemble', icon: <Gauge size={15} /> }] : []),
    ...(f.metrics.enabled && f.metrics.allowed ? [{ id: 'services', label: 'Sites', icon: <Globe size={15} /> }] : []),
    ...(f.alerts.enabled && f.alerts.allowed ? [{ id: 'alerts', label: 'Alertes', icon: <BellRing size={15} />, count: f.firing || undefined }] : []),
    ...(f.ips.enabled && f.ips.allowed ? [{ id: 'ips', label: 'Adresses IP', icon: <Activity size={15} /> }] : []),
    ...(f.bans.enabled && f.bans.allowed ? [{ id: 'bans', label: 'Bannies', icon: <Ban size={15} /> }] : []),
    ...(f.ips.enabled && f.ips.allowed ? [{ id: 'automod', label: 'Automod', icon: <Bot size={15} /> }] : []),
    ...(f.bans.enabled && f.bans.allowed ? [{ id: 'trusted', label: 'Validées', icon: <ShieldCheck size={15} /> }] : []),
    ...(f.metrics.enabled && f.metrics.explore ? [{ id: 'explore', label: 'Explorateur', icon: <LineIcon size={15} /> }] : []),
  ]
  const tab = tabs.some((t) => t.id === params.get('tab')) ? params.get('tab') : tabs[0]?.id
  return (
    <>
      <div className="page-head"><div><h1>Surveillance</h1><p>Trafic, erreurs, alertes et adresses IP de ce nœud.{f.remoteNote && <> {f.remoteNote}</>}</p></div></div>
      {tabs.length === 0 ? <div className="card"><Empty><Activity size={30} /><div>Rien n'est branché sur ce nœud, ou vous n'avez pas les droits correspondants.<br />Prometheus (métriques, alertes) et le journal d'accès (adresses IP) sont facultatifs : l'aide explique comment les activer.</div></Empty></div> : <>
        <Tabs value={tab} onChange={(t) => setParams({ tab: t })} tabs={tabs} />
        {tab === 'overview' && <Overview f={f} onTab={(t) => setParams({ tab: t })} />}
        {tab === 'services' && (params.get('service') ? <SiteStats service={params.get('service')} onBack={() => setParams({ tab: 'services' })} /> : <Services onOpen={(svc) => setParams({ tab: 'services', service: svc })} />)}
        {tab === 'alerts' && <Alerts onOpenIp={(ref) => setParams({ tab: 'ips', ip: ref })} />}
        {tab === 'ips' && <Ips key={['s', 'kind', 'only'].map((k) => params.get(k)).join('|')} init={{ s: params.get('s'), kind: params.get('kind'), only: params.get('only') }} f={f} openRef={params.get('ip')} onOpened={() => { const n = new URLSearchParams(params); n.delete('ip'); setParams(n, { replace: true }) }} />}
        {tab === 'bans' && <Bans f={f} />}
        {tab === 'trusted' && <Trusted f={f} />}
        {tab === 'automod' && <Automod f={f} />}
        {tab === 'explore' && <Explorer />}
      </>}
    </>
  )
}

// ------------------------------------------------------------------ métriques
function Kpi({ label, value, kind, hint, to }) {
  const go = useGo()
  return <div className={'card kpi ' + (kind || '') + (to ? ' link' : '')} role={to ? 'link' : undefined} tabIndex={to ? 0 : undefined} onClick={to ? () => go(to) : undefined} onKeyDown={to ? (e) => e.key === 'Enter' && go(to) : undefined}><b>{value}</b><span>{label}{hint && <Info>{hint}</Info>}</span></div>
}

function MetricsOverview() {
  const [rng, setRng] = usePersist('range-overview', '1h')
  const ov = useQuery({ queryKey: ['obs-overview'], queryFn: () => api.get('/obs/overview'), refetchInterval: 30000, retry: false })
  const ser = (name) => useQuery({ queryKey: ['obs-series', name, rng], queryFn: () => api.get(`/obs/series?${qs({ name, range: rng })}`), refetchInterval: 60000, retry: false }) // eslint-disable-line react-hooks/rules-of-hooks
  const req = ser('requests'), l50 = ser('latency50'), l95 = ser('latency95'), l99 = ser('latency99'), conn = ser('connections'), bin = ser('bytesIn'), bout = ser('bytesOut')
  if (ov.isLoading) return <Loading />
  if (ov.error) return <div className="banner err">{ov.error.message}</div>
  const i = ov.data.instant; const total = i.reqRate
  const pct = (v) => (total ? `${fmtNum((v / total) * 100, 1)} %` : '—')
  const down = ov.data.targets.filter((t) => t.health !== 'up')
  const certKind = i.certDays == null ? '' : i.certDays < 7 ? 'err' : i.certDays < 21 ? 'warn' : 'ok'
  const first = (q) => q.data?.[0]?.points || []
  return (
    <div className="col gap-lg">
      {total == null && <div className="banner">{metricsHelp}</div>}
      {down.length > 0 && <div className="banner err"><ShieldAlert size={16} /> {down.length} cible(s) Prometheus injoignable(s) : {down.map((t) => `${t.job} (${t.instance})`).join(', ')}</div>}
      {i.certExpired > 0 && <div className="banner"><ShieldAlert size={16} /> {fmtNum(i.certExpired, 0)} certificat(s) déjà expiré(s) restent dans Traefik : le plus souvent des restes de sites supprimés (ils échouent à se renouveler). Si l'un d'eux est un site actif, c'est urgent ; sinon, retirez ces entrées de <code>acme.json</code>.</div>}
      {i.reloadFail != null && i.reloadOk != null && i.reloadFail > i.reloadOk && <div className="banner err"><ShieldAlert size={16} /> Traefik a refusé sa dernière configuration : il garde l'ancienne (voir « Traefik avancé » → Problèmes).</div>}
      <div className="grid stats">
        <Kpi label="Requêtes / s" value={fmtRate(total)} hint="Moyenne sur 5 minutes, tous points d'entrée confondus." />
        <Kpi to="/monitoring?tab=services" label="Erreurs serveur (5xx)" value={pct(i.err5)} kind={total && i.err5 / total > 0.05 ? 'err' : i.err5 > 0 ? 'warn' : 'ok'} hint="Part des réponses 5xx : un site en panne ou qui plante." />
        <Kpi label="Erreurs client (4xx)" value={pct(i.err4)} hint="404, 403, 401… : liens cassés, accès refusés, balayages." />
        <Kpi label="Latence p95" value={fmtMs(i.p95)} hint="95 % des requêtes sont plus rapides que cette valeur." />
        <Kpi label="Connexions ouvertes" value={fmtNum(i.openConn, 0)} />
        <Kpi label="Débit entrant / sortant" value={`${fmtBytes(i.bytesIn)} · ${fmtBytes(i.bytesOut)}`} />
        <Kpi to="/traefik?tab=certs" label="Prochain certificat à expirer" value={i.certDays == null ? '—' : `${Math.floor(i.certDays)} j`} kind={certKind} hint="Le plus proche de l'expiration parmi les certificats encore valides de Traefik." />
        <Kpi label="Cibles Prometheus" value={`${ov.data.targets.length - down.length} / ${ov.data.targets.length}`} kind={down.length ? 'err' : 'ok'} />
      </div>
      <RangePicker value={rng} onChange={setRng} />
      <div className="grid two">
        <div className="card"><h3>Requêtes par seconde <Info>Par famille de réponse : 2xx réussies, 3xx redirections, 4xx erreurs du visiteur, 5xx erreurs du serveur.</Info></h3>
          <LineChart area fmt={(v) => fmtRate(v)} series={(req.data || []).map((s) => ({ name: s.labels.class, color: CLASS_COLOR[s.labels.class] || 'var(--muted)', points: s.points }))} /></div>
        <div className="card"><h3>Latence <Info>Temps de réponse : la moitié (p50), 95 % (p95) et 99 % (p99) des requêtes sont plus rapides.</Info></h3>
          <LineChart fmt={fmtMs} series={[{ name: 'p50', color: 'var(--ok)', points: first(l50) }, { name: 'p95', color: 'var(--warn)', points: first(l95) }, { name: 'p99', color: 'var(--err)', points: first(l99) }]} /></div>
        <div className="card"><h3>Connexions ouvertes</h3><LineChart area fmt={(v) => fmtNum(v, 0)} series={[{ name: 'connexions', color: 'var(--accent)', points: first(conn) }]} /></div>
        <div className="card"><h3>Débit</h3><LineChart fmt={fmtBytes} series={[{ name: 'entrant', color: 'var(--info)', points: first(bin) }, { name: 'sortant', color: 'var(--accent)', points: first(bout) }]} /></div>
      </div>
      <PromBreakdown rng={rng} />
      {ov.data.targets.length > 0 && <div className="card flush"><table className="t"><thead><tr><th>Cible Prometheus</th><th>Adresse</th><th>État</th><th>Dernière lecture</th></tr></thead><tbody>
        {ov.data.targets.map((t, k) => <tr key={k}><td><b>{t.job}</b></td><td className="mono">{t.instance}</td><td><Badge kind={t.health === 'up' ? 'ok' : 'err'}>{t.health === 'up' ? 'en ligne' : 'hors ligne'}</Badge>{t.lastError && <span className="muted small"> {t.lastError}</span>}</td><td className="muted">{fmtDate(t.lastScrape)}</td></tr>)}</tbody></table></div>}
    </div>
  )
}

function Overview({ f, onTab }) {
  const metrics = f.metrics.enabled && f.metrics.allowed; const ips = f.ips.enabled && f.ips.allowed
  return (
    <div className="col gap-lg">
      {metrics && <MetricsOverview />}
      {ips && <Visitors f={f} onTab={onTab} />}
    </div>
  )
}

function Services({ onOpen }) {
  const q = useQuery({ queryKey: ['obs-services'], queryFn: () => api.get('/obs/services'), refetchInterval: 30000, retry: false })
  const [s, setS] = useState('')
  const { rows: sorted, th } = useSort(q.data, { key: 'rate', dir: -1 }, { rate: (r) => r.rate ?? -1, p95: (r) => r.p95 ?? -1 })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const rows = sorted.filter((r) => !s || r.service.toLowerCase().includes(s.toLowerCase()))
  return (
    <div className="col">
      <div className="row"><SearchBox value={s} onChange={setS} placeholder="Filtrer un service…" /><span className="muted small right">Moyennes sur 5 minutes</span><Fresh q={q} className="" /></div>
      {q.data.length === 0 ? <div className="card"><Empty><Globe size={28} /><div>{metricsHelp}</div></Empty></div> : (
        <div className="card flush"><table className="t"><thead><tr><th>{th('service', 'Service')}</th><th>{th('rate', 'Requêtes / s')}</th><th>{th('err5', 'Erreurs 5xx')}</th><th>{th('err4', 'Erreurs 4xx')}</th><th>{th('p95', 'Latence p95')}</th></tr></thead><tbody>
          {rows.map((r) => <tr key={r.service} onClick={() => onOpen?.(r.service)} style={{ cursor: 'pointer' }} title="Voir les statistiques de ce site"><td><b>{r.service}</b></td><td>{fmtRate(r.rate)}</td>
            <td><Badge kind={r.err5 > 5 ? 'err' : r.err5 > 0.5 ? 'warn' : 'ok'}>{fmtNum(r.err5, 1)} %</Badge></td><td><Badge kind={r.err4 > 30 ? 'err' : r.err4 > 10 ? 'warn' : 'ok'}>{fmtNum(r.err4, 1)} %</Badge></td><td>{fmtMs(r.p95)}</td></tr>)}</tbody></table></div>)}
    </div>
  )
}

// ------------------------------------------------------------------ alertes
const STATE_BADGE = { firing: ['err', 'En cours'], pending: ['warn', 'En attente'], resolved: ['ok', 'Résolue'] }

function Alerts({ onOpenIp }) {
  const { toast } = useUi(); const qc = useQueryClient()
  const q = useQuery({ queryKey: ['obs-alerts'], queryFn: () => api.get('/obs/alerts'), refetchInterval: 30000 })
  const save = useMutation({ mutationFn: ({ key, body }) => api.put(`/obs/alerts/rules/${key}`, body), onSuccess: () => qc.invalidateQueries({ queryKey: ['obs-alerts'] }), onError: (e) => toast(e.message, 'err') })
  const inval = () => qc.invalidateQueries({ queryKey: ['obs-alerts'] })
  const unsnooze = useMutation({ mutationFn: (s) => api.del(`/obs/alerts/snooze?${qs({ key: s.key, subject: s.subject || '', ref: s.ref || '' })}`), onSuccess: () => { inval(); toast('Sourdine levée') }, onError: (e) => toast(e.message, 'err') })
  const snooze = useMutation({ mutationFn: ({ s, hours }) => api.post('/obs/alerts/snooze', { key: s.key, subject: s.subject || '', ref: s.ref || null, hours }),
    onSuccess: (_, v) => { inval(); toast(`Alerte en sourdine ${v.hours === 1 ? '1 heure' : v.hours + ' heures'} : plus de message au webhook, elle reste affichée`, 'ok', { label: 'Annuler', run: () => unsnooze.mutate(v.s) }) }, onError: (e) => toast(e.message, 'err') })
  const hook = useMutation({ mutationFn: () => api.post('/obs/alerts/test-webhook'), onSuccess: () => toast('Message de test envoyé'), onError: (e) => toast(e.message, 'err') })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data; const label = (k) => d.rules.find((r) => r.key === k)?.label || k
  return (
    <div className="col gap-lg">
      {d.state.length === 0 ? <div className="banner ok"><BellRing size={16} /> Aucune alerte en cours.</div> : (
        <div className="card flush"><table className="t"><thead><tr><th>Alerte</th><th>Depuis</th><th>État</th><th /></tr></thead><tbody>
          {d.state.map((s, i) => { const [k, l] = STATE_BADGE[s.state]; return <tr key={i} onClick={s.ref ? () => onOpenIp(s.ref) : undefined} style={s.ref ? { cursor: 'pointer' } : undefined} title={s.ref ? 'Voir cette adresse' : undefined}><td><b>{label(s.key)}</b>{s.subject && <span className="muted"> · {s.subject}</span>}{s.ref && <span className="muted small"> · voir l'adresse →</span>}{s.value != null && <span className="muted small"> · {fmtNum(s.value, 2)}</span>}{s.snoozedUntil && <Badge title={`Plus de message au webhook jusqu'au ${fmtDate(s.snoozedUntil * 1000)}`}> <BellOff size={10} /> en sourdine</Badge>}</td><td className="muted">{ago(s.since)}</td><td><Badge kind={k}>{l}</Badge></td>
            <td className="actions" onClick={(e) => e.stopPropagation()}>{d.canManage && (s.snoozedUntil ? <Btn small kind="ghost" onClick={() => unsnooze.mutate(s)}><BellOff size={13} /> Rétablir</Btn> : <><Btn small kind="ghost" title="Plus de message au webhook pendant 1 heure (l'alerte reste affichée)" onClick={() => snooze.mutate({ s, hours: 1 })}><BellOff size={13} /> 1 h</Btn><Btn small kind="ghost" title="Plus de message au webhook pendant 24 heures" onClick={() => snooze.mutate({ s, hours: 24 })}>24 h</Btn></>)}</td></tr> })}</tbody></table></div>)}
      <div className="card flush">
        <div className="row" style={{ padding: '12px 16px' }}><h3>Règles</h3><Info>Évaluées chaque minute. Une alerte passe « en attente » dès que la condition est vraie, et « en cours » si elle dure au moins la durée choisie. Le message part alors au webhook du nœud (s'il y en a un) et reste dans l'historique 30 jours.</Info>
          {d.webhookSet ? <Badge kind="ok">webhook défini</Badge> : <Badge>pas de webhook</Badge>}
          {d.canManage && d.webhookSet && <Btn small className="right" busy={hook.isPending} onClick={() => hook.mutate()}><Play size={13} /> Tester le webhook</Btn>}</div>
        <table className="t"><thead><tr><th style={{ width: 70 }}>Active</th><th>Règle</th><th>Seuil</th><th>Durée minimale<Info>La condition doit rester vraie ce temps-là avant de déclencher (évite les pics d'une seconde).</Info></th><th>Gravité</th></tr></thead><tbody>
          {d.rules.map((r) => (
            <tr key={r.key} style={{ opacity: r.available ? 1 : .55 }}>
              <td><Switch value={r.enabled} disabled={!d.canManage} onChange={(v) => save.mutate({ key: r.key, body: { enabled: v } })} /></td>
              <td><b>{r.label}</b>{!r.available && <Badge kind="warn" title="Cette règle ne s'évaluera que lorsque la source sera branchée (voir l'aide « Fonctionnalités facultatives »)."> {r.source === 'prom' ? 'Prometheus requis' : 'journal d\'accès requis'}</Badge>}<div className="muted small">{r.hint}</div></td>
              <td>{r.hasThreshold ? <div className="row"><input className="input" type="number" min="0" step="any" style={{ width: 90 }} defaultValue={r.threshold} disabled={!d.canManage} key={r.key + r.threshold}
                onBlur={(e) => { const v = parseFloat(e.target.value); if (!Number.isNaN(v) && v !== r.threshold) save.mutate({ key: r.key, body: { threshold: v } }) }} /><span className="muted small">{r.unit}</span></div> : <span className="muted">—</span>}</td>
              <td><select className="select" style={{ width: 'auto' }} value={r.for_s} disabled={!d.canManage} onChange={(e) => save.mutate({ key: r.key, body: { forSeconds: Number(e.target.value) } })}>
                {[0, 60, 120, 300, 600, 1800, 3600].map((s) => <option key={s} value={s}>{s === 0 ? 'tout de suite' : s < 3600 ? `${s / 60} min` : '1 h'}</option>)}</select></td>
              <td><select className="select" style={{ width: 'auto' }} value={r.severity} disabled={!d.canManage} onChange={(e) => save.mutate({ key: r.key, body: { severity: e.target.value } })}>{Object.entries(SEV).map(([k, [, l]]) => <option key={k} value={k}>{l}</option>)}</select></td>
            </tr>))}</tbody></table>
      </div>
      {d.native.length > 0 && <div className="card flush"><div style={{ padding: '12px 16px' }}><h3>Alertes de Prometheus <Info>Celles de vos propres règles Prometheus (Alertmanager) : lecture seule, elles se gèrent dans Prometheus.</Info></h3></div>
        <table className="t"><tbody>{d.native.map((a, i) => <tr key={i}><td><b>{a.name}</b><div className="muted small">{a.summary}</div></td><td className="muted small">{Object.entries(a.labels).map(([k, v]) => `${k}=${v}`).join(' ')}</td><td><Badge kind={a.state === 'firing' ? 'err' : 'warn'}>{a.state === 'firing' ? 'en cours' : 'en attente'}</Badge></td></tr>)}</tbody></table></div>}
      {d.nativeError && <div className="muted small">Alertes natives de Prometheus indisponibles : {d.nativeError}</div>}
      <div className="card flush"><div style={{ padding: '12px 16px' }}><h3>Historique <Info>30 jours : chaque alerte déclenchée ou résolue.</Info></h3></div>
        {d.history.length === 0 ? <Empty>Rien pour l'instant.</Empty> : <table className="t"><tbody>{d.history.map((h, i) => { const [k, l] = STATE_BADGE[h.state] || ['', h.state]; return <tr key={i} onClick={h.ref ? () => onOpenIp(h.ref) : undefined} style={h.ref ? { cursor: 'pointer' } : undefined} title={h.ref ? 'Voir cette adresse' : undefined}><td className="muted" style={{ width: 150 }}>{fmtDate(h.at * 1000)}</td><td><Badge kind={k}>{l}</Badge></td><td>{h.text}</td></tr> })}</tbody></table>}</div>
    </div>
  )
}

// ------------------------------------------------------------------ adresses IP
const DURATIONS = [{ v: 1, l: '1 heure' }, { v: 24, l: '24 heures' }, { v: 168, l: '7 jours' }, { v: 720, l: '30 jours' }, { v: '', l: 'définitivement' }]

function Ips({ f, openRef, onOpened, init }) {
  const [only, setOnly] = useState(init?.only || ''); const [sort, setSort] = usePersist('ips-sort', 'errors'); const [s, setS] = useState(init?.s || ''); const [days, setDays] = usePersist('ips-days', 7); const [kind, setKind] = useState(init?.kind || '')
  const [hiddenCols, setHiddenCols] = usePersist('ips-cols', []); const showCol = (c) => !hiddenCols.includes(c); const [colMenu, setColMenu] = useState(false)
  const [sel, setSel] = useState(() => new Set()); const [bulk, setBulk] = useState(false); const [country, setCountry] = useState(null); const hostSvc = useHostService(); const go = useGo()
  const [ban, setBan] = useState(null); const [trust, setTrust] = useState(null); const [detail, setDetail] = useState(null); const [view, setView] = usePersist('ips-view', 'list')
  const { toast } = useUi(); const qc = useQueryClient()
  const dl = useMutation({ mutationFn: () => api.post('/obs/geo/update', {}), onSuccess: () => { qc.invalidateQueries({ queryKey: ['obs-ips'] }); toast('Base de localisation installée') }, onError: (e) => toast(e.message, 'err') })
  const q = useQuery({ queryKey: ['obs-ips', only, sort, s, days, kind], queryFn: () => api.get(`/obs/ips?${qs({ only, sort, search: s, days, kind })}`), refetchInterval: 30000, retry: false })
  const reveal = f.ips.reveal
  const toOpen = useQuery({ queryKey: ['obs-ip-open', openRef], queryFn: () => api.get(`/obs/ips/${openRef}`), enabled: !!openRef, retry: false })
  useEffect(() => { if (toOpen.data) { setDetail(toOpen.data); onOpened?.() } else if (toOpen.error) { toast(toOpen.error.message, 'err'); onOpened?.() } }, [toOpen.data, toOpen.error])   // eslint-disable-line react-hooks/exhaustive-deps
  const { rows: sortedRows, th } = useSort(q.data?.rows, null, { ip: (r) => r.ip, hits: (r) => r.hits, errors: (r) => r.c401 + r.c403 + r.c404 + r.c5, last: (r) => r.last_host || '', seen: (r) => r.last_seen })
  const [anchor, setAnchor] = useState(null)
  const selectable = sortedRows.filter((r) => !r.banned && !r.exempt)
  // clic = (dé)coche ; Maj + clic = toute la plage entre la dernière case cliquée et celle-ci
  const pick = (e, r) => {
    const i = selectable.findIndex((x) => x.ref === r.ref)
    setSel((o) => {
      const n = new Set(o)
      if (e.shiftKey && anchor !== null && selectable[anchor]) {
        const on = !o.has(r.ref); const [a, b] = [Math.min(anchor, i), Math.max(anchor, i)]
        for (let k = a; k <= b; k++) { if (on) n.add(selectable[k].ref); else n.delete(selectable[k].ref) }
      } else if (n.has(r.ref)) n.delete(r.ref); else n.add(r.ref)
      return n
    })
    setAnchor(i)
  }
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const t = q.data?.total
  return (
    <div className="col">
      {!reveal && <div className="banner info"><EyeOff size={16} /> Les adresses sont masquées (203.0.xxx.xxx) : il vous manque le droit « Voir les adresses IP en clair ». Vous pouvez tout de même en bannir une depuis cette liste.</div>}
      {t && <div className="grid stats"><Kpi label="Adresses vues" value={fmtNum(t.ips, 0)} /><Kpi label="Requêtes" value={fmtNum(t.hits, 0)} /><Kpi label="Adresses suspectes" value={fmtNum(t.suspicious, 0)} kind={t.suspicious ? 'warn' : 'ok'} hint="Beaucoup d'erreurs 401 / 403 / 404, ou des chemins typiques de balayage (.env, wp-login, phpmyadmin…). Les adresses de votre réseau local ne comptent pas." /><Kpi label="Validées" value={fmtNum(t.trusted, 0)} hint="Adresses que vous avez reconnues : elles ne sont plus marquées ni bannissables. Le réseau local est validé d'office." /><Kpi label="Bannies" value={fmtNum(t.banned, 0)} /></div>}
      {t?.leaks > 0 && <div className="banner err"><Siren size={16} /> <b>Fuite possible :</b> {t.leaks} adresse(s) ont obtenu une réponse 200 sur un fichier sensible (.env, .git, sauvegarde…). Vérifiez ces fichiers sans attendre : filtre « Fichier sensible servi » ci-dessous.</div>}
      {q.data && !q.data.geo?.available && <div className="banner info row" style={{ gap: 10 }}><MapPin size={16} /><span className="grow">Pas de localisation : téléchargez la base gratuite DB-IP (environ 130 Mo, installée sur ce serveur : aucune adresse n'est envoyée à l'extérieur) pour voir le pays, la ville approximative et une carte.</span>
        <Btn small busy={dl.isPending} onClick={() => dl.mutate()}><Download size={13} /> Télécharger la base</Btn></div>}
      {q.data?.kinds?.length > 0 && <div className="row wrap" style={{ gap: 8 }}><span className="muted small">Types d'attaque probables :</span>
        {q.data.kinds.map((k) => <button key={k.kind} type="button" className={'badge ' + ATK_KIND[k.severity]} style={{ cursor: 'pointer', border: 0, outline: kind === k.kind ? '2px solid var(--accent)' : 'none' }} title={k.why} onClick={() => setKind(kind === k.kind ? '' : k.kind)}>{k.label} · {k.ips} adresse{k.ips > 1 ? 's' : ''}</button>)}
        {kind && <Btn small kind="ghost" onClick={() => setKind('')}>Tous les types</Btn>}</div>}
      <div className="row wrap">
        <Seg options={[{ id: 'all', label: 'Toutes' }, { id: 'public', label: 'Publiques' }, { id: 'suspicious', label: 'Suspectes' }, { id: 'attacks', label: 'Attaques' }, { id: 'trusted', label: 'Validées' }, { id: 'banned', label: 'Bannies' }]} value={only || 'all'} onChange={(v) => setOnly(v === 'all' ? '' : v)} />
        {q.data?.geo?.available && <Seg options={[{ id: 'list', label: 'Liste' }, { id: 'map', label: 'Carte' }]} value={view} onChange={setView} />}
        <select className="select" style={{ width: 'auto' }} value={sort} onChange={(e) => setSort(e.target.value)}><option value="hits">Trier : requêtes</option><option value="errors">Trier : erreurs</option><option value="recent">Trier : récentes</option></select>
        <select className="select" style={{ width: 'auto' }} value={days} onChange={(e) => setDays(Number(e.target.value))}>{[1, 3, 7].map((d) => <option key={d} value={d}>{d === 1 ? '24 heures' : `${d} jours`}</option>)}</select>
        <SearchBox value={s} onChange={setS} placeholder={reveal ? 'Adresse, site ou chemin…' : 'Site ou chemin…'} />
        <span className="muted small right">{q.data?.status?.lastLine ? `Journal lu ${ago(q.data.status.lastLine)}` : q.data?.status?.error ? `Journal : ${q.data.status.error}` : 'Journal en cours de lecture…'} · conservé 7 jours</span><Fresh q={q} className="" />
        <span className="menu-wrap"><Btn small kind="ghost" onClick={() => setColMenu((v) => !v)}><List size={14} /> Colonnes</Btn>{colMenu && <div className="menu-pop" onMouseLeave={() => setColMenu(false)}>{[['errors', 'Erreurs'], ['last', 'Dernière requête'], ['seen', 'Vue']].map(([k, l]) => <label key={k} className="row" style={{ padding: '6px 10px', gap: 8, cursor: 'pointer' }}><input type="checkbox" checked={showCol(k)} onChange={() => setHiddenCols((h) => (h.includes(k) ? h.filter((x) => x !== k) : [...h, k]))} />{l}</label>)}</div>}</span>
        {q.data?.rows?.length > 0 && <Btn small kind="ghost" title="Télécharger la liste affichée (CSV)" onClick={() => downloadCsv('adresses-ip.csv', ['Adresse', 'Pays', 'Ville', 'Requêtes', 'Erreurs', '401', '403', '404', '5xx', 'Attaques', 'Suspecte', 'Validée', 'Bannie', 'Dernière requête'], q.data.rows.map((r) => [r.ip, r.geo?.countryName || '', r.geo?.city || '', r.hits, r.errors, r.c401, r.c403, r.c404, r.c5, r.attacks.map((a) => a.label).join(' | '), r.suspicious ? 'oui' : '', r.trusted ? 'oui' : '', r.banned ? 'oui' : '', `${r.last_host || ''}${r.last_path || ''}`]))}><FileDown size={14} /> CSV</Btn>}
      </div>
      {view === 'map' && q.data?.geo?.available && <GeoPanel rows={q.data.rows} canBan={f.bans.manage} canAutomod={f.ips.automod} onDetail={setDetail} onBan={setBan} onTrust={setTrust} onCountry={setCountry} />}
      {view === 'list' && (q.isLoading ? <Loading /> : q.data.rows.length === 0 ? <div className="card"><Empty><Activity size={28} /><div>Aucune adresse pour ce filtre.</div>{(only || kind || s) && <Btn style={{ marginTop: 10 }} onClick={() => { setOnly(''); setKind(''); setS('') }}>Tout afficher</Btn>}</Empty></div> : (
        <div className="card flush"><table className="t sticky"><thead><tr>{f.bans.manage && <th style={{ width: 30 }}><input type="checkbox" title="Tout sélectionner (les adresses bannissables)" checked={sel.size > 0 && sel.size === selectable.length} onChange={(e) => { setSel(e.target.checked ? new Set(selectable.map((r) => r.ref)) : new Set()); setAnchor(null) }} /></th>}<th>{th('ip', 'Adresse')}</th><th>{th('hits', 'Requêtes')}</th>{showCol('errors') && <th>{th('errors', 'Erreurs')}<Info>401 (identification refusée) · 403 (interdit) · 404 (introuvable) · 5xx (panne du serveur).</Info></th>}{showCol('last') && <th>{th('last', 'Dernière requête')}</th>}{showCol('seen') && <th>{th('seen', 'Vue')}</th>}<th /></tr></thead><tbody>
          {sortedRows.map((r) => (
            <tr key={r.ref} onClick={() => setDetail(r)} style={{ cursor: 'pointer' }}>
              {f.bans.manage && <td onClick={(e) => { e.stopPropagation(); if (!r.banned && !r.exempt) pick(e, r) }} onMouseDown={(e) => { if (e.shiftKey) e.preventDefault() }} style={{ cursor: r.banned || r.exempt ? 'default' : 'pointer', userSelect: 'none' }} title={r.banned || r.exempt ? undefined : 'Maj + clic : sélectionne une plage'}>{!r.banned && !r.exempt && <input type="checkbox" checked={sel.has(r.ref)} readOnly style={{ pointerEvents: 'none' }} />}</td>}
              <td><div className="row wrap">{reveal ? <Copyable text={r.ip}><b className="mono">{r.ip}</b></Copyable> : <b className="mono">{r.ip}</b>}{!reveal && <Lock size={11} className="muted" />}{r.attacks.map((a) => <Badge key={a.kind} kind={ATK_KIND[a.severity]} title={`${a.why}${a.sample ? ' — ex. ' + a.sample : ''}`}>{a.label}</Badge>)}{r.suspicious && !r.private && !r.attacks.length && <Badge kind="warn">suspecte</Badge>}{r.private && <Badge title="Adresse privée : validée d'office, jamais marquée">réseau local</Badge>}{r.trusted && <Badge kind="ok"><ShieldCheck size={11} /> validée</Badge>}{r.banned && <Badge kind="err"><Ban size={11} /> bannie</Badge>}</div>
                {r.geo && <div className="muted small"><MapPin size={11} style={{ verticalAlign: -1 }} /> {[r.geo.city, r.geo.countryName].filter(Boolean).join(', ')}</div>}
                {r.probes.length > 0 && <div className="muted small">teste : {r.probes.join(' · ')}</div>}</td>
              <td>{fmtNum(r.hits, 0)}</td>
              {showCol('errors') && <td className="small" style={{ whiteSpace: 'nowrap' }} title={`401 : ${r.c401} · 403 : ${r.c403} · 404 : ${r.c404} · 5xx : ${r.c5}`}><span className="errbar"><i style={{ width: `${Math.min(100, ((r.c401 + r.c403 + r.c404) / Math.max(1, r.hits)) * 100)}%`, background: 'var(--warn)' }} /><i style={{ width: `${Math.min(100, (r.c5 / Math.max(1, r.hits)) * 100)}%`, background: 'var(--err)' }} /></span>{r.c401 + r.c403 + r.c404 + r.c5}{r.c5 > 0 && <span style={{ color: 'var(--err)' }}> · {r.c5} en 5xx</span>}</td>}
              {showCol('last') && <td className="small"><div className="ellipsis" style={{ maxWidth: 190 }}>{hostSvc(r.last_host) ? <span className="linkcell" title="Statistiques de ce site" onClick={(e) => { e.stopPropagation(); go(`/monitoring?tab=services&service=${encodeURIComponent(hostSvc(r.last_host))}`) }}>{r.last_host}</span> : r.last_host}<span className="muted">{r.last_path}</span></div></td>}
              {showCol('seen') && <td className="muted small" style={{ whiteSpace: 'nowrap' }}>{ago(r.last_seen)}</td>}
              <td className="actions">{f.bans.manage && !r.banned && !r.exempt && <Btn small kind="ghost" title="Je reconnais cette adresse : ne plus la marquer" onClick={(e) => { e.stopPropagation(); setTrust(r) }}><ShieldCheck size={13} /> Valider</Btn>}{f.bans.manage && !r.banned && !r.exempt && <Btn small kind="ghost" onClick={(e) => { e.stopPropagation(); setBan(r) }}><Ban size={13} /> Bannir</Btn>}</td>
            </tr>))}</tbody></table></div>))}
      {view === 'list' && q.data?.rows?.length >= 500 && <div className="muted small" style={{ textAlign: 'center' }}>Les 500 premières adresses sont affichées : affinez avec la recherche, le type d'attaque ou la période.</div>}
      {sel.size > 0 && f.bans.manage && <div className="sel-bar"><b>{sel.size} adresse{sel.size > 1 ? 's' : ''} sélectionnée{sel.size > 1 ? 's' : ''}</b><Btn small kind="danger" className="right" onClick={() => setBulk(true)}><Ban size={14} /> Bannir</Btn><Btn small onClick={() => setSel(new Set())}>Désélectionner</Btn></div>}
      {bulk && <BulkBanModal refs={[...sel]} rows={q.data?.rows || []} onClose={(done) => { setBulk(false); if (done) setSel(new Set()) }} />}
      {country && <CountryBanModal code={country.code} name={country.name} onClose={() => setCountry(null)} />}
      {ban && <BanModal target={ban} onClose={() => setBan(null)} />}
      {trust && <TrustModal target={trust} onClose={() => setTrust(null)} />}
      {detail && <IpDetail row={detail} canBan={f.bans.manage} canAutomod={f.ips.automod} onCountry={() => { setCountry({ code: detail.geo.country, name: detail.geo.countryName }); setDetail(null) }} onTrust={() => { setTrust(detail); setDetail(null) }} onBan={() => { setBan(detail); setDetail(null) }} onClose={() => setDetail(null)} />}
    </div>
  )
}

function GeoPanel({ rows, canBan, canAutomod, onDetail, onBan, onTrust, onCountry }) {
  const [pick, setPick] = useState(null)
  const located = rows.filter((r) => r.geo && typeof r.geo.lat === 'number')
  const kindOf = (r) => (r.attacks.length || r.banned ? 'err' : r.suspicious ? 'warn' : '')
  const byCountry = {}
  for (const r of rows) if (r.geo) { const c = (byCountry[r.geo.country] ||= { code: r.geo.country, name: r.geo.countryName, ips: 0, hits: 0 }); c.ips += 1; c.hits += r.hits }
  const top = Object.values(byCountry).sort((a, b) => b.hits - a.hits).slice(0, 12)
  const label = (r) => `${r.ip} — ${[r.geo.city, r.geo.countryName].filter(Boolean).join(', ')} — ${r.hits} requêtes`
  return (
    <div className="card col">
      {located.length === 0 ? <Empty><MapPin size={28} /><div>Aucune adresse localisable pour ce filtre.</div></Empty> : (
        <WorldMap points={located.map((r) => ({ lat: r.geo.lat, lon: r.geo.lon, hits: r.hits, country: r.geo.country, kind: kindOf(r), ip: r.ip, place: [r.geo.city, r.geo.countryName].filter(Boolean).join(', '), errors: r.errors, banned: r.banned, attacks: r.attacks.map((a) => a.label), row: r }))} onPick={(p) => setPick(p)} />)}
      {pick && <div className="row wrap" style={{ gap: 10, padding: '10px 12px', border: '1px solid var(--accent)', borderRadius: 10, background: 'var(--accent-soft)' }}>
        <Copyable text={pick.ip}><b className="mono">{pick.ip}</b></Copyable><span className="muted">{[pick.geo?.city, pick.geo?.countryName].filter(Boolean).join(', ')}</span><span className="small">{fmtNum(pick.hits, 0)} requêtes · {pick.errors} erreurs</span>{pick.banned && <Badge kind="err">bannie</Badge>}{pick.trusted && <Badge kind="ok">validée</Badge>}
        <span className="right row" style={{ gap: 6 }}><Btn small onClick={() => onDetail(pick)}>Détail</Btn>{canBan && !pick.banned && !pick.exempt && <Btn small onClick={() => onTrust(pick)}><ShieldCheck size={13} /> Valider</Btn>}{canBan && !pick.banned && !pick.exempt && <Btn small kind="danger" onClick={() => onBan(pick)}><Ban size={13} /> Bannir</Btn>}{canAutomod && pick.geo?.country && <Btn small kind="ghost" title="Crée une règle d'automod pour ce pays" onClick={() => onCountry({ code: pick.geo.country, name: pick.geo.countryName })}><Flag size={13} /> Ce pays</Btn>}<Btn small kind="ghost" icon title="Fermer" onClick={() => setPick(null)}>×</Btn></span></div>}
      <div className="row small muted" style={{ gap: 14 }}><span>Couleur = pays</span><span><span style={{ color: 'var(--warn)' }}>◯</span> contour orange : suspecte</span><span><span style={{ color: 'var(--err)' }}>◯</span> contour rouge : attaque ou bannie</span><span className="right">Taille = requêtes · molette : zoom · glisser : déplacer · position approximative</span></div>
      {top.length > 0 && <div className="row wrap" style={{ gap: 8 }}>{top.map((c) => <Badge key={c.name}><span style={{ color: countryColor(c.code) }}>●</span> {c.name} · {c.ips} adresse{c.ips > 1 ? 's' : ''} · {fmtNum(c.hits, 0)} req.</Badge>)}</div>}
    </div>
  )
}

function BanModal({ target, onClose, manual }) {
  const { toast } = useUi(); const qc = useQueryClient()
  const [hours, setHours] = useState(24); const [reason, setReason] = useState(''); const [ip, setIp] = useState('')
  const go = useMutation({ mutationFn: () => api.post('/obs/bans', { ...(manual ? { ip: ip.trim() } : { ref: target.ref }), hours: hours === '' ? null : Number(hours), reason }),
    onSuccess: (b) => { const inv = () => { qc.invalidateQueries({ queryKey: ['obs-ips'] }); qc.invalidateQueries({ queryKey: ['obs-bans'] }); qc.invalidateQueries({ queryKey: ['features'] }) }; inv(); toast('Adresse bannie : Traefik répond 403 sur tous les sites de ce nœud', 'ok', b?.id ? { label: 'Annuler', run: () => api.del(`/obs/bans/${b.id}`).then(() => { inv(); toast('Bannissement annulé') }).catch((e) => toast(e.message, 'err')) } : null); onClose() }, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal small title={manual ? 'Bannir une adresse' : `Bannir ${target.ip}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="danger" busy={go.isPending} disabled={manual && !ip.trim()} onClick={() => go.mutate()}><Ban size={14} /> Bannir</Btn></>}>
      <div className="col">
        <div className="muted">L'adresse recevra une erreur 403 sur <b>tous les sites de ce nœud</b>, en HTTP comme en HTTPS. Une règle est écrite dans un fichier de Traefik (<code>zz-webui-bans.yml</code>) ; elle disparaît quand le bannissement se termine.</div>
        {manual && <Field label="Adresse ou réseau" help="Une adresse (45.12.34.56) ou un réseau jusqu'à /16 (45.12.34.0/24). Les adresses privées et la vôtre sont refusées."><input className="input mono" value={ip} onChange={(e) => setIp(e.target.value)} placeholder="45.12.34.56" autoFocus /></Field>}
        <Field label="Pendant"><select className="select" value={hours} onChange={(e) => setHours(e.target.value)}>{DURATIONS.map((d) => <option key={d.l} value={d.v}>{d.l}</option>)}</select></Field>
        <Field label="Raison" help="Facultatif, pour s'en souvenir."><input className="input" value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} placeholder="balayage de /.env" /></Field>
      </div>
    </Modal>
  )
}

function IpDetail({ row, canBan, canAutomod, onCountry, onBan, onTrust, onClose }) {
  const hostSvc = useHostService(); const go = useGo()
  const q = useQuery({ queryKey: ['obs-ip', row.ref], queryFn: () => api.get(`/obs/ips/${row.ref}`), retry: false })
  return (
    <Modal title={<Copyable text={row.ip}>{row.ip}</Copyable>} onClose={onClose} footer={<>{canAutomod && row.geo?.country && <Btn kind="ghost" title="Crée une règle d'automod pour ce pays" onClick={onCountry}><Flag size={14} /> Bannir ce pays</Btn>}{canBan && !row.banned && !row.exempt && <Btn onClick={onTrust}><ShieldCheck size={14} /> Valider</Btn>}{canBan && !row.banned && !row.exempt && <Btn kind="danger" onClick={onBan}><Ban size={14} /> Bannir</Btn>}<Btn onClick={onClose}>Fermer</Btn></>}>
      {q.isLoading ? <Loading /> : q.error ? <div className="banner err">{q.error.message}</div> : (
        <div className="col gap-lg">
          <div className="row wrap small muted"><span>Vue pour la première fois {ago(q.data.firstSeen)}</span><span>· dernière fois {ago(q.data.lastSeen)}</span>{q.data.lastUa && <span className="ellipsis" style={{ maxWidth: 360 }}>· {q.data.lastUa}</span>}</div>
          {q.data.geo && <div className="row" style={{ gap: 6 }}><MapPin size={14} className="muted" /><span>{[q.data.geo.city, q.data.geo.countryName].filter(Boolean).join(', ')}</span><span className="muted small">(approximatif : celui du fournisseur d'accès)</span></div>}
          {q.data.attacks?.length > 0 && <div><h3>Attaques probables <Info>Reconnues par signatures dans les requêtes : ce sont des indices, pas des preuves. Les paramètres des requêtes ne sont jamais conservés.</Info></h3>
            <div className="col" style={{ gap: 8 }}>{q.data.attacks.map((a) => <div key={a.kind}><Badge kind={ATK_KIND[a.severity]}>{a.label}</Badge> <span className="muted small">{a.hits} fois{a.sample ? ` · ex. ${a.sample}` : ''}</span><div className="muted small">{a.why}</div></div>)}</div></div>}
          <div><h3>Activité des 48 dernières heures <Info>Chaque bâton est une heure (les petits traits gris : heures sans activité) ; en bleu les requêtes, en rouge les erreurs. Un bâton tout rouge = toutes les requêtes de cette heure ont échoué.</Info></h3><Bars data={q.data.timeline} /></div>
          <div><h3>Chemins demandés</h3>
            <table className="t"><thead><tr><th>Site</th><th>Chemin</th><th>Requêtes</th><th>Erreurs</th></tr></thead><tbody>
              {q.data.paths.map((p, i) => <tr key={i}><td className="small">{hostSvc(p.host) ? <span className="linkcell" title="Statistiques de ce site" onClick={() => go(`/monitoring?tab=services&service=${encodeURIComponent(hostSvc(p.host))}`)}>{p.host}</span> : (p.host || '—')}</td><td className="mono small">{p.path}{p.probe && <Badge kind="warn"> typique d'un balayage</Badge>}</td><td>{p.hits}</td><td>{p.errors}</td></tr>)}</tbody></table></div>
        </div>)}
    </Modal>
  )
}

function BulkBanModal({ refs, rows, onClose }) {
  const { toast } = useUi(); const qc = useQueryClient(); const [hours, setHours] = useState(24); const [reason, setReason] = useState(''); const [busy, setBusy] = useState(false)
  const byRef = Object.fromEntries(rows.map((r) => [r.ref, r]))
  const inv = () => { qc.invalidateQueries({ queryKey: ['obs-ips'] }); qc.invalidateQueries({ queryKey: ['obs-bans'] }); qc.invalidateQueries({ queryKey: ['features'] }) }
  const go = async () => {
    setBusy(true); const ids = []; let fail = 0; let why = ''
    for (const ref of refs) {
      try { const b = await api.post('/obs/bans', { ref, hours: hours === '' ? null : Number(hours), reason }); ids.push(b.id) } catch (e) { fail += 1; why = e.message }
    }
    setBusy(false); inv()
    toast(`${ids.length} adresse${ids.length > 1 ? 's' : ''} bannie${ids.length > 1 ? 's' : ''}${fail ? ` · ${fail} refusée${fail > 1 ? 's' : ''} (${why})` : ''}`, fail && !ids.length ? 'err' : 'ok',
      ids.length ? { label: 'Annuler', run: async () => { for (const id of ids) { try { await api.del(`/obs/bans/${id}`) } catch { /* déjà levé */ } } inv(); toast('Bannissements annulés') } } : null)
    onClose(true)
  }
  return (
    <Modal small title={`Bannir ${refs.length} adresse${refs.length > 1 ? 's' : ''}`} onClose={() => onClose(false)} footer={<><Btn onClick={() => onClose(false)}>Annuler</Btn><Btn kind="danger" busy={busy} onClick={go}><Ban size={14} /> Bannir</Btn></>}>
      <div className="col">
        <div className="muted">Chacune recevra une erreur 403 sur <b>tous les sites de ce nœud</b>. Les adresses du réseau local et celles que vous avez validées sont refusées d'office.</div>
        <div className="mono small" style={{ maxHeight: 120, overflow: 'auto' }}>{refs.map((r) => byRef[r]?.ip || r).join('  ·  ')}</div>
        <Field label="Pendant"><select className="select" value={hours} onChange={(e) => setHours(e.target.value)}>{DURATIONS.map((d) => <option key={d.l} value={d.v}>{d.l}</option>)}</select></Field>
        <Field label="Raison" help="Facultatif."><input className="input" value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} placeholder="balayage en groupe" /></Field>
      </div>
    </Modal>
  )
}

// « Bannir ce pays » : crée une règle d'automod (observation d'abord, par prudence).
function CountryBanModal({ code, name, onClose }) {
  const { toast } = useUi(); const qc = useQueryClient(); const go = useGo()
  const [mode, setMode] = useState('observe'); const [hours, setHours] = useState(24)
  const save = useMutation({ mutationFn: () => api.post('/obs/automod', { name: `Pays : ${name}`, conditions: { country: { mode: 'in', codes: [code] } }, mode, hours: hours === '' ? null : Number(hours), enabled: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['obs-automod'] }); toast(mode === 'block' ? `Règle créée : les adresses de ${name} seront bannies automatiquement` : `Règle créée en observation : rien n'est banni, elle journalise ce qu'elle aurait banni`); onClose(); go('/monitoring?tab=automod') }, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal small title={`Bannir les adresses de ${name}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind={mode === 'block' ? 'danger' : 'primary'} busy={save.isPending} onClick={() => save.mutate()}>Créer la règle</Btn></>}>
      <div className="col">
        <div className="muted">Crée une règle d'automod : toute adresse dont le pays est <b>{name}</b> sera concernée. Les adresses du réseau local et les adresses validées ne le sont jamais.</div>
        <Field label="Mode"><Seg options={[{ id: 'observe', label: 'Observation (recommandé)' }, { id: 'block', label: 'Blocage' }]} value={mode} onChange={setMode} /></Field>
        <div className="muted small">{mode === 'observe' ? 'Rien n\'est banni : l\'onglet Automod montre ce que la règle aurait fait. Vous la passez en blocage quand vous êtes sûr.' : 'Les adresses de ce pays reçoivent un 403 sur tous les sites de ce nœud, dès la prochaine passe (moins d\'une minute).'}</div>
        {mode === 'block' && <Field label="Bannies pendant"><select className="select" value={hours} onChange={(e) => setHours(e.target.value)}>{DURATIONS.map((d) => <option key={d.l} value={d.v}>{d.l}</option>)}</select></Field>}
      </div>
    </Modal>
  )
}

function TrustModal({ target, onClose, manual }) {
  const { toast } = useUi(); const qc = useQueryClient()
  const [note, setNote] = useState(''); const [ip, setIp] = useState('')
  const go = useMutation({ mutationFn: () => api.post('/obs/trusted', { ...(manual ? { ip: ip.trim() } : { ref: target.ref }), note }),
    onSuccess: (t) => { const inv = () => { qc.invalidateQueries({ queryKey: ['obs-ips'] }); qc.invalidateQueries({ queryKey: ['obs-trusted'] }) }; inv(); toast('Adresse validée : elle n\'est plus marquée', 'ok', t?.id ? { label: 'Annuler', run: () => api.del(`/obs/trusted/${t.id}`).then(() => { inv(); toast('Validation annulée') }).catch((e) => toast(e.message, 'err')) } : null); onClose() }, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal small title={manual ? 'Valider une adresse' : `Valider ${target.ip}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={go.isPending} disabled={manual && !ip.trim()} onClick={() => go.mutate()}><ShieldCheck size={14} /> Valider</Btn></>}>
      <div className="col">
        <div className="muted">Une adresse validée est une adresse que vous reconnaissez (vous, un collègue, un service de supervision) : elle n'est plus marquée suspecte ni comme attaque, ne déclenche plus d'alerte et ne peut pas être bannie. Ses requêtes restent visibles. Vous pouvez retirer la validation à tout moment (onglet « Validées »).</div>
        {manual && <Field label="Adresse ou réseau" help="Une adresse (203.0.113.7) ou un réseau jusqu'à /16 (203.0.113.0/24). Le réseau local est déjà validé d'office."><input className="input mono" value={ip} onChange={(e) => setIp(e.target.value)} placeholder="203.0.113.7" autoFocus /></Field>}
        <Field label="Note" help="Facultatif, pour se souvenir de qui c'est."><input className="input" value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder="mon bureau, supervision…" /></Field>
      </div>
    </Modal>
  )
}

function Trusted({ f }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient(); const [add, setAdd] = useState(false)
  const q = useQuery({ queryKey: ['obs-trusted'], queryFn: () => api.get('/obs/trusted'), refetchInterval: 60000 })
  const del = useMutation({ mutationFn: (id) => api.del(`/obs/trusted/${id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ['obs-trusted'] }); qc.invalidateQueries({ queryKey: ['obs-ips'] }); toast('Validation retirée') }, onError: (e) => toast(e.message, 'err') })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const unvalidate = async (t) => { if (await confirm({ title: `Retirer la validation de ${t.ip} ?`, message: 'L\'adresse pourra de nouveau être marquée suspecte et déclencher des alertes.', confirmLabel: 'Retirer' })) del.mutate(t.id) }
  return (
    <div className="col">
      <div className="row"><div className="muted grow">Les adresses validées ne sont plus marquées suspectes ni comme attaques, ne déclenchent pas d'alerte et ne peuvent pas être bannies. <b>Le réseau local</b> (10.x, 172.16-31.x, 192.168.x, loopback) est validé d'office, sans rien à enregistrer.</div>
        {q.data.canManage && f.ips.reveal && <Btn kind="primary" onClick={() => setAdd(true)}><ShieldCheck size={14} /> Valider une adresse</Btn>}</div>
      {q.data.trusted.length === 0 ? <div className="card"><Empty><ShieldCheck size={28} /><div>Aucune adresse validée (hors réseau local). Depuis l'onglet « Adresses IP », le bouton « Valider » reconnaît une adresse.</div>{q.data.canManage && f.ips.reveal && <Btn kind="primary" style={{ marginTop: 10 }} onClick={() => setAdd(true)}><ShieldCheck size={14} /> Valider une adresse</Btn>}</Empty></div> : (
        <div className="card flush"><table className="t"><thead><tr><th>Adresse</th><th>Note</th><th>Par</th><th>Depuis</th><th /></tr></thead><tbody>
          {q.data.trusted.map((t) => <tr key={t.id}><td className="mono"><Copyable text={t.ip}><b>{t.ip}</b></Copyable>{t.geo && <div className="muted small">{[t.geo.city, t.geo.countryName].filter(Boolean).join(', ')}</div>}</td><td>{t.note || <span className="muted">—</span>}</td><td className="muted">{t.created_by}</td><td className="muted small">{ago(t.created_at)}</td>
            <td className="actions">{q.data.canManage && <Btn small kind="ghost" onClick={() => unvalidate(t)}><Trash2 size={13} /> Retirer</Btn>}</td></tr>)}</tbody></table></div>)}
      {add && <TrustModal manual onClose={() => setAdd(false)} />}
    </div>
  )
}

const EDIT_PRESETS = [[1, '1 heure'], [6, '6 heures'], [24, '24 heures'], [72, '3 jours'], [168, '7 jours'], [720, '30 jours'], [2160, '90 jours'], [8760, '1 an'], [null, 'Définitivement']]

function BanEditModal({ ban, onClose }) {
  const { toast } = useUi(); const qc = useQueryClient()
  const [choice, setChoice] = useState('24'); const [n, setN] = useState(2); const [u, setU] = useState('d'); const [reason, setReason] = useState(ban.reason || '')
  const hours = choice === 'perm' ? null : choice === 'custom' ? Number(n) * { h: 1, d: 24 }[u] : Number(choice)
  const valid = hours === null || (hours > 0 && hours <= 8760)
  const go = useMutation({ mutationFn: () => api.put(`/obs/bans/${ban.id}`, { hours, reason }), onSuccess: () => { qc.invalidateQueries({ queryKey: ['obs-bans'] }); qc.invalidateQueries({ queryKey: ['obs-ips'] }); toast(hours === null ? 'Bannissement rendu définitif' : 'Durée modifiée'); onClose() }, onError: (e) => toast(e.message, 'err') })
  const until = hours === null ? 'définitivement' : `jusqu'au ${fmtDate((Date.now() + hours * 3600000))}`
  return (
    <Modal small title={`Durée du bannissement de ${ban.ip}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={go.isPending} disabled={!valid} onClick={() => go.mutate()}>Enregistrer</Btn></>}>
      <div className="col">
        <div className="muted">Actuellement : {ban.expires_at ? `jusqu'au ${fmtDate(ban.expires_at * 1000)}` : 'définitif'}. La nouvelle durée compte <b>à partir de maintenant</b>.</div>
        <Field label="Bannie pendant"><select className="select" value={choice} onChange={(e) => setChoice(e.target.value)}>
          {EDIT_PRESETS.map(([h, l]) => <option key={l} value={h === null ? 'perm' : String(h)}>{l}</option>)}<option value="custom">Durée personnalisée…</option></select></Field>
        {choice === 'custom' && <div className="row" style={{ gap: 8 }}><input className="input" type="number" min="1" style={{ width: 100 }} value={n} onChange={(e) => setN(e.target.value)} /><select className="select" style={{ width: 'auto' }} value={u} onChange={(e) => setU(e.target.value)}><option value="h">heures</option><option value="d">jours</option></select><span className="muted small">1 an au plus</span></div>}
        <div className="small">{valid ? <>Fin du bannissement : <b>{until}</b></> : <span style={{ color: 'var(--err)' }}>Durée invalide (1 heure à 1 an).</span>}</div>
        <Field label="Raison" help="Facultatif."><input className="input" value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} /></Field>
      </div>
    </Modal>
  )
}

// ------------------------------------------------------------------ bannissements
// Compteur « 142 / 2 000 » et réglage de la limite de bannissements de ce nœud (nombre ou illimité, et que faire au débordement).
function BanLimit({ d }) {
  const { toast } = useUi(); const qc = useQueryClient(); const [edit, setEdit] = useState(false)
  const [unl, setUnl] = useState(false); const [v, setV] = useState(''); const [ov, setOv] = useState(d.overflow)
  const save = useMutation({ mutationFn: (b) => api.put('/obs/bans-limit', b), onSuccess: () => { qc.invalidateQueries({ queryKey: ['obs-bans'] }); setEdit(false); toast('Limite enregistrée', 'ok') }, onError: (e) => toast(e.message, 'err') })
  const open = () => { setUnl(d.limit === 0); setV(String(d.limit || d.defaultLimit)); setOv(d.overflow); setEdit(true) }
  const unlimited = d.limit === 0
  const pct = unlimited ? 0 : Math.min(100, Math.round((d.count / d.limit) * 100)); const kind = unlimited ? 'ok' : d.count >= d.limit ? 'err' : pct >= 80 ? 'warn' : 'ok'
  const rule = d.overflow.mode === 'refuse' ? 'les nouvelles adresses sont refusées' : `les bannissements d'automod ${d.overflow.by === 'oldest' ? 'les plus anciens' : 'les moins longs à courir'} sont levés${d.overflow.permanent ? ' (définitifs compris)' : ''}`
  return (
    <div className="col" style={{ gap: 8 }}>
      <div className="row" style={{ gap: 10, flexWrap: 'wrap' }}>
        <Badge kind={kind} title="Bannissements enregistrés pour ce nœud / limite autorisée">{unlimited ? `${fmtNum(d.count, 0)} bannissements · illimité` : `${fmtNum(d.count, 0)} / ${fmtNum(d.limit, 0)} bannissements`}</Badge>
        {!unlimited && <span className="muted small">{d.count >= d.limit ? `Limite atteinte : ${rule}.` : pct >= 80 ? `Vous approchez de la limite (au-delà : ${rule}).` : `Au-delà : ${rule}.`}</span>}
        {d.canBan && !edit && <Btn small kind="ghost" title="Changer la limite de bannissements" onClick={open}><Pencil size={13} /> Limite</Btn>}
      </div>
      {edit && <div className="card col" style={{ gap: 10 }}>
        <div className="row" style={{ gap: 14, flexWrap: 'wrap' }}>
          <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={unl} onChange={(e) => setUnl(e.target.checked)} /> Illimité</label>
          {!unl && <label className="row" style={{ gap: 6 }}>Limite <input className="input" type="number" min={d.limitRange[0]} max={d.limitRange[1]} step="100" style={{ width: 120 }} value={v} onChange={(e) => setV(e.target.value)} autoFocus /></label>}
          {!unl && <span className="muted small">Entre {fmtNum(d.limitRange[0], 0)} et {fmtNum(d.limitRange[1], 0)}.</span>}
        </div>
        {unl ? <div className="muted small">Aucune limite : plus il y a d'adresses, plus la règle Traefik est longue à évaluer à chaque requête.</div> : <>
          <Field label="Quand la limite est atteinte">
            <select className="select" value={ov.mode} onChange={(e) => setOv({ ...ov, mode: e.target.value })}>
              <option value="refuse">Ne pas bannir les nouvelles adresses</option>
              <option value="evict">Bannir les nouvelles et lever d'anciens bannissements d'automod</option>
            </select></Field>
          {ov.mode === 'evict' && <>
            <Field label="Lever en premier">
              <select className="select" value={ov.by} onChange={(e) => setOv({ ...ov, by: e.target.value })}>
                <option value="remaining">Ceux qui ont le moins de temps de ban restant</option>
                <option value="oldest">Les plus anciens (date du ban)</option>
              </select></Field>
            <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={ov.permanent} onChange={(e) => setOv({ ...ov, permanent: e.target.checked })} /> Peut aussi lever les bannissements définitifs d'automod</label>
            <div className="muted small">Seuls les bannissements posés par l'automod peuvent être levés ; ceux posés à la main restent toujours. S'il n'y en a plus à lever, la nouvelle adresse est refusée.</div>
          </>}
        </>}
        <div className="row" style={{ gap: 8 }}>
          <Btn small kind="primary" busy={save.isPending} disabled={!unl && !v} onClick={() => save.mutate({ limit: unl ? 0 : Number(v), overflow: ov })}>Enregistrer</Btn>
          <Btn small kind="ghost" onClick={() => save.mutate({ limit: null, overflow: { mode: 'refuse', by: 'remaining', permanent: false } })} title={`Revenir à ${d.defaultLimit}, nouvelles adresses refusées`}>Par défaut ({fmtNum(d.defaultLimit, 0)})</Btn>
          <Btn small kind="ghost" onClick={() => setEdit(false)}>Annuler</Btn>
        </div>
      </div>}
    </div>
  )
}

function Bans({ f }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient(); const [add, setAdd] = useState(false); const [editBan, setEditBan] = useState(null)
  const q = useQuery({ queryKey: ['obs-bans'], queryFn: () => api.get('/obs/bans'), refetchInterval: 60000 })
  const [bs, setBs] = useState('')
  const needle = bs.trim().toLowerCase()
  const shown = (q.data?.bans || []).filter((b) => !needle || [b.ip, b.reason, b.created_by, b.geo?.city, b.geo?.countryName, fmtDate(b.created_at * 1000)].some((x) => x && String(x).toLowerCase().includes(needle)))
  const { rows: sortedBans, th } = useSort(shown, { key: 'created', dir: -1 }, { ip: (b) => b.ip, reason: (b) => b.reason || '', by: (b) => b.created_by, created: (b) => b.created_at, attempts: (b) => b.attempts, until: (b) => b.expires_at || Infinity })
  const del = useMutation({ mutationFn: (id) => api.del(`/obs/bans/${id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ['obs-bans'] }); qc.invalidateQueries({ queryKey: ['obs-ips'] }); toast('Bannissement levé') }, onError: (e) => toast(e.message, 'err') })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const unban = async (b) => { if (await confirm({ title: `Lever le bannissement de ${b.ip} ?`, message: 'L\'adresse pourra de nouveau accéder aux sites de ce nœud.', confirmLabel: 'Lever' })) del.mutate(b.id) }
  const st = q.data.stats
  const top = st.countries[0]
  const cards = st.total ? [
    ['Adresses bannies', st.total, `${st.permanent} définitive${st.permanent > 1 ? 's' : ''} · ${st.temporary} temporaire${st.temporary > 1 ? 's' : ''}${st.networks ? ` · ${st.networks} réseau${st.networks > 1 ? 'x' : ''}` : ''}`],
    ['Pays le plus banni', top ? top.name : '—', top ? `${top.n} adresse${top.n > 1 ? 's' : ''}${st.countryCount > 1 ? ` · ${st.countryCount} pays au total` : ''}` : 'pays inconnu (base GeoIP absente ?)'],
    ['Tentatives bloquées', fmtNum(st.attempts), st.busiest ? `la plus insistante : ${st.busiest.attempts} requêtes` : 'depuis les bannissements'],
    ['Ajoutées en 24 h', st.last24h, st.automod ? `dont ${st.automod} par l\'automod` : 'aucune par l\'automod'],
    ['Expirent sous 24 h', st.expiring24h, st.topAuthor ? `plus actif : ${st.topAuthor.name} (${st.topAuthor.n})` : ''],
  ] : []
  return (
    <div className="col">
      <BanLimit d={q.data} />
      {cards.length > 0 && <div className="grid stats">{cards.map(([l, v, s]) => <div key={l} className="card stat static"><div className="muted">{l}</div><b>{v}</b><div className="sub">{s}</div></div>)}</div>}
      {st.countries.length > 1 && <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}><span className="muted small">Par pays :</span>{st.countries.map((c) => <Badge key={c.code}>{c.name} · {c.n}</Badge>)}</div>}
      <div className="row"><SearchBox value={bs} onChange={setBs} placeholder="Adresse, raison, auteur, pays, date…" /><div className="muted grow">Une adresse bannie reçoit une erreur 403 sur tous les sites de ce nœud (règle Traefik, fichier <code>zz-webui-bans.yml</code>). Derrière un autre proxy (Cloudflare…), Traefik voit l'adresse du proxy.</div>
        {q.data.bans.length > 0 && <Btn small kind="ghost" title="Télécharger la liste (CSV)" onClick={() => downloadCsv('adresses-bannies.csv', ['Adresse', 'Raison', 'Par', 'Depuis', 'Jusqu\'au'], q.data.bans.map((b) => [b.ip, b.reason || '', b.created_by, new Date(b.created_at * 1000).toISOString(), b.expires_at ? new Date(b.expires_at * 1000).toISOString() : 'définitif']))}><FileDown size={14} /> CSV</Btn>}{f.bans.manage && f.ips.reveal && <Btn kind="primary" onClick={() => setAdd(true)}><Ban size={14} /> Bannir une adresse</Btn>}</div>
      {q.data.bans.length === 0 ? <div className="card"><Empty><Ban size={28} /><div>Aucune adresse bannie.</div>{f.bans.manage && f.ips.reveal && <Btn kind="primary" style={{ marginTop: 10 }} onClick={() => setAdd(true)}><Ban size={14} /> Bannir une adresse</Btn>}</Empty></div> : (
        <div className="card flush"><table className="t"><thead><tr><th>{th('ip', 'Adresse')}</th><th>{th('reason', 'Raison')}</th><th>{th('by', 'Par')}</th><th>{th('created', 'Banni le')}</th><th>{th('attempts', 'Tentatives')}</th><th>{th('until', "Jusqu'au")}</th><th /></tr></thead><tbody>
          {sortedBans.length === 0 && <tr><td colSpan={7} className="muted" style={{ textAlign: 'center' }}>Aucun bannissement ne correspond à « {bs} ».</td></tr>}
          {sortedBans.map((b) => <tr key={b.id}><td className="mono"><Copyable text={b.ip}><b>{b.ip}</b></Copyable>{b.geo && <div className="muted small">{[b.geo.city, b.geo.countryName].filter(Boolean).join(', ')}</div>}</td><td>{b.reason || <span className="muted">—</span>}</td><td className="muted">{b.created_by.startsWith('automod:') ? <Badge kind="info"><Bot size={11} /> automod</Badge> : b.created_by}</td><td className="small" title={ago(b.created_at)}>{fmtDate(b.created_at * 1000)}<div className="muted small">{ago(b.created_at)}</div></td><td className="small">{b.attempts == null ? <span className="muted" title="Réseau : les requêtes de ses adresses ne sont pas comptées une à une">—</span> : b.attempts ? <span title={`Dernière : ${ago(b.last_attempt)}`}>{fmtNum(b.attempts)}</span> : <span className="muted">0</span>}</td><td className="small">{b.expires_at ? <span title={fmtDate(b.expires_at * 1000)}>{inFuture(b.expires_at)}<div className="muted small">{fmtDate(b.expires_at * 1000)}</div></span> : <Badge kind="err">définitif</Badge>}</td>
            <td className="actions">{q.data.canBan && <><Btn small kind="ghost" title="Changer la durée" onClick={() => setEditBan(b)}><Pencil size={13} /> Durée</Btn><Btn small kind="ghost" onClick={() => unban(b)}><Trash2 size={13} /> Lever</Btn></>}</td></tr>)}</tbody></table></div>)}
      {add && <BanModal manual onClose={() => setAdd(false)} />}
      {editBan && <BanEditModal ban={editBan} onClose={() => setEditBan(null)} />}
    </div>
  )
}
