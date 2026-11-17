import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Play, Terminal } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Empty, Loading, useUi } from './ui.jsx'
import { JobOutput } from './ComposeBar.jsx'

// Les commandes (cibles) du Makefile du projet, avec leur description (« cible: ## texte » ou le commentaire au-dessus), et de quoi les lancer.
export default function ProjectMake({ id }) {
  const q = useQuery({ queryKey: ['project-make', id], queryFn: () => api.get(`/projects/${id}/make`) })
  const { confirm, toast } = useUi(); const [job, setJob] = useState(null); const [busy, setBusy] = useState('')
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data
  if (!d.file) return <div className="card"><Empty><Terminal size={28} /><div>Aucun Makefile dans ce projet.</div></Empty></div>
  const run = async (t) => {
    if (!await confirm({ title: `Lancer « make ${t.name} » ?`, message: `Les commandes du Makefile s'exécutent dans le dossier du projet, DANS le conteneur d'Armadok (outils disponibles : make, docker, git). Elles peuvent modifier des fichiers et des conteneurs. Un Makefile qui demande d'autres outils (npm, python…) échouera.`, confirmLabel: 'Lancer' })) return
    setBusy(t.name)
    try { setJob({ ...(await api.post(`/projects/${id}/make`, { target: t.name })), action: 'make' }) } catch (e) { toast(e.message, 'err') } finally { setBusy('') }
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
            <span className="row" style={{ gap: 6 }}><b className="mono">{t.name}</b>{t.phony && <Badge title="Déclarée .PHONY : une commande, pas un fichier à produire">cmd</Badge>}</span>
            <span className="mk-doc small">{t.doc || <span className="muted">—</span>}{t.deps.length > 0 && <span className="muted"> · d'abord : {t.deps.join(', ')}</span>}</span>
            <Btn small kind="primary" disabled={!d.canRun || d.make === false} busy={busy === t.name} onClick={() => run(t)} title={d.canRun ? `Lancer make ${t.name}` : 'Droit « terminal » requis'}><Play size={12} /> Lancer</Btn>
          </div>))}</div>)}
      {job && <JobOutput id={id} job={job} onClose={() => setJob(null)} />}
    </div>
  )
}
