import { api } from '../api.js'

// « Publier sur Traefik » : ouvre directement l'assistant de site, déjà rempli (nom et adresse que Traefik utilisera pour joindre le conteneur).
// Les alertes de joignabilité (aucun réseau commun, aucun port) sont rappelées en notification, l'assistant reste le seul écran.
export async function publishNow(container, toast) {
  try {
    const h = await api.get(`/docker/containers/${encodeURIComponent(container)}/publish-hint`)
    const t = h.traefik || {}
    window.dispatchEvent(new CustomEvent('new-site', { detail: { name: h.name, url: h.url, dns: true } }))
    if (!h.canCreate) toast?.('Vous ne pouvez pas créer de site sur ce nœud', 'err')
    else if (!h.port) toast?.('Ce conteneur n\'expose aucun port : complétez l\'adresse cible (http://nom:port)', 'err')
    else if (t.found && t.reachable === false) toast?.('Traefik et ce conteneur ne partagent aucun réseau : Traefik ne pourra pas l\'atteindre. Rattachez-les à un réseau commun (onglet Docker → Réseaux).', 'err')
  } catch (e) { toast?.(e.message, 'err') }
}
