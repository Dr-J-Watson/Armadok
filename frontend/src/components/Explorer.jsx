import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Code2, Download, Eye, EyeOff, Play, Plus, Search, Wand2, X } from 'lucide-react'
import { api, qs } from '../api.js'
import { Badge, Btn, Empty, Field, Info, Loading, Seg } from './ui.jsx'
import { LineChart } from './Charts.jsx'
import RangePicker from './RangePicker.jsx'
import { fmtBytes, fmtMs, fmtNum } from '../lib/features.js'
import { usePersist } from '../lib/persist.js'
import { downloadBlob } from '../lib/csv.js'
import { useGo } from '../lib/nav.js'
import Fresh from './Fresh.jsx'

// Explorateur : « que voulez-vous voir ? » (questions toutes prêtes), un constructeur de requête pas à pas, ou du PromQL libre. Lecture seule.
const COLORS = ['#5b5bf0', '#12a05c', '#c27a00', '#d93a4a', '#0c8599', '#9b51e0', '#e8590c', '#5c7cfa', '#2b8a3e', '#c2255c', '#1098ad', '#862e9c']

const PRESETS = [
  ['Trafic', [
    ['Requêtes par seconde, par site', 'sum by (service) (rate(traefik_service_requests_total[5m]))', 'num', 'Le trafic de chaque site (service Traefik).'],
    ['Requêtes par seconde, par code HTTP', 'sum by (code) (rate(traefik_entrypoint_requests_total[5m]))', 'num', '200, 404, 500… : à quoi ressemble le trafic.'],
    ['Octets reçus et envoyés', 'sum(rate(traefik_entrypoint_requests_bytes_total[5m]))', 'bytes', 'Débit entrant (requêtes) ; remplacez requests par responses pour le débit sortant.'],
    ['Connexions ouvertes, par entrée', "sum by (entrypoint) (traefik_open_connections{entrypoint!='traefik'})", 'num', 'Connexions en cours sur chaque entrypoint.'],
  ]],
  ['Erreurs', [
    ['Erreurs serveur 5xx, par site', 'sum by (service) (rate(traefik_service_requests_total{code=~"5.."}[5m]))', 'num', 'Un site qui plante ou ne répond plus.'],
    ['Erreurs client 4xx, par site', 'sum by (service) (rate(traefik_service_requests_total{code=~"4.."}[5m]))', 'num', 'Liens cassés, accès refusés, balayages.'],
    ['Part des erreurs 5xx (%)', '100 * sum(rate(traefik_service_requests_total{code=~"5.."}[5m])) / sum(rate(traefik_service_requests_total[5m]))', 'percent', 'Sur l\'ensemble du trafic.'],
    ['Requêtes refusées 401 / 403', 'sum by (code) (rate(traefik_entrypoint_requests_total{code=~"401|403"}[5m]))', 'num', 'Identifications refusées et accès interdits.'],
  ]],
  ['Lenteur', [
    ['Latence médiane (p50), par site', 'histogram_quantile(0.5, sum by (le, service) (rate(traefik_service_request_duration_seconds_bucket[5m])))', 'seconds', 'La moitié des requêtes est plus rapide que ça.'],
    ['Latence p95, par site', 'histogram_quantile(0.95, sum by (le, service) (rate(traefik_service_request_duration_seconds_bucket[5m])))', 'seconds', '95 % des requêtes sont plus rapides que ça : c\'est la lenteur « ressentie ».'],
    ['Latence p99, par site', 'histogram_quantile(0.99, sum by (le, service) (rate(traefik_service_request_duration_seconds_bucket[5m])))', 'seconds', 'Les pires cas.'],
  ]],
  ['Certificats et santé', [
    ['Jours avant expiration des certificats', '(traefik_tls_certs_not_after - time()) / 86400', 'days', 'Négatif = déjà expiré.'],
    ['Cibles surveillées en ligne (1) ou non (0)', 'up', 'num', 'Ce que Prometheus arrive à lire.'],
    ['Rechargements de la configuration de Traefik', 'sum(rate(traefik_config_reloads_total[5m]))', 'num', 'Monte quand vous modifiez beaucoup de fichiers.'],
    ['Mémoire de Prometheus', 'process_resident_memory_bytes{job="prometheus"}', 'bytes_plain', 'Octets utilisés.'],
  ]],
]

