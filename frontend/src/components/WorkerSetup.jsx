import { useEffect, useState } from 'react'
import { Check, KeyRound, Loader2, Terminal } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api.js'
import { Badge, CopyBtn, Field, Info, Seg } from './ui.jsx'
import { composeFor, imageFor, WORKER_DEFAULTS, WORKER_TYPES } from '../lib/worker.js'
import { fmtDate } from '../lib/util.js'
import { liveState, onLive } from '../lib/live.js'

// Installation d'un worker, dans l'ordre : le jeton (toujours visible), UNE commande prête à coller, puis la détection de la connexion.
export default function WorkerSetup({ node, token, expires, onOnline, modules }) {
  const meta = useQuery({ queryKey: ['meta'], queryFn: () => api.get('/meta'), staleTime: 60000 })
  const status = useQuery({ queryKey: ['node-conn', node.id, token], queryFn: () => api.get(`/nodes/${node.id}`), refetchInterval: (q) => (q.state.data?.connection === 'online' ? false : liveState() === 'live' ? 10000 : 2500) })
  const online = status.data?.connection === 'online'
  // Temps réel : le hub signale la connexion du worker de CE nœud (il n'est pas forcément le nœud courant : on écoute directement)
  useEffect(() => onLive((topics) => {
    if (topics.some((t) => t.t === 'all' || ((t.t === 'node' || t.t === 'nodes') && (!t.node || t.node === node.id)))) status.refetch()
  }), [node.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const hub = window.location.origin
  const [mode0, setMode] = useState('make')
  const [o, setO] = useState({ ...WORKER_DEFAULTS, ...(modules ? { modules } : {}) })
  const set = (k) => (e) => setO((x) => ({ ...x, [k]: e.target.value }))
  const mode = mode0
  const tr = o.modules !== 'docker'; const dk = o.modules !== 'traefik'
  const yaml = composeFor({ hub, token, version: meta.data?.version, ...o })
  const quick = `mkdir armadok-worker && cd armadok-worker\ncurl -fsSLO https://raw.githubusercontent.com/Dr-J-Watson/armadok/main/Makefile\nmake worker HUB_URL=${hub} ENROLL_TOKEN=${token} WORKER_IMAGE=${imageFor(meta.data?.version)}${o.modules === 'traefik' ? '' : ` WORKER_MODULES=${o.modules}`}${dk && o.addr.trim() ? ` WORKER_ADDRESSES=${o.addr.trim().replace(/\s+/g, '')}` : ''}`
  const until = expires ? `à ${fmtDate(expires * 1000)}` : 'dans 15 minutes'
  useEffect(() => { if (online) onOnline?.() }, [online]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="col gap-lg">
      <div className="card" style={{ background: 'var(--panel2)' }}>
        <div className="row"><KeyRound size={16} /><b>Jeton d'enrôlement</b><Info>Il prouve à ce hub que le worker est bien celui de ce nœud. Il ne sert qu'une fois et expire vite. Une fois le worker connecté, il n'est plus utile : la machine garde sa propre clé, qui ne la quitte jamais.</Info>
          <Badge kind="warn" className="right">usage unique · expire {until}</Badge></div>
        <div className="codebox slim" style={{ marginTop: 8 }}><CopyBtn text={token} /><pre>{token}</pre></div>
        <div className="muted small" style={{ marginTop: 6 }}>Copiez-le maintenant : il n'est affiché qu'ici. Il est déjà inclus dans la commande ci-dessous. En cas de perte, « Ce nœud → Connexion » en génère un nouveau.</div>
      </div>
      <div className="step"><span className="num">1</span><div className="grow col" style={{ gap: 8 }}>
        <b>Installez le worker sur la machine à relier</b>
        <Field label="Que doit gérer ce worker ?" tip="Un worker peut gérer Traefik + DNS (sites, protections, certificats), Docker (conteneurs, projets compose, Git, images) ou les deux. Le DNS lui-même reste géré par ce hub."><Seg value={o.modules} onChange={(m) => setO((x) => ({ ...x, modules: m }))} options={WORKER_TYPES} /></Field>
        <Seg value={mode} onChange={setMode} options={[{ id: 'make', label: 'Terminal (Makefile)', hint: 'Un script pose les questions et démarre tout (ou n\'écrit que le .env et le compose)' }, { id: 'compose', label: 'docker-compose.yml', hint: 'Un fichier compose à enregistrer sur la machine, sans le Makefile' }]} />
        {mode === 'make' ? <>
          <div className="codebox wrap"><CopyBtn text={quick} /><pre><Terminal size={12} /> {quick}</pre></div>
          <div className="muted small">Le script utilise la même version que ce hub. Il vous demande selon le type choisi : où se trouve la configuration de votre Traefik (dossier ou volume Docker), son journal d'accès, les dossiers de vos projets compose, puis il se connecte à ce hub avec l'adresse et le jeton déjà fournis : rien d'autre à saisir. Aucun port à ouvrir : le worker appelle ce hub, qui doit être joignable en HTTPS depuis cette machine ({window.location.host}).</div>
          <details className="small"><summary className="muted" style={{ cursor: 'pointer' }}>Avec les questions guidées : make install</summary>
            <div className="muted small" style={{ marginTop: 6 }}>Lancez <code>make install</code>, choisissez « worker ». Il demandera dans l'ordre : ce que gère le worker, votre Traefik et/ou Docker, l'adresse du hub (<code>{hub}</code>), puis le jeton ci-dessus.</div></details>
        </> : <>
          <div className="muted small">Répondez aux champs : le fichier ci-dessous se met à jour. Enregistrez-le en <code>docker-compose.yml</code> sur la machine à relier, puis lancez <code>docker compose up -d</code>. Aucune variable d'environnement à saisir : le hub et le jeton y sont déjà.</div>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
            {tr && <Field label="Config dynamique de Traefik" tip="Ce que Traefik lit en file provider (ses fichiers .yml). Un volume Docker existant (son nom : docker volume ls) ou le chemin absolu d'un dossier de la machine.">
              <input className="input" value={o.dyn} onChange={set('dyn')} placeholder="traefik_dynamic  ou  /opt/traefik/dynamic" /></Field>}
            {tr && <Field label="Réseau de Traefik (facultatif)" tip="Le réseau Docker où se trouve Traefik. Nécessaire seulement pour lire son état en direct via son API.">
              <input className="input" value={o.net} onChange={set('net')} placeholder="proxy" /></Field>}
            {tr && <Field label="API de Traefik (facultatif)" tip="Pour afficher l'état en direct des routeurs dans ce hub. Nécessite le réseau ci-contre. Sans elle, tout fonctionne, sans les statuts.">
              <input className="input" value={o.api} onChange={set('api')} placeholder="http://traefik:8080" /></Field>}
            <Field label="Utilisateur du worker (PUID:PGID)" tip="Le worker prend cette identité (les données de son volume lui sont données automatiquement). Elle doit pouvoir écrire dans la config dynamique de Traefik et/ou dans les dossiers des projets. Pour le connaître : ls -ln sur le dossier, ou « docker run --rm -v VOLUME:/d alpine stat -c %u:%g /d ». 1000:1000 convient à la plupart des installations.">
              <div className="row"><input className="input" value={o.puid} onChange={set('puid')} style={{ width: 90 }} /><span>:</span><input className="input" value={o.pgid} onChange={set('pgid')} style={{ width: 90 }} /></div></Field>
            {tr && <Field label="Journal d'accès de Traefik (facultatif)" tip="Le DOSSIER qui contient le journal d'accès de Traefik (volume Docker ou chemin absolu). Le worker le lit et l'envoie au hub : statistiques de visiteurs, adresses IP, alertes et bannissements automatiques pour ce nœud. Il doit être lisible par l'utilisateur du worker (PUID:PGID).">
              <div className="row"><input className="input mono" value={o.logs} onChange={set('logs')} placeholder="traefik_logs  ou  /opt/traefik/logs" /><input className="input mono" value={o.logFile} onChange={set('logFile')} style={{ width: 130 }} /></div></Field>}
            {dk && <Field label="Dossiers des projets compose" tip="Chemins ABSOLUS de la machine, séparés par des virgules. Ils sont montés avec le même chemin dans le worker (sinon les chemins relatifs des compose seraient faux) et bornent ce que ce nœud lit et écrit.">
              <input className="input mono" value={o.roots} onChange={set('roots')} placeholder="/opt/stacks, /srv/apps" /></Field>}
            {dk && <Field label="Groupe du socket Docker (GID)" tip="Le worker doit appartenir au groupe du socket Docker. Pour le connaître : stat -c %g /var/run/docker.sock">
              <input className="input mono" value={o.dgid} onChange={set('dgid')} style={{ width: 120 }} /></Field>}
          </div>
          {dk && <Field label="Adresse de cette machine pour un Traefik distant (facultatif)" tip="Si les sites de cette machine passent par le Traefik d'un AUTRE nœud (par exemple celui du hub), indiquez ici l'adresse par laquelle ce Traefik la joint : IP du réseau local ou du VPN, ou un nom. Séparez-en plusieurs par des virgules. Le hub relie alors tout seul ses conteneurs aux règles de ce Traefik (Ce nœud → Docker : « Publier les sites avec le Traefik de… »)."><input className="input mono" value={o.addr} onChange={set('addr')} placeholder="192.168.1.20" /></Field>}
          {dk && <div className="banner warn">Donner Docker à un worker lui donne un accès équivalent à <b>root</b> sur cette machine. Les droits « Docker » de ce nœud (menu « Droits ») décident qui peut s'en servir.</div>}
          {dk && <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={o.exec} onChange={(e) => setO((x) => ({ ...x, exec: e.target.checked }))} />Autoriser le terminal dans les conteneurs de cette machine<Info>Ajoute WORKER_ALLOW_EXEC=true. Sans cela, le worker refuse tout terminal, même si le hub le demande. Il faut aussi le droit « docker.exec » sur ce nœud (jamais donné d'office, 2FA possible).</Info></label>}
          {tr && <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={o.certs} onChange={(e) => setO((x) => ({ ...x, certs: e.target.checked }))} />Afficher les certificats de ce Traefik (relais sans clé privée)<Info>Ajoute un second conteneur, sans réseau et en lecture seule, qui lit acme.json et ne transmet que les noms de domaines et les dates d'expiration. Les clés privées ne quittent jamais ce conteneur.</Info></label>}
          {tr && o.certs && <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
            <Field label="Dossier ou volume d'acme.json"><input className="input" value={o.acme} onChange={set('acme')} placeholder="traefik_acme  ou  /opt/traefik/acme" /></Field>
            <Field label="Nom du fichier"><input className="input" value={o.acmeFile} onChange={set('acmeFile')} /></Field>
          </div>}
          {tr && !o.dyn && <div className="muted small">Indiquez la config dynamique : sinon le chemin d'exemple reste à remplacer à la main.</div>}
          <div className="codebox"><CopyBtn text={yaml} /><pre>{yaml}</pre></div>
          <div className="muted small">Les volumes nommés sont déclarés <code>external</code> : ils doivent déjà exister (docker volume ls). Le jeton est à usage unique ; une fois le worker connecté, vous pouvez le retirer du fichier.</div>
        </>}</div></div>
      <div className={'step ' + (online ? 'done' : '')}><span className="num">{online ? <Check size={14} /> : 2}</span><div className="grow col" style={{ gap: 4 }}>
        <b>{online ? 'Worker connecté !' : 'En attente du worker…'}</b>
        <div className="muted small">{online ? `Version ${status.data.workerVersion || '?'} — le nœud est prêt, vous pouvez gérer son Traefik d'ici.${Array.isArray(status.data.certResolvers) && status.data.certResolvers.length === 0 ? ' Aucun résolveur de certificats n\'est défini : ajoutez-le dans « Ce nœud → Réglages » pour le HTTPS automatique des sites.' : ''}` : <><Loader2 size={12} className="spin" style={{ verticalAlign: -2 }} /> Cette page détecte la connexion toute seule : vous n'avez rien à cliquer.</>}</div></div></div>
    </div>
  )
}
