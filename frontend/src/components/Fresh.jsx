import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Btn } from './ui.jsx'

// « Actualiser » + « mis à jour il y a X » pour une requête react-query.
export default function Fresh({ q, className = 'right' }) {
  const [, tick] = useState(0)
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 10000); return () => clearInterval(t) }, [])
  const s = q.dataUpdatedAt ? Math.max(0, Math.round((Date.now() - q.dataUpdatedAt) / 1000)) : null
  const txt = s == null ? '' : s < 15 ? 'à l\'instant' : s < 90 ? `il y a ${s} s` : `il y a ${Math.round(s / 60)} min`
  return <span className={'row ' + className} style={{ gap: 6 }}>{txt && <span className="muted small">mis à jour {txt}</span>}<Btn small kind="ghost" icon title="Actualiser maintenant" busy={q.isFetching} onClick={() => q.refetch()}><RefreshCw size={14} /></Btn></span>
}
