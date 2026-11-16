import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, ExternalLink, Pencil } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { api, qs } from '../api.js'
import { Badge, Info, Loading } from './ui.jsx'
import { usePersist } from '../lib/persist.js'
import Fresh from './Fresh.jsx'
import { LineChart, BarList, StackBar } from './Charts.jsx'
import RangePicker from './RangePicker.jsx'
import { fmtBytes, fmtMs, fmtNum, fmtRate } from '../lib/features.js'

const CLASS_COLOR = { '2xx': 'var(--ok)', '3xx': 'var(--info)', '4xx': 'var(--warn)', '5xx': 'var(--err)' }
const big = (v) => (v == null ? '—' : v >= 1e6 ? `${(v / 1e6).toFixed(2).replace('.', ',')} M` : v >= 1e4 ? `${(v / 1e3).toFixed(1).replace('.', ',')} k` : fmtNum(v, 0))
const bytes = (v) => (v == null ? '—' : v >= 1e9 ? `${(v / 1e9).toFixed(2).replace('.', ',')} Go` : v >= 1e6 ? `${(v / 1e6).toFixed(1).replace('.', ',')} Mo` : v >= 1e3 ? `${(v / 1e3).toFixed(1).replace('.', ',')} Ko` : `${Math.round(v)} o`)
const Kpi = ({ label, value, hint, kind }) => <div className={'card kpi ' + (kind || '')}><b>{value}</b><span>{label}{hint && <Info>{hint}</Info>}</span></div>

// Statistiques d'un site (un service Traefik) : trafic, erreurs, lenteur, volumes.
export default function SiteStats({ service, onBack }) {
  const [rng, setRng] = usePersist('range-site', '6h')
  const q = useQuery({ queryKey: ['obs-site', service, rng], queryFn: () => api.get(`/obs/site?${qs({ service, range: rng })}`), refetchInterval: 60000, retry: false, placeholderData: (p) => p })
  const d = q.data
  const first = (n) => d?.series?.[n]?.[0]?.points || []
  const share = (v) => (d?.total ? `${fmtNum((v / d.total) * 100, 1)} %` : '—')
  const classes = {}
  for (const c of d?.byCode || []) { const k = (c.name.slice(0, 1) || '?') + 'xx'; classes[k] = (classes[k] || 0) + c.value }
  return (
    <div className="col gap-lg">
      <div className="row"><button type="button" className="btn sm" onClick={onBack}><ArrowLeft size={14} /> Tous les sites</button><h2 className="grow mono" style={{ margin: 0 }}>{service}</h2>
        <NavLink className="btn sm ghost" to="/sites"><Pencil size={13} /> Voir les sites</NavLink></div>
      <div className="row wrap"><RangePicker value={rng} onChange={setRng} /><Fresh q={q} /></div>
      {q.isLoading ? <Loading /> : q.error ? <div className="banner err">{q.error.message}</div> : (
        <>
          {d.total === 0 && <div className="banner info">Aucune requête sur cette période pour ce service. Le nom est peut-être différent côté Traefik (les services Docker s'appellent « nom@docker »).</div>}
          <div className="grid stats">
            <Kpi label="Requêtes" value={big(d.total)} hint="Sur la période choisie." />
            <Kpi label="Erreurs serveur (5xx)" value={d.total ? `${big(d.err5 ?? 0)} · ${share(d.err5 || 0)}` : '—'} kind={d.err5 ? (d.err5 / d.total > 0.05 ? 'err' : 'warn') : 'ok'} />
            <Kpi label="Erreurs client (4xx)" value={d.total ? `${big(d.err4 ?? 0)} · ${share(d.err4 || 0)}` : '—'} />
            <Kpi label="Latence p95" value={fmtMs(d.p95)} kind={d.p95 > 2 ? 'err' : d.p95 > 0.8 ? 'warn' : ''} hint="95 % des requêtes sont plus rapides que cette valeur." />
            <Kpi label="Données reçues" value={bytes(d.bytesIn)} />
            <Kpi label="Données envoyées" value={bytes(d.bytesOut)} />
          </div>
          <div className="grid two">
            <div className="card"><h3>Requêtes par seconde <Info>Par famille de réponse : 2xx réussies, 3xx redirections, 4xx erreurs du visiteur, 5xx erreurs du serveur.</Info></h3>
              <LineChart area fmt={(v) => fmtRate(v)} series={(d.series.requests || []).map((s) => ({ name: s.labels.class, color: CLASS_COLOR[s.labels.class] || 'var(--muted)', points: s.points }))} /></div>
            <div className="card"><h3>Latence</h3>
              <LineChart fmt={fmtMs} series={[{ name: 'p50', color: 'var(--ok)', points: first('latency50') }, { name: 'p95', color: 'var(--warn)', points: first('latency95') }, { name: 'p99', color: 'var(--err)', points: first('latency99') }]} /></div>
            <div className="card"><h3>Débit</h3>
              <LineChart fmt={fmtBytes} series={[{ name: 'reçu', color: 'var(--info)', points: first('bytesIn') }, { name: 'envoyé', color: 'var(--accent)', points: first('bytesOut') }]} /></div>
            <div className="card"><h3>Codes de réponse</h3>
              <StackBar parts={Object.entries(classes).sort().map(([name, value]) => ({ name, value, color: CLASS_COLOR[name] || 'var(--muted)' }))} fmt={big} />
              <div className="muted small" style={{ margin: '10px 0 4px' }}>Les plus fréquents</div>
              <BarList items={(d.byCode || []).slice(0, 6).map((c) => ({ name: c.name, value: c.value }))} fmt={big} /></div>
            <div className="card"><h3>Méthodes HTTP</h3><BarList items={(d.byMethod || []).map((m) => ({ name: m.name, value: m.value }))} fmt={big} /></div>
            {d.backends?.length > 0 && <div className="card"><h3>Services Traefik correspondants</h3><div className="row wrap" style={{ gap: 6 }}>{d.backends.map((b) => <Badge key={b}>{b}</Badge>)}</div></div>}
          </div>
        </>)}
    </div>
  )
}
