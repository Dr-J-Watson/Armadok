import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Globe, MapPin } from 'lucide-react'
import { api, qs } from '../api.js'
import { Badge, Empty, Info, Loading, Seg } from './ui.jsx'
import { Bars, BarList, StackBar } from './Charts.jsx'
import { fmtMs, fmtNum } from '../lib/features.js'
import { countryColor } from './WorldMap.jsx'
import { useGo } from '../lib/nav.js'

const Kpi = ({ label, value, hint, kind, to }) => {
  const go = useGo()
  return <div className={'card kpi ' + (kind || '') + (to ? ' link' : '')} role={to ? 'link' : undefined} tabIndex={to ? 0 : undefined} onClick={to ? () => go(to) : undefined} onKeyDown={to ? (e) => e.key === 'Enter' && go(to) : undefined}><b>{value}</b><span>{label}{hint && <Info>{hint}</Info>}</span></div>
}
const CLASS_COLOR = { '2xx': 'var(--ok)', '3xx': 'var(--info)', '4xx': 'var(--warn)', '5xx': 'var(--err)' }
const bytes = (v) => (v == null ? '—' : v >= 1e12 ? `${(v / 1e12).toFixed(2).replace('.', ',')} To` : v >= 1e9 ? `${(v / 1e9).toFixed(2).replace('.', ',')} Go` : v >= 1e6 ? `${(v / 1e6).toFixed(1).replace('.', ',')} Mo` : v >= 1e3 ? `${(v / 1e3).toFixed(1).replace('.', ',')} Ko` : `${Math.round(v)} o`)
const big = (v) => (v == null ? '—' : v >= 1e6 ? `${(v / 1e6).toFixed(2).replace('.', ',')} M` : v >= 1e4 ? `${(v / 1e3).toFixed(1).replace('.', ',')} k` : fmtNum(v, 0))

// Totaux et répartitions sur la période choisie, d'après Prometheus.
export function PromBreakdown({ rng }) {
  const go = useGo(); const svc = (n) => () => go(`/monitoring?tab=services&service=${encodeURIComponent(n)}`)
  const q = useQuery({ queryKey: ['obs-breakdown', rng], queryFn: () => api.get(`/obs/breakdown?${qs({ range: rng })}`), refetchInterval: 60000, retry: false, placeholderData: (p) => p })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data
  const errs = Object.fromEntries(d.byClass.map((c) => [c.name, c.value]))
  const share = (v) => (d.total ? `${fmtNum((v / d.total) * 100, 1)} %` : '—')
  const errBySvc = Object.fromEntries(d.errorsByService.map((e) => [e.name, e.value]))
  return (
    <div className="col gap-lg">
      <div className="grid stats">
        <Kpi label="Requêtes sur la période" value={big(d.total)} hint="Total des requêtes reçues par les points d'entrée de Traefik pendant la durée choisie." />
        <Kpi label="Réussies (2xx / 3xx)" value={share((errs['2xx'] || 0) + (errs['3xx'] || 0))} kind="ok" />
        <Kpi label="Erreurs client (4xx)" value={`${big(errs['4xx'] || 0)} · ${share(errs['4xx'] || 0)}`} />
        <Kpi to="/monitoring?tab=services" label="Erreurs serveur (5xx)" value={`${big(errs['5xx'] || 0)} · ${share(errs['5xx'] || 0)}`} kind={errs['5xx'] ? 'warn' : 'ok'} />
        <Kpi label="Données reçues" value={bytes(d.bytesIn)} hint="Poids des requêtes envoyées par les visiteurs." />
        <Kpi label="Données envoyées" value={bytes(d.bytesOut)} hint="Poids des réponses renvoyées aux visiteurs." />
        <Kpi label="En HTTPS" value={d.total && d.tls != null ? share(d.tls) : '—'} kind={d.total && d.tls != null && d.tls / d.total < 0.9 ? 'warn' : ''} hint="Part des requêtes chiffrées (TLS). Le reste passe en clair, souvent avant la redirection vers HTTPS." />
        <Kpi label="Rechargements de config" value={d.reloads == null ? '—' : fmtNum(d.reloads, 0)} hint="Nombre de fois où Traefik a relu sa configuration pendant la période." />
      </div>
      <div className="grid two">
        <div className="card"><h3>Sites les plus sollicités <Info>Part des requêtes de chaque service Traefik sur la période. En rouge : au moins 5 % d'erreurs serveur.</Info></h3>
          <BarList items={d.byService.map((s) => { const e = errBySvc[s.name] || 0; const p = s.value ? (e / s.value) * 100 : 0; return { name: s.name, onClick: svc(s.name), value: s.value, sub: p >= 0.1 ? `${fmtNum(p, 1)} % d'erreurs 5xx` : share(s.value), color: p >= 5 ? 'var(--err)' : p >= 0.5 ? 'var(--warn)' : undefined } })} fmt={big} /></div>
        <div className="card"><h3>Sites les plus lents <Info>Latence p95 par service sur la période : 95 % des requêtes sont plus rapides que cette valeur.</Info></h3>
          <BarList items={d.slowest.map((s) => ({ name: s.name, onClick: svc(s.name), value: s.value, color: s.value > 2 ? 'var(--err)' : s.value > 0.8 ? 'var(--warn)' : 'var(--ok)' }))} fmt={fmtMs} empty="Pas de mesure de latence." /></div>
        <div className="card"><h3>Codes de réponse</h3>
          <StackBar parts={d.byClass.map((c) => ({ name: c.name, value: c.value, color: CLASS_COLOR[c.name] || 'var(--muted)' }))} fmt={big} />
          <div className="muted small" style={{ marginTop: 10 }}>Les plus fréquents : {d.byCode.slice(0, 6).map((c) => `${c.name} (${big(c.value)})`).join(' · ') || '—'}</div></div>
        <div className="card"><h3>Méthodes, protocoles et entrées</h3>
          <div className="col" style={{ gap: 14 }}>
            <div><div className="muted small">Méthodes HTTP</div><BarList items={d.byMethod.map((m) => ({ name: m.name, value: m.value }))} fmt={big} /></div>
            <div className="grid two"><div><div className="muted small">Protocole</div><BarList items={d.byProtocol.map((m) => ({ name: m.name, value: m.value }))} fmt={big} /></div>
              <div><div className="muted small">Point d'entrée</div><BarList items={d.byEntrypoint.map((m) => ({ name: m.name, value: m.value }))} fmt={big} /></div></div>
          </div></div>
      </div>
    </div>
  )
}

