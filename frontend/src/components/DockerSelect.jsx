import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Btn, useUi } from './ui.jsx'

// Sélection multiple d'un tableau : `rows` = lignes affichées, `keyOf` = clé d'une ligne, `can(row)` = la ligne peut-elle être cochée.
// Les clés cochées qui ne sont plus affichées (autre filtre, ligne disparue) sont simplement ignorées.
export function useSelect(rows, keyOf, can = () => true, why = () => 'Utilisé donc pas sélectionnable') {
  const [keys, setKeys] = useState(() => new Set()); const last = useRef(null)   // last : dernière case cliquée, point de départ d'une plage Maj+clic
  const ok = rows.filter(can)
  const picked = ok.filter((r) => keys.has(keyOf(r)))
  return {
    picked, has: (r) => keys.has(keyOf(r)), can, why,
    // Clic : coche / décoche. Maj+clic : toute la plage entre la dernière case cliquée et celle-ci prend l'état de celle-ci (dans l'ordre affiché).
    toggle: (r, range = false) => {
      const n = new Set(keys); const k = keyOf(r); const on = !keys.has(k)
      const a = ok.findIndex((x) => keyOf(x) === last.current); const b = ok.findIndex((x) => keyOf(x) === k)
      if (range && a >= 0 && b >= 0) ok.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((x) => (on ? n.add(keyOf(x)) : n.delete(keyOf(x))))
      else if (on) n.add(k); else n.delete(k)
      last.current = k; setKeys(n)
    },
    all: picked.length > 0 && picked.length === ok.length, some: picked.length > 0, total: ok.length,
    setAll: (on) => setKeys(on ? new Set(ok.map(keyOf)) : new Set()), clear: () => setKeys(new Set()),
  }
}

// Case « tout cocher » de l'en-tête (état intermédiaire quand une partie seulement est cochée)
export function SelectAllBox({ s }) {
  const ref = useRef(null)
  useEffect(() => { if (ref.current) ref.current.indeterminate = s.some && !s.all }, [s.some, s.all])
  return <input ref={ref} type="checkbox" aria-label="Tout sélectionner" checked={s.all} disabled={s.total === 0} onChange={(e) => s.setAll(e.target.checked)} />
}

export function SelectBox({ s, row }) {
  // L'infobulle est portée par l'enveloppe (data-tip) et non par la case : un « title » sur la case ferait apparaître celle du navigateur EN PLUS de celle de l'appli,
  // et une case désactivée ne reçoit aucun événement souris (pointer-events: none la laisse passer à l'enveloppe).
  // onClick (et non onChange) : seul le clic sait si Maj était enfoncée ; mousedown évite le texte surligné par Maj+clic.
  const ok = s.can(row)
  return <span className={'selbox ' + (ok ? '' : 'off')} data-tip={ok ? 'Maj+clic : sélectionner une plage' : s.why(row)}>
    <input type="checkbox" aria-label={ok ? 'Sélectionner' : s.why(row)} checked={s.has(row)} disabled={!ok}
      onChange={() => {}} onMouseDown={(e) => e.shiftKey && e.preventDefault()} onClick={(e) => { e.stopPropagation(); s.toggle(row, e.shiftKey) }} /></span>
}

// Barre collée en bas quand au moins une ligne est cochée : « N sélectionné(s) » + les actions (enfants) + « Désélectionner »
export function BulkBar({ s, noun = 'élément', children }) {
  if (!s.some) return null
  return <div className="sel-bar"><b>{s.picked.length} {noun}{s.picked.length > 1 ? 's' : ''} sélectionné{s.picked.length > 1 ? 's' : ''}</b><span className="row wrap right" style={{ gap: 8 }}>{children}<Btn small onClick={s.clear}>Désélectionner</Btn></span></div>
}

// Lance une action sur plusieurs éléments, l'un après l'autre (le démon Docker n'aime pas trente suppressions simultanées),
// puis résume : combien ont réussi, lesquels ont échoué et pourquoi.
export function useBulk(invalidate) {
  const { toast, confirm } = useUi(); const qc = useQueryClient(); const [busy, setBusy] = useState(false)
  const run = async ({ items, label, fn, ask, onDone }) => {
    if (ask && !(await confirm(ask))) return
    setBusy(true)
    const failed = []; let done = 0
    for (const it of items) {
      try { await fn(it); done++ } catch (e) { failed.push(`${label(it)} : ${e.message}`) }
    }
    setBusy(false)
    for (const k of invalidate) qc.invalidateQueries({ queryKey: [k] })
    onDone?.()
    if (!failed.length) toast(`${done} fait${done > 1 ? 's' : ''}`)
    else toast(`${done} sur ${items.length} fait${done > 1 ? 's' : ''}. Échec : ${failed.slice(0, 3).join(' · ')}${failed.length > 3 ? ` (+${failed.length - 3})` : ''}`, 'err')
  }
  return { run, busy }
}
