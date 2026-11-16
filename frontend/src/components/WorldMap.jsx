import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Maximize2, Minus, Plus } from 'lucide-react'

const Land = lazy(() => import('../lib/worldPath.js').then((m) => ({ default: () => <>{[-360, 0, 360].map((dx) => <path key={dx} className="land" transform={`translate(${dx} 0)`} d={m.WORLD} />)}</> })))

const W = 360, H = 135, TOP = 20   // la carte visible au départ : de 70° N à 65° S
const HUES = [152, 205, 28, 280, 340, 48, 175, 255, 10, 100, 310, 225]
// Couleur stable par pays (le même pays a toujours la même couleur)
export const countryColor = (code = '') => { let h = 0; for (const ch of code) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return `hsl(${HUES[h % HUES.length]} 70% 52%)` }

const SPREAD_ZOOM = 6   // à partir de ce zoom, des adresses au MÊME endroit exact s'écartent en cercle (sinon elles resteraient empilées)

// Regroupe les points proches à l'écran (distance en unités de carte) ; plus on zoome, plus les groupes se défont.
function cluster(points, d, k) {
  const exact = new Map()
  if (k >= SPREAD_ZOOM) for (const p of points) { const key = p.lat + ',' + p.lon; if (!exact.has(key)) exact.set(key, []); exact.get(key).push(p) }
  const out = []
  const done = new Set()
  for (const p of [...points].sort((a, b) => b.hits - a.hits)) {
    if (done.has(p)) continue
    const twins = k >= SPREAD_ZOOM ? exact.get(p.lat + ',' + p.lon) : null
    if (twins && twins.length > 1) {   // même position exacte : disposées en cercle autour d'elle
      twins.forEach((q, i) => { done.add(q); const a = (2 * Math.PI * i) / twins.length - Math.PI / 2; out.push({ items: [q], lat: q.lat, lon: q.lon, ring: { a, n: twins.length } }) })
      continue
    }
    const c = { items: [p], lat: p.lat, lon: p.lon }
    done.add(p)
    for (const q of points) {
      if (done.has(q) || (k >= SPREAD_ZOOM && exact.get(q.lat + ',' + q.lon)?.length > 1)) continue
      if (Math.hypot(q.lon - p.lon, q.lat - p.lat) < d) { c.items.push(q); done.add(q) }
    }
    out.push(c)
  }
  return out
}

