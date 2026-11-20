import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Download, FileStack, MoreHorizontal, Play, Power, Rocket, RotateCw, Square } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Modal, useUi } from './ui.jsx'
import { stripAnsi } from '../lib/docker.js'

const TITLES = { make: 'Commande du Makefile', 'git-pull': 'Récupération des changements (git pull)', 'git-fetch': 'Interrogation du dépôt', 'git-checkout': 'Changement de version', 'git-clone': 'Clonage du dépôt', up: 'Déploiement', recreate: 'Recréation', down: 'Arrêt et suppression des conteneurs', restart: 'Redémarrage', stop: 'Arrêt', start: 'Démarrage', pull: 'Téléchargement des images', build: 'Construction des images', config: 'Configuration résolue', ps: 'État' }

// Petits boutons d'action d'UN service (selon son état) : chaque commande ne vise que lui (docker compose <action> <service>).
export function ServiceActions({ id, svc, perms, onDone }) {
  const { toast } = useUi(); const [job, setJob] = useState(null)
  const run = useMutation({ mutationFn: (action) => api.post(`/projects/${id}/compose`, { action, services: [svc.name] }), onSuccess: (r, action) => setJob({ ...r, action }), onError: (e) => toast(e.message, 'err') })
  if (!perms.deploy) return null
  const st = svc.done ? 'done' : svc.state
  const REDEPLOY = ['up', Rocket, 'Redéployer ce service : applique le fichier compose et recrée le conteneur si sa configuration ou son image a changé']
  const items = st === 'running' ? [['restart', RotateCw, 'Redémarrer ce service (sans le recréer)'], ['stop', Square, 'Arrêter ce service (son conteneur est gardé)'], REDEPLOY]
    : st === 'partial' ? [['start', Play, 'Démarrer les conteneurs arrêtés de ce service'], ['restart', RotateCw, 'Redémarrer ce service'], ['stop', Square, 'Arrêter ce service'], REDEPLOY]
    : st === 'stopped' ? [['start', Play, 'Démarrer ce service'], REDEPLOY]
    : st === 'done' ? [['up', Play, 'Relancer ce service (il s\'est terminé)']]
    : [['up', Play, 'Déployer ce service (créer et démarrer son conteneur)']]
  return (
    <>
      {items.map(([action, Icon, tip]) => <Btn key={action} small kind="ghost" title={tip} aria-label={tip} disabled={run.isPending} onClick={() => run.mutate(action)}><Icon size={14} /></Btn>)}
      {job && <JobOutput id={id} job={job} onClose={() => { setJob(null); onDone?.() }} />}
    </>
  )
}

