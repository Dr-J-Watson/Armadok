import { Boxes, DoorOpen, FilePen, FilePlus, FileX, Globe, KeyRound, LogIn, Move, Network, ShieldAlert, Trash2, UserMinus, UserPlus, Users, Workflow, Zap, Cloud, Plug, Pencil, Ban, Bot, BellRing, ShieldCheck, RefreshCw, MapPin } from 'lucide-react'

// Actions du journal d'audit → texte lisible, icône, couleur et catégorie.
const A = (label, icon, tone = '', cat = 'node') => ({ label, icon, tone, cat })
export const ACTIONS = {
  'traefik.site.save': A('Site enregistré', Workflow, 'info', 'traefik'),
  'traefik.site.delete': A('Site supprimé', Trash2, 'err', 'traefik'),
  'traefik.item.save': A('Élément Traefik modifié', Pencil, 'info', 'traefik'),
  'traefik.item.delete': A('Élément Traefik supprimé', Trash2, 'err', 'traefik'),
  'traefik.file.create': A('Fichier créé', FilePlus, 'ok', 'traefik'),
  'traefik.file.save': A('Fichier modifié', FilePen, 'info', 'traefik'),
  'traefik.file.delete': A('Fichier supprimé', FileX, 'err', 'traefik'),
  'traefik.file.move': A('Fichier déplacé', Move, 'info', 'traefik'),
  'dns.create': A('Enregistrement DNS créé', Globe, 'ok', 'dns'),
  'dns.update': A('Enregistrement DNS modifié', Globe, 'info', 'dns'),
  'dns.delete': A('Enregistrement DNS supprimé', Globe, 'err', 'dns'),
  'dns.bulk': A('Enregistrements DNS modifiés', Globe, 'info', 'dns'),
  'dns.refresh': A('Zone DNS appliquée', Globe, 'ok', 'dns'),
  'dns.import': A('Zone DNS importée', Globe, 'warn', 'dns'),
  'dns.restore': A('Zone DNS restaurée', Globe, 'warn', 'dns'),
  'dns.dnssec.enable': A('DNSSEC activé', Globe, 'ok', 'dns'),
  'dns.dnssec.disable': A('DNSSEC désactivé', Globe, 'warn', 'dns'),
  'domain.import': A('Domaine importé', Cloud, 'ok', 'dns'),
  'domain.access': A('Accès à un domaine modifié', Users, 'info', 'access'),
  'domain.remove': A('Domaine retiré', Cloud, 'err', 'dns'),
  'credential.add': A('Compte OVH ajouté', Cloud, 'ok', 'dns'),
  'credential.edit': A('Compte OVH modifié', Cloud, 'info', 'dns'),
  'credential.delete': A('Compte OVH supprimé', Cloud, 'err', 'dns'),
  'node.create': A('Nœud créé', Boxes, 'ok', 'node'),
  'node.update': A('Réglages du nœud modifiés', Pencil, 'info', 'node'),
  'node.delete': A('Nœud supprimé', Trash2, 'err', 'node'),
  'node.transfer': A('Propriété du nœud transférée', Users, 'warn', 'access'),
  'node.member.add': A('Membre ajouté', UserPlus, 'ok', 'access'),
  'node.member.update': A('Droits d\'un membre modifiés', Users, 'info', 'access'),
  'node.member.remove': A('Membre retiré', UserMinus, 'warn', 'access'),
  'node.domain.import': A('Domaine rattaché au nœud', Cloud, 'ok', 'node'),
  'node.domain.unlink': A('Domaine retiré du nœud', Cloud, 'warn', 'node'),
  'node.worker.enroll': A('Worker enrôlé', Plug, 'ok', 'node'),
  'node.worker.connect': A('Worker connecté', Plug, 'ok', 'node'),
  'node.worker.replaced': A('Worker remplacé par une autre connexion', Plug, 'warn', 'node'),
  'node.worker.auth_failed': A('Connexion de worker refusée', Plug, 'err', 'node'),
  'node.worker.token': A('Nouveau jeton de worker', KeyRound, 'warn', 'node'),
  'admin.emergency.start': A('Accès d\'urgence ouvert', ShieldAlert, 'err', 'admin'),
  'admin.emergency.end': A('Accès d\'urgence terminé', DoorOpen, 'warn', 'admin'),
  'admin.node.suspend': A('Nœud suspendu par la plateforme', Zap, 'err', 'admin'),
  'admin.node.resume': A('Nœud repris', Zap, 'ok', 'admin'),
  'admin.node.revoke_worker': A('Worker coupé par la plateforme', Zap, 'err', 'admin'),
  'admin.node.ban_owner': A('Propriétaire banni', Zap, 'err', 'admin'),
  'admin.node.delete': A('Nœud supprimé par la plateforme', Zap, 'err', 'admin'),
  'admin.node.delete_and_ban': A('Nœud supprimé et propriétaire banni', Zap, 'err', 'admin'),
  'admin.node.restore': A('Nœud restauré', Zap, 'ok', 'admin'),
  'admin.node.purge': A('Nœud purgé', Zap, 'err', 'admin'),
  'admin.node.take_ownership': A('Propriété reprise par la plateforme', Zap, 'err', 'admin'),
  'admin.node.remove_quota': A('Droit de créer des nœuds retiré', Zap, 'warn', 'admin'),
  'user.create': A('Utilisateur créé', UserPlus, 'ok', 'access'),
  'user.update': A('Utilisateur modifié', Users, 'info', 'access'),
  'user.delete': A('Utilisateur supprimé', UserMinus, 'err', 'access'),
  'user.password.reset': A('Mot de passe réinitialisé', KeyRound, 'warn', 'access'),
  'user.2fa.reset': A('2FA réinitialisée', KeyRound, 'warn', 'access'),
  'ip.ban': A('Adresse bannie', Ban, 'err', 'security'),
  'ip.ban.edit': A('Durée de bannissement modifiée', Ban, 'info', 'security'),
  'ip.unban': A('Bannissement levé', Ban, 'ok', 'security'),
  'ip.trust': A('Adresse validée', ShieldCheck, 'ok', 'security'),
  'ip.untrust': A('Validation retirée', ShieldCheck, 'warn', 'security'),
  'automod.create': A('Règle d\'automod créée', Bot, 'info', 'security'),
  'automod.update': A('Règle d\'automod modifiée', Bot, 'info', 'security'),
  'automod.delete': A('Règle d\'automod supprimée', Bot, 'err', 'security'),
  'alert.snooze': A('Alerte mise en sourdine', BellRing, 'info', 'security'),
  'alert.rule': A('Alerte réglée', BellRing, 'info', 'security'),
  'alert.firing': A('Alerte déclenchée', BellRing, 'err', 'alerts'),
  'alert.resolved': A('Alerte résolue', BellRing, 'ok', 'alerts'),
  'cert.purge': A('Certificats retirés', RefreshCw, 'warn', 'traefik'),
  'cert.ask': A('Certificat redemandé', RefreshCw, 'info', 'traefik'),
  'geo.update': A('Base de localisation installée', MapPin, 'info', 'security'),
  login: A('Connexion', LogIn, '', 'auth'),
  'login.failed': A('Connexion échouée', LogIn, 'err', 'auth'),
}
export const CATEGORIES = [['all', 'Tout'], ['traefik', 'Sites et fichiers'], ['dns', 'DNS'], ['access', 'Accès'], ['security', 'Sécurité'], ['alerts', 'Alertes'], ['node', 'Nœud'], ['admin', 'Plateforme']]

