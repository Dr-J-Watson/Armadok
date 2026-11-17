import { api } from '../api.js'

// « Publier sur Traefik » : ouvre directement l'assistant de site, déjà rempli (nom et adresse que Traefik utilisera pour joindre le conteneur).
// Les alertes de joignabilité (aucun réseau commun, aucun port) sont rappelées en notification, l'assistant reste le seul écran.
export async function publishNow(container, toast, setNode) {
  try {
    const h = await api.get(`/docker/containers/${encodeURIComponent(container)}/publish-hint`)
    const t = h.traefik || {}
    if (h.via) {   // les sites de cette machine passent par le Traefik d'un autre nœud : l'assistant y crée le site
      if (!h.canCreate) return toast?.(`Vous ne pouvez pas créer de site sur « ${h.via.name} »`, 'err')
      if (!h.via.address || !h.via.publicPort) toast?.(!h.via.address ? 'Adresse de cette machine inconnue : complétez l\'adresse cible dans l\'assistant' : 'Ce conteneur ne publie aucun port sur la machine : le Traefik distant ne pourra pas l\'atteindre', 'err')
      window.dispatchEvent(new CustomEvent('new-site', { detail: { name: h.name, url: h.url, dns: true } }))   // les requêtes Traefik de ce nœud vont déjà au Traefik partagé : pas de changement de nœud
      return
    }
    window.dispatchEvent(new CustomEvent('new-site', { detail: { name: h.name, url: h.url, dns: true } }))
    if (!h.canCreate) toast?.('Vous ne pouvez pas créer de site sur ce nœud', 'err')
    else if (!h.port) toast?.('Ce conteneur n\'expose aucun port : complétez l\'adresse cible (http://nom:port)', 'err')
    else if (t.found && t.reachable === false) toast?.('Traefik et ce conteneur ne partagent aucun réseau : Traefik ne pourra pas l\'atteindre. Rattachez-les à un réseau commun (onglet Docker → Réseaux).', 'err')
  } catch (e) { toast?.(e.message, 'err') }
}