// Carte du monde locale (aucune requête vers un serveur de tuiles) : molette = zoom vers le curseur, glisser = déplacer, double-clic = zoom.
// Des adresses proches sont regroupées en un seul rond (avec leur nombre) qui se défait quand on zoome ; le survol donne l'essentiel, le clic ouvre l'adresse
// (ou, pour un groupe, zoome dessus).
// points : [{ lat, lon, hits, country, kind: 'err' | 'warn' | '', ip, place, attacks: [libellés], row }]
export default function WorldMap({ points, onPick }) {
  const svg = useRef(null)
  const [v, setV] = useState({ k: 1, cx: W / 2, cy: TOP + H / 2 })   // zoom et centre (en unités de la carte)
  const [px, setPx] = useState(1000)                                  // largeur de la carte à l'écran, en pixels
  const [tip, setTip] = useState(null)
  const drag = useRef(null)
  const max = Math.max(1, ...points.map((p) => p.hits))
  const w = W / v.k, h = H / v.k
  const u = w / px   // unités de carte par pixel d'écran : tout ce qui est dessiné est dimensionné en pixels
  useEffect(() => {
    const el = svg.current; if (!el) return
    const ro = new ResizeObserver(() => setPx(el.getBoundingClientRect().width || 1000)); ro.observe(el); setPx(el.getBoundingClientRect().width || 1000)
    return () => ro.disconnect()
  }, [])
  const clamp = (n) => ({ ...n, cx: Math.min(W + 60, Math.max(-60, n.cx)), cy: Math.min(TOP + H, Math.max(TOP, n.cy)) })
  const zoom = (f, px0 = 0.5, py0 = 0.5) => setV((o) => {   // (px0, py0) : point fixe, en fraction de la vue
    const k = Math.min(60, Math.max(1, o.k * f)); const nw = W / k, nh = H / k, ow = W / o.k, oh = H / o.k
    return clamp({ k, cx: o.cx + (px0 - 0.5) * (ow - nw), cy: o.cy + (py0 - 0.5) * (oh - nh) })
  })
  const zoomOn = (lon, lat, f) => setV((o) => clamp({ k: Math.min(60, o.k * f), cx: lon + 180, cy: 90 - lat }))
  useEffect(() => {   // molette : écouteur non passif, pour que la page ne défile pas
    const el = svg.current; if (!el) return
    const on = (e) => { e.preventDefault(); setTip(null); const r = el.getBoundingClientRect(); zoom(e.deltaY < 0 ? 1.4 : 1 / 1.4, (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height) }
    el.addEventListener('wheel', on, { passive: false }); return () => el.removeEventListener('wheel', on)
  })
  const down = (e) => { drag.current = { x: e.clientX, y: e.clientY, moved: false }; setTip(null) }
  const move = (e) => {
    const d = drag.current; if (!d) return
    const r = svg.current.getBoundingClientRect(); const dx = e.clientX - d.x, dy = e.clientY - d.y
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true
    d.x = e.clientX; d.y = e.clientY
    setV((o) => clamp({ ...o, cx: o.cx - dx / r.width * w, cy: o.cy - dy / r.height * h }))
  }
  const groups = cluster(points, 16 * u, v.k)
  const show = (e, g) => {
    const r = svg.current.parentElement.getBoundingClientRect()
    setTip({ x: e.clientX - r.left, y: e.clientY - r.top, g })
  }
  const kinds = (items) => (items.some((i) => i.kind === 'err') ? 'err' : items.some((i) => i.kind === 'warn') ? 'warn' : '')
  return (
    <div style={{ position: 'relative' }}>
      <svg ref={svg} className="worldmap" viewBox={`${v.cx - w / 2} ${v.cy - h / 2} ${w} ${h}`} role="img" aria-label="Carte des adresses"
        style={{ touchAction: 'none', cursor: 'grab' }} onPointerDown={down} onPointerMove={move} onPointerUp={() => { setTimeout(() => { drag.current = null }, 0) }} onPointerLeave={() => { drag.current = null; setTip(null) }}
        onDoubleClick={(e) => { const r = svg.current.getBoundingClientRect(); zoom(2, (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height) }}>
        <Suspense fallback={null}><Land /></Suspense>
        {groups.map((g, i) => {
          const n = g.items.length; const total = g.items.reduce((a, b) => a + b.hits, 0); const kind = kinds(g.items)
          const same = g.items.every((x) => x.country === g.items[0].country)
          const rpx = n > 1 ? 9 + 9 * Math.sqrt(total / (max * 2)) : 4.5 + 5.5 * Math.sqrt(total / max)
          const ringR = g.ring ? Math.max(17, 8 + g.ring.n * 3) * u : 0
          const cx0 = g.lon + 180 + (g.ring ? Math.cos(g.ring.a) * ringR : 0), cy0 = 90 - g.lat + (g.ring ? Math.sin(g.ring.a) * ringR : 0)
          return [-360, 0, 360].map((dx) => (
            <g key={i + ':' + dx} style={{ cursor: 'pointer' }} onPointerMove={(e) => { e.stopPropagation(); if (!drag.current) show(e, g) }} onPointerLeave={() => setTip(null)}
              onClick={() => { if (drag.current?.moved) return; if (n === 1) onPick?.(g.items[0].row); else zoomOn(g.lon, g.lat, 3) }}>
              {g.ring && <line x1={g.lon + 180 + dx} y1={90 - g.lat} x2={cx0 + dx} y2={cy0} stroke="var(--muted)" strokeWidth=".6" vectorEffect="non-scaling-stroke" opacity=".5" />}
              <circle cx={cx0 + dx} cy={cy0} r={rpx * u} fill={n > 1 && !same ? 'var(--accent)' : countryColor(g.items[0].country)} fillOpacity={n > 1 ? .85 : .8}
                stroke={kind === 'err' ? 'var(--err)' : kind === 'warn' ? 'var(--warn)' : 'var(--panel)'} strokeWidth={kind ? 2 : 1} vectorEffect="non-scaling-stroke" />
              {n > 1 && <text x={cx0 + dx} y={cy0} fontSize={11 * u} fontWeight="700" textAnchor="middle" dominantBaseline="central" fill="#fff" style={{ pointerEvents: 'none' }}>{n}</text>}
            </g>))
        })}
      </svg>
      {tip && <Tip tip={tip} right={tip.x > px * 0.6} />}
      <div className="col" style={{ position: 'absolute', top: 8, right: 8, gap: 4 }}>
        <button type="button" className="btn sm icon" title="Zoom +" onClick={() => zoom(1.6)}><Plus size={14} /></button>
        <button type="button" className="btn sm icon" title="Zoom −" onClick={() => zoom(1 / 1.6)}><Minus size={14} /></button>
        <button type="button" className="btn sm icon" title="Tout voir" onClick={() => setV({ k: 1, cx: W / 2, cy: TOP + H / 2 })}><Maximize2 size={14} /></button>
      </div>
    </div>
  )
}

// Infobulle : une adresse (détail) ou un groupe (nombre, lieu, les plus actives).
function Tip({ tip, right }) {
  const items = [...tip.g.items].sort((a, b) => b.hits - a.hits); const n = items.length
  const places = [...new Set(items.map((i) => i.place).filter(Boolean))]
  const total = items.reduce((a, b) => a + b.hits, 0)
  return (
    <div className="maptip" style={{ left: tip.x + (right ? -14 : 14), top: tip.y + 14, transform: right ? 'translateX(-100%)' : undefined }}>
      {n === 1 ? <>
        <b className="mono">{items[0].ip}</b>
        <div className="muted">{items[0].place || 'lieu inconnu'}</div>
        <div>{items[0].hits} requête{items[0].hits > 1 ? 's' : ''}{items[0].errors ? ` · ${items[0].errors} en erreur` : ''}</div>
        {items[0].attacks?.length > 0 && <div style={{ color: 'var(--err)' }}>{items[0].attacks.join(' · ')}</div>}
        {items[0].banned && <div style={{ color: 'var(--err)' }}>bannie</div>}
        <div className="muted small">Cliquer : détail</div>
      </> : <>
        <b>{n} adresses</b> · {total} requêtes
        <div className="muted">{places.slice(0, 3).join(' · ')}{places.length > 3 ? ` … (+${places.length - 3})` : ''}</div>
        {items.slice(0, 5).map((i) => <div key={i.ip} className="small"><span className="mono">{i.ip}</span> · {i.hits}{i.attacks?.length ? <span style={{ color: 'var(--err)' }}> · {i.attacks[0]}</span> : ''}</div>)}
        {n > 5 && <div className="muted small">… et {n - 5} autre{n - 5 > 1 ? 's' : ''}</div>}
        <div className="muted small">Cliquer : zoomer · ou molette</div>
      </>}
    </div>
  )
}
