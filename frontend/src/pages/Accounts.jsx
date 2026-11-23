import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cloud, Download, Home, Pencil, Plug, Plus, Share2, Shield, Trash2, Users } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Empty, Field, Loading, Modal, useUi } from '../components/ui.jsx'
import ShareModal from '../components/ShareModal.jsx'
import { useAuth } from '../auth.jsx'
import { useNode } from '../node.jsx'
import { useOverview } from './Traefik.jsx'
import { extractHosts } from '../lib/util.js'
import { useGo } from '../lib/nav.js'

const LOCAL_SRV = ['adguard', 'pihole']   // serveurs DNS locaux pilotés par leur API
const SRV = {
  adguard: { name: 'AdGuard Home', user: true, url: 'http://192.168.1.5:3000', urlHelp: 'Ex. http://192.168.1.5:3000 ou http://adguard:3000', intro: <>Les noms de vos sites locaux sont créés comme des <b>réécritures DNS</b> d\'AdGuard Home (nom → adresse). Rien n\'est publié sur Internet. Ce serveur doit pouvoir joindre AdGuard (même réseau Docker, ou son adresse sur le réseau local). L\'identifiant et le mot de passe sont chiffrés et jamais renvoyés au navigateur.</> },
  pihole: { name: 'Pi-hole', user: false, url: 'http://192.168.1.2', urlHelp: 'Ex. http://192.168.1.2 ou http://pihole (version 6 uniquement)', intro: <>Les noms de vos sites locaux sont créés dans le <b>DNS local</b> et les <b>CNAME</b> de Pi-hole 6 (nom → adresse). Rien n\'est publié sur Internet. Pas de jokers (*). Si la double authentification est activée sur Pi-hole, utilisez un <b>mot de passe d\'application</b> (Réglages → Web interface / API). Ce serveur doit pouvoir joindre Pi-hole ; le mot de passe est chiffré et jamais renvoyé au navigateur.</> },
}
const ENDPOINTS = [['ovh-eu', 'OVH Europe'], ['ovh-ca', 'OVH Canada'], ['ovh-us', 'OVH US'], ['kimsufi-eu', 'Kimsufi EU'], ['soyoustart-eu', 'So you Start EU']]

// Ce que chaque fournisseur permet : l'interface s'adapte (enregistrements libres, une seule adresse, ou rien à gérer).
// DuckDNS n'offre aucun moyen de lister les noms d'un compte à partir du jeton : on les lit dans ce que vous collez.
// Si le texte contient des « xxx.duckdns.org » (copié depuis la page de votre compte), seuls ceux-là sont gardés, sans le bruit autour.
const duckNames = (raw) => {
  const full = [...new Set((raw.match(/[a-z0-9][a-z0-9-]*\.duckdns\.org/gi) || []).map((x) => x.toLowerCase()))]
  return full.length ? full : raw.split(/[\s,;]+/).filter(Boolean)
}
const DUCK_HELP = 'Un par ligne, ou collez la liste copiée depuis la page de votre compte sur duckdns.org : les noms xxx.duckdns.org sont repérés tout seuls.'

const PROVIDERS = [
  { id: 'ovh', label: 'OVH', icon: Cloud, needsKey: true, text: 'Zone DNS complète : créez, modifiez et supprimez les enregistrements depuis ici. Nécessite une clé API OVH.' },
  { id: 'duckdns', label: 'DuckDNS', icon: Cloud, needsKey: true, text: 'Noms gratuits en xxx.duckdns.org : une adresse IP par nom, tous les sous-domaines la suivent. Nécessite votre jeton DuckDNS.' },
  { id: 'adguard', label: 'AdGuard Home', icon: Shield, needsKey: true, text: 'Votre serveur DNS local : les noms de vos sites locaux y sont créés (A, AAAA, CNAME) vers l\'adresse de votre choix, sans rien publier. Nécessite son adresse et un identifiant.' },
  { id: 'pihole', label: 'Pi-hole', icon: Shield, needsKey: true, text: 'Votre Pi-hole (version 6) : les noms de vos sites locaux y sont créés (DNS local et CNAME), sans rien publier. Nécessite son adresse et son mot de passe. Pas de jokers.' },
  { id: 'local', label: 'DNS local', icon: Home, needsKey: false, text: 'Votre domaine est résolu par votre propre DNS (Pi-hole, AdGuard, routeur…). Aucun compte, aucune clé : rien n\'est publié.' },
]

export default function Accounts() {
  const { toast, confirm } = useUi(); const qc = useQueryClient(); const { user } = useAuth(); const { can, node } = useNode(); const canManage = can('node.domains.link')
  const creds = useQuery({ queryKey: ['credentials', node?.id], queryFn: () => api.get('/credentials') })   // seulement ce qui est rattaché à CE nœud
  // Ce que je gère déjà ailleurs et que je peux rattacher ici (le serveur refuse sans le droit « lier des domaines » ; on ne demande donc que si on l'a)
  const canImport = !!node && canManage && !!user.canAddDomains
  const importable = useQuery({ queryKey: ['importable', node?.id], queryFn: () => api.get(`/nodes/${node.id}/importable`), enabled: canImport, retry: false })
  const ov = useOverview(); const go = useGo()
  const zones = useQuery({ queryKey: ['zones'], queryFn: () => api.get('/dns/zones'), retry: false })
  const zoneOf = (n) => (zones.data || []).find((z) => z.name === n)
  const sitesOf = (n) => (ov.data?.items || []).filter((i) => i.kind === 'routers' && i.proto === 'http' && !/(^|\/)zz-webui-/.test(i.file || '') && extractHosts(i.spec?.rule || '').some((h) => h === n || h.endsWith('.' + n)))
  const doms = useQuery({ queryKey: ['domains'], queryFn: () => api.get('/domains') })
  const [add, setAdd] = useState(false); const [imp, setImp] = useState(null); const [share, setShare] = useState(null); const [more, setMore] = useState(null); const [srvNames, setSrvNames] = useState(null); const [srvEdit, setSrvEdit] = useState(null)
  const testSrv = (c) => api.post(`/credentials/${c.id}/test`).then((r) => toast(`Connexion réussie · ${r.rewrites} entrée(s)${r.version ? ' · ' + r.version : ''}`)).catch((e) => toast(e.message, 'err'))
  const refresh = () => { qc.invalidateQueries({ queryKey: ['credentials'] }); qc.invalidateQueries({ queryKey: ['domains'] }); qc.invalidateQueries({ queryKey: ['zones'] }); qc.invalidateQueries({ queryKey: ['meta'] }); qc.invalidateQueries({ queryKey: ['sync'] }); qc.invalidateQueries({ queryKey: ['importable'] }); qc.invalidateQueries({ queryKey: ['node-domains'] }) }
  const levelOf = (n) => doms.data?.find((d) => d.name === n)
  const delCred = async (c) => { if (await confirm({ title: `Supprimer « ${c.label} » ?`, message: (c.provider === 'local' ? 'Ses domaines disparaissent de l\'interface (rien n\'est modifié dans votre DNS local).' : 'Les clés sont effacées et ses domaines disparaissent de l\'interface (rien n\'est modifié chez le fournisseur).') + (c.elsewhere > 0 ? `\n\nAttention : ${c.elsewhere} domaine${c.elsewhere > 1 ? 's' : ''} de ce compte ${c.elsewhere > 1 ? 'sont rattachés' : 'est rattaché'} à d'autres nœuds : ${c.elsewhere > 1 ? 'ils disparaissent' : 'il disparaît'} aussi de là-bas.` : ''), confirmLabel: 'Supprimer' })) api.del(`/credentials/${c.id}`).then(() => { refresh(); toast('Compte supprimé') }).catch((e) => toast(e.message, 'err')) }
  const delDom = async (n) => { if (await confirm({ title: `Retirer ${n} ?`, message: 'Le domaine disparaît de l\'interface et ses partages sont supprimés. Rien n\'est modifié chez le fournisseur ni dans votre DNS.', confirmLabel: 'Retirer' })) api.del(`/domains/${n}`).then(() => { refresh(); toast('Domaine retiré') }).catch((e) => toast(e.message, 'err')) }
  const doImport = useMutation({ mutationFn: (names) => api.post(`/nodes/${node.id}/domains`, { names }), onSuccess: (r) => { refresh(); toast(r.added.length ? `${r.added.length} domaine${r.added.length > 1 ? 's' : ''} rattaché${r.added.length > 1 ? 's' : ''} à « ${node.name} »` : 'Rien à importer') }, onError: (e) => toast(e.message, 'err') })
  const groups = (() => { const m = new Map(); for (const d of importable.data || []) { if (!m.has(d.accountId)) m.set(d.accountId, { id: d.accountId, label: d.account, mine: d.mine, items: [] }); m.get(d.accountId).items.push(d) } return [...m.values()] })()
  if (creds.isLoading) return <Loading />
  return (
    <>
      <div className="page-head"><div><h1>Comptes DNS</h1><p>Branchez vos fournisseurs (OVH, DuckDNS) ou déclarez un domaine en DNS local, et choisissez qui peut y accéder.{node && <> Les domaines ajoutés ici rejoignent le nœud <b>{node.name}</b>.</>}</p></div><Btn kind="primary" className="right" onClick={() => setAdd(true)}><Plus size={15} /> Ajouter un compte</Btn></div>
      {groups.length > 0 && <div className="card col" style={{ gap: 10, marginBottom: 14 }}>
        <div className="row wrap"><Download size={18} /><h2>Importer sur ce nœud</h2><Badge>{importable.data.length}</Badge>
          <Btn small kind="primary" className="right" busy={doImport.isPending} title="Rattache tous ces domaines à ce nœud : vos clés ne sont jamais copiées" onClick={() => doImport.mutate(importable.data.map((d) => d.name))}><Download size={13} /> Tout importer</Btn></div>
        <div className="muted small">Vous gérez déjà ces domaines, mais ils ne sont pas encore sur « {node.name} ». Importer ne copie pas vos clés : le domaine apparaît simplement aussi ici.</div>
        {groups.map((g) => (
          <div key={g.id} className="col" style={{ gap: 6 }}>
            <div className="row wrap"><b>{g.label}</b>{!g.mine && <Badge kind="info" title="Ce compte n'est pas à vous : un domaine vous en est confié.">partagé</Badge>}
              {g.items.length > 1 && <Btn small className="right" disabled={doImport.isPending} onClick={() => doImport.mutate(g.items.map((d) => d.name))}>Importer les {g.items.length}</Btn>}</div>
            {g.items.map((d) => (
              <div className="mini row wrap" style={{ '--bc': '#0c8599' }} key={d.name}><b className="grow">{d.name}</b>
                {d.nodes.length ? d.nodes.map((n) => <Badge key={n} title="Déjà sur ce nœud">{n}</Badge>) : <Badge kind="warn" title="Ce domaine n'est encore sur aucun nœud">sur aucun nœud</Badge>}
                <Btn small disabled={doImport.isPending} onClick={() => doImport.mutate([d.name])}><Download size={13} /> Importer</Btn></div>))}
          </div>))}
      </div>}
      {creds.data.length === 0 && groups.length === 0 && <div className="card"><Empty><Cloud size={30} /><div>Aucun compte sur ce nœud : ajoutez un compte ou un domaine local.</div></Empty></div>}
      <div className="col gap-lg">
        {creds.data.map((c) => {
          const P = PROVIDERS.find((x) => x.id === c.provider) || PROVIDERS[0]
          return (
            <div className="card" key={c.id}>
              <div className="row wrap"><P.icon size={18} /><h2>{c.label}</h2><Badge kind={c.provider === 'ovh' ? '' : 'accent'}>{c.provider === 'ovh' ? c.endpoint : c.providerLabel}</Badge>{LOCAL_SRV.includes(c.provider) && <code className="muted small">{c.url}</code>}{c.system && <Badge kind="accent">principal · défini dans le .env</Badge>}
                {!c.system && <div className="row right">
                  {c.provider === 'ovh' && <Btn small onClick={() => setImp(c)}><Plus size={13} /> Importer des domaines</Btn>}
                  {c.provider === 'duckdns' && <Btn small onClick={() => setMore(c)}><Plus size={13} /> Ajouter un nom</Btn>}
                  {c.provider === 'local' && <Btn small onClick={() => setAdd('local')}><Plus size={13} /> Ajouter un domaine</Btn>}
                  {LOCAL_SRV.includes(c.provider) && <><Btn small onClick={() => testSrv(c)} title="Vérifie que l'interface joint ce serveur avec ces identifiants"><Plug size={13} /> Tester</Btn><Btn small onClick={() => setSrvEdit(c)}><Pencil size={13} /> Modifier</Btn><Btn small onClick={() => setSrvNames(c)}><Plus size={13} /> Ajouter un domaine</Btn></>}
                  <Btn small kind="danger" onClick={() => delCred(c)}><Trash2 size={13} /> Supprimer</Btn></div>}</div>
              <div className="col" style={{ marginTop: 12, gap: 8 }}>
                {c.domains.length === 0 && <div className="muted">Aucun domaine.</div>}
                {c.domains.map((n) => { const d = levelOf(n); return (
                  <div className="mini row wrap" style={{ '--bc': '#0c8599' }} key={n}><b className="grow">{n}</b>{(() => { const z = zoneOf(n); const ss = sitesOf(n); return <>{z?.dirty && <Badge kind="warn" title="Des modifications attendent d'être appliquées chez le fournisseur">à appliquer</Badge>}{z?.dnssec === 'enabled' && <Badge kind="ok">DNSSEC</Badge>}{ss.length > 0 && <Badge kind="info" title={ss.map((x) => x.name).join(', ')}>{ss.length} site{ss.length > 1 ? 's' : ''}</Badge>}<Btn small kind="ghost" onClick={() => go(`/dns?zone=${encodeURIComponent(n)}`)}>Enregistrements</Btn></> })()}
                    {c.nodeless?.includes(n) && <Badge kind="warn" title="Ce domaine vous appartient mais n'est sur aucun nœud : créez un nœud, ou importez-le depuis « Ce nœud → Domaines », pour l'utiliser dans les sites.">pas encore sur un nœud</Badge>}
                    {d?.shared > 0 && <Badge kind="info"><Users size={11} /> {d.shared}</Badge>}
                    {d?.level === 'manage' && <><Btn small onClick={() => setShare(n)}><Share2 size={13} /> Accès</Btn>{!c.system && <Btn small kind="ghost" icon onClick={() => delDom(n)}><Trash2 size={13} /></Btn>}</>}
                  </div>) })}
                {c.elsewhere > 0 && <div className="muted small" title="Ces domaines sont rattachés à d'autres de vos nœuds : ils se voient en changeant de nœud.">+ {c.elsewhere} domaine{c.elsewhere > 1 ? 's' : ''} sur d'autres nœuds (non affiché{c.elsewhere > 1 ? 's' : ''} ici)</div>}
              </div>
            </div>)
        })}
      </div>
      {add && <AddAccount mode="account" only={add === 'local' ? 'local' : null} canKeys={!!user.canAddDomains || canManage} onClose={() => setAdd(false)} onDone={(c) => { refresh(); setAdd(false); if (c?.provider === 'ovh') setImp(c) }} />}
      {imp && <ImportZones cred={imp} onClose={() => setImp(null)} onDone={refresh} />}
      {more && <AddDuckNames cred={more} onClose={() => setMore(null)} onDone={() => { refresh(); setMore(null) }} />}
      {srvNames && <AddServerNames cred={srvNames} onClose={() => setSrvNames(null)} onDone={() => { refresh(); setSrvNames(null) }} />}
      {srvEdit && <AddLocalServer provider={srvEdit.provider} cred={srvEdit} onClose={() => setSrvEdit(null)} onDone={() => { refresh(); setSrvEdit(null) }} />}
      {share && <ShareModal domain={share} onClose={() => setShare(null)} />}
    </>
  )
}

// Étape 1 : le fournisseur. Étape 2 : seulement ce que ce fournisseur demande.
// Deux entrées, deux intentions :
//  - mode « account » (page Comptes DNS) : brancher un FOURNISSEUR (OVH, DuckDNS), avec ses clés ;
//  - mode « domain » (page Domaines) : obtenir un DOMAINE, d'abord depuis ce qu'on a déjà (comptes existants, DNS local), sinon un nouveau fournisseur.
export function AddAccount({ onClose, onDone, canKeys, only, mode = 'account', onImport, currentCred, currentName }) {
  const [pick, setPick] = useState(only)
  const creds = useQuery({ queryKey: ['credentials', 'all'], queryFn: () => api.get('/credentials?all=1'), enabled: mode === 'domain' })
  const providers = PROVIDERS.filter((p) => p.needsKey && canKeys)
  if (pick === 'ovh') return <AddCredential onClose={onClose} onDone={onDone} />
  if (pick === 'duckdns') return <AddDuck onClose={onClose} onDone={onDone} />
  if (LOCAL_SRV.includes(pick)) return <AddLocalServer provider={pick} onClose={onClose} onDone={onDone} />
  if (pick === 'local') return <AddLocal onClose={onClose} onDone={onDone} />
  if (pick?.serverNames) return <AddServerNames cred={pick.serverNames} onClose={onClose} onDone={() => onDone(null)} />
  if (pick?.duckNames) return <AddDuckNames cred={pick.duckNames} onClose={onClose} onDone={() => onDone(null)} />
  const providerList = (title, intro) => (
    <Modal title={title} onClose={onClose} footer={<Btn onClick={onClose}>Annuler</Btn>}>
      <div className="col">
        <div className="muted">{intro}</div>
        {providers.length === 0 && <div className="banner">Vous n'avez pas le droit d'ajouter un compte DNS : demandez-le à un administrateur.</div>}
        {providers.map((p) => (
          <button type="button" key={p.id} className="role-card" style={{ textAlign: 'left' }} onClick={() => setPick(p.id)}>
            <b className="row"><p.icon size={16} />{p.label}</b><span className="muted small">{p.text}</span></button>))}
      </div>
    </Modal>
  )
  if (mode === 'account' || pick === 'provider') return providerList(mode === 'account' ? 'Ajouter un compte DNS' : 'Nouveau fournisseur DNS', 'Chez quel fournisseur se trouve votre DNS ? Vous donnez ses clés (ou l\'adresse de votre serveur) une seule fois ; ensuite vous ajoutez ses domaines.')
  // Page Domaines : la liste des DNS où l'on peut ajouter un domaine, puis « nouveau compte DNS ».
  const mine = (creds.data || []).filter((c) => ['ovh', 'duckdns', ...LOCAL_SRV].includes(c.provider)).sort((a, b) => (b.id === currentCred) - (a.id === currentCred))   // le DNS du domaine affiché d'abord
  const item = (key, icon, title, sub, onClick) => (
    <button type="button" key={key} className="role-card" style={{ textAlign: 'left' }} onClick={onClick}>
      <b className="row">{icon}{title}{key === currentCred && <Badge kind="ok">celui de {currentName}</Badge>}</b>{sub && <span className="muted small">{sub}</span>}</button>)
  return (
    <Modal title="Ajouter un domaine" onClose={onClose} footer={<Btn onClick={onClose}>Annuler</Btn>}>
      <div className="col">
        <div className="muted">Dans quel DNS se trouve ce domaine ?</div>
        {creds.isLoading && <Loading />}
        {mine.map((c) => c.provider === 'ovh'
          ? item(c.id, <Cloud size={16} />, c.label, 'OVH : choisir parmi les zones du compte', () => { onClose(); onImport?.(c) })
          : LOCAL_SRV.includes(c.provider) ? item(c.id, <Shield size={16} />, c.label, `${SRV[c.provider].name} : ajouter un domaine géré par ce serveur`, () => setPick({ serverNames: c }))
          : item(c.id, <Cloud size={16} />, c.label, 'DuckDNS : ajouter un nom', () => setPick({ duckNames: c })))}
        {item('local', <Home size={16} />, 'DNS local', 'Votre DNS local (Pi-hole, routeur…) sans compte : rien n\'est créé automatiquement', () => setPick('local'))}
        {canKeys && item('new', <Plus size={16} />, 'Ajouter un nouveau compte DNS', 'OVH, DuckDNS, AdGuard Home ou Pi-hole', () => setPick('provider'))}
      </div>
    </Modal>
  )
}

const SRV_HELP = 'Un par ligne (ou séparés par des virgules), ex. maison.exemple.fr ou home.lan. Les sous-domaines sont couverts.'

// Serveur DNS local (AdGuard Home, Pi-hole) : adresse + identifiants ; mêmes champs pour créer et pour modifier (mot de passe vide = inchangé).
function AddLocalServer({ provider, cred, onClose, onDone }) {
  const { toast } = useUi(); const edit = !!cred; const m = SRV[provider]
  const [f, setF] = useState({ label: cred?.label || m.name, url: cred?.url || '', username: '', password: '', insecureTls: !!cred?.insecureTls, names: '' }); const set = (k, v) => setF({ ...f, [k]: v })
  const names = f.names.split(/[\s,;]+/).filter(Boolean)
  const creds = () => ({ url: f.url.trim(), username: f.username.trim(), password: f.password, insecureTls: f.insecureTls })
  const okMsg = (r) => toast(`Connexion réussie · ${r.rewrites} entrée(s)${r.version ? ' · ' + r.version : ''}`)
  const test = useMutation({ mutationFn: () => api.post(`/credentials/${provider}/test`, creds()), onSuccess: okMsg, onError: (e) => toast(e.message, 'err') })
  const save = useMutation({
    mutationFn: () => edit
      ? api.put(`/credentials/${cred.id}`, { label: f.label.trim(), url: f.url.trim(), ...(f.username.trim() ? { username: f.username.trim() } : {}), ...(f.password ? { password: f.password } : {}), insecureTls: f.insecureTls })
      : api.post(`/credentials/${provider}`, { label: f.label.trim(), ...creds(), domains: names }),
    onSuccess: (r) => { toast(edit ? 'Compte modifié' : `Compte ajouté${r.added?.length ? ` · ${r.added.length} domaine(s)` : ''}`); onDone(r) }, onError: (e) => toast(e.message, 'err') })
  const https = /^https:/i.test(f.url.trim())
  const ok = f.label.trim() && f.url.trim() && (edit || ((!m.user || f.username.trim()) && f.password))
  return (
    <Modal title={edit ? `Modifier « ${cred.label} »` : `Ajouter un serveur ${m.name}`} onClose={onClose}
      footer={<><Btn onClick={onClose}>Annuler</Btn>{!edit && <Btn busy={test.isPending} disabled={!ok} onClick={() => test.mutate()}><Plug size={14} /> Tester</Btn>}<Btn kind="primary" busy={save.isPending} disabled={!ok} onClick={() => save.mutate()}>{edit ? 'Enregistrer' : 'Tester et ajouter'}</Btn></>}>
      <div className="col">
        <div className="banner info" style={{ margin: 0 }}><div>{m.intro}</div></div>
        <Field label="Nom du compte"><input className="input" value={f.label} onChange={(e) => set('label', e.target.value)} /></Field>
        <Field label="Adresse de l'interface d'administration" tip={`L'adresse que vous ouvrez dans le navigateur pour ${m.name} (sans /admin). Si c'est un conteneur sur le même réseau Docker que cette interface, son nom suffit.`} help={m.urlHelp}><input className="input mono" value={f.url} onChange={(e) => set('url', e.target.value)} placeholder={m.url} autoFocus={!edit} /></Field>
        {https && <label className="row small"><input type="checkbox" checked={f.insecureTls} onChange={(e) => set('insecureTls', e.target.checked)} /> Certificat auto-signé (ne pas vérifier le certificat HTTPS)</label>}
        <div className="fgrid">{m.user && <Field label="Identifiant" help={edit ? 'Vide = inchangé' : undefined}><input className="input" value={f.username} onChange={(e) => set('username', e.target.value)} autoComplete="off" /></Field>}
          <Field label="Mot de passe" help={edit ? 'Vide = inchangé' : undefined}><input className="input" type="password" value={f.password} onChange={(e) => set('password', e.target.value)} autoComplete="new-password" /></Field></div>
        {!edit && <Field label="Domaines gérés par ce serveur" tip={`Les domaines dont vous voulez créer des noms dans ${m.name}. Ils s'utilisent ensuite comme les autres (sites, droits, partage). Vous pourrez en ajouter d'autres plus tard.`} help={SRV_HELP}><textarea className="textarea" rows={3} value={f.names} onChange={(e) => set('names', e.target.value)} placeholder="maison.exemple.fr" /></Field>}
      </div>
    </Modal>
  )
}

function AddServerNames({ cred, onClose, onDone }) {
  const { toast } = useUi(); const [names, setNames] = useState('')
  const list = names.split(/[\s,;]+/).filter(Boolean)
  const save = useMutation({ mutationFn: () => api.post(`/credentials/${cred.id}/names`, { names: list }), onSuccess: (r) => { toast(`${r.added.length} domaine(s) ajouté(s)` + (r.skipped.length ? ` · ${r.skipped.length} ignoré(s)` : '')); onDone() }, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal small title={`Ajouter un domaine · ${cred.label}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={save.isPending} disabled={!list.length} onClick={() => save.mutate()}>Ajouter</Btn></>}>
      <div className="col"><Field label="Domaines" help={SRV_HELP}><textarea className="textarea" rows={3} value={names} onChange={(e) => setNames(e.target.value)} autoFocus /></Field></div>
    </Modal>
  )
}

function AddDuck({ onClose, onDone }) {
  const { toast } = useUi()
  const [f, setF] = useState({ label: 'DuckDNS', token: '', names: '', ip: '' }); const set = (k, v) => setF({ ...f, [k]: v })
  const names = duckNames(f.names)
  const save = useMutation({ mutationFn: () => api.post('/credentials/duckdns', { label: f.label, token: f.token.trim(), domains: names, ip: f.ip.trim() || null }),
    onSuccess: (r) => { toast(`${r.added.length} nom(s) ajouté(s)`); onDone(r) }, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal title="Ajouter un compte DuckDNS" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={save.isPending} disabled={!f.label.trim() || f.token.trim().length < 8 || !names.length} onClick={() => save.mutate()}>Tester et ajouter</Btn></>}>
      <div className="col">
        <div className="banner info" style={{ margin: 0 }}><div>Le jeton est affiché sur <a href="https://www.duckdns.org" target="_blank" rel="noreferrer">duckdns.org</a> après connexion. Il est chiffré et jamais renvoyé au navigateur.</div></div>
        <Field label="Nom du compte"><input className="input" value={f.label} onChange={(e) => set('label', e.target.value)} /></Field>
        <Field label="Jeton (token)" tip="Votre jeton personnel DuckDNS : il autorise la mise à jour de vos noms. Chiffré avant d'être enregistré."><input className="input mono" type="password" value={f.token} onChange={(e) => set('token', e.target.value)} autoComplete="off" /></Field>
        <Field label="Noms à gérer" tip="Un nom par ligne : monsite ou monsite.duckdns.org. Les sous-domaines (app.monsite.duckdns.org…) sont automatiquement couverts." help={DUCK_HELP}><textarea className="textarea" rows={3} value={f.names} onChange={(e) => set('names', e.target.value)} placeholder="monsite" /></Field>
        <Field label="Adresse IP à appliquer" tip="Facultative si le nom a déjà une adresse : le jeton est alors vérifié sans rien changer. Obligatoire pour un nom tout neuf." help="Facultatif si le nom pointe déjà quelque part."><input className="input mono" value={f.ip} onChange={(e) => set('ip', e.target.value)} placeholder="203.0.113.10" /></Field>
      </div>
    </Modal>
  )
}

function AddDuckNames({ cred, onClose, onDone }) {
  const { toast } = useUi(); const [names, setNames] = useState(''); const [ip, setIp] = useState('')
  const list = names.split(/[\s,;]+/).filter(Boolean)
  const save = useMutation({ mutationFn: () => api.post(`/credentials/${cred.id}/duckdns-names`, { domains: list, ip: ip.trim() || null }), onSuccess: (r) => { toast(`${r.added.length} nom(s) ajouté(s)`); onDone() }, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal small title={`Ajouter un nom · ${cred.label}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={save.isPending} disabled={!list.length} onClick={() => save.mutate()}>Ajouter</Btn></>}>
      <div className="col"><Field label="Noms" help={DUCK_HELP}><textarea className="textarea" rows={3} value={names} onChange={(e) => setNames(e.target.value)} autoFocus /></Field>
        <Field label="Adresse IP (facultatif)" help="Obligatoire seulement pour un nom qui n'a pas encore d'adresse."><input className="input mono" value={ip} onChange={(e) => setIp(e.target.value)} /></Field></div>
    </Modal>
  )
}

function AddLocal({ onClose, onDone }) {
  const { toast } = useUi(); const [names, setNames] = useState('')
  const list = names.split(/[\s,;]+/).filter(Boolean)
  const save = useMutation({ mutationFn: () => api.post('/domains/local', { names: list }), onSuccess: (r) => { toast(`${r.added.length} domaine(s) ajouté(s)` + (r.skipped.length ? ` · ${r.skipped.length} ignoré(s)` : '')); onDone(r) }, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal small title="Ajouter un domaine en DNS local" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={save.isPending} disabled={!list.length} onClick={() => save.mutate()}>Ajouter</Btn></>}>
      <div className="col">
        <div className="banner info" style={{ margin: 0 }}><div>Aucun compte ni clé : le domaine est seulement un nom pour vos sites et leurs droits. C'est <b>votre DNS local</b> qui le résout ; rien n'est créé chez un fournisseur.</div></div>
        <Field label="Domaines" tip="Un nom complet avec au moins un point : maison.exemple.fr, home.lan… Les sous-domaines (app.maison.exemple.fr) sont couverts." help="Un par ligne (ou séparés par des virgules)."><textarea className="textarea" rows={3} value={names} onChange={(e) => setNames(e.target.value)} placeholder="maison.exemple.fr" autoFocus /></Field>
      </div>
    </Modal>
  )
}

function AddCredential({ onClose, onDone }) {
  const { toast } = useUi()
  const [f, setF] = useState({ label: '', endpoint: 'ovh-eu', applicationKey: '', applicationSecret: '', consumerKey: '' }); const set = (k, v) => setF({ ...f, [k]: v })
  const save = useMutation({ mutationFn: () => api.post('/credentials', f), onSuccess: onDone, onError: (e) => toast(e.message, 'err') })
  const ok = Object.values(f).every((v) => v.trim())
  return (
    <Modal title="Ajouter un compte OVH" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={save.isPending} disabled={!ok} onClick={() => save.mutate()}>Tester et ajouter</Btn></>}>
      <div className="col">
        <div className="banner info" style={{ margin: 0 }}><div>Créez une clé sur <a href="https://api.ovh.com/createToken/" target="_blank" rel="noreferrer">api.ovh.com/createToken</a> avec les droits <code>GET/POST/PUT/DELETE</code> sur <code>/domain/zone/*</code> (et <code>/domain/zone</code>), sans limite de durée. Les clés sont chiffrées et jamais renvoyées au navigateur.</div></div>
        <div className="fgrid"><Field label="Nom du compte"><input className="input" value={f.label} onChange={(e) => set('label', e.target.value)} placeholder="Perso, Client X…" autoFocus /></Field>
          <Field label="Région" tip="Le site OVH sur lequel votre compte a été créé (Europe, Canada, États-Unis…)."><select className="select" value={f.endpoint} onChange={(e) => set('endpoint', e.target.value)}>{ENDPOINTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field></div>
        <Field label="Application key" tip="Identifiant public de votre application OVH. Créez-le sur la page « createToken » d'OVH, avec les droits sur /domain/zone/*."><input className="input mono" value={f.applicationKey} onChange={(e) => set('applicationKey', e.target.value.trim())} autoComplete="off" /></Field>
        <Field label="Application secret" tip="Secret de l'application. Il est chiffré avant d'être enregistré et n'est jamais ré-affiché."><input className="input mono" type="password" value={f.applicationSecret} onChange={(e) => set('applicationSecret', e.target.value.trim())} autoComplete="off" /></Field>
        <Field label="Consumer key" tip="Clé qui autorise l'application à agir sur votre compte avec les droits choisis à sa création. Chiffrée elle aussi."><input className="input mono" type="password" value={f.consumerKey} onChange={(e) => set('consumerKey', e.target.value.trim())} autoComplete="off" /></Field>
      </div>
    </Modal>
  )
}

export function ImportZones({ cred, onClose, onDone }) {
  const { toast } = useUi()
  const zones = useQuery({ queryKey: ['credzones', cred.id], queryFn: () => api.get(`/credentials/${cred.id}/zones`) })
  const [sel, setSel] = useState(null)
  const list = zones.data || []
  const chosen = sel ?? new Set(list.filter((z) => z.available).map((z) => z.name))
  const go = () => api.post(`/credentials/${cred.id}/import`, { zones: [...chosen] }).then((r) => { toast(`${r.added.length} domaine(s) importé(s)`); onDone(); onClose() }).catch((e) => toast(e.message, 'err'))
  return (
    <Modal title={`Importer depuis « ${cred.label} »`} onClose={onClose} footer={<><Btn onClick={onClose}>Fermer</Btn><Btn kind="primary" disabled={!chosen.size} onClick={go}>Importer {chosen.size}</Btn></>}>
      {zones.isLoading ? <Loading /> : zones.error ? <div className="banner err">{zones.error.message}</div> : (
        <div className="col">{list.length === 0 && <div className="empty">Ce compte n'a aucune zone DNS.</div>}
          {list.map((z) => <label key={z.name} className="row" style={{ opacity: z.available ? 1 : .5 }}><input type="checkbox" disabled={!z.available} checked={chosen.has(z.name)} onChange={(e) => { const n = new Set(chosen); e.target.checked ? n.add(z.name) : n.delete(z.name); setSel(n) }} /><b>{z.name}</b>{!z.available && <Badge>déjà utilisé</Badge>}</label>)}</div>)}
    </Modal>
  )
}