const UNITS = { auto: 'Automatique', num: 'Nombre', seconds: 'Secondes / ms', bytes: 'Octets par seconde', bytes_plain: 'Octets', percent: 'Pourcentage', days: 'Jours' }
const fmtBytesPlain = (v) => (v == null ? '—' : v >= 1e9 ? `${(v / 1e9).toFixed(2).replace('.', ',')} Go` : v >= 1e6 ? `${(v / 1e6).toFixed(1).replace('.', ',')} Mo` : v >= 1e3 ? `${(v / 1e3).toFixed(1).replace('.', ',')} Ko` : `${Math.round(v)} o`)
const formatter = (u) => ({ seconds: fmtMs, bytes: fmtBytes, bytes_plain: fmtBytesPlain, percent: (v) => `${fmtNum(v, 1)} %`, days: (v) => `${fmtNum(v, 1)} j` }[u] || ((v) => fmtNum(v, 3)))
const guessUnit = (expr) => (/_seconds/.test(expr) ? 'seconds' : /_bytes_total/.test(expr) ? 'bytes' : /_bytes\b|memory/.test(expr) ? 'bytes_plain' : 'num')

// Ajoute un filtre étiquette="valeur" à chaque mesure de la requête (pour « ne garder que cette courbe »)
const FUNCS = new Set(['histogram_quantile', 'label_replace', 'label_join', 'count_values', 'quantile_over_time', 'predict_linear', 'holt_winters', 'absent_over_time', 'sort_desc', 'clamp_min', 'clamp_max'])
export function isolate(expr, k, v) {
  const m = `${k}="${String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`
  return expr.replace(/\b([a-zA-Z_:][a-zA-Z0-9_:]*_[a-zA-Z0-9_:]*|up)\b(\{([^}]*)\})?(?!\s*\()/g, (all, name, _b, inner) => {
    if (FUNCS.has(name) || /^(by|without|on|ignoring|group_left|group_right)$/.test(name)) return all
    if (inner && new RegExp(`(^|,)\\s*${k}\\s*(=|!=|=~|!~)`).test(inner)) return all
    return `${name}{${inner ? inner + ', ' : ''}${m}}`
  })
}

const esc = (v) => String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"')

// Construit la requête PromQL d'après les choix du constructeur.
function buildExpr(b) {
  if (!b.metric) return ''
  const hist = b.type === 'histogram'
  const name = hist ? `${b.metric}_bucket` : b.metric
  const f = b.filters.filter((x) => x.k && x.v !== '').map((x) => `${x.k}${x.op}"${esc(x.v)}"`)
  const sel = (n = name) => `${n}${f.length ? `{${f.join(', ')}}` : ''}`
  const by = b.groupBy.length ? ` by (${b.groupBy.join(', ')})` : ''
  const agg = (inner) => (b.agg === 'none' ? inner : `${b.agg}${by} (${inner})`)
  if (hist) {
    if (b.calc === 'avg') return `sum${by} (rate(${sel(`${b.metric}_sum`)}[5m])) / sum${by} (rate(${sel(`${b.metric}_count`)}[5m]))`
    const q = { p50: 0.5, p90: 0.9, p95: 0.95, p99: 0.99 }[b.calc] || 0.95
    return `histogram_quantile(${q}, sum by (le${b.groupBy.length ? ', ' + b.groupBy.join(', ') : ''}) (rate(${sel()}[5m])))`
  }
  if (b.type === 'counter') return agg(b.calc === 'increase' ? `increase(${sel()}[1h])` : `rate(${sel()}[5m])`)
  return agg(sel())
}

