import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api.js'
import { Btn, Field, Loading, Modal, useUi } from './ui.jsx'
import { fmtSize } from '../lib/docker.js'

const SKIP_DIR = /(^|\/)(data|logs?|node_modules|\.git|backups?|cache|tmp|volumes?|world[^/]*|db)(\/|$)/i
const TEXT_EXT = /\.(ya?ml|env|json|conf|cfg|ini|toml|txt|md|sh|properties|xml|sql|lua|py|js|css|html)$|(^|\/)(Makefile|Dockerfile|\.env[^/]*)$/i
const isCompose = (p) => /(^|\/)(docker-)?compose([._-][^/]*)?\.ya?ml$/.test(p)

// Enregistre un projet comme modèle : on choisit les fichiers (le compose et le .env d'office), les secrets du .env sont vidés.
export default function SaveTemplate({ id, name, onClose }) {
  const { toast } = useUi(); const qc = useQueryClient()
  const tree = useQuery({ queryKey: ['project-tree', id], queryFn: () => api.get(`/projects/${id}/tree`) })
  const files = (tree.data?.entries || []).filter((e) => e.type === 'file' && !SKIP_DIR.test(e.path) && TEXT_EXT.test(e.path) && e.size <= 256 * 1024)
  const [sel, setSel] = useState(null); const [tname, setTname] = useState(name); const [desc, setDesc] = useState(''); const [keep, setKeep] = useState(false)
  const chosen = sel ?? files.filter((e) => isCompose(e.path) || e.path === '.env' || e.path === 'Makefile' || (!e.path.includes('/') && e.size <= 32 * 1024 && /\.(ya?ml|conf|json|toml|properties)$/.test(e.path))).map((e) => e.path)
  const toggle = (p) => setSel(chosen.includes(p) ? chosen.filter((x) => x !== p) : [...chosen, p])
  const hasEnv = chosen.includes('.env')
  const save = useMutation({ mutationFn: () => api.post('/projects/templates', { name: tname.trim(), description: desc.trim(), projectId: Number(id), files: chosen, keepAllSecrets: keep }), onSuccess: () => { qc.invalidateQueries({ queryKey: ['templates'] }); toast('Modèle enregistré : Nouveau projet → À partir d\'un modèle'); onClose() }, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal wide title="Enregistrer ce projet comme modèle" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" disabled={!tname.trim() || !chosen.some(isCompose)} busy={save.isPending} onClick={() => save.mutate()}>Enregistrer le modèle</Btn></>}>
      <div className="col gap-lg">
        <div className="muted">Un modèle garde des fichiers de ce projet. Pour en créer un nouveau, on remplit les variables de son <code>.env</code> (valeur de départ proposée, champ masqué pour les secrets) ; le nom de ce projet devient un paramètre (<code>{'{{name}}'}</code>) dans les fichiers.</div>
        <div className="fgrid"><Field label="Nom du modèle"><input className="input" value={tname} maxLength={80} onChange={(e) => setTname(e.target.value)} /></Field>
          <Field label="Description" help="Facultatif : à quoi il sert."><input className="input" value={desc} maxLength={300} onChange={(e) => setDesc(e.target.value)} placeholder="Serveur Minecraft Paper, 2 Go…" /></Field></div>
        <div className="col" style={{ gap: 4 }}>
          <b>Fichiers à garder</b>
          {tree.isLoading ? <Loading /> : files.length === 0 ? <div className="muted small">Aucun fichier texte utilisable dans ce projet.</div> : files.map((e) => (
            <label key={e.path} className="row" style={{ gap: 8, cursor: 'pointer' }}><input type="checkbox" checked={chosen.includes(e.path)} onChange={() => toggle(e.path)} /><span className="mono small">{e.path}</span><span className="muted small">{fmtSize(e.size)}</span></label>))}
          <div className="muted small">Les dossiers de données (data, logs, world…), les fichiers lourds et les binaires sont écartés d'office.</div>
        </div>
        {hasEnv && <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />Garder aussi les valeurs des secrets du .env (mots de passe, jetons, clés)<span className="muted">— sinon elles sont vidées et à ressaisir à chaque création</span></label>}
        <div className="muted small">Le modèle est chiffré en base et partagé avec les personnes qui peuvent créer des projets sur ce serveur.</div>
      </div>
    </Modal>
  )
}
