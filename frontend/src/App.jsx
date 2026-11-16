import { useEffect, useState } from 'react'
import { NavLink, Route, Routes, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { fmtDate } from './lib/util.js'
import { Activity, ArrowLeft, BellRing, Menu as MenuIcon, X as XIcon, Boxes, Container, FolderGit2, LifeBuoy, KeyRound, Cloud, ShieldAlert, ShieldCheck, Globe, Layers, LayoutDashboard, LogOut, Plus, Search, Server, UserCog, Users, Wrench, Workflow } from 'lucide-react'
import { useAuth } from './auth.jsx'
import { useNode, useLiveInvalidate } from './node.jsx'
import LiveDot from './components/LiveDot.jsx'
import { api, setToken } from './api.js'
import Login from './pages/Login.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Traefik from './pages/Traefik.jsx'
import Sites from './pages/Sites.jsx'
import Middlewares from './pages/Middlewares.jsx'
import Dns from './pages/Dns.jsx'
import Team from './pages/Team.jsx'
import Accounts from './pages/Accounts.jsx'
import Node from './pages/Node.jsx'
import Nodes from './pages/Nodes.jsx'
import Rights from './pages/Rights.jsx'
import Help from './pages/Help.jsx'
import Monitoring from './pages/Monitoring.jsx'
import Docker from './pages/Docker.jsx'
import Projects from './pages/Projects.jsx'
import Project from './pages/Project.jsx'
import { useFeatures } from './lib/features.js'
import NoticeGate from './components/NoticeGate.jsx'
import { Countdown } from './components/Countdown.jsx'
import NodeSwitcher from './components/NodeSwitcher.jsx'
import ThemeMenu, { useThemePrefs } from './components/ThemeMenu.jsx'
import SiteBuilder from './components/SiteBuilder.jsx'
import CommandPalette from './components/CommandPalette.jsx'
import { backLabel } from './lib/nav.js'
import { Keys, SHORTCUTS, SHORTCUT_NOTE } from './lib/shortcuts.jsx'
import { Badge, Btn, Empty, Field, Loading, Modal, useUi, Info } from './components/ui.jsx'

// Titre de l'onglet du navigateur : « Page · Armadok » sur CHAQUE page (une fiche projet pose le sien).
const PAGE_TITLES = { '/': 'Accueil', '/sites': 'Sites', '/middlewares': 'Middlewares', '/projects': 'Projets', '/docker': 'Docker', '/dns': 'Domaines', '/accounts': 'Comptes DNS', '/node': 'Ce nœud', '/rights': 'Droits', '/monitoring': 'Surveillance', '/traefik': 'Traefik avancé', '/team': 'Équipe', '/nodes': 'Tous les nœuds', '/help': 'Aide' }
function useDocTitle(user) {
  const { pathname } = useLocation()
  useEffect(() => {
    if (/^\/projects\/\d+/.test(pathname)) return   // la fiche d'un projet affiche son nom (voir Project.jsx)
    const page = user ? PAGE_TITLES[pathname.replace(/\/+$/, '') || '/'] : 'Connexion'
    document.title = page ? `${page} · Armadok` : 'Armadok'
  }, [pathname, user])
}

function TwoFactor({ user, onChange }) {
  const { toast, confirm } = useUi(); const { reload } = useAuth()
  const [setup, setSetup] = useState(null); const [code, setCode] = useState(''); const [codes, setCodes] = useState(null)
  const [off, setOff] = useState(false); const [pw, setPw] = useState('')
  const start = () => api.post('/auth/2fa/setup').then(setSetup).catch((e) => toast(e.message, 'err'))
  const enable = () => api.post('/auth/2fa/enable', { code }).then((r) => { setCodes(r.recoveryCodes); setSetup(null); setCode(''); reload() }).catch((e) => toast(e.message, 'err'))
  const disable = () => api.post('/auth/2fa/disable', { password: pw, code }).then((d) => { setToken(d.access_token); toast('Double authentification désactivée'); setOff(false); setPw(''); setCode(''); reload() }).catch((e) => toast(e.message, 'err'))
  if (codes) return (
    <div className="col">
      <div className="banner ok" style={{ margin: 0 }}><ShieldCheck size={18} /> Double authentification activée !</div>
      <div><b>Codes de secours</b><div className="muted small">À conserver en lieu sûr : chacun sert une seule fois si vous perdez votre téléphone. Ils ne seront plus affichés.</div></div>
      <div className="rule-preview mono" style={{ columns: 2, lineHeight: 1.9 }}>{codes.map((c) => <div key={c}>{c}</div>)}</div>
      <div className="row"><Btn small onClick={() => { navigator.clipboard?.writeText(codes.join('\n')); toast('Copiés') }}>Copier</Btn>
        <Btn small onClick={() => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([codes.join('\n') + '\n'])); a.download = 'codes-de-secours.txt'; a.click() }}>Télécharger</Btn><Btn small kind="primary" className="right" onClick={() => setCodes(null)}>J'ai noté mes codes</Btn></div>
    </div>)
  if (setup) return (
    <div className="col">
      <div><b>1.</b> Ouvrez votre application d'authentification (Google Authenticator, Authy, Aegis, 2FAS…) et scannez ce code.</div>
      <div className="qr" dangerouslySetInnerHTML={{ __html: setup.qr }} />
      <details className="small"><summary className="muted" style={{ cursor: 'pointer' }}>Impossible de scanner ? Saisir la clé à la main</summary><code style={{ wordBreak: 'break-all' }}>{setup.secret}</code></details>
      <div><b>2.</b> Entrez le code à 6 chiffres affiché pour confirmer.</div>
      <div className="row"><input className="input mono" style={{ letterSpacing: 3, textAlign: 'center', maxWidth: 160 }} value={code} onChange={(e) => setCode(e.target.value)} placeholder="000000" inputMode="numeric" autoComplete="one-time-code" />
        <Btn kind="primary" disabled={code.replace(/\s/g, '').length < 6} onClick={enable}>Activer</Btn><Btn onClick={() => { setSetup(null); setCode('') }}>Annuler</Btn></div>
    </div>)
  if (user.twoFactor) return off ? (
    <div className="col"><div className="muted small">Pour désactiver, confirmez votre mot de passe et un code actuel.</div>
      <input className="input" type="password" placeholder="Mot de passe" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" />
      <input className="input mono" placeholder="Code à 6 chiffres ou code de secours" value={code} onChange={(e) => setCode(e.target.value)} />
      <div className="row"><Btn kind="danger" disabled={!pw || !code} onClick={disable}>Désactiver</Btn><Btn onClick={() => setOff(false)}>Annuler</Btn></div></div>
  ) : (
    <div className="row"><Badge kind="ok"><ShieldCheck size={12} /> Activée</Badge><Btn small kind="ghost" className="right" onClick={() => setOff(true)}>Désactiver</Btn></div>)
  return (
    <div className="col"><div className="muted">Ajoute un code temporaire (application sur votre téléphone) en plus du mot de passe. Facultatif, mais recommandé.</div>
      <div><Btn kind="primary" onClick={start}><ShieldCheck size={14} /> Activer la double authentification</Btn></div></div>)
}

