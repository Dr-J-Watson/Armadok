import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Download, MoreHorizontal, Play, Power, RotateCw, Square } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Modal, useUi } from './ui.jsx'
import { stripAnsi } from '../lib/docker.js'

const TITLES = { 'git-pull': 'Récupération des changements (git pull)', 'git-fetch': 'Interrogation du dépôt', 'git-checkout': 'Changement de version', 'git-clone': 'Clonage du dépôt', up: 'Déploiement', recreate: 'Recréation', down: 'Arrêt et suppression des conteneurs', restart: 'Redémarrage', stop: 'Arrêt', start: 'Démarrage', pull: 'Téléchargement des images', build: 'Construction des images', config: 'Configuration résolue', ps: 'État' }

// Boutons d'action d'un projet (docker compose) ; chaque commande s'exécute en tâche de fond et sa sortie s'affiche en direct.
export default function ComposeBar({ id, perms, onDone }) {
  const { confirm, toast } = useUi(); const [job, setJob] = useState(null); const [menu, setMenu] = useState(false)
  const run = useMutation({ mutationFn: (body) => api.post(`/projects/${id}/compose`, body), onSuccess: (r, v) => { setMenu(false); setJob({ ...r, action: v.action }) }, onError: (e) => toast(e.message, 'err') })
  if (!perms.deploy) return null
  const go = (body) => run.mutate(body)
  const ask = async (title, message, body, label) => { if (await confirm({ title, message, confirmLabel: label })) go(body) }
  return (
    <>
      <div className="row wrap" style={{ gap: 6, position: 'relative' }}>
        <Btn kind="primary" busy={run.isPending} onClick={() => go({ action: 'up' })} title="docker compose up -d : crée ou met à jour les conteneurs d'après les fichiers"><Play size={14} /> Déployer</Btn>
        <Btn busy={run.isPending} onClick={() => go({ action: 'restart' })}><RotateCw size={14} /> Redémarrer</Btn>
        <Btn busy={run.isPending} onClick={() => go({ action: 'pull' })} title="Télécharge les nouvelles versions des images (sans redéployer)"><Download size={14} /> Pull</Btn>
        <Btn busy={run.isPending} onClick={() => ask('Arrêter le projet ?', 'Les conteneurs sont arrêtés mais gardés ; « Démarrer » ou « Déployer » les relance.', { action: 'stop' }, 'Arrêter')}><Square size={14} /> Arrêter</Btn>
        <Btn busy={run.isPending} onClick={() => ask('Down : supprimer les conteneurs ?', 'Les conteneurs et les réseaux du projet sont supprimés. Les volumes (les données) sont conservés.', { action: 'down' }, 'Down')}><Power size={14} /> Down</Btn>
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
      {job && <JobOutput id={id} job={job} onClose={() => { setJob(null); onDone?.() }} />}
    </>
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
