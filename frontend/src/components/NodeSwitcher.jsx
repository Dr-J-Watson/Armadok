import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, ChevronsUpDown, LayoutGrid, Plus, Settings2 } from 'lucide-react'
import { ROLES, useNode } from '../node.jsx'
import { useAuth } from '../auth.jsx'
import NewNode from './NewNode.jsx'
import { useSwitchNode } from '../lib/nav.js'

export const CONN = { online: ['ok', 'En ligne'], offline: ['err', 'Hors ligne'], pending: ['warn', 'En attente du worker'] }

// Couleur stable par nœud : on reconnaît d'un coup d'œil où l'on travaille.
const hue = (id = '') => [...id].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7)

export function NodeAvatar({ node, size = 32 }) {
  const h = hue(node.id)
  return <div className="node-av" style={{ width: size, height: size, fontSize: size * 0.45, background: `linear-gradient(135deg, hsl(${h} 72% 56%), hsl(${(h + 45) % 360} 70% 46%))` }}>{(node.name || '?').trim().charAt(0).toUpperCase()}</div>
}

export default function NodeSwitcher() {
  const { nodes, node, setNode, canNode, fullView, setFullView } = useNode(); const canManage = canNode; const { user } = useAuth(); const sw = useSwitchNode(); const nav = useNavigate()
  const [open, setOpen] = useState(false); const [creating, setCreating] = useState(false); const ref = useRef(null)
  const owned = nodes.filter((n) => n.kind === 'remote' && n.role === 'owner').length
  const canCreate = user.maxNodes !== 0 && (user.maxNodes < 0 || owned < user.maxNodes)
  useEffect(() => {
    if (!open) return
    const h = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    const k = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', h); window.addEventListener('keydown', k)
    return () => { document.removeEventListener('mousedown', h); window.removeEventListener('keydown', k) }
  }, [open])
  // L'assistant « Nouveau nœud » reste à une place FIXE de l'arbre : quand le nœud vient d'être créé, l'écran passe de « aucun nœud » à
  // « un nœud » ; s'il était rendu dans l'une des deux branches, il serait recréé de zéro et le jeton affiché disparaîtrait aussitôt.
  const wizard = creating ? <NewNode key="new-node" onClose={() => setCreating(false)} /> : null
  if (!node) return <>{canCreate ? <div className="nodesw"><button className="nodebtn" onClick={() => setCreating(true)}><Plus size={18} /><span className="grow lbl"><b>Créer un nœud</b><span className="muted small">Ajoutez un Traefik</span></span></button></div> : null}{wizard}</>
  const pick = (id) => { setOpen(false); sw.to(id) }
  return (
    <>
    <div className="nodesw" ref={ref}>
      <button className={'nodebtn ' + (open ? 'open' : '')} onClick={() => setOpen(!open)} aria-haspopup="listbox" aria-expanded={open}>
        <NodeAvatar node={node} />
        <span className="grow lbl"><b className="ellipsis">{node.name}</b><span className="muted small">{fullView ? `Affichage complet · ${nodes.length} nœuds` : <>{node.roleLabel || ROLES[node.role]?.label}{node.kind === 'local' ? ' · cette machine' : ''}</>}</span></span>
        {node.kind === 'remote' && <span className={'dot ' + CONN[node.connection][0]} title={CONN[node.connection][1]} />}
        <ChevronsUpDown size={15} className="muted lbl" />
      </button>
      {open && (
        <div className="nodemenu" role="listbox">
          <div className="muted small" style={{ padding: '6px 10px 4px' }}>{nodes.length > 1 ? 'Changer de nœud (g puis 1-9, g n / g p)' : 'Votre nœud'}</div>
          {nodes.map((n, idx) => (
            <button key={n.id} role="option" aria-selected={n.id === node.id} className={'nodeopt ' + (n.id === node.id ? 'active' : '')} onClick={() => pick(n.id)}>
              <NodeAvatar node={n} size={28} />
              <span className="grow"><b className="ellipsis">{n.name}</b><span className="muted small">{n.roleLabel || ROLES[n.role]?.label} · {n.domains} domaine{n.domains > 1 ? 's' : ''}</span></span>
              {n.kind === 'remote' && <span className={'dot ' + CONN[n.connection][0]} title={CONN[n.connection][1]} />}
              {n.emergency && <span className="badge err">urgence</span>}
              {n.status === 'suspended' && <span className="dot err" title="Suspendu" />}
              {n.id === node.id && <Check size={15} />}
            {idx < 9 && <kbd className="kbd" title={`Raccourci : g puis ${idx + 1}`} style={{ marginLeft: 6 }}>{idx + 1}</kbd>}</button>))}
          {(canManage || canCreate || nodes.length > 1) && <div className="sep" />}
          {canManage && <button className="nodeopt" onClick={() => { setOpen(false); nav('/node') }}><Settings2 size={16} /><span className="grow">Réglages du nœud</span></button>}
          {nodes.length > 1 && <button className={'nodeopt ' + (fullView ? 'active' : '')} role="option" aria-selected={fullView} title="Les listes (Docker, projets…) montrent tous vos nœuds d'un coup" onClick={() => { setOpen(false); setFullView(!fullView) }}><LayoutGrid size={16} /><span className="grow">Affichage complet<span className="muted small">Tous les nœuds à la fois</span></span>{fullView && <Check size={15} />}</button>}
          {canCreate && <button className="nodeopt" onClick={() => { setOpen(false); setCreating(true) }}><Plus size={16} /><span className="grow">Nouveau nœud</span></button>}
        </div>)}
    </div>
    {wizard}
    </>
  )
}

// Le nœud d'une ligne en affichage complet : sa pastille de couleur et son nom.
export function NodeChip({ node }) {
  if (!node) return null
  return <span className="row" style={{ gap: 6, flexWrap: 'nowrap' }} title={`Nœud « ${node.name} »`}><NodeAvatar node={node} size={18} /><span className="small ellipsis" style={{ maxWidth: 120 }}>{node.name}</span></span>
}
