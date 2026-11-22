import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Plus, RotateCcw, SlidersHorizontal, Trash2, TriangleAlert } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Loading, Modal, useUi } from './ui.jsx'

const LABELS = {
  up: ['Lancer / déployer', 'Le bouton « Lancer » (docker compose up -d).'], stop: ['Arrêter', 'Le bouton « Arrêter ».'], start: ['Démarrer', 'Démarre des conteneurs déjà créés.'],
  restart: ['Redémarrer', 'Le bouton « Redémarrer ».'], recreate: ['Redéployer (recréer)', 'Recrée les conteneurs même sans changement.'],
  pull: ['Télécharger les images', 'Le bouton « Télécharger » de l\'onglet Images.'], build: ['Construire', 'Construit les images du projet.'], down: ['Supprimer les conteneurs (down)', 'Arrête puis supprime les conteneurs et les réseaux du projet.'],
}
const KIND = { compose: 'docker compose', make: 'cible du Makefile', cmd: 'commande' }

const describe = (s) => (s.kind === 'compose' ? `docker compose ${s.action}` : s.kind === 'make' ? `make ${s.target}${Object.keys(s.vars || {}).length ? ` (${Object.keys(s.vars).join(', ')})` : ''}` : s.line)
const toVars = (txt) => Object.fromEntries(txt.split(/\s+/).filter((x) => x.includes('=')).map((x) => [x.slice(0, x.indexOf('=')), x.slice(x.indexOf('=') + 1)]))
const fromVars = (v) => Object.entries(v || {}).map(([k, x]) => `${k}=${x}`).join(' ')

// Onglet « Avancé » : remplacer le comportement d'une action (lancer, arrêter, redéployer…) par une suite d'étapes lancées d'affilée.
export default function ProjectActions({ id }) {
  const qc = useQueryClient(); const { toast } = useUi(); const [edit, setEdit] = useState(null)
  const q = useQuery({ queryKey: ['project-actions', id], queryFn: () => api.get(`/projects/${id}/actions`) })
  const save = useMutation({ mutationFn: ({ action, steps }) => api.put(`/projects/${id}/actions/${action}`, { steps }), onSuccess: (_, v) => { qc.invalidateQueries({ queryKey: ['project-actions', id] }); setEdit(null); toast(v.steps.length ? 'Action personnalisée enregistrée' : 'Comportement normal rétabli') }, onError: (e) => toast(e.message, 'err') })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data
  return (
    <div className="col">
      <div className="muted small">Pour chaque action, vous pouvez remplacer la commande normale par <b>une suite d'étapes</b> lancées l'une après l'autre ; elle s'arrête à la première qui échoue. Les boutons de la barre du projet (Lancer, Arrêter…) lancent alors votre suite. Une action sur un seul service (« redéployer cette image ») et « supprimer avec les volumes » gardent la commande normale.</div>
      {!d.canExec && <div className="banner info"><TriangleAlert size={14} /> Les étapes « cible du Makefile » et « commande » demandent le droit « terminal dans les conteneurs » et, sur un worker, WORKER_ALLOW_EXEC=true. Sans cela, seules les étapes docker compose sont possibles.</div>}
      <div className="card flush">{d.names.map((a) => { const steps = d.actions[a]; const [lab, help] = LABELS[a] || [a, '']; return (
        <div key={a} className="mk-row">
          <span className="row wrap" style={{ gap: 6 }}><b>{lab}</b>{steps ? <Badge kind="info">{steps.length} étape{steps.length > 1 ? 's' : ''}</Badge> : <Badge>normal</Badge>}</span>
          <span className="mk-doc small">{steps ? steps.map((s, i) => <div key={i} className="mono ellipsis" style={{ maxWidth: 520 }}>{i + 1}. {s.kind === 'cmd' ? s.line.split(/\s+/)[0] + (s.line.trim().includes(' ') ? ' …' : '') : describe(s)}</div>) : <span className="muted">{help}</span>}</span>
          <Btn small onClick={() => setEdit({ action: a, steps: steps || [] })}><SlidersHorizontal size={12} /> {steps ? 'Modifier' : 'Personnaliser'}</Btn>
        </div>) })}</div>
      {edit && <Editor edit={edit} canExec={d.canExec} max={d.max} busy={save.isPending} onClose={() => setEdit(null)} onSave={(steps) => save.mutate({ action: edit.action, steps })} />}
    </div>
  )
}

