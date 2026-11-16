import { useState } from 'react'
import { Seg } from './ui.jsx'

export const RANGES = [['15m', '15 min'], ['30m', '30 min'], ['1h', '1 h'], ['3h', '3 h'], ['6h', '6 h'], ['12h', '12 h'], ['24h', '24 h'], ['3d', '3 j'], ['7d', '7 j'], ['14d', '14 j'], ['30d', '30 j']].map(([id, label]) => ({ id, label }))
const UNIT_SEC = { m: 60, h: 3600, d: 86400 }

// Durée du graphique : une dizaine de durées usuelles, ou une durée libre (de 1 minute à 90 jours).
export default function RangePicker({ value, onChange }) {
  const preset = RANGES.some((r) => r.id === value)
  const m = /^(\d+)([mhd])$/.exec(value) || []
  const [custom, setCustom] = useState(!preset)
  const [n, setN] = useState(preset ? 2 : Number(m[1]) || 2); const [u, setU] = useState(preset ? 'h' : m[2] || 'h')
  const apply = (nn, uu) => { const sec = Number(nn) * UNIT_SEC[uu]; if (nn >= 1 && sec >= 60 && sec <= 90 * 86400) onChange(`${Math.floor(nn)}${uu}`) }
  return (
    <div className="row wrap" style={{ gap: 8 }}>
      <Seg options={[...RANGES, { id: 'custom', label: 'Perso' }]} value={custom ? 'custom' : value} onChange={(v) => { if (v === 'custom') { setCustom(true); apply(n, u) } else { setCustom(false); onChange(v) } }} />
      {custom && <>
        <input className="input" type="number" min="1" max="129600" style={{ width: 90 }} value={n} onChange={(e) => { setN(e.target.value); apply(e.target.value, u) }} aria-label="Durée" />
        <select className="select" style={{ width: 'auto' }} value={u} onChange={(e) => { setU(e.target.value); apply(n, e.target.value) }}><option value="m">minutes</option><option value="h">heures</option><option value="d">jours</option></select>
        <span className="muted small">1 minute à 90 jours (Prometheus ne garde que sa durée de rétention : 15 jours par défaut)</span></>}
    </div>
  )
}