// Boutons d'action d'un projet (docker compose) ; chaque commande s'exécute en tâche de fond et sa sortie s'affiche en direct.
export default function ComposeBar({ id, perms, onDone, compose, chipSlot }) {
  const { confirm, toast } = useUi(); const [job, setJob] = useState(null); const [menu, setMenu] = useState(false); const [pick, setPick] = useState(null); const qc = useQueryClient()
  const run = useMutation({ mutationFn: (body) => api.post(`/projects/${id}/compose`, body), onSuccess: (r, v) => { setMenu(false); setJob({ ...r, action: v.action }) }, onError: (e) => toast(e.message, 'err') })
  if (!perms.deploy) return null
  const multi = (compose?.candidates?.length || 0) > (compose?.default?.length || 0)   // d'autres fichiers que le principal + son override : on demande lequel
  const go = (body) => {
    if (multi && !compose.choice.length && ['up', 'recreate'].includes(body.action) && !body.files) { setPick({ body }); return }   // lancement : l'utilisateur choisit
    run.mutate(body)
  }
  const ask = async (title, message, body, label) => { if (await confirm({ title, message, confirmLabel: label })) go(body) }
  const chipEl = compose && compose.candidates.length > 0 ? (
        <button type="button" className="compose-files" onClick={() => setPick({})} title="Les fichiers compose utilisés pour lancer ce projet. Cliquez pour les choisir (par exemple le principal + un override).">
          <FileStack size={13} /> <span className="muted">Fichiers :</span> <b className="mono">{compose.effective.join(' + ')}</b>
          <span className="muted small">{compose.choice.length ? '(choisis)' : compose.candidates.length > 1 ? '(automatique)' : ''}</span>
          {!compose.choice.length && compose.last.length > 0 && compose.last.join('+') !== compose.effective.join('+') && <Badge kind="warn" title="Ce projet a été lancé avec d'autres fichiers : arrêt et redémarrage reprennent ceux du dernier lancement, sauf choix explicite : cliquez sur les fichiers pour choisir ceux à utiliser, et cet avertissement disparaît.">lancé avec {compose.last.join(' + ')}</Badge>}
        </button>) : null
  return (
    <div className="compose-bar">
      {chipEl && !chipSlot && chipEl}
      {chipEl && chipSlot && createPortal(chipEl, chipSlot)}
      <div className="row wrap" style={{ gap: 6, position: 'relative' }}>
        <Btn kind="primary" busy={run.isPending} onClick={() => go({ action: 'up' })} title="docker compose up -d : crée ou met à jour les conteneurs d'après les fichiers"><Play size={14} /> Déployer</Btn>
        <Btn title="Redémarre les conteneurs du projet (sans recréer ni changer leur configuration)" busy={run.isPending} onClick={() => go({ action: 'restart' })}><RotateCw size={14} /> Redémarrer</Btn>
        <Btn busy={run.isPending} onClick={() => go({ action: 'pull' })} title="Télécharge les nouvelles versions des images (sans redéployer)"><Download size={14} /> Pull</Btn>
        <Btn title="Arrête les conteneurs du projet : ils restent en place et se redémarrent d'un clic" busy={run.isPending} onClick={() => ask('Arrêter le projet ?', 'Les conteneurs sont arrêtés mais gardés ; « Démarrer » ou « Déployer » les relance.', { action: 'stop' }, 'Arrêter')}><Square size={14} /> Arrêter</Btn>
        <Btn title="Down : supprime les conteneurs et les réseaux du projet (les volumes et les fichiers restent)" busy={run.isPending} onClick={() => ask('Down : supprimer les conteneurs ?', 'Les conteneurs et les réseaux du projet sont supprimés. Les volumes (les données) sont conservés.', { action: 'down' }, 'Down')}><Power size={14} /> Down</Btn>
        <Btn kind="ghost" onClick={() => setMenu((v) => !v)} aria-label="Plus d'actions"><MoreHorizontal size={16} /></Btn>
        {menu && <div className="pop-menu" onMouseLeave={() => setMenu(false)}>
          <button type="button" onClick={() => go({ action: 'start' })}>Démarrer</button>
          <button type="button" onClick={() => go({ action: 'up', build: true })}>Déployer en reconstruisant les images</button>
          <button type="button" onClick={() => go({ action: 'up', pull: true })}>Déployer en téléchargeant les images</button>
          <button type="button" onClick={() => ask('Recréer les conteneurs ?', 'Tous les conteneurs sont recréés, même sans changement : brève coupure.', { action: 'recreate' }, 'Recréer')}>Recréer tous les conteneurs</button>
          <button type="button" onClick={() => go({ action: 'build' })}>Construire les images</button>
          <button type="button" onClick={() => go({ action: 'build', noCache: true })}>Construire sans cache</button>
          <button type="button" onClick={() => go({ action: 'ps' })}>État (compose ps)</button>
          {perms.env && <button type="button" onClick={() => go({ action: 'config' })}>Configuration résolue (affiche les variables)</button>}
          {perms.delete && <button type="button" className="danger" onClick={() => ask('Down ET supprimer les volumes ?', 'Les conteneurs, les réseaux ET LES VOLUMES du projet sont supprimés : les données sont perdues. Irréversible.', { action: 'down', volumes: true }, 'Tout supprimer')}>Down + supprimer les volumes</button>}
        </div>}
      </div>
      {pick && compose && <ComposeFilesModal id={id} compose={compose} launch={pick.body} onClose={() => setPick(null)}
        onLaunch={(files) => { const b = pick.body; setPick(null); run.mutate({ ...b, files }) }} onSaved={() => { qc.invalidateQueries({ queryKey: ['project', id] }); setPick(null); onDone?.() }} />}
      {job && <JobOutput id={id} job={job} onClose={() => { setJob(null); onDone?.() }} />}
    </div>
  )
}