function Editor({ edit, canExec, max, busy, onClose, onSave }) {
  const [steps, setSteps] = useState(edit.steps.map((s) => (s.kind === 'make' ? { ...s, argsText: fromVars(s.vars) } : s)))
  const upd = (i, p) => setSteps(steps.map((s, j) => (j === i ? { ...s, ...p } : s)))
  const move = (i, k) => { const n = [...steps]; [n[i], n[i + k]] = [n[i + k], n[i]]; setSteps(n) }
  const add = (kind) => setSteps([...steps, kind === 'compose' ? { kind, action: 'pull' } : kind === 'make' ? { kind, target: '', argsText: '' } : { kind, line: '' }])
  const clean = steps.map((s) => (s.kind === 'make' ? { kind: 'make', target: s.target.trim(), vars: toVars(s.argsText || '') } : s.kind === 'cmd' ? { kind: 'cmd', line: s.line.trim() } : { kind: 'compose', action: s.action }))
  const valid = clean.every((s) => (s.kind === 'make' ? s.target : s.kind === 'cmd' ? s.line : true))
  const locked = !canExec && steps.some((s) => s.kind !== 'compose')   // des commandes déjà là : on ne les touche pas sans le droit
  return (
    <Modal wide title={`Personnaliser : ${(LABELS[edit.action] || [edit.action])[0]}`} onClose={onClose}
      footer={<>{edit.steps.length > 0 && <Btn kind="ghost" className="left" busy={busy} disabled={!canExec && edit.steps.some((s) => s.kind !== 'compose')} onClick={() => onSave([])} title="Reprendre la commande normale de cette action"><RotateCcw size={13} /> Comportement normal</Btn>}<Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={!steps.length || !valid || locked} onClick={() => onSave(clean)}>Enregistrer</Btn></>}>
      <div className="col" style={{ gap: 10 }}>
        <div className="muted small">Les étapes se lancent dans l'ordre, dans le dossier du projet. Une commande est lancée <b>sans shell</b> : <code>;</code>, <code>&amp;&amp;</code>, <code>|</code> et <code>$(…)</code> n'ont aucun effet (pour enchaîner ou scripter, utilisez une cible du Makefile). Elle s'exécute dans le conteneur d'Armadok (outils : docker, git, make).</div>
        {steps.map((s, i) => (
          <div key={i} className="card col" style={{ gap: 8 }}>
            <div className="row" style={{ gap: 6 }}><b className="grow small">Étape {i + 1} · {KIND[s.kind]}</b>
              <Btn small kind="ghost" icon disabled={i === 0} aria-label="Monter" onClick={() => move(i, -1)}><ArrowUp size={13} /></Btn>
              <Btn small kind="ghost" icon disabled={i === steps.length - 1} aria-label="Descendre" onClick={() => move(i, 1)}><ArrowDown size={13} /></Btn>
              <Btn small kind="danger" icon aria-label="Retirer" disabled={!canExec && s.kind !== 'compose'} onClick={() => setSteps(steps.filter((_, j) => j !== i))}><Trash2 size={13} /></Btn></div>
            {s.kind === 'compose' && <select className="select" style={{ maxWidth: 360 }} value={s.action} onChange={(e) => upd(i, { action: e.target.value })}>{Object.entries(LABELS).map(([k, [l]]) => <option key={k} value={k}>docker compose {k} — {l}</option>)}</select>}
            {s.kind === 'make' && <div className="row wrap" style={{ gap: 8 }}><input className="input mono" style={{ maxWidth: 220 }} disabled={!canExec} placeholder="cible (ex. deploy)" value={s.target} onChange={(e) => upd(i, { target: e.target.value })} /><input className="input mono grow" disabled={!canExec} placeholder="arguments : ENV=prod TAG=1.2 (facultatif)" value={s.argsText} onChange={(e) => upd(i, { argsText: e.target.value })} /></div>}
            {s.kind === 'cmd' && <input className="input mono" disabled={!canExec} placeholder="ex. docker image prune -f" value={s.line} onChange={(e) => upd(i, { line: e.target.value })} spellCheck={false} />}
          </div>))}
        {steps.length < max && <div className="row wrap" style={{ gap: 6 }}><Btn small onClick={() => add('compose')}><Plus size={13} /> docker compose</Btn><Btn small disabled={!canExec} onClick={() => add('make')} title={canExec ? '' : 'Droit « terminal » requis'}><Plus size={13} /> Cible du Makefile</Btn><Btn small disabled={!canExec} onClick={() => add('cmd')} title={canExec ? '' : 'Droit « terminal » requis'}><Plus size={13} /> Commande</Btn></div>}
        <div className="muted small">Le journal des actions note le type de chaque étape, jamais le texte d'une commande (il peut contenir un secret).</div>
      </div>
    </Modal>
  )
}
