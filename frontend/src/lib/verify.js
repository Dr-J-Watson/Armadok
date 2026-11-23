// Après un enregistrement : demande à Traefik s'il a bien pris en compte le routeur (le serveur signale ses changements d'état en temps réel).
export async function verifyDeployed(api, toast, name, proto = 'http') {
  const key = `${proto}/${name}@file`
  for (let i = 0; i < 6; i++) {
    await new Promise((r) => setTimeout(r, 1500))   // Traefik recharge ses fichiers en moins d'une seconde
    let live
    try { live = await api.get('/traefik/live') } catch { return }
    if (!live?.available) return                                    // pas d'API Traefik configurée : rien à vérifier
    const r = live.routers?.[key]
    if (!r) continue
    if (r.status === 'enabled') toast(`✓ Traefik a rechargé la configuration : « ${name} » est actif`)
    else toast(`Traefik n'active pas « ${name} » : ${r.error || r.status}`, 'err')
    return
  }
}
