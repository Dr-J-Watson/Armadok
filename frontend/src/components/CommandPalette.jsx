import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Activity, Boxes, Cloud, Container, FolderGit2, Search as SearchIcon, CornerDownLeft, FileCode2, Globe, Layers, LayoutDashboard, Lock, Plus, Route, Server, ShieldCheck, Users, Workflow, Wrench } from 'lucide-react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { api } from '../api.js'
import { useFeatures } from '../lib/features.js'
import { Modal } from './ui.jsx'
import { useOverview } from '../pages/Traefik.jsx'
import { useSwitchNode } from '../lib/nav.js'
import { useAuth } from '../auth.jsx'
import { useNode } from '../node.jsx'
import { extractHosts } from '../lib/util.js'
import { techName } from '../traefik/human.js'

const PER_GROUP = 6          // sans recherche, pour que la liste reste lisible
const PER_GROUP_SEARCH = 10

export default function CommandPalette({ onClose, openWizard }) {
  const nav = useNavigate(); const ov = useOverview(); const { user } = useAuth(); const { hasTraefik, hasDocker, canManage, canNode, can } = useNode(); const sw = useSwitchNode()
  const [q, setQ] = useState(''); const [i, setI] = useState(0); const feats = useFeatures()
  const certs = useQuery({ queryKey: ['certs'], queryFn: () => api.get('/traefik/certs'), enabled: hasTraefik && can('certs.view'), staleTime: 60000, retry: false })
  const zones = useQuery({ queryKey: ['zones'], queryFn: () => api.get('/dns/zones'), staleTime: 60000, retry: false })
  const listRef = useRef(null)
  // Les enregistrements DNS (sous-domaines) de chaque domaine : chargés seulement quand on tape une recherche (même cache que la page Domaines).
  const searching = q.trim().length >= 2
  const recQs = useQueries({ queries: (zones.data || []).filter((z) => z.capabilities?.records !== false).slice(0, 12).map((z) => ({ queryKey: ['records', z.name], queryFn: () => api.get(`/dns/zones/${z.name}/records`), enabled: searching, staleTime: 60000, retry: false })) })
  const recKey = recQs.map((r) => r.dataUpdatedAt).join()
  const recEntries = useMemo(() => {
    const out = []
    ;(zones.data || []).filter((z) => z.capabilities?.records !== false).slice(0, 12).forEach((z, k) => {
      for (const r of recQs[k]?.data || []) {
        const fq = r.subDomain ? `${r.subDomain}.${z.name}` : z.name
        out.push({ group: 'Enregistrements DNS', label: fq, sub: `${r.fieldType} → ${r.target}`, Icon: Globe, run: () => nav(`/dns?zone=${encodeURIComponent(z.name)}&q=${encodeURIComponent(r.subDomain || r.target)}`), extra: '' })
      }
    })
    return out
  }, [recKey, zones.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const entries = useMemo(() => {
    const e = []
    const add = (group, label, sub, Icon, run, extra = '') => e.push({ group, label, sub, Icon, run, extra })
    if (can('sites.create')) add('Actions', 'Nouveau site', 'Adresse, protections et destination, pas à pas', Plus, () => openWizard({}))
    sw.nodes.forEach((n, i) => { if (n.id !== sw.node?.id) add('Nœuds', `Aller au nœud « ${n.name} »`, `${i < 9 ? `g puis ${i + 1} · ` : ''}on reste sur la même page`, Server, () => sw.to(n.id), 'noeud changer basculer') })
    add('Pages', 'Vue d\'ensemble', 'Accueil : chiffres clés et activité', LayoutDashboard, () => nav('/'))
    if (hasTraefik) add('Pages', 'Sites', 'Tous vos sites et leurs protections', Workflow, () => nav('/sites'))
    if (hasDocker && can('projects.view')) add('Pages', 'Projets', 'Dossiers docker compose : état, sites, visiteurs', FolderGit2, () => nav('/projects'))
    if (hasDocker && can('docker.view')) add('Pages', 'Docker', 'Conteneurs, images, réseaux, volumes', Container, () => nav('/docker'))
    add('Pages', 'Domaines (DNS)', 'Enregistrements chez OVH', Globe, () => nav('/dns'))
    if (user.canAddDomains || can('node.domains.link')) add('Pages', 'Comptes DNS', 'OVH, DuckDNS, domaines en DNS local', Cloud, () => nav('/accounts'))
    if (user.role === 'admin') add('Pages', 'Équipe', 'Comptes, rôles, double authentification', Users, () => nav('/team'))
    if (can('middlewares.view')) add('Pages', 'Middlewares', 'Protections : créer, couper, voir qui les utilise', Layers, () => nav('/middlewares'))
    if (canNode) add('Pages', 'Réglages du nœud', 'Nom, adresses par défaut, membres', Server, () => nav('/node'))
    if (canManage) add('Pages', 'Traefik avancé', 'Routeurs, services, middlewares, TLS', Wrench, () => nav('/traefik'))
    if (can('files.view')) add('Pages', 'Fichiers Traefik', 'Éditeur, dossiers, historique', FileCode2, () => nav('/traefik?tab=files'))
    if (user.role === 'admin') add('Pages', 'Tous les nœuds', 'Vue super-admin, accès d\'urgence', Boxes, () => nav('/nodes'))
    for (const it of ov.data?.items || []) {
      const site = it.proto === 'http' && it.kind === 'routers'
      if (!site && !canManage) continue
      const off = it.disabled ? ' · désactivé' : ''
      const rule = it.spec?.rule || ''
      const open = site ? () => nav(`/sites?open=${encodeURIComponent(it.name)}`) : () => nav(`/traefik?edit=${encodeURIComponent(it.id)}`)
      if (site) add('Sites', it.name, (extractHosts(rule).join(', ') || rule) + off, Route, open, rule)
      else if (it.kind === 'routers') add('Routeurs TCP / UDP', it.name, `${it.proto.toUpperCase()} · ${rule}${off}`, Route, open, rule)
      else if (it.kind === 'services') {
        const lb = it.spec?.loadBalancer?.servers?.map((s) => s.url || s.address).filter(Boolean).join(', ')
        add('Services', it.name, (lb || `${it.proto.toUpperCase()} · ${it.type || 'service'}`) + off, Server, open)
      } else if (it.kind === 'middlewares') add('Protections (middlewares)', it.name, techName(it.type) + off, ShieldCheck, open)
      else add('Autres éléments', it.name, `${it.proto} ${it.kind}${off}`, it.proto === 'tls' ? Lock : Layers, open)
    }
    for (const z of zones.data || []) add('Domaines (DNS)', z.name, 'Enregistrements de ce domaine', Globe, () => nav(`/dns?zone=${encodeURIComponent(z.name)}`))
    for (const c of certs.data?.certs || []) add('Certificats', c.main, `${c.issuer || '—'} · ${c.daysLeft < 0 ? 'expiré' : c.daysLeft + ' j'}${c.usedBy?.length ? ' · ' + c.usedBy.slice(0, 2).join(', ') : ''}`, Lock, () => nav(`/traefik?tab=certs&host=${encodeURIComponent(c.main)}`), (c.names || []).join(' '))
    return e
  }, [ov.data, user, canManage, hasTraefik, hasDocker, certs.data, zones.data, sw.nodes, sw.node?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // regroupe en gardant l'ordre ; `flat` est la liste dans l'ordre d'affichage (pour les flèches du clavier)
  const { groups, flat } = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const extra = []
    const ipsOk = feats?.ips.enabled && feats.ips.allowed
    if (needle && ipsOk && feats.ips.reveal && /^[0-9a-f:.]{3,45}$/i.test(needle) && /[.:]/.test(needle)) extra.push({ group: 'Surveillance', label: `Chercher l'adresse ${q.trim()}`, sub: 'Dans les adresses IP vues par Traefik', Icon: Activity, run: () => nav(`/monitoring?tab=ips&s=${encodeURIComponent(q.trim())}`), extra: '' })
    if (needle.length >= 3 && ipsOk) extra.push({ group: 'Surveillance', label: `Chercher « ${q.trim()} » dans les requêtes`, sub: 'Adresse, site ou chemin demandé', Icon: SearchIcon, run: () => nav(`/monitoring?tab=ips&s=${encodeURIComponent(q.trim())}`), extra: '' })
    const hit = (x) => !needle || `${x.label} ${x.sub} ${x.group} ${x.extra}`.toLowerCase().includes(needle)
    const match = [...entries.filter(hit), ...(needle.length >= 2 ? recEntries.filter(hit) : []), ...extra]
    const order = []
    for (const x of match) if (!order.includes(x.group)) order.push(x.group)
    const groups = order.map((name) => {
      const all = match.filter((x) => x.group === name)
      return { name, total: all.length, items: all.slice(0, needle ? PER_GROUP_SEARCH : PER_GROUP) }
    })
    return { groups, flat: groups.flatMap((g) => g.items) }
  }, [entries, recEntries, q, feats])

  useEffect(() => { listRef.current?.querySelector('.pal-row.sel')?.scrollIntoView({ block: 'nearest' }) }, [i, q])
  const go = (e) => { e.run(); onClose() }
  const move = (d) => setI((x) => (flat.length ? (x + d + flat.length) % flat.length : 0))

  let n = -1
  return (
    <Modal small title="Aller à…" onClose={onClose}>
      <input className="input" autoFocus value={q} placeholder="Un site, un domaine, un sous-domaine, une page…" onChange={(e) => { setQ(e.target.value); setI(0) }}
        onKeyDown={(e) => { if (e.key === 'ArrowDown') { e.preventDefault(); move(1) } else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1) } else if (e.key === 'Enter' && flat[i]) go(flat[i]) }} />
      <div className="pal-list" ref={listRef}>
        {flat.length === 0 && <div className="empty">Rien ne correspond à « {q} ».</div>}
        {groups.map((g) => (
          <div key={g.name}>
            <div className="pal-group">{g.name}<span>{g.total > g.items.length ? `${g.items.length} sur ${g.total}` : g.total}</span></div>
            {g.items.map((e) => {
              const k = ++n
              return (
                <div key={g.name + e.label + k} className={'pal-row ' + (k === i ? 'sel' : '')} onMouseEnter={() => setI(k)} onClick={() => go(e)}>
                  <span className="pal-ic"><e.Icon size={15} /></span>
                  <span className="pal-main"><b>{e.label}</b><span className="pal-sub">{e.sub}</span></span>
                  {k === i && <span className="kbd"><CornerDownLeft size={11} /></span>}
                </div>
              )
            })}
          </div>
        ))}
      </div>
      <div className="pal-foot"><span><span className="kbd">↑</span> <span className="kbd">↓</span> naviguer</span><span><span className="kbd">Entrée</span> ouvrir</span><span><span className="kbd">Échap</span> fermer</span></div>
    </Modal>
  )
}
