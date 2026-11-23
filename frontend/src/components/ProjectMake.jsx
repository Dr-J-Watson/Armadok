import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Play, Terminal } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Empty, Field, Loading, Modal, useUi } from './ui.jsx'
import { JobOutput } from './ComposeBar.jsx'
import MakeTerminal from './MakeTerminal.jsx'

// Les commandes (cibles) du Makefile du projet, avec leur description (« cible: ## texte » ou le commentaire au-dessus), et de quoi les lancer.
export default function ProjectMake({ id }) {
  const q = useQuery({ queryKey: ['project-make', id], queryFn: () => api.get(`/projects/${id}/make`) })
  const { confirm, toast } = useUi(); const [job, setJob] = useState(null); const [busy, setBusy] = useState(''); const [args, setArgs] = useState(null); const [term, setTerm] = useState(null)   // term : cible ouverte dans le terminal interactif
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data
  if (!d.file) return <div className="card"><Empty><Terminal size={28} /><div>Aucun Makefile dans ce projet.</div></Empty></div>
  const WARN = "Les commandes du Makefile s'exécutent dans le dossier du projet, DANS le conteneur d'Armadok (outils disponibles : make, docker, git). Elles peuvent modifier des fichiers et des conteneurs. Un Makefile qui demande d'autres outils (npm, python…) échouera."
  const go = async (t, vars) => {
    setBusy(t.name)
    try { setJob({ ...(await api.post(`/projects/${id}/make`, { target: t.name, vars: vars || {} })), action: 'make' }) } catch (e) { toast(e.message, 'err') } finally { setBusy('') }
  }
  // bg = false : dans un terminal (on voit la sortie et on peut répondre aux questions) ; bg = true : en tâche de fond, sans saisie possible (longues commandes)
  const start = (t, vars, bg) => (bg ? go(t, vars) : setTerm({ target: t.name, vars: vars || {} }))
  const run = async (t, bg = false) => {
    if (t.vars?.length) { setArgs({ t, bg }); return }   // la fenêtre des arguments sert aussi de confirmation
    if (!await confirm({ title: `Lancer « make ${t.name} » ?`, message: WARN, confirmLabel: 'Lancer' })) return
    start(t, {}, bg)
  }
  return (
    <div className="col">
      <div className="muted small">Les cibles de <b className="mono">{d.file}</b>. La description vient de « <code>cible: ## texte</code> » ou du commentaire juste au-dessus.</div>
      {d.make === false && <div className="banner warn">La commande make n'est pas installée dans cette image : les cibles se listent mais ne se lancent pas.</div>}
      {!d.canRun && <div className="banner info">Lancer une cible demande le droit « terminal dans les conteneurs » (et « déployer »), et, sur un worker, WORKER_ALLOW_EXEC=true : ce sont des commandes exécutées sur la machine.</div>}
      {d.error && <div className="banner err">{d.error}</div>}
      {d.targets.length === 0 ? <div className="card"><Empty><Terminal size={28} /><div>Aucune cible repérée dans ce Makefile.</div></Empty></div> : (
        <div className="card flush">{d.targets.map((t) => (
          <div key={t.name} className="mk-row">
            <span className="row wrap" style={{ gap: 6 }}><b className="mono">{t.name}</b>{t.phony && <Badge title="Déclarée .PHONY : une commande, pas un fichier à produire">cmd</Badge>}{(t.vars || []).filter((v) => v.required).map((v) => <Badge key={v.name} kind="warn" title="Argument obligatoire : il sera demandé au lancement">{v.name} *</Badge>)}</span>
            <span className="mk-doc small">{t.doc || <span className="muted">—</span>}{t.deps.length > 0 && <span className="muted"> · d'abord : {t.deps.join(', ')}</span>}{(t.vars || []).some((v) => !v.required) && <span className="muted"> · options : {t.vars.filter((v) => !v.required).map((v) => v.name).join(', ')}</span>}</span>
            <span className="row" style={{ gap: 4 }}>
              <Btn small kind="primary" disabled={!d.canRun || d.make === false} onClick={() => run(t)} title={d.canRun ? `Lancer make ${t.name} dans un terminal : vous voyez la sortie et pouvez répondre aux questions` : 'Droit « terminal » requis'}><Play size={12} /> Lancer</Btn>
              <Btn small kind="ghost" disabled={!d.canRun || d.make === false} busy={busy === t.name} onClick={() => run(t, true)} title="Lancer en arrière-plan : la commande continue même si vous fermez la fenêtre, mais elle ne peut pas poser de question"><Terminal size={12} /></Btn>
            </span>
          </div>))}</div>)}
      {args && <MakeArgs t={args.t} warn={WARN} onClose={() => setArgs(null)} onRun={(vars) => { const a = args; setArgs(null); start(a.t, vars, a.bg) }} />}
      {term && <MakeTerminal project={Number(id)} target={term.target} vars={term.vars} onClose={() => setTerm(null)} />}
      {job && <JobOutput id={id} job={job} onClose={() => setJob(null)} />}
    </div>
  )
}

// Les arguments d'une cible (NOM=valeur) : obligatoires ceux que le Makefile exige, les autres facultatifs (vides : ignorés, le défaut du Makefile s'applique).
function MakeArgs({ t, warn, onClose, onRun }) {
  const [vals, setVals] = useState(() => Object.fromEntries(t.vars.map((v) => [v.name, ''])))
  const missing = t.vars.some((v) => v.required && !vals[v.name].trim())
  const submit = () => { if (!missing) onRun(Object.fromEntries(Object.entries(vals).filter(([, v]) => v.trim() !== '').map(([k, v]) => [k, v.trim()]))) }
  return (
    <Modal small title={`Lancer « make ${t.name} »`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" disabled={missing} onClick={submit}><Play size={12} /> Lancer</Btn></>}>
      <div className="col">
        <div className="muted small">Cette commande attend des arguments, passés comme <span className="mono">make {t.name} NOM=valeur</span>.</div>
        {t.vars.map((v, i) => (
          <Field key={v.name} label={<span className="mono">{v.name}{v.required ? ' *' : ''}</span>} help={v.required ? 'Obligatoire : le Makefile s\'arrête s\'il est absent.' : v.default ? `Facultatif. Si vous laissez vide : « ${v.default} » (défaut du Makefile).` : 'Facultatif. Laissez vide pour ne pas le fournir.'}>
            <input className="input mono" autoFocus={i === 0} type={/TOKEN|PASS|SECRET|KEY/i.test(v.name) ? 'password' : 'text'} autoComplete="off" value={vals[v.name]} maxLength={1000} placeholder={v.default || ''}
              onChange={(e) => setVals({ ...vals, [v.name]: e.target.value })} onKeyDown={(e) => { if (e.key === 'Enter') submit() }} />
          </Field>))}
        <div className="muted small">{warn}</div>
      </div>
    </Modal>
  )
}
