// Publie une zone chez le fournisseur après une modification. Un échec n'est plus avalé en silence : sans ce rafraîchissement,
// le changement existe dans la zone mais n'est pas encore visible pour le reste d'Internet.
export async function refreshZone(api, toast, zone) {
  if (localStorage.getItem('autoRefresh') === '0') return
  try { await api.post(`/dns/zones/${zone}/refresh`) } catch (e) {
    toast(`Modification enregistrée, mais la zone ${zone} n'a pas pu être rafraîchie (${e.message}). Utilisez « Appliquer » sur la page Domaines.`, 'err')
  }
}
