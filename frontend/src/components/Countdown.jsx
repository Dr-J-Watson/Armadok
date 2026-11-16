import { useEffect, useState } from 'react'

// Compte à rebours mm:ss jusqu'à `until` (secondes Unix) ; appelle onEnd une fois écoulé.
export function Countdown({ until, onEnd }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])
  const left = Math.max(0, Math.round(until * 1000 - now) / 1000)
  useEffect(() => { if (left <= 0) onEnd?.() }, [left <= 0]) // eslint-disable-line react-hooks/exhaustive-deps
  const m = Math.floor(left / 60), s = Math.floor(left % 60)
  return <span className="mono">{m}:{String(s).padStart(2, '0')}</span>
}