export function describe(entry) {
  const a = ACTIONS[entry.action]
  if (a) return a
  return { label: entry.action.replace(/[._]/g, ' ').replace(/^./, (c) => c.toUpperCase()), icon: Network, tone: '', cat: entry.action.startsWith('admin') ? 'admin' : 'node' }
}

// « 2026-10-26T18:24:46+0000 » (sans deux-points dans le fuseau) n'est pas lu partout : on le normalise.
export const parseTs = (ts) => new Date(String(ts).replace(/([+-]\d\d)(\d\d)$/, '$1:$2'))

export function dayLabel(d) {
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const day = new Date(d); day.setHours(0, 0, 0, 0)
  const diff = Math.round((today - day) / 86400000)
  if (diff === 0) return 'Aujourd\'hui'
  if (diff === 1) return 'Hier'
  return d.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', year: today.getFullYear() === d.getFullYear() ? undefined : 'numeric' })
}
export const timeLabel = (d) => d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })

// Regroupe des éléments par jour (l'ordre d'entrée est conservé).
export function byDay(items, getDate) {
  const groups = []
  for (const it of items) {
    const d = getDate(it); const key = dayLabel(d)
    if (!groups.length || groups[groups.length - 1].key !== key) groups.push({ key, items: [] })
    groups[groups.length - 1].items.push(it)
  }
  return groups
}

// Où mène une ligne du journal : le site, l'onglet de surveillance, la zone DNS… (null = rien à ouvrir)
export function targetOf(e) {
  const a = e.action || ''; const d = String(e.detail || '')
  if (a === 'traefik.site.save') return `/sites?open=${encodeURIComponent(d.split(' -> ')[0].trim())}`
  if (a === 'traefik.site.delete') return '/sites'
  if (a.startsWith('traefik.file.')) return '/traefik?tab=files'
  if (a === 'traefik.item.save') return '/traefik'
  if (a.startsWith('ip.ban')) return '/monitoring?tab=bans'
  if (a.startsWith('ip.unban')) return '/monitoring?tab=bans'
  if (a.startsWith('ip.trust') || a.startsWith('ip.untrust')) return '/monitoring?tab=trusted'
  if (a.startsWith('automod.')) return '/monitoring?tab=automod'
  if (a.startsWith('alert.')) return '/monitoring?tab=alerts'
  if (a.startsWith('cert.')) return '/traefik?tab=certs'
  if (a.startsWith('dns.')) return '/dns'
  if (a.startsWith('node.member') || a.startsWith('node.update')) return '/node'
  return null
}
