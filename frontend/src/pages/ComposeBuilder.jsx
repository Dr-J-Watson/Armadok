import { useMemo, useState } from 'react'
import { AlertTriangle, Download, Info as InfoIcon, RefreshCw } from 'lucide-react'
import { Btn, CopyBtn, Field, Info, Tabs, useUi } from '../components/ui.jsx'
import { DEFAULTS, DNS_PROVIDERS, allFiles, problems, randomHex, steps } from '../lib/composeBuilder.js'
import { downloadBlob } from '../lib/csv.js'

const KEY = 'compose-builder'
const load = () => { try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || '{}') } } catch { return { ...DEFAULTS } } }
const save = (v) => { try { localStorage.setItem(KEY, JSON.stringify({ ...v, hashPlaceholder: '' })) } catch { /* navigation privée : on oublie simplement */ } }   // jamais les secrets : seulement les cases

function Opt({ value, onChange, label, hint, children, disabled }) {
  return (
    <div className="col" style={{ gap: 6 }}>
      <label className="row" style={{ alignItems: 'flex-start', opacity: disabled ? .5 : 1 }}>
        <input type="checkbox" checked={!!value} disabled={disabled} onChange={(e) => onChange(e.target.checked)} style={{ marginTop: 3 }} />
        <span><b>{label}</b>{hint && <span className="muted small"> — {hint}</span>}</span>
      </label>
      {value && children && <div className="col" style={{ marginLeft: 24, gap: 8 }}>{children}</div>}
    </div>
  )
}

const input = (c, set, k, ph, mono = true) => <input className={'input' + (mono ? ' mono' : '')} value={c[k]} onChange={(e) => set(k, e.target.value)} placeholder={ph} />

