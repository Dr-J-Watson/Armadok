import { ArrowLeft, Box, CornerUpRight, FolderMinus, Gauge, KeyRound, Link2, Network, Package, Shield, ShieldCheck, TriangleAlert, Wrench } from 'lucide-react'
import { Btn } from './ui.jsx'
import { MW_PRESETS } from '../traefik/presets.js'
import { GROUP_COLOR, humanOf, techName } from '../traefik/human.js'

const ICONS = { KeyRound, ShieldCheck, Network, Shield, CornerUpRight, FolderMinus, Gauge, Package, Link2, TriangleAlert, Box }
const Ico = ({ name, size = 18 }) => { const I = ICONS[name] || Box; return <I size={size} /> }

// Étape 1 de la création d'un middleware : choisir ce qu'on veut faire, pas un nom technique.
export function MiddlewareGallery({ onPick, onOther }) {
  return (
    <div className="col">
      <h3>Que voulez-vous faire ?</h3>
      <div className="muted small">Choisissez un cas courant : un réglage de départ sensé est déjà rempli. Les autres types restent disponibles.</div>
      <div className="mwgrid">
        {MW_PRESETS.map((p) => (
          <button type="button" key={p.type} className="mwcard" style={{ '--bc': GROUP_COLOR[humanOf(p.type)[2]] }} onClick={() => onPick(p)}>
            <span className="ic"><Ico name={p.icon} /></span>
            <span className="grow"><b>{p.title}</b><span className="muted small">{p.desc}</span><code className="small muted">{techName(p.type)}</code></span>
          </button>))}
        <button type="button" className="mwcard other" onClick={onOther}><span className="ic"><Wrench size={18} /></span><span className="grow"><b>Autre type…</b><span className="muted small">Tous les middlewares Traefik (redirections par motif, CORS, plugins…).</span></span></button>
      </div>
    </div>
  )
}

// Une fois le type choisi : rappel de ce qu'il fait, et moyen d'en changer.
export function TypeHeader({ type, preset, desc, onBack, backLabel = 'Retour' }) {
  const [label, icon, group] = humanOf(type)
  return (
    <div className="mwhead" style={{ '--bc': GROUP_COLOR[group] }}>
      <span className="ic"><Ico name={preset?.icon || icon} /></span>
      <div className="grow"><b>{techName(type)}</b> <span className="muted small">{preset ? `· ${preset.title}` : ''}</span><div className="muted small">{preset?.hint || preset?.desc || label || desc}</div></div>
      <Btn small onClick={onBack}>{backLabel === 'Retour' && <ArrowLeft size={14} />} {backLabel}</Btn>
    </div>
  )
}