// Visiteurs : d'après le journal d'accès (adresses, pays, pages), conservé 7 jours.
export function Visitors({ f, onTab }) {
  const go = useGo()
  const [days, setDays] = useState(1)
  const q = useQuery({ queryKey: ['obs-stats', days], queryFn: () => api.get(`/obs/stats?days=${days}`), refetchInterval: 60000, retry: false, placeholderData: (p) => p })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data; const t = d.total
  const cls = Object.fromEntries(d.classes.map((c) => [c.name, c.value]))
  const sh = d.hosts.reduce((a, h) => a + h.hits, 0)
  const errPct = d.scoped ? (sh ? (d.hosts.reduce((a, h) => a + h.errors, 0) / sh) * 100 : 0) : d.hits ? ((cls['401'] + cls['403'] + cls['404'] + cls['autres 4xx'] + cls['5xx']) / d.hits) * 100 : 0
  const peak = d.timeline.reduce((a, b) => (b.hits > a.hits ? b : a), { hits: 0, t: 0 })
  return (
    <div className="col gap-lg">
      <div className="row"><h3>Visiteurs</h3><Seg options={[{ id: 1, label: '24 h' }, { id: 3, label: '3 jours' }, { id: 7, label: '7 jours' }]} value={days} onChange={setDays} /><span className="muted small right">d'après le journal d'accès de Traefik</span></div>
      <div className="grid stats">
        <Kpi label="Adresses différentes" value={fmtNum(t.ips, 0)} hint="Adresses IP distinctes vues sur la période (réseau local compris)." />
        <Kpi label="Nouvelles (24 h)" value={fmtNum(d.newest, 0)} hint="Adresses vues pour la première fois dans les dernières 24 heures." />
        <Kpi label="Requêtes" value={big(d.hits)} />
        <Kpi label="Part d'erreurs" value={`${fmtNum(errPct, 1)} %`} kind={errPct > 40 ? 'warn' : ''} hint="401, 403, 404 et autres erreurs, 5xx compris, sur l'ensemble des requêtes." />
        <Kpi to="/monitoring?tab=ips&only=suspicious" label="Adresses suspectes" value={fmtNum(t.suspicious, 0)} kind={t.suspicious ? 'warn' : 'ok'} />
        {!d.scoped && <Kpi label="Heure la plus chargée" value={peak.hits ? new Date(peak.t * 1000).toLocaleString('fr-FR', { weekday: days > 1 ? 'short' : undefined, hour: '2-digit', minute: '2-digit' }).replace(/:00$/, ' h') : '—'} hint={peak.hits ? `${fmtNum(peak.hits, 0)} requêtes pendant cette heure` : undefined} />}
        <Kpi to="/monitoring?tab=bans" label="Bannies" value={fmtNum(t.banned, 0)} />
        <Kpi to="/monitoring?tab=trusted" label="Validées" value={fmtNum(t.trusted, 0)} />
      </div>
      {d.scoped && <div className="muted small">Vue limitée aux sites de ce nœud. Le journal d'accès ne garde la répartition par heure et par code de réponse que pour l'ensemble du Traefik : elle n'est pas montrée ici.</div>}
      {!d.scoped && <div className="card"><h3>Requêtes par heure <Info>Chaque bâton est une heure : en bleu les requêtes, en rouge les erreurs.</Info></h3><Bars data={d.timeline} height={90} /></div>}
      <div className="grid two">
        {!d.scoped && <div className="card"><h3>Réponses <Info>Ce que les visiteurs ont reçu.</Info></h3>
          <StackBar parts={d.classes.map((c) => ({ name: c.name, value: c.value, color: { '2xx': 'var(--ok)', '3xx': 'var(--info)', '401': 'var(--warn)', '403': 'var(--warn)', '404': '#d9a441', 'autres 4xx': '#c27a00', '5xx': 'var(--err)' }[c.name] }))} fmt={big} /></div>}
        <div className="card"><h3><Globe size={14} style={{ verticalAlign: -2 }} /> Pays <Info>D'après la base de localisation locale (le pays est fiable). Réseau local exclu.</Info></h3>
          {d.geo.available ? <BarList items={d.countries.map((c) => ({ name: c.name, value: c.hits, sub: `${c.ips} adresse${c.ips > 1 ? 's' : ''}${c.attacks ? ` · ${c.attacks} attaque${c.attacks > 1 ? 's' : ''}` : ''}`, color: countryColor(c.code), title: c.name }))} fmt={big} empty="Aucune adresse localisée." />
            : <Empty><MapPin size={26} /><div>Pas de localisation. <button type="button" className="linklike" onClick={() => onTab?.('ips')}>Télécharger la base</button> (onglet « Adresses IP »).</div></Empty>}</div>
        <div className="card"><h3>Sites les plus visités</h3><BarList items={d.hosts.map((h) => ({ name: h.name, onClick: () => go(`/monitoring?tab=ips&s=${encodeURIComponent(h.name)}`), value: h.hits, sub: `${h.ips} adresse${h.ips > 1 ? 's' : ''}${h.errors ? ` · ${big(h.errors)} erreur${h.errors > 1 ? 's' : ''}` : ''}` }))} fmt={big} /></div>
        <div className="card"><h3>Pages les plus demandées</h3><BarList items={d.paths.map((p) => ({ name: `${p.host || ''}${p.path}`, onClick: () => go(`/monitoring?tab=ips&s=${encodeURIComponent(p.path)}`), value: p.hits, sub: p.errors ? `${big(p.errors)} erreur${p.errors > 1 ? 's' : ''}` : undefined, color: p.errors > p.hits / 2 ? 'var(--warn)' : undefined }))} fmt={big} /></div>
      </div>
      <div className="grid two">
        <div className="card flush"><div style={{ padding: '12px 16px' }}><h3>Adresses les plus actives</h3></div>
          {d.topIps.length === 0 ? <Empty>Rien pour l'instant.</Empty> : <table className="t"><tbody>{d.topIps.map((r) => (
            <tr key={r.ref} style={{ cursor: 'pointer' }} title="Voir cette adresse" onClick={() => go(`/monitoring?tab=ips&ip=${r.ref}`)}><td><b className="mono">{r.ip}</b>{r.geo && <div className="muted small">{[r.geo.city, r.geo.countryName].filter(Boolean).join(', ')}</div>}</td>
              <td>{big(r.hits)}<div className="muted small">{r.errors} err.</div></td>
              <td>{r.attacks.slice(0, 2).map((a) => <Badge key={a} kind="err">{a}</Badge>)}{r.trusted && <Badge kind="ok">validée</Badge>}{r.banned && <Badge kind="err">bannie</Badge>}</td></tr>))}</tbody></table>}</div>
        <div className="card"><h3>Types d'attaque détectés <Info>Reconnus par signatures dans les requêtes : des indices, pas des preuves.</Info></h3>
          {d.kinds.length === 0 ? <div className="muted small" style={{ padding: '14px 0', textAlign: 'center' }}>Aucune attaque reconnue sur la période.</div> : <BarList items={d.kinds.map((k) => ({ name: k.label, onClick: () => go(`/monitoring?tab=ips&kind=${k.kind}`), value: k.ips, sub: `${big(k.hits)} requêtes`, color: k.severity === 'critical' || k.severity === 'high' ? 'var(--err)' : 'var(--warn)', title: k.why }))} fmt={(v) => `${v} adresse${v > 1 ? 's' : ''}`} />}
          <div className="muted small" style={{ marginTop: 10 }}>Le détail et le blocage : onglets « Adresses IP », « Bannies » et « Automod ».</div></div>
      </div>
    </div>
  )
}
