import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api.js'
import { ChevronDown, ChevronRight, Folder, FolderOpen } from 'lucide-react'
import { Btn, Field, Loading, Modal, Seg, useUi } from './ui.jsx'
import { buildRows, FileMark } from './ProjectFiles.jsx'
import { ManageTemplates } from './NewProject.jsx'
import { kindOf } from '../lib/files.js'
import { fmtSize } from '../lib/docker.js'

const SKIP_DIR = /(^|\/)(data|logs?|node_modules|\.git|backups?|cache|tmp|volumes?|world[^/]*|db)(\/|$)/i
const TEXT_EXT = /\.(ya?ml|env|json|conf|cfg|ini|toml|txt|md|sh|properties|xml|sql|lua|py|js|css|html)$|(^|\/)(Makefile|Dockerfile|\.env[^/]*)$/i
const isCompose = (p) => /(^|\/)(docker-)?compose([._-][^/]*)?\.ya?ml$/.test(p)

// Enregistre un projet comme modèle : on choisit les fichiers (le compose et le .env d'office), les secrets du .env sont vidés.
export default function SaveTemplate({ id, name, onClose }) {
  const { toast } = useUi(); const qc = useQueryClient()
  const tree = useQuery({ queryKey: ['project-tree', id], queryFn: () => api.get(`/projects/${id}/tree`) })
  const SKIP_ALWAYS = /(^|\/)(\.git|node_modules)(\/|$)/
  const files = (tree.data?.entries || []).filter((e) => e.type === 'file' && !SKIP_ALWAYS.test(e.path) && TEXT_EXT.test(e.path) && e.size <= 256 * 1024)
  const paths = new Set(files.map((e) => e.path))
  const size = (p) => files.find((e) => e.path === p)?.size || 0
  const defaults = files.filter((e) => !SKIP_DIR.test(e.path) && (isCompose(e.path) || e.path === '.env' || e.path === 'Makefile' || (!e.path.includes('/') && e.size <= 32 * 1024 && /\.(ya?ml|conf|json|toml|properties)$/.test(e.path)))).map((e) => e.path)
  const [tname, setTname] = useState(name); const [desc, setDesc] = useState(''); const [keep, setKeep] = useState(false); const [shared, setShared] = useState(false); const [manage, setManage] = useState(false)
  const [sel, setSel] = useState(null); const [collapsed, setCollapsed] = useState(null)
  const chosen = sel ?? defaults
  const entries = useMemo(() => {   // fichiers retenus + les dossiers qui les contiennent (un dossier sans fichier utilisable n'est pas montré)
    const dirs = new Set(); files.forEach((e) => { const parts = e.path.split('/'); for (let i = 1; i < parts.length; i++) dirs.add(parts.slice(0, i).join('/')) })
    return [...[...dirs].map((d) => ({ path: d, type: 'dir' })), ...files]
  }, [tree.data]) // eslint-disable-line react-hooks/exhaustive-deps
  const coll = collapsed ?? new Set(entries.filter((e) => e.type === 'dir' && SKIP_DIR.test(e.path)).map((e) => e.path))   // data, logs, world… repliés d'office
  const below = (dir) => files.filter((e) => e.path.startsWith(dir + '/')).map((e) => e.path)
  const toggleFile = (p) => setSel(chosen.includes(p) ? chosen.filter((x) => x !== p) : [...chosen, p])
  const toggleDir = (d) => { const all = below(d); const full = all.every((x) => chosen.includes(x)); setSel(full ? chosen.filter((x) => !all.includes(x)) : [...new Set([...chosen, ...all])]) }
  const toggleFold = (d) => { const n = new Set(coll); n.has(d) ? n.delete(d) : n.add(d); setCollapsed(n) }
  const rows = useMemo(() => buildRows(entries, coll), [entries, collapsed]) // eslint-disable-line react-hooks/exhaustive-deps
  const total = chosen.reduce((n, p) => n + size(p), 0)
  const tooMany = chosen.length > 60 || total > 1024 * 1024
  const hasEnv = chosen.includes('.env')
  const save = useMutation({ mutationFn: () => api.post('/projects/templates', { name: tname.trim(), description: desc.trim(), projectId: Number(id), files: chosen, shared, keepAllSecrets: keep && !shared }), onSuccess: () => { qc.invalidateQueries({ queryKey: ['templates'] }); toast('Modèle enregistré : Nouveau projet → À partir d\'un modèle'); onClose() }, onError: (e) => toast(e.message, 'err') })
  if (manage) return <ManageTemplates onClose={() => setManage(false)} />
  return (
    <Modal wide title="Enregistrer ce projet comme modèle" onClose={onClose} footer={<><Btn kind="ghost" title="Renommer, décrire, partager ou supprimer vos modèles" onClick={() => setManage(true)}>Gérer mes modèles</Btn><span className="grow" /><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" disabled={!tname.trim() || !chosen.some(isCompose) || tooMany} busy={save.isPending} onClick={() => save.mutate()}>Enregistrer le modèle</Btn></>}>
      <div className="col gap-lg">
        <div className="muted">Un modèle garde des fichiers de ce projet. Pour en créer un nouveau, on remplit les variables de son <code>.env</code> (valeur de départ proposée, champ masqué pour les secrets) ; le nom de ce projet devient un paramètre (<code>{'{{name}}'}</code>) dans les fichiers.</div>
        <div className="fgrid"><Field label="Nom du modèle"><input className="input" value={tname} maxLength={80} onChange={(e) => setTname(e.target.value)} /></Field>
          <Field label="Description" help="Facultatif : à quoi il sert."><input className="input" value={desc} maxLength={300} onChange={(e) => setDesc(e.target.value)} placeholder="Serveur Minecraft Paper, 2 Go…" /></Field></div>
        <Field label="Qui peut l'utiliser ?"><Seg value={shared ? 'shared' : 'private'} onChange={(v) => setShared(v === 'shared')} options={[{ id: 'private', label: 'Privé', hint: 'Vous seul le voyez et l\'utilisez (un administrateur le voit aussi)' }, { id: 'shared', label: 'Partagé', hint: 'Tous ceux qui peuvent créer des projets sur ce serveur : les secrets sont toujours vidés' }]} /></Field>
        <div className="col" style={{ gap: 4 }}>
          <b>Fichiers à garder</b>
          {tree.isLoading ? <Loading /> : files.length === 0 ? <div className="muted small">Aucun fichier texte utilisable dans ce projet.</div> : (
            <div className="ft card" style={{ maxHeight: '44vh', overflow: 'auto', padding: 6 }} role="tree">
              {rows.map((e) => {
                const dir = e.type === 'dir'; const all = dir ? below(e.path) : []; const on = dir ? all.length > 0 && all.every((x) => chosen.includes(x)) : chosen.includes(e.path); const some = dir && !on && all.some((x) => chosen.includes(x)); const k = dir ? null : kindOf(e.path)
                return (
                  <div key={e.path} className="ft-item" style={{ paddingLeft: 4 + e.depth * 14 }} role="treeitem">
                    <input type="checkbox" checked={on} ref={(el) => { if (el) el.indeterminate = some }} onChange={() => (dir ? toggleDir(e.path) : toggleFile(e.path))} aria-label={e.path} style={{ margin: '0 6px' }} />
                    <button type="button" className="ft-main" onClick={() => (dir ? toggleFold(e.path) : toggleFile(e.path))} title={e.path}>
                      {dir ? (coll.has(e.path) ? <ChevronRight size={14} className="muted" /> : <ChevronDown size={14} className="muted" />) : <span className="ft-sp" />}
                      {dir ? (coll.has(e.path) ? <Folder size={15} className="fk fk-dir" /> : <FolderOpen size={15} className="fk fk-dir" />) : <FileMark path={e.path} />}
                      <span className="ellipsis">{e.name}</span>
                      {k?.key && <span className={'ft-tag fk-bg-' + k.id}>{k.label}</span>}
                      {dir ? <span className="muted small" style={{ marginLeft: 'auto' }}>{all.filter((x) => chosen.includes(x)).length}/{all.length}</span> : <span className="muted small" style={{ marginLeft: 'auto' }}>{fmtSize(size(e.path))}</span>}
                    </button>
                  </div>)
              })}
            </div>)}
          <div className="row wrap" style={{ gap: 8 }}>
            <span className={'small ' + (tooMany ? '' : 'muted')} style={tooMany ? { color: 'var(--err)' } : undefined}>{chosen.length} fichier{chosen.length > 1 ? 's' : ''} · {fmtSize(total)}{tooMany ? ' — trop gros (60 fichiers et 1 Mo au plus)' : ''}</span>
            <span className="right row" style={{ gap: 6 }}><Btn small kind="ghost" onClick={() => setSel(defaults)}>Fichiers par défaut</Btn><Btn small kind="ghost" onClick={() => setSel([])}>Tout décocher</Btn></span>
          </div>
          <div className="muted small">Les dossiers de données (data, logs, world…) sont repliés et décochés d'office ; les binaires et les fichiers de plus de 256 Ko ne sont pas proposés.</div>
        </div>
        {hasEnv && !shared && <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />Garder aussi les valeurs des secrets du .env (mots de passe, jetons, clés)<span className="muted">— sinon elles sont vidées et à ressaisir à chaque création</span></label>}
        <div className="muted small">Le modèle est chiffré en base. Vous pourrez le passer de privé à partagé (et inversement) plus tard ; en le partageant, les valeurs des secrets sont vidées.</div>
      </div>
    </Modal>
  )
}
