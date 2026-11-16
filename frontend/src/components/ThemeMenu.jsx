import { useEffect, useRef, useState } from 'react'
import { Check, Monitor, Moon, Palette, Sun } from 'lucide-react'

// Thème : clair / sombre / système / perso (couleur choisie par l'utilisateur, avec une base claire ou sombre).
const BASE = {
  light: { bg: '#f5f6fa', panel2: '#f0f2f8', border: '#e2e5ef' },
  dark: { bg: '#0e1018', panel2: '#1d2130', border: '#2a2f44' },
}
const PRESETS = ['#5b5bf0', '#8b5cf6', '#d946ef', '#ef4444', '#f97316', '#16a34a', '#0d9488', '#0ea5e9']
const DEFAULT_COLOR = PRESETS[0]
const MODES = [['light', 'Clair', Sun], ['dark', 'Sombre', Moon], ['system', 'Système', Monitor], ['custom', 'Perso', Palette]]
const OWNED = ['--accent', '--accent-soft', '--bg', '--panel2', '--border', '--on-accent']

const read = (k, d) => { try { return localStorage.getItem(k) || d } catch { return d } }
const write = (k, v) => { try { localStorage.setItem(k, v) } catch { /* */ } }
const isHex = (c) => /^#[0-9a-f]{6}$/i.test(c || '')

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
const toHex = (c) => '#' + c.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0')).join('')
const mix = (a, b, t) => toHex(rgb(a).map((v, i) => v * (1 - t) + rgb(b)[i] * t))
const lum = (hex) => { const [r, g, b] = rgb(hex).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }); return 0.2126 * r + 0.7152 * g + 0.0722 * b }

// Une couleur trop proche du fond serait illisible : on la rapproche du contraste utile.
const readableAccent = (hex, base) => (base === 'dark' && lum(hex) < 0.12 ? mix(hex, '#ffffff', 0.45) : base === 'light' && lum(hex) > 0.6 ? mix(hex, '#000000', 0.35) : hex)

export function applyTheme({ mode, base, color }) {
  const root = document.documentElement
  const resolved = mode === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : mode === 'custom' ? base : mode
  root.dataset.theme = resolved
  OWNED.forEach((k) => root.style.removeProperty(k))
  if (mode === 'custom' && isHex(color)) {
    const a = readableAccent(color, resolved); const b = BASE[resolved]
    root.style.setProperty('--accent', a)
    root.style.setProperty('--accent-soft', mix(a, b.bg, resolved === 'dark' ? 0.78 : 0.86))
    root.style.setProperty('--bg', mix(b.bg, a, 0.05))
    root.style.setProperty('--panel2', mix(b.panel2, a, 0.06))
    root.style.setProperty('--border', mix(b.border, a, 0.1))
    root.style.setProperty('--on-accent', lum(a) > 0.5 ? '#111111' : '#ffffff')
  }
  window.dispatchEvent(new Event('themechange'))
}

export function useThemePrefs() {
  const [prefs, setPrefs] = useState(() => {
    const mode = read('theme', 'system')
    return { mode: ['light', 'dark', 'system', 'custom'].includes(mode) ? mode : 'system', base: read('theme-base', 'light') === 'dark' ? 'dark' : 'light', color: isHex(read('theme-color')) ? read('theme-color') : DEFAULT_COLOR }
  })
  useEffect(() => {
    applyTheme(prefs)
    write('theme', prefs.mode); write('theme-base', prefs.base); write('theme-color', prefs.color)
    if (prefs.mode !== 'system') return
    const mq = matchMedia('(prefers-color-scheme: dark)'); const h = () => applyTheme(prefs)
    mq.addEventListener('change', h); return () => mq.removeEventListener('change', h)
  }, [prefs])
  return [prefs, (p) => setPrefs((cur) => ({ ...cur, ...p }))]
}

export default function ThemeMenu({ prefs, set }) {
  const [open, setOpen] = useState(false); const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    const h = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    const k = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', h); window.addEventListener('keydown', k)
    return () => { document.removeEventListener('mousedown', h); window.removeEventListener('keydown', k) }
  }, [open])
  const Cur = MODES.find((m) => m[0] === prefs.mode)[2]
  return (
    <div className="themew" ref={ref}>
      <button className="nav" onClick={() => setOpen(!open)} aria-haspopup="dialog" aria-expanded={open}>
        <Cur size={17} /><span className="lbl">Thème</span>
        {prefs.mode === 'custom' && <span className="swatch" style={{ background: prefs.color, marginLeft: 'auto' }} />}
      </button>
      {open && (
        <div className="thememenu" role="dialog" aria-label="Thème">
          <div className="muted small">Apparence</div>
          <div className="theme-modes">{MODES.map(([id, label, Icon]) => (
            <button key={id} className={prefs.mode === id ? 'active' : ''} onClick={() => set({ mode: id })}><Icon size={16} />{label}</button>))}</div>
          {prefs.mode === 'custom' && (
            <div className="col" style={{ gap: 10 }}>
              <div className="seg" style={{ alignSelf: 'flex-start' }}>
                {[['light', 'Fond clair'], ['dark', 'Fond sombre']].map(([id, l]) => <button key={id} className={prefs.base === id ? 'active' : ''} onClick={() => set({ base: id })}>{l}</button>)}</div>
              <div className="muted small">Couleur</div>
              <div className="swatches">
                {PRESETS.map((c) => <button key={c} className="swatch big" style={{ background: c }} aria-label={c} onClick={() => set({ color: c })}>{prefs.color.toLowerCase() === c && <Check size={13} color="#fff" />}</button>)}
              </div>
              <label className="row picker"><input type="color" value={prefs.color} onChange={(e) => set({ color: e.target.value })} /><span className="grow">Choisir une autre couleur</span><code>{prefs.color}</code></label>
            </div>)}
        </div>)}
    </div>
  )
}
