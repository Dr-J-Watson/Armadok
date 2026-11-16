import { useEffect, useState } from 'react'

// Comme useState, mais la valeur est retenue dans ce navigateur (filtre, période…). Si le stockage est indisponible, ça marche quand même.
export function usePersist(key, init) {
  const k = 'ui:' + key
  const [v, setV] = useState(() => { try { const r = localStorage.getItem(k); return r === null ? init : JSON.parse(r) } catch { return init } })
  useEffect(() => { try { localStorage.setItem(k, JSON.stringify(v)) } catch { /* stockage indisponible */ } }, [k, v])
  return [v, setV]
}
