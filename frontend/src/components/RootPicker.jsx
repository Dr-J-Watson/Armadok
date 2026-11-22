import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api.js'
import { useNode } from '../node.jsx'
import { Field } from './ui.jsx'

// Dossier de stockage d'un nouveau projet : n'apparaît que si le nœud en a plusieurs (PROJECT_ROOTS). Une seule racine = aucune question.
export default function RootPicker({ value, onChange }) {
  const { node } = useNode()
  const q = useQuery({ queryKey: ['project-roots', node?.id], queryFn: () => api.get('/projects/roots'), retry: false, staleTime: 60000 })
  const roots = q.data?.roots || []
  useEffect(() => { if (roots.length > 1 && !roots.includes(value)) onChange(roots[0]) }, [roots.join('|')]) // eslint-disable-line react-hooks/exhaustive-deps
  if (roots.length < 2) return null
  return (
    <Field label="Dossier de stockage" help="Ce nœud a plusieurs dossiers de projets : choisissez celui où ce projet sera créé.">
      <select className="select mono" style={{ maxWidth: 420 }} value={value || roots[0]} onChange={(e) => onChange(e.target.value)}>{roots.map((r) => <option key={r} value={r}>{r}</option>)}</select>
    </Field>
  )
}
