// Raccourcis clavier : une seule liste, utilisée par la fenêtre « ? » et par l'aide.
// keys : touches à presser l'une après l'autre (« g » puis « s ») ou ensemble (« Ctrl »+« K »)
export const SHORTCUTS = [
  { group: 'Aller quelque part', items: [
    { keys: ['Ctrl', 'K'], plus: true, alt: ['/'], desc: 'Rechercher : un site, une page, un domaine, un certificat, ou une adresse IP à chercher dans la surveillance' },
    { keys: ['g', 'a'], desc: 'Accueil' },
    { keys: ['g', 's'], desc: 'Sites' },
    { keys: ['g', 'd'], desc: 'Domaines (DNS)' },
    { keys: ['g', 'm'], desc: 'Surveillance' },
    { keys: ['g', 't'], desc: 'Traefik avancé' },
    { keys: ['g', 'c'], desc: 'Certificats' },
  ] },
  { group: 'Agir', items: [
    { keys: ['n'], desc: 'Nouveau site' },
    { keys: ['Échap'], desc: 'Fermer la fenêtre ou le menu ouvert' },
  ] },
  { group: 'Aide', items: [
    { keys: ['?'], desc: 'Afficher la liste des raccourcis' },
  ] },
]
export const SHORTCUT_NOTE = 'Les raccourcis sont inactifs pendant que vous écrivez dans un champ, et quand une fenêtre est ouverte.'

export function Keys({ item }) {
  const k = (t) => <kbd key={t} className="kbd">{t}</kbd>
  return <span className="keys">{item.keys.map((t, i) => <span key={t + i}>{i > 0 && <span className="muted small" style={{ margin: '0 4px' }}>{item.plus ? '+' : 'puis'}</span>}{k(t)}</span>)}{item.alt && item.alt.map((t) => <span key={t}><span className="muted small" style={{ margin: '0 6px' }}>ou</span>{k(t)}</span>)}</span>
}