function NoticeHistory() {
  const q = useQuery({ queryKey: ['notices-all'], queryFn: () => api.get('/notices?all=true') })
  if (!q.data?.length) return null
  return (
    <details className="col"><summary style={{ cursor: 'pointer' }}><h3 style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}><BellRing size={13} /> Avis de l'administrateur ({q.data.length})</h3></summary>
      <div className="col" style={{ marginTop: 10 }}>{q.data.map((n) => (
        <div key={n.id} className="notice"><div className="row"><b className="grow">{n.nodeName}</b><span className="muted small">{fmtDate(n.createdAt * 1000)}</span></div><div>{n.message}</div>
          {n.actions.length > 0 && <div className="muted small">{n.actions.length} action{n.actions.length > 1 ? 's' : ''} pendant l'intervention : {n.actions.map((a) => a.action.replace(/^traefik\./, '')).join(', ')}</div>}</div>))}</div></details>)
}

// Ce nœud exige la double authentification pour certains droits : la personne l'apprend et peut l'activer d'un clic.
function MfaBanner({ caps, node, onActivate }) {
  const cat = useQuery({ queryKey: ['rights-catalog'], queryFn: () => api.get('/rights/catalog'), staleTime: 3600000 })
  const label = Object.fromEntries((cat.data?.groups || []).flatMap((g) => g.caps.map((c) => [c.key, c.label])))
  const names = caps.map((k) => label[k] || k)
  return <div className="banner info"><ShieldCheck size={18} /><span className="grow">« {node.name} » exige la double authentification pour : <b>{names.slice(0, 4).join(', ')}{names.length > 4 ? ` et ${names.length - 4} autre${names.length - 4 > 1 ? 's' : ''}` : ''}</b>. Activez-la pour retrouver ces droits.</span><Btn small kind="primary" onClick={onActivate}>Activer la 2FA</Btn></div>
}

function ProfileModal({ user, onClose }) {
  const { toast } = useUi(); const [cur, setCur] = useState(''); const [pw, setPw] = useState('')
  const go = () => api.post('/auth/password', { current: cur, new: pw }).then((d) => { setToken(d.access_token); toast('Mot de passe changé : vos autres appareils sont déconnectés'); setCur(''); setPw('') }).catch((e) => toast(e.message, 'err'))
  return (
    <Modal title={`Mon compte · ${user.username}`} onClose={onClose}>
      <div className="col gap-lg">
        <div className="col"><div className="row"><h3>Double authentification</h3><Info>{'En plus du mot de passe, un code à 6 chiffres généré par une application (Google Authenticator, Authy, Aegis…). Facultative, mais conseillée : sans elle, un mot de passe volé suffit.\nVous recevez aussi des codes de secours à garder en lieu sûr.'}</Info></div><TwoFactor user={user} /></div>
        <NoticeHistory />
        <div className="col"><h3>Mot de passe</h3>
          {user.source === 'env' ? <div className="muted">Le mot de passe du compte propriétaire se change dans le fichier <code>.env</code> (ADMIN_PASSWORD_HASH).</div> : (<>
            <Field label="Mot de passe actuel"><input className="input" type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" /></Field>
            <Field label="Nouveau mot de passe" help="12 caractères minimum (72 octets au plus), différent de votre nom d'utilisateur"><input className="input" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" /></Field>
            <div><Btn kind="primary" disabled={!cur || pw.length < 12} onClick={go}>Changer le mot de passe</Btn></div></>)}
        </div>
        <div className="col"><h3>Sessions</h3><div className="muted small">Déconnecte tous vos autres appareils (téléphone, autre navigateur). Cet appareil reste connecté.</div>
          <div><Btn onClick={() => api.post('/auth/logout-all').then((d) => { setToken(d.access_token); toast('Autres appareils déconnectés') }).catch((e) => toast(e.message, 'err'))}>Me déconnecter partout ailleurs</Btn></div></div>
      </div>
    </Modal>
  )
}

// Sur téléphone, un tableau large devient une pile de fiches : chaque cellule reprend le titre de sa colonne (data-label).
function useStackTables() {
  useEffect(() => {
    let raf = 0
    const run = () => {
      raf = 0
      document.querySelectorAll('table.t').forEach((t) => {
        const ths = [...t.querySelectorAll('thead th')]
        if (ths.length < 4) { t.classList.remove('m-stack'); return }
        t.classList.add('m-stack')
        const labels = ths.map((th) => (th.innerText || '').replace(/\s*\?\s*$/, '').trim())
        t.querySelectorAll('tbody tr').forEach((tr) => { let i = 0; for (const td of tr.children) { if (td.tagName === 'TD' && labels[i] && td.dataset.label !== labels[i]) td.dataset.label = labels[i]; i += td.colSpan || 1 } })
      })
    }
    const mo = new MutationObserver(() => { if (!raf) raf = requestAnimationFrame(run) })
    mo.observe(document.body, { childList: true, subtree: true }); run()
    return () => { mo.disconnect(); if (raf) cancelAnimationFrame(raf) }
  }, [])
}

// « ← Retour » : affiché quand on arrive d'un lien interne (accueil → site, alerte → adresse…)
function BackBar() {
  const loc = useLocation(); const nav = useNavigate(); const back = loc.state?.back
  if (!back) return null
  return <div className="backbar"><button type="button" className="btn sm" onClick={() => nav(back)}><ArrowLeft size={14} /> Retour {backLabel(back)}</button></div>
}

function ShortcutsModal({ onClose }) {
  const nav = useNavigate()
  return <Modal small title="Raccourcis clavier" onClose={onClose} footer={<><Btn onClick={() => { onClose(); nav('/help?tab=keys') }}>Voir dans l'aide</Btn><Btn kind="primary" onClick={onClose}>Fermer</Btn></>}>
    <div className="col gap-lg">{SHORTCUTS.map((g) => <div key={g.group} className="col" style={{ gap: 6 }}><h3>{g.group}</h3>{g.items.map((it) => <div key={it.desc} className="row" style={{ gap: 12, alignItems: 'flex-start' }}><span style={{ minWidth: 120 }}><Keys item={it} /></span><span>{it.desc}</span></div>)}</div>)}
      <div className="muted small">{SHORTCUT_NOTE}</div></div></Modal>
}

// Nœud distant dont le worker n'est pas (ou plus) connecté : rien de Traefik n'est lisible, on explique quoi faire.
function NodeOffline({ node, canManage }) {
  const pending = node.connection === 'pending'
  return (
    <div className="card col gap-lg" style={{ maxWidth: 640 }}>
      <div className="row"><Server size={20} /><h2>{pending ? `Le worker de « ${node.name} » n'est pas encore installé` : `« ${node.name} » est hors ligne`}</h2></div>
      <div className="muted">{pending ? 'Ce nœud est créé, mais aucun worker ne s\'y est encore connecté : les sites et les fichiers Traefik de cette machine apparaîtront dès que le worker sera installé.' : 'Son worker ne répond plus : la machine est peut-être éteinte, ou sans accès Internet. Ses données reviendront dès qu\'il se reconnecte.'}</div>
      {canManage ? <div><NavLink to="/node" className="btn primary">{pending ? 'Installer le worker' : 'Voir la connexion'}</NavLink></div> : <div className="muted">Demandez au propriétaire du nœud de terminer son installation.</div>}
    </div>
  )
}

export default function App() {
  const { user, ready, logout } = useAuth(); const { node, nodes, setNode, reload, hasTraefik, hasModule, hasDocker, canManage: canManageAll, canNode, canRights, can } = useNode(); const { toast } = useUi()
  const tf = hasModule('traefik'); const dk = hasDocker && can('docker.view'); const pj = dk && can('projects.view'); const canManage = canManageAll && tf   // un nœud sans Traefik n'a ni Traefik avancé, ni middlewares, ni surveillance du trafic
  const [theme, setTheme] = useThemePrefs(); const feats = useFeatures()
  const [site, setSite] = useState(null) // { item?, prefill? }
  const [palette, setPalette] = useState(false); const [profile, setProfile] = useState(false); const [keys, setKeys] = useState(false); const nav = useNavigate(); const loc = useLocation(); const [sheet, setSheet] = useState(false)
  const meta = useQuery({ queryKey: ['meta'], queryFn: () => api.get('/meta'), enabled: !!user, staleTime: 60000 })
  useLiveInvalidate()

  useEffect(() => {
    const open = () => setProfile(true)
    window.addEventListener('open-profile', open); return () => window.removeEventListener('open-profile', open)
  }, [])
  useEffect(() => {
    let chord = 0
    const h = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette(true); return }
      const t = e.target; const typing = t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))
      if (typing || e.ctrlKey || e.metaKey || e.altKey || document.querySelector('.overlay')) return
      const k = e.key
      if (chord && Date.now() - chord < 1500) {
        chord = 0
        const to = { a: '/', s: '/sites', d: '/dns', m: '/monitoring', t: '/traefik', c: '/traefik?tab=certs' }[k]
        if (to) { e.preventDefault(); nav(to) }
        return
      }
      if (k === 'g') chord = Date.now()
      else if (k === '/') { e.preventDefault(); setPalette(true) }
      else if (k === '?') setKeys(true)
      else if (k === 'n') window.dispatchEvent(new Event('new-site'))
    }
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h)
  }, [nav])
  useEffect(() => { const h = (e) => can('sites.create') && setSite({ prefill: e?.detail || {} }); window.addEventListener('new-site', h); return () => window.removeEventListener('new-site', h) }, [can])
  useEffect(() => { setSheet(false) }, [loc.pathname, loc.search])
  useStackTables()
  const certsQ = useQuery({ queryKey: ['certs'], queryFn: () => api.get('/traefik/certs'), enabled: !!user && !!hasTraefik && can('certs.view'), staleTime: 60000, retry: false })
  const certsBad = certsQ.data?.configured ? certsQ.data.sites.filter((x) => ['expired', 'expiring', 'missing'].includes(x.worst)).length : 0

  useDocTitle(user)
  if (!ready) return <Loading />
  if (!user) return <Login />
  const admin = user.role === 'admin'
  const suspended = node?.status === 'suspended' && !node.emergency
  const offline = node?.kind === 'remote' && node.connection !== 'online'
  const endEmergency = () => api.post(`/admin/nodes/${node.id}/emergency/end`).then(() => { toast('Accès d\'urgence terminé'); setNode(nodes.find((n) => n.id !== node.id && !n.emergency)?.id); reload() }).catch((e) => toast(e.message, 'err'))
  const openWizard = (prefill) => setSite({ prefill })
  const openSite = (item) => setSite({ item })
  const link = (to, icon, label, hint, dot) => <NavLink to={to} end={to === '/'} data-tip={hint} className={({ isActive }) => 'nav ' + (isActive ? 'active' : '')}>{icon}<span className="lbl">{label}</span>{dot ? <span className="nav-dot warn" title={dot} /> : null}</NavLink>
  return (
    <div className="shell">
      <aside className={'side ' + (sheet ? 'open' : '')}>
        <div className="brand"><div className="logo"><b style={{ fontSize: 17, lineHeight: 1 }}>A</b></div>Armadok</div>
        <NodeSwitcher />
        <button className="nav" data-tip="Aller directement à un site, un domaine ou une page (raccourci : Ctrl K)." onClick={() => setPalette(true)}><Search size={17} /><span className="lbl">Rechercher</span><span className="kbd" style={{ marginLeft: 'auto' }}>Ctrl K</span></button>
        {tf && can('sites.create') && <button className="nav" data-tip="Assistant : adresse du site, protections, application de destination, et l'enregistrement DNS si besoin." onClick={() => openWizard({})} style={{ color: 'var(--accent)' }}><Plus size={17} /><span className="lbl">Nouveau site</span></button>}
        <div className="sep" />
        {link('/', <LayoutDashboard size={17} />, 'Accueil', 'Vue d\'ensemble : vos sites, vos domaines, les problèmes à régler et l\'activité récente.')}
        {(hasTraefik || (tf && can('middlewares.view'))) && <div className="side-g">Sites</div>}
        {hasTraefik && link('/sites', <Workflow size={17} />, 'Sites', 'Vos sites : chacun relie une adresse, des protections et l\'application qui répond.')}
        {tf && can('middlewares.view') && link('/middlewares', <Layers size={17} />, 'Middlewares', 'Les protections et transformations appliquées avant d\'envoyer la requête à l\'application : créez-les, coupez-les, voyez qui les utilise.')}
        {dk && <div className="side-g">Docker</div>}
        {pj && link('/projects', <FolderGit2 size={17} />, 'Projets', 'Vos dossiers docker compose : état, services, sites et visiteurs de chacun, fichiers et déploiement.')}
        {dk && link('/docker', <Container size={17} />, 'Docker', 'Les conteneurs, images, réseaux et volumes de ce nœud, avec les règles Traefik et les visiteurs de chacun.')}
        <div className="side-g">DNS</div>
        {link('/dns', <Globe size={17} />, 'Domaines', 'Vos domaines et leur DNS (OVH, DuckDNS ou DNS local) : enregistrements, adresse, partage d\'accès, aide certificats.')}
        {(user.canAddDomains || can('node.domains.link')) && link('/accounts', <Cloud size={17} />, 'Comptes DNS', 'Vos fournisseurs DNS (OVH, DuckDNS) et vos domaines en DNS local : où se trouve le DNS de chaque domaine.')}
        {(canNode || canRights || canManage || (feats && (feats.metrics.enabled || feats.alerts.enabled || feats.ips.enabled || feats.bans.enabled))) && <div className="side-g">Gestion du nœud</div>}
        {canNode && link('/node', <Server size={17} />, 'Ce nœud', 'Réglages de cette instance Traefik : nom, adresses par défaut, membres et leurs droits, connexion.')}
        {canRights && link('/rights', <KeyRound size={17} />, 'Droits', 'Rôles, groupes et attributions : qui peut faire quoi sur vos nœuds, pour une durée limitée si besoin.')}
        {tf && feats && ((feats.metrics.enabled && feats.metrics.allowed) || (feats.alerts.enabled && feats.alerts.allowed) || (feats.ips.enabled && feats.ips.allowed) || (feats.bans.enabled && feats.bans.allowed)) && (
          <NavLink to="/monitoring" data-tip="Trafic, erreurs, alertes, adresses IP qui se connectent et bannissements. Les parties non branchées (Prometheus, journal d'accès) sont masquées : l'aide explique comment les activer." className={({ isActive }) => 'nav ' + (isActive ? 'active' : '')}><Activity size={17} /><span className="lbl">Surveillance</span>{feats.firing > 0 && <span className="badge err" style={{ marginLeft: 'auto' }}>{feats.firing}</span>}</NavLink>)}
        {canManage && link(certsBad ? '/traefik?tab=certs' : '/traefik', <Wrench size={17} />, 'Traefik avancé', 'Édition complète de la configuration : routeurs, services, middlewares, TLS et fichiers.', certsBad ? `${certsBad} site${certsBad > 1 ? 's' : ''} avec un certificat à régler` : null)}
        {admin && <div className="side-g">Plateforme</div>}
        {admin && link('/team', <Users size={17} />, 'Équipe', 'Les comptes de cette interface : rôles, quota de nœuds, double authentification.')}
        {admin && link('/nodes', <Boxes size={17} />, 'Tous les nœuds', 'Vue super-admin de tous les nœuds : accès d\'urgence (tracé et notifié) et actions rapides.')}
        <div style={{ flex: 1 }} />
        <ThemeMenu prefs={theme} set={setTheme} />
        {link('/help', <LifeBuoy size={17} />, 'Aide', 'Pense-bête par catégorie : certificats, site local, DNS, Docker, sécurité… ce qu\'il faut régler pour activer chaque fonctionnalité.')}
        <button className="nav" onClick={() => setProfile(true)}><UserCog size={17} /><span className="lbl">{user.username}</span></button>
        <button className="nav" onClick={logout}><LogOut size={17} /><span className="lbl">Déconnexion</span></button>
        <LiveDot />
        {meta.data?.version && <div className="muted small lbl" style={{ padding: '6px 10px' }}>{/^v?\d/.test(meta.data.version) ? 'v' + meta.data.version.replace(/^v/, '') : meta.data.version}</div>}
      </aside>
      <nav className="mobilebar" aria-label="Navigation">
        <NavLink to="/" end className={({ isActive }) => 'mb ' + (isActive ? 'active' : '')}><LayoutDashboard size={20} /><span>Accueil</span></NavLink>
        {hasTraefik && <NavLink to="/sites" className={({ isActive }) => 'mb ' + (isActive ? 'active' : '')}><Workflow size={20} /><span>Sites</span></NavLink>}
        <NavLink to="/dns" className={({ isActive }) => 'mb ' + (isActive ? 'active' : '')}><Globe size={20} /><span>Domaines</span></NavLink>
        {tf && feats && ((feats.metrics.enabled && feats.metrics.allowed) || (feats.alerts.enabled && feats.alerts.allowed) || (feats.ips.enabled && feats.ips.allowed) || (feats.bans.enabled && feats.bans.allowed)) && <NavLink to="/monitoring" className={({ isActive }) => 'mb ' + (isActive ? 'active' : '')}><Activity size={20} /><span>Surveillance</span>{feats.firing > 0 && <b className="mb-dot">{feats.firing}</b>}</NavLink>}
        <button type="button" className={'mb ' + (sheet ? 'active' : '')} aria-expanded={sheet} onClick={() => setSheet((v) => !v)}>{sheet ? <XIcon size={20} /> : <MenuIcon size={20} />}<span>{sheet ? 'Fermer' : 'Menu'}</span></button>
      </nav>
      <main className="main">
        <BackBar />
        {node?.mfaBlocked?.length > 0 && !user.twoFactor && <MfaBanner caps={node.mfaBlocked} node={node} onActivate={() => setProfile(true)} />}
        {node?.emergency && <div className="banner err"><ShieldAlert size={18} /><span className="grow">Accès d'urgence au nœud « {node.name} » — expire dans <Countdown until={node.emergency.expires} onEnd={reload} />. Le propriétaire en est informé.</span><Btn small onClick={endEmergency}>Terminer</Btn></div>}
        {node?.kind === 'remote' && node.connection !== 'online' && <div className="banner"><Server size={18} /><span className="grow">{node.connection === 'pending' ? `Le worker de « ${node.name} » n'est pas encore installé.` : `« ${node.name} » est hors ligne : son worker ne répond plus.`}</span>{canManage && <NavLink to="/node" className="btn sm">Voir la connexion</NavLink>}</div>}
        {suspended ? <div className="card"><Empty><ShieldAlert size={30} /><div><b>« {node.name} » est suspendu</b><br />L'administrateur de la plateforme a fermé ce nœud. Contactez-le pour en savoir plus ; vous pouvez changer de nœud dans le menu.</div></Empty></div> : <Routes>
          <Route path="/" element={<Dashboard openWizard={openWizard} />} />
          <Route path="/sites" element={hasTraefik ? (offline ? <NodeOffline node={node} canManage={canManage} /> : <Sites openSite={openSite} openWizard={openWizard} />) : <Navigate to="/" />} />
          <Route path="/dns" element={<Dns openWizard={openWizard} />} />
          <Route path="/rights" element={canRights ? <Rights /> : <Navigate to="/" />} />
          <Route path="/accounts" element={user.canAddDomains || can('node.domains.link') ? <Accounts /> : <Navigate to="/" />} />
          <Route path="/team" element={admin ? <Team /> : <Navigate to="/" />} />
          <Route path="/middlewares" element={tf && can('middlewares.view') ? (offline ? <NodeOffline node={node} canManage={canManage} /> : <Middlewares />) : <Navigate to="/" />} />
          <Route path="/traefik" element={canManage ? (offline ? <NodeOffline node={node} canManage={canManage} /> : <Traefik />) : <Navigate to="/" />} />
          <Route path="/node" element={canNode ? <Node /> : <Navigate to="/" />} />
          <Route path="/nodes" element={admin ? <Nodes /> : <Navigate to="/" />} />
          <Route path="/help" element={<Help />} />
          <Route path="/builder" element={<Navigate to="/help?tab=builder" replace />} />
          <Route path="/docker" element={dk ? <Docker /> : <Navigate to="/" />} />
          <Route path="/projects" element={pj ? <Projects /> : <Navigate to="/" />} />
          <Route path="/projects/:id" element={pj ? <Project /> : <Navigate to="/" />} />
          <Route path="/monitoring" element={tf ? <Monitoring /> : <Navigate to="/" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>}
      </main>
      {site && <SiteBuilder key={(site.item?.id || 'new') + ':' + (site.rev || 0)} item={site.item} prefill={site.prefill} onClose={() => setSite(null)} onReload={(it) => setSite({ item: it, rev: (site.rev || 0) + 1 })} />}
      {palette && <CommandPalette onClose={() => setPalette(false)} openWizard={openWizard} />}
      {profile && <ProfileModal user={user} onClose={() => setProfile(false)} />}
      {keys && <ShortcutsModal onClose={() => setKeys(false)} />}
      <NoticeGate />
    </div>
  )
}
