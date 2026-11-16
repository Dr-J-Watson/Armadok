import { useLiveState } from '../lib/live.js'

// Petit témoin « temps réel » : vert quand les changements (même faits ailleurs) arrivent tout seuls.
export default function LiveDot() {
  const s = useLiveState()
  const txt = s === 'live' ? 'Temps réel actif : les changements faits ailleurs s\'affichent tout seuls.' : s === 'connecting' ? 'Connexion au temps réel…' : 'Temps réel interrompu : l\'écran se met à jour périodiquement, reconnexion automatique.'
  return <div className="livedot lbl" data-tip={txt}><span className={'dot ' + (s === 'live' ? 'ok' : s === 'connecting' ? 'warn' : 'err')} />{s === 'live' ? 'Temps réel' : s === 'connecting' ? 'Connexion…' : 'Hors ligne'}</div>
}
