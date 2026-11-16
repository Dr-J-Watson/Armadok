import { useMemo, useState } from 'react'

// Courbes SVG légères (pas de bibliothèque) : plusieurs séries, repères, info-bulle au survol.
// series : [{ name, color, points: [[t, v], …] }]  ·  fmt : formate une valeur de l'axe vertical
export function LineChart({ series, height = 170, fmt = (v) => String(Math.round(v * 100) / 100), area = false, empty = 'Pas encore de données' }) {
  const [hover, setHover] = useState(null)
  const W = 640, PL = 46, PR = 8, PT = 8, PB = 20
  const info = useMemo(() => {
    const pts = series.flatMap((s) => s.points)
    if (!pts.length) return null
    const t0 = Math.min(...pts.map((p) => p[0])), t1 = Math.max(...pts.map((p) => p[0]))
    const vmax = Math.max(...pts.map((p) => p[1]), 1e-9)
    return { t0, t1: t1 === t0 ? t0 + 1 : t1, vmax: vmax * 1.08 }
  }, [series])
  if (!info) return <div className="muted small" style={{ padding: '30px 0', textAlign: 'center' }}>{empty}</div>
  const x = (t) => PL + ((t - info.t0) / (info.t1 - info.t0)) * (W - PL - PR)
  const y = (v) => PT + (1 - v / info.vmax) * (height - PT - PB)
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * info.vmax)
  const timeLbl = (t) => { const d = new Date(t * 1000); return info.t1 - info.t0 > 86400 * 1.5 ? d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' }) : d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }) }
  const ref = series.reduce((a, s) => (s.points.length > a.length ? s.points : a), [])
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect(); const px = ((e.clientX - r.left) / r.width) * W
    const t = info.t0 + ((px - PL) / (W - PL - PR)) * (info.t1 - info.t0)
    let best = ref[0]; for (const p of ref) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p
    setHover(best ? best[0] : null)
  }
  const at = (s, t) => s.points.reduce((a, p) => (a && Math.abs(a[0] - t) <= Math.abs(p[0] - t) ? a : p), null)
  return (
    <div style={{ position: 'relative' }}>
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" style={{ display: 'block' }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {ticks.map((v, i) => <g key={i}><line x1={PL} x2={W - PR} y1={y(v)} y2={y(v)} stroke="var(--border)" strokeWidth="1" /><text x={PL - 6} y={y(v) + 4} textAnchor="end" fontSize="10" fill="var(--muted)">{fmt(v)}</text></g>)}
        <text x={PL} y={height - 4} fontSize="10" fill="var(--muted)">{timeLbl(info.t0)}</text><text x={W - PR} y={height - 4} fontSize="10" fill="var(--muted)" textAnchor="end">{timeLbl(info.t1)}</text>
        {series.map((s, i) => {
          if (!s.points.length) return null
          const d = s.points.map((p, j) => `${j ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join('')
          return <g key={i}>{area && <path d={`${d}L${x(s.points[s.points.length - 1][0]).toFixed(1)},${y(0)}L${x(s.points[0][0]).toFixed(1)},${y(0)}Z`} fill={s.color} opacity=".12" />}<path d={d} fill="none" stroke={s.color} strokeWidth="1.8" strokeLinejoin="round" /></g>
        })}
        {hover != null && <line x1={x(hover)} x2={x(hover)} y1={PT} y2={height - PB} stroke="var(--muted)" strokeDasharray="3 3" />}
      </svg>
      {hover != null && (
        <div className="chart-tip" style={{ left: `${Math.min(78, Math.max(2, (x(hover) / W) * 100))}%` }}>
          <b>{new Date(hover * 1000).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })}</b>
          {series.map((s, i) => { const p = at(s, hover); return p ? <div key={i}><span className="dot" style={{ background: s.color }} /> {s.name} : {fmt(p[1])}</div> : null })}
        </div>)}
      {series.length > 1 && <div className="row wrap small muted" style={{ gap: 12, marginTop: 4 }}>{series.map((s, i) => <span key={i}><span className="dot" style={{ background: s.color }} /> {s.name}</span>)}</div>}
    </div>
  )
}

// Petits bâtons (activité heure par heure d'une adresse) : { t, hits, errors }
export function Bars({ data, height = 60 }) {
  const max = Math.max(1, ...data.map((d) => d.hits))
  const tot = data.reduce((a, d) => a + d.hits, 0); const err = data.reduce((a, d) => a + d.errors, 0)
  const act = data.filter((d) => d.hits).length
  return (
    <div>
      <svg viewBox={`0 0 ${data.length * 8} ${height}`} width="100%" height={height} preserveAspectRatio="none" style={{ display: 'block' }}>
        <line x1="0" x2={data.length * 8} y1={height - 0.5} y2={height - 0.5} stroke="var(--border)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {data.map((d, i) => { const h = d.hits ? Math.max(3, (d.hits / max) * (height - 2)) : 0; const e = d.errors ? Math.max(3, (d.errors / max) * (height - 2)) : 0
          return <g key={i}><title>{`${new Date(d.t * 1000).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })} : ${d.hits} requêtes, ${d.errors} erreurs`}</title>
            <rect x={i * 8} y="0" width="8" height={height} fill="transparent" />
            {d.hits ? <rect x={i * 8 + 1} y={height - h} width="6" height={h} fill="var(--accent)" opacity=".55" /> : <rect x={i * 8 + 2.5} y={height - 2} width="3" height="2" fill="var(--border)" />}
            {e > 0 && <rect x={i * 8 + 1} y={height - e} width="6" height={e} fill="var(--err)" />}</g> })}
      </svg>
      <div className="row small muted" style={{ justifyContent: 'space-between', marginTop: 4 }}><span>il y a {data.length} h</span><span>{tot} requête{tot > 1 ? 's' : ''}{err ? `, dont ${err} en erreur` : ''} · {act} heure{act > 1 ? 's' : ''} active{act > 1 ? 's' : ''}</span><span>maintenant</span></div>
    </div>
  )
}

// Liste de barres horizontales : [{ name, value, sub?, color?, title? }] — la plus longue = 100 %.
export function BarList({ items, fmt = (v) => String(Math.round(v)), empty = 'Rien pour l\'instant.' }) {
  const max = Math.max(1e-9, ...items.map((i) => i.value))
  if (!items.length) return <div className="muted small" style={{ padding: '14px 0', textAlign: 'center' }}>{empty}</div>
  return (
    <div className="col" style={{ gap: 7 }}>{items.map((i, k) => (
      <div key={k} title={i.onClick ? (i.title ? i.title + ' — ' : '') + 'Cliquer pour ouvrir' : i.title} role={i.onClick ? 'link' : undefined} tabIndex={i.onClick ? 0 : undefined} style={i.onClick ? { cursor: 'pointer' } : undefined} onClick={i.onClick} onKeyDown={i.onClick ? (e) => e.key === 'Enter' && i.onClick() : undefined}>
        <div className="row" style={{ justifyContent: 'space-between', gap: 8, fontSize: 12.5 }}><span className={'ellipsis' + (i.onClick ? ' linkcell' : '')} style={{ maxWidth: '70%' }}>{i.name}</span><span><b>{fmt(i.value)}</b>{i.sub && <span className="muted"> · {i.sub}</span>}</span></div>
        <div style={{ height: 6, borderRadius: 4, background: 'var(--panel2)', marginTop: 3 }}><div style={{ height: 6, borderRadius: 4, width: `${Math.max(2, (i.value / max) * 100)}%`, background: i.color || 'var(--accent)', opacity: .8 }} /></div>
      </div>))}</div>
  )
}

// Une barre découpée en parts (familles de réponses…) avec sa légende : [{ name, value, color }]
export function StackBar({ parts, fmt = (v) => String(Math.round(v)) }) {
  const total = parts.reduce((a, p) => a + p.value, 0)
  if (!total) return <div className="muted small" style={{ padding: '14px 0', textAlign: 'center' }}>Rien pour l'instant.</div>
  return (
    <div className="col" style={{ gap: 8 }}>
      <div style={{ display: 'flex', height: 14, borderRadius: 7, overflow: 'hidden', background: 'var(--panel2)' }}>{parts.filter((p) => p.value > 0).map((p) => <div key={p.name} title={`${p.name} : ${fmt(p.value)} (${(p.value / total * 100).toFixed(1)} %)`} style={{ width: `${p.value / total * 100}%`, background: p.color }} />)}</div>
      <div className="row wrap small" style={{ gap: 12 }}>{parts.filter((p) => p.value > 0).map((p) => <span key={p.name}><span className="dot" style={{ background: p.color }} /> {p.name} <b>{(p.value / total * 100).toFixed(p.value / total < 0.1 ? 1 : 0)} %</b> <span className="muted">({fmt(p.value)})</span></span>)}</div>
    </div>
  )
}