export default function ComposeBuilder({ embedded = false }) {
  const { toast } = useUi()
  const [c, setC] = useState(load)
  const [secrets, setSecrets] = useState(() => ({ jwt: randomHex(), enc: randomHex() }))
  const [tab, setTab] = useState('steps')
  const set = (k, v) => setC((p) => { const n = { ...p, [k]: v }; save(n); return n })
  const files = useMemo(() => allFiles(c, secrets), [c, secrets])
  const text = useMemo(() => ({ steps: steps(c), ...files }), [c, files])
  const probs = useMemo(() => problems(c), [c])
  const names = Object.keys(text)
  const cur = names.includes(tab) ? tab : 'steps'
  const download = (name) => { downloadBlob(name === 'steps' ? 'installation.sh' : name.split('/').pop(), text[name]) }
  const copyAll = () => { navigator.clipboard?.writeText(names.filter((n) => n !== 'steps').map((n) => `# ===== ${n} =====\n${text[n]}`).join('\n\n')); toast('Tous les fichiers sont copiés') }
  const short = (n) => (n === 'steps' ? 'Étapes' : n.replace('traefik/traefik-dynamic/', '…/').replace('traefik/', 'traefik/'))
  const hasErr = probs.some((p) => p[0] === 'err')
  return (
    <>
      {embedded ? <div className="muted" style={{ marginBottom: 14 }}>Cochez ce que vous voulez : vous obtenez la pile de Traefik (compose, <code>.env</code>, configuration dynamique, Prometheus) et celle de cette interface, prêtes à poser. Rien n'est envoyé au serveur ; vos choix restent dans ce navigateur, les secrets ne sont jamais enregistrés.</div>
      : <div className="page-head"><div><h1>Constructeur de piles Docker</h1><p>Cochez ce que vous voulez : vous obtenez la pile de Traefik (compose, <code>.env</code>, configuration dynamique, Prometheus) et celle de cette interface, prêtes à poser. Rien n'est envoyé au serveur ; vos choix restent dans ce navigateur, les secrets ne sont jamais enregistrés.</p></div></div>}
      <div className="grid builder-grid" style={{ gridTemplateColumns: 'minmax(320px, 440px) minmax(0, 1fr)', alignItems: 'start' }}>
        <div className="col gap-lg">
          <div className="card col">
            <h3>Votre installation</h3>
            <Field label="Adresse de cette interface" help="Le nom (DNS) qui mènera à elle."><>{input(c, set, 'webuiHost', 'armadok.exemple.fr')}</></Field>
            <Field label="E-mail pour Let's Encrypt" help="Il prévient avant une expiration."><>{input(c, set, 'email', 'vous@exemple.fr')}</></Field>
            <div className="fgrid"><Field label="Utilisateur (UID)" tip="Celui qui possède les dossiers : id -u. Traefik et l'interface tournent avec lui (jamais en root)."><>{input(c, set, 'uid', '1000')}</></Field><Field label="Groupe (GID)" tip="id -g"><>{input(c, set, 'gid', '1000')}</></Field></div>
            <div className="fgrid"><Field label="Réseau Docker partagé"><>{input(c, set, 'network', 'proxy')}</></Field><Field label="Image de Traefik"><>{input(c, set, 'traefikImage', 'traefik:v3.7')}</></Field></div>
          </div>

          <div className="card col">
            <h3>Traefik</h3>
            <Opt value={c.http3} onChange={(v) => set('http3', v)} label="HTTP/3 (QUIC)" hint="publie aussi le port 443 en UDP" />
            <Opt value={c.dockerProvider} onChange={(v) => set('dockerProvider', v)} label="Lire aussi les conteneurs Docker" hint="étiquettes traefik.* ; monte le socket Docker en lecture seule">
              <Field label="GID du groupe docker" help="getent group docker | cut -d: -f3"><>{input(c, set, 'dockerGid', '984')}</></Field></Opt>
            <Opt value={c.apiInternal} onChange={(v) => set('apiInternal', v)} label="API interne de Traefik" hint="état en direct des sites ; sert aussi aux métriques (port 8080, jamais publié)" />
            <Opt value={c.dashboard} onChange={(v) => set('dashboard', v)} label="Tableau de bord de Traefik" hint="derrière la chaîne privée">
              <Field label="Adresse du tableau de bord"><>{input(c, set, 'dashboardHost', 'traefik.exemple.fr')}</></Field></Opt>
            <Field label="Niveau de journal"><select className="select" value={c.logLevel} onChange={(e) => set('logLevel', e.target.value)}>{['ERROR', 'WARN', 'INFO', 'DEBUG'].map((l) => <option key={l}>{l}</option>)}</select></Field>
          </div>

          <div className="card col">
            <div className="row"><h3>Certificats HTTPS</h3><Info>Deux méthodes, qui peuvent coexister : la validation HTTP (sites publics, ports 80/443 ouverts) et la validation DNS (sites locaux, aucun port à ouvrir). Voir l'aide, « Certificats HTTPS ».</Info></div>
            <Opt value={c.tlsHttp} onChange={(v) => set('tlsHttp', v)} label="Validation HTTP" hint="sites publics">
              <Field label="Nom du résolveur"><>{input(c, set, 'httpName', 'lehttp')}</></Field></Opt>
            <Opt value={c.tlsDns} onChange={(v) => set('tlsDns', v)} label="Validation DNS" hint="sites locaux, certificats jokers">
              <div className="fgrid"><Field label="Fournisseur"><select className="select" value={c.dnsProvider} onChange={(e) => set('dnsProvider', e.target.value)}>{Object.entries(DNS_PROVIDERS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select></Field>
                <Field label="Nom du résolveur"><>{input(c, set, 'dnsName', 'lednsovh')}</></Field></div>
              <div className="muted small">{DNS_PROVIDERS[c.dnsProvider].help}</div></Opt>
            <Opt value={c.staging} onChange={(v) => set('staging', v)} label="Serveur de test (staging)" hint="aucune limite, mais certificats non reconnus : pour essayer" disabled={!c.tlsHttp && !c.tlsDns} />
          </div>

          <div className="card col">
            <h3>Protection</h3>
            <Opt value={c.chainPrivate} onChange={(v) => set('chainPrivate', v)} label="Chaîne privée" hint="en-têtes de sécurité + restriction d'adresse, appliquée à cette interface">
              <Field label="Réseaux autorisés" help="Séparés par des virgules. Ajoutez votre IP publique si vous accédez depuis chez vous par le nom public."><>{input(c, set, 'lan', '10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16')}</></Field>
              <Field label="Adresses en plus"><>{input(c, set, 'extraIps', '203.0.113.7/32')}</></Field></Opt>
          </div>

          <div className="card col">
            <h3>Surveillance</h3>
            <Opt value={c.metrics} onChange={(v) => set('metrics', v)} label="Métriques Prometheus" hint="requêtes, erreurs, latence par site">
              <Opt value={c.prometheus} onChange={(v) => set('prometheus', v)} label="Ajouter Prometheus à la pile" hint="sinon, branchez le vôtre" />
              <Opt value={c.routersLabels} onChange={(v) => set('routersLabels', v)} label="Étiquettes par routeur" hint="plus fin, plus de séries à stocker" />
              {c.prometheus && <Opt value={c.prometheusUrlSeed} onChange={(v) => set('prometheusUrlSeed', v)} label="Brancher Prometheus à l'interface" hint="PROMETHEUS_URL (valeur de départ)" />}
            </Opt>
            <Opt value={c.accessLog} onChange={(v) => set('accessLog', v)} label="Journal d'accès en fichier JSON" hint="adresses IP, attaques probables, bannissement" />
          </div>

          <div className="card col">
            <h3>Cette interface</h3>
            <Field label="Image"><>{input(c, set, 'webuiImage', 'ghcr.io/dr-j-watson/armadok:latest')}</></Field>
            <Opt value={c.certsRelay} onChange={(v) => set('certsRelay', v)} label="Gérer les certificats depuis l'interface" hint="voir les échéances, supprimer, renouveler (donne accès au socket Docker à l'interface)" disabled={!c.tlsHttp && !c.tlsDns} />
            <Field label="IP proposée pour les nouveaux sous-domaines" help="Facultatif (DEFAULT_DNS_TARGET) : en général l'IP publique de cette machine."><>{input(c, set, 'dnsTarget', '203.0.113.10')}</></Field>
            <Field label="Hachage du mot de passe" help="Facultatif : collez-le ici (commande dans « Étapes »), il est mis dans le .env. Il n'est jamais enregistré."><input className="input mono" value={c.hashPlaceholder} onChange={(e) => setC((p) => ({ ...p, hashPlaceholder: e.target.value.trim() }))} placeholder="$2b$12$…" autoComplete="off" /></Field>
          </div>
        </div>

        <div className="col" style={{ position: 'sticky', top: 12 }}>
          {probs.map((p, i) => <div key={i} className={'banner ' + (p[0] === 'err' ? 'err' : p[0] === 'info' ? 'info' : '')} style={{ margin: 0 }}>{p[0] === 'info' ? <InfoIcon size={16} /> : <AlertTriangle size={16} />}<span className="grow">{p[1]}</span></div>)}
          <div className="row" style={{ gap: 8 }}>
            <Btn small onClick={copyAll} disabled={hasErr}>Copier tous les fichiers</Btn>
            <Btn small kind="ghost" title="Tire de nouveaux JWT_SECRET et ENCRYPTION_KEY (à ne pas changer une fois l'interface démarrée)" onClick={() => setSecrets({ jwt: randomHex(), enc: randomHex() })}><RefreshCw size={13} /> Nouveaux secrets</Btn>
          </div>
          <Tabs value={cur} onChange={setTab} tabs={names.map((n) => ({ id: n, label: short(n) }))} />
          <div className="card flush" style={{ position: 'relative' }}>
            <div className="row" style={{ padding: '8px 12px', borderBottom: '1px solid var(--border)' }}>
              <code className="grow ellipsis small">{cur === 'steps' ? 'À lancer dans un terminal' : cur}</code>
              <CopyBtn text={text[cur]} /><Btn small kind="ghost" icon title="Télécharger" onClick={() => download(cur)}><Download size={14} /></Btn>
            </div>
            <pre className="mono" style={{ margin: 0, padding: 14, overflow: 'auto', maxHeight: '68vh', fontSize: 12.5, lineHeight: 1.5 }}>{text[cur]}</pre>
          </div>
          <div className="muted small">Les secrets (<code>JWT_SECRET</code>, <code>ENCRYPTION_KEY</code>) sont tirés dans votre navigateur : copiez les fichiers avant de quitter la page. Gardez <code>ENCRYPTION_KEY</code> : sans elle, les clés enregistrées ne se déchiffrent plus.</div>
        </div>
      </div>
    </>
  )
}