export function JobOutput({ id, job, onClose, onFinished, jobUrl }) {
  const [lines, setLines] = useState([]); const [st, setSt] = useState({ done: false, code: null }); const since = useRef(0); const box = useRef(null); const qc = useQueryClient(); const { toast } = useUi()
  useEffect(() => {
    let stop = false; let timer
    const tick = async () => {
      try {
        const r = await api.get(jobUrl ? jobUrl(job.job, since.current) : `/projects/${id}/jobs/${job.job}?since=${since.current}`)
        if (stop) return
        since.current = r.next
        if (r.lines.length) setLines((l) => [...l, ...r.lines])
        setSt({ done: r.done, code: r.code })
        if (r.done) { onFinished?.(r.code); qc.invalidateQueries({ queryKey: ['project', id] }); qc.invalidateQueries({ queryKey: ['project-git', id] }); qc.invalidateQueries({ queryKey: ['project-tree', id] }); qc.invalidateQueries({ queryKey: ['projects'] }); qc.invalidateQueries({ queryKey: ['docker-list'] }); toast(r.code === 0 ? `${TITLES[job.action] || 'Commande'} : terminé` : `${TITLES[job.action] || 'Commande'} : échec (code ${r.code})`, r.code === 0 ? 'ok' : 'err'); return }
      } catch (e) { if (!stop) { setSt({ done: true, code: -1 }); setLines((l) => [...l, e.message]); return } }
      if (!stop) timer = setTimeout(tick, 900)
    }
    tick()
    return () => { stop = true; clearTimeout(timer) }
  }, [job.job]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight }, [lines])
  return (
    <Modal wide title={<span className="row" style={{ gap: 8 }}>{TITLES[job.action] || 'Commande'}{st.done ? <Badge kind={st.code === 0 ? 'ok' : 'err'}>{st.code === 0 ? 'terminé' : `échec (${st.code})`}</Badge> : <Badge kind="info">en cours…</Badge>}</span>} onClose={onClose} footer={<Btn onClick={onClose}>{st.done ? 'Fermer' : 'Fermer (la commande continue)'}</Btn>}>
      <div className="col"><div className="muted small mono">{job.label}</div>
        <div className="dk-logs" ref={box}>{lines.length === 0 ? <div className="muted">En attente de la sortie…</div> : lines.map((l, i) => <div key={i}>{stripAnsi(l)}</div>)}</div></div>
    </Modal>
  )
}


// Quels fichiers compose utiliser ? Docker Compose les fusionne dans l'ordre : le dernier l'emporte. Sans choix : le principal + son override (comme « docker compose up »).
function ComposeFilesModal({ id, compose, launch, onClose, onLaunch, onSaved }) {
  const { toast } = useUi()
  const [sel, setSel] = useState(compose.choice.length ? compose.choice : compose.default); const [keep, setKeep] = useState(true)
  const toggle = (f) => setSel((s) => (s.includes(f) ? s.filter((x) => x !== f) : [...s, f]))
  const move = (f, d) => setSel((s) => { const i = s.indexOf(f); const j = i + d; if (j < 0 || j >= s.length) return s; const n = [...s]; [n[i], n[j]] = [n[j], n[i]]; return n })
  const save = useMutation({ mutationFn: (files) => api.put(`/projects/${id}/compose-files`, { files }), onSuccess: () => { toast('Choix enregistré'); onSaved() }, onError: (e) => toast(e.message, 'err') })
  const go = () => (launch ? (keep ? save.mutateAsync(sel).then(() => onLaunch(sel)).catch(() => {}) : onLaunch(sel)) : save.mutate(sel))
  return (
    <Modal wide title={launch ? 'Avec quels fichiers compose lancer ?' : 'Fichiers compose du projet'} onClose={onClose}
      footer={<><Btn onClick={onClose}>Annuler</Btn>{!launch && compose.choice.length > 0 && <Btn busy={save.isPending} onClick={() => save.mutate([])} title="Revenir au comportement de Docker Compose : le fichier principal et son override">Automatique</Btn>}
        <Btn kind="primary" disabled={!sel.length} busy={save.isPending} onClick={go}>{launch ? 'Lancer avec ces fichiers' : 'Enregistrer'}</Btn></>}>
      <div className="col">
        <div className="muted">Ce dossier contient plusieurs fichiers compose. Cochez ceux à utiliser ensemble : Docker Compose les fusionne dans l'ordre (le dernier l'emporte sur un même réglage). Sans choix, il prend le fichier principal et son <code>override</code>.</div>
        {compose.last.length > 0 && <div className="banner info" style={{ margin: 0 }}>Dernier lancement : <b className="mono">{compose.last.join(' + ')}</b>. L'arrêt, le démarrage et le redémarrage reprennent ces fichiers tant que vous n'en choisissez pas d'autres.</div>}
        <div className="col" style={{ gap: 6 }}>
          {compose.candidates.map((f) => (
            <label key={f} className="row" style={{ gap: 8, cursor: 'pointer' }}>
              <input type="checkbox" checked={sel.includes(f)} onChange={() => toggle(f)} /><span className="mono">{f}</span>
              {compose.default.includes(f) && <Badge title="Chargé d'office par docker compose">par défaut</Badge>}
              {sel.includes(f) && <span className="right row" style={{ gap: 2 }}><span className="muted small">{sel.indexOf(f) + 1}</span><Btn small kind="ghost" title="Charger plus tôt" onClick={(e) => { e.preventDefault(); move(f, -1) }}><ArrowUp size={12} /></Btn><Btn small kind="ghost" title="Charger plus tard (l'emporte)" onClick={(e) => { e.preventDefault(); move(f, 1) }}><ArrowDown size={12} /></Btn></span>}
            </label>))}
        </div>
        {launch && <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />Retenir ce choix pour ce projet (modifiable ensuite)</label>}
      </div>
    </Modal>
  )
}