const CALCS = {
  counter: [['rate', 'Débit par seconde (moyenne sur 5 min)'], ['increase', 'Augmentation sur 1 heure']],
  gauge: [['value', 'Valeur']],
  histogram: [['p50', 'Médiane (p50)'], ['p90', 'p90'], ['p95', 'p95'], ['p99', 'p99'], ['avg', 'Moyenne']],
}

const stats = (pts) => {
  if (!pts.length) return {}
  let min = Infinity, max = -Infinity, sum = 0
  for (const [, v] of pts) { if (v < min) min = v; if (v > max) max = v; sum += v }
  return { last: pts[pts.length - 1][1], min, max, avg: sum / pts.length }
}

function csv(series, names) {
  const rows = ['temps,série,valeur']
  series.forEach((s, i) => s.points.forEach(([t, v]) => rows.push(`${new Date(t * 1000).toISOString()},"${names[i].replace(/"/g, '""')}",${v}`)))
  return rows.join('\n')
}

function Builder({ b, setB }) {
  const [search, setSearch] = useState('')
  const metrics = useQuery({ queryKey: ['obs-metrics'], queryFn: () => api.get('/obs/metrics'), staleTime: 60000, retry: false })
  const labels = useQuery({ queryKey: ['obs-labels', b.metric], queryFn: () => api.get(`/obs/labels?${qs({ metric: b.metric })}`), enabled: !!b.metric, staleTime: 60000, retry: false })
  const list = (metrics.data?.metrics || []).filter((m) => !search || m.name.toLowerCase().includes(search.toLowerCase()) || m.help.toLowerCase().includes(search.toLowerCase()))
  const labs = labels.data?.labels || {}
  const pick = (m) => setB({ ...b, metric: m.name, type: m.type === 'histogram' || m.type === 'counter' ? m.type : 'gauge', calc: m.type === 'histogram' ? 'p95' : m.type === 'counter' ? 'rate' : 'value', agg: m.type === 'counter' ? 'sum' : 'none', groupBy: [], filters: [] })
  const toggleBy = (k) => setB({ ...b, groupBy: b.groupBy.includes(k) ? b.groupBy.filter((x) => x !== k) : [...b.groupBy, k] })
  const setF = (i, patch) => setB({ ...b, filters: b.filters.map((x, j) => (j === i ? { ...x, ...patch } : x)) })
  return (
    <div className="col gap-lg">
      <div className="col" style={{ gap: 6 }}>
        <div className="row"><b>1. Quoi mesurer ?</b>{b.metric && <Badge kind="accent">{b.metric}</Badge>}{b.metric && <Badge>{b.type === 'counter' ? 'compteur' : b.type === 'histogram' ? 'durées' : 'valeur instantanée'}</Badge>}</div>
        <div className="row"><div className="searchbox" style={{ flex: 1, position: 'relative' }}><Search size={14} style={{ position: 'absolute', left: 10, top: 10, opacity: .5 }} /><input className="input" style={{ paddingLeft: 30 }} placeholder="Chercher une mesure (requests, duration, certs…)" value={search} onChange={(e) => setSearch(e.target.value)} /></div></div>
        {metrics.isLoading ? <Loading /> : metrics.error ? <div className="banner err">{metrics.error.message}</div> : (
          <div className="metric-list">{list.length === 0 ? <div className="muted small" style={{ padding: 10 }}>Aucune mesure ne correspond.</div> : list.map((m) => (
            <button type="button" key={m.name} className={'metric-item' + (m.name === b.metric ? ' on' : '')} onClick={() => pick(m)} title={m.help}>
              <span className="mono">{m.name}</span><span className="muted small">{m.type === 'counter' ? 'compteur' : m.type === 'histogram' ? 'durées' : m.type === 'gauge' ? 'valeur' : m.type}</span></button>))}</div>)}
      </div>
      {b.metric && <>
        <div className="grid two" style={{ alignItems: 'start' }}>
          <Field label="2. Calcul"><select className="select" value={b.calc} onChange={(e) => setB({ ...b, calc: e.target.value })}>{CALCS[b.type].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
          {b.type !== 'histogram' && <Field label="3. Regrouper les valeurs" help="Additionner, faire la moyenne… des séries qui partagent les étiquettes choisies ci-dessous."><select className="select" value={b.agg} onChange={(e) => setB({ ...b, agg: e.target.value })}><option value="none">Ne pas regrouper (une courbe par série)</option><option value="sum">Somme</option><option value="avg">Moyenne</option><option value="max">Maximum</option><option value="min">Minimum</option></select></Field>}
        </div>
        <div className="col" style={{ gap: 6 }}>
          <div className="row"><b>{b.type === 'histogram' ? '3' : '4'}. Une courbe par…</b><Info>Séparer les courbes selon une étiquette (le site, le code HTTP…). Sans choix, tout est regroupé en une seule courbe.</Info></div>
          {labels.isLoading ? <Loading /> : Object.keys(labs).length === 0 ? <div className="muted small">Cette mesure n'a pas d'étiquette.</div> : (
            <div className="row wrap" style={{ gap: 6 }}>{Object.keys(labs).map((k) => <button type="button" key={k} className={'xchip' + (b.groupBy.includes(k) ? ' on' : '')} onClick={() => toggleBy(k)} title={`${labs[k].length} valeur(s) : ${labs[k].slice(0, 5).join(', ')}${labs[k].length > 5 ? '…' : ''}`}>{k}</button>)}</div>)}
        </div>
        <div className="col" style={{ gap: 6 }}>
          <div className="row"><b>{b.type === 'histogram' ? '4' : '5'}. Filtres</b><Btn small kind="ghost" onClick={() => setB({ ...b, filters: [...b.filters, { k: Object.keys(labs)[0] || '', op: '=', v: '' }] })} disabled={!Object.keys(labs).length}><Plus size={13} /> Ajouter un filtre</Btn></div>
          {b.filters.map((x, i) => (
            <div className="row" key={i} style={{ gap: 6 }}>
              <select className="select" style={{ width: 'auto' }} value={x.k} onChange={(e) => setF(i, { k: e.target.value, v: '' })}>{Object.keys(labs).map((k) => <option key={k}>{k}</option>)}</select>
              <select className="select" style={{ width: 'auto' }} value={x.op} onChange={(e) => setF(i, { op: e.target.value })}><option value="=">est</option><option value="!=">n'est pas</option><option value="=~">correspond à (regex)</option></select>
              <input className="input" style={{ width: 240 }} list={`vals-${i}`} value={x.v} onChange={(e) => setF(i, { v: e.target.value })} placeholder="valeur" /><datalist id={`vals-${i}`}>{(labs[x.k] || []).map((v) => <option key={v} value={v} />)}</datalist>
              <Btn small kind="ghost" icon onClick={() => setB({ ...b, filters: b.filters.filter((_, j) => j !== i) })} title="Retirer"><X size={14} /></Btn>
            </div>))}
        </div>
      </>}
    </div>
  )
}

export default function Explorer() {
  const [mode, setMode] = useState('presets')
  const [b, setB] = useState({ metric: '', type: 'counter', calc: 'rate', agg: 'sum', groupBy: [], filters: [] })
  const [expr, setExpr] = useState(PRESETS[0][1][0][1]); const [run, setRun] = useState(PRESETS[0][1][0][1])
  const [unit, setUnit] = useState('auto'); const [presetUnit, setPresetUnit] = useState('num'); const [title, setTitle] = useState(PRESETS[0][1][0][0])
  const [rng, setRng] = usePersist('range-explorer', '1h'); const [refresh, setRefresh] = useState(0); const goto = useGo()
  const [hidden, setHidden] = useState(() => new Set())
  const built = useMemo(() => buildExpr(b), [b])
  const q = useQuery({ queryKey: ['obs-explore', run, rng], queryFn: () => api.get(`/obs/query?${qs({ expr: run, range: rng })}`), enabled: !!run, retry: false, refetchInterval: refresh || false, placeholderData: (p) => p })
  const go = (e, t, u) => { setExpr(e); setRun(e); setHidden(new Set()); if (t) setTitle(t); setPresetUnit(u || guessUnit(e)) }
  const eff = unit === 'auto' ? presetUnit : unit
  const fmt = formatter(eff)
  const data = q.data
  const names = useMemo(() => {
    const ss = data?.series || []
    const keys = [...new Set(ss.flatMap((s) => Object.keys(s.labels)))].filter((k) => new Set(ss.map((s) => s.labels[k])).size > 1 || (ss.length === 1 && !['job', 'instance'].includes(k)))
    return ss.map((s) => keys.map((k) => `${k}=${s.labels[k] ?? '—'}`).join(' · ') || (Object.values(s.labels).join(' · ') || 'valeur'))
  }, [data])
  const series = (data?.series || []).map((s, i) => ({ name: names[i], labels: s.labels, color: COLORS[i % COLORS.length], points: s.points, idx: i }))
  const shown = series.filter((s) => !hidden.has(s.idx))
  const download = () => { downloadBlob('metriques.csv', csv(series, names), 'text/csv') }
  return (
    <div className="col gap-lg">
      <div className="muted">Regardez ce que Prometheus a mesuré : une question toute prête, un constructeur pas à pas, ou une requête PromQL libre. Tout est en lecture seule.</div>
      <Seg options={[{ id: 'presets', label: 'Questions courantes' }, { id: 'builder', label: 'Constructeur' }, { id: 'promql', label: 'PromQL' }]} value={mode} onChange={(m) => { setMode(m); if (m === 'builder' && built) setExpr(built) }} />

      {mode === 'presets' && (
        <div className="card col gap-lg">{PRESETS.map(([cat, items]) => (
          <div key={cat} className="col" style={{ gap: 8 }}><b>{cat}</b>
            <div className="row wrap" style={{ gap: 8 }}>{items.map(([label, e, u, hint]) => <button type="button" key={label} className={'xchip big' + (run === e ? ' on' : '')} title={hint} onClick={() => go(e, label, u)}>{label}</button>)}</div></div>))}
        </div>)}

      {mode === 'builder' && (
        <div className="card col gap-lg">
          <Builder b={b} setB={setB} />
          <div className="row wrap" style={{ gap: 10 }}>
            <Btn kind="primary" disabled={!built} onClick={() => go(built, `${b.metric}`, guessUnit(built))}><Play size={14} /> Afficher</Btn>
            {built && <details className="grow"><summary className="muted small" style={{ cursor: 'pointer' }}><Code2 size={13} style={{ verticalAlign: -2 }} /> Requête générée</summary><pre className="codebox" style={{ margin: '6px 0 0', whiteSpace: 'pre-wrap' }}>{built}</pre><Btn small kind="ghost" onClick={() => { setExpr(built); setMode('promql') }}><Wand2 size={13} /> La modifier à la main</Btn></details>}
          </div>
        </div>)}

      {mode === 'promql' && (
        <div className="card col">
          <textarea className="textarea mono" rows={4} value={expr} onChange={(e) => setExpr(e.target.value)} onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') go(expr) }} spellCheck={false} placeholder="sum by (service) (rate(traefik_service_requests_total[5m]))" />
          <div className="row"><Btn kind="primary" onClick={() => go(expr)}><Play size={14} /> Exécuter</Btn><span className="muted small">Ctrl + Entrée · PromQL, sur tout ce que Prometheus connaît (pas seulement Traefik)</span></div>
        </div>)}

      <div className="row wrap" style={{ gap: 10 }}>
        <RangePicker value={rng} onChange={setRng} />
        <select className="select" style={{ width: 'auto' }} value={refresh} onChange={(e) => setRefresh(Number(e.target.value))} title="Rafraîchissement automatique"><option value={0}>Pas d'actualisation</option><option value={15000}>Actualiser : 15 s</option><option value={60000}>Actualiser : 1 min</option></select>
        <select className="select" style={{ width: 'auto' }} value={unit} onChange={(e) => setUnit(e.target.value)} title="Unité des valeurs">{Object.entries(UNITS).map(([k, v]) => <option key={k} value={k}>{k === 'auto' ? 'Unité : automatique' : v}</option>)}</select>
      </div>

      {q.isLoading ? <Loading /> : q.error ? <div className="banner err">{q.error.message}</div> : data && (
        <div className="card col">
          <div className="row"><h3 className="grow">{title}</h3><Fresh q={q} className="" />{series.length > 0 && <Btn small kind="ghost" onClick={download}><Download size={13} /> CSV</Btn>}</div>
          {data.truncated && <div className="banner warn" style={{ margin: 0 }}>Seules les 40 premières courbes sont affichées : filtrez ou regroupez (« Une courbe par… »).</div>}
          <LineChart fmt={fmt} series={shown} height={230} empty={series.length ? 'Toutes les courbes sont masquées.' : 'La requête ne renvoie rien sur cette période.'} />
          {series.length > 0 && (
            <div className="card flush" style={{ marginTop: 6 }}><table className="t"><thead><tr><th style={{ width: 30 }}>
              <button type="button" className="btn sm ghost icon" title={hidden.size ? 'Tout afficher' : 'Tout masquer'} onClick={() => setHidden(hidden.size ? new Set() : new Set(series.map((s) => s.idx)))}>{hidden.size ? <EyeOff size={14} /> : <Eye size={14} />}</button></th><th>Courbe</th><th>Dernière</th><th>Min</th><th>Moyenne</th><th>Max</th></tr></thead>
              <tbody>{series.map((s) => { const st = stats(s.points); const off = hidden.has(s.idx); return (
                <tr key={s.idx} style={{ cursor: 'pointer', opacity: off ? .45 : 1 }} onClick={() => setHidden((o) => { const n = new Set(o); n.has(s.idx) ? n.delete(s.idx) : n.add(s.idx); return n })}>
                  <td><span className="dot" style={{ background: s.color }} /></td><td className="mono small">{Object.keys(s.labels || {}).length ? Object.entries(s.labels).filter(([k]) => !['job', 'instance'].includes(k) || Object.keys(s.labels).length === 1).map(([k, v]) => <span key={k} className="xchip" style={{ marginRight: 4, cursor: 'pointer' }} title={`Ne garder que ${k}=${v}`} onClick={(e) => { e.stopPropagation(); go(isolate(run, k, v), `${title} · ${v}`, presetUnit) }}>{k}={v}</span>) : s.name}{s.labels?.service && <a href={`/monitoring?tab=services&service=${encodeURIComponent(s.labels.service)}`} className="linkcell" style={{ marginLeft: 6 }} title="Statistiques de ce site" onClick={(e) => { e.preventDefault(); e.stopPropagation(); goto(`/monitoring?tab=services&service=${encodeURIComponent(s.labels.service)}`) }}>statistiques</a>}</td><td><b>{fmt(st.last)}</b></td><td>{fmt(st.min)}</td><td>{fmt(st.avg)}</td><td>{fmt(st.max)}</td></tr>) })}</tbody></table></div>)}
        </div>)}
      {!q.isLoading && !q.error && !data && <Empty><Search size={28} /><div>Choisissez une question ci-dessus.</div></Empty>}
    </div>
  )
}
