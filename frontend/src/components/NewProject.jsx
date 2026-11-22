import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Boxes, FileCode2, FolderPlus, GitFork, Globe, Lock, Rocket, Trash2, Wand2 } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Field, Loading, Modal, Seg, useUi } from './ui.jsx'
import ConvertContainer from './ConvertContainer.jsx'
import RootPicker from './RootPicker.jsx'
import { useGo } from '../lib/nav.js'

const EMPTY = '# Projet vide : ajoutez vos services sous « services: ».\nservices: {}\n'

// « Nouveau projet » : vide, modèle de base, depuis un conteneur lancé à la main, depuis un dépôt Git, ou un Traefik de base.
export default function NewProject({ onClose, onClone, onTraefik, traefikMissing }) {
  const { toast } = useUi(); const go = useGo(); const qc = useQueryClient()
  const list = useQuery({ queryKey: ['convertible'], queryFn: () => api.get('/projects/from-container/list'), retry: false })
  const [mode, setMode] = useState(null); const [name, setName] = useState(''); const [conv, setConv] = useState(null); const [root, setRoot] = useState('')
  const create = useMutation({
    mutationFn: () => api.post('/projects/create', { name, files: { 'docker-compose.yml': EMPTY }, root }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['projects'] }); toast('Projet créé'); onClose(); if (r.id) go(`/projects/${r.id}`) }, onError: (e) => toast(e.message, 'err'),
  })
  const cs = list.data?.containers || []
  if (mode === 'tpl') return <FromTemplate onBack={() => setMode(null)} onClose={onClose} />
  if (mode === 'container' && !conv) return <PickContainer onPick={setConv} onClose={() => setMode(null)} />
  if (conv) return <ConvertContainer container={conv} onClose={() => { setConv(null); onClose() }} />
  const Card = ({ id, icon, title, text, onClick, off }) => (
    <button type="button" className={'card np-card ' + (mode === id ? 'on' : '')} disabled={off} onClick={onClick || (() => setMode(id))}><div className="row" style={{ gap: 8 }}>{icon}<b>{title}</b></div><div className="muted small">{text}</div></button>)
  return (
    <Modal wide title="Nouveau projet" onClose={onClose} footer={mode ? <><Btn onClick={() => setMode(null)}>Retour</Btn><Btn kind="primary" disabled={!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$/.test(name)} busy={create.isPending} onClick={() => create.mutate()}>Créer le projet</Btn></> : <Btn onClick={onClose}>Fermer</Btn>}>
      {mode ? (
        <div className="col">
          <div className="muted">Un dossier avec un docker-compose.yml sans service : vous le remplissez dans l'éditeur.</div>
          <Field label="Nom du dossier" help="Lettres, chiffres, « - », « _ » et « . ». Créé dans le dossier des projets de ce nœud."><input className="input mono" autoFocus style={{ maxWidth: 320 }} value={name} onChange={(e) => setName(e.target.value.trim())} placeholder="mon-projet" /></Field>
          <RootPicker value={root} onChange={setRoot} />
        </div>
      ) : (
        <div className="np-grid">
          <Card id="empty" icon={<FolderPlus size={16} />} title="Projet vide" text="Un dossier et un compose sans service, à remplir vous-même." />
          <Card id="tpl" icon={<FileCode2 size={16} />} title="À partir d'un modèle" text="Le modèle de base, un exemple Minecraft, ou vos propres modèles (enregistrez un projet comme modèle depuis sa fiche)." />
          <Card id="container" icon={<Boxes size={16} />} title="Depuis un conteneur" off={list.isLoading || !cs.length || list.data?.canConvert === false}
            text={list.isLoading ? 'Recherche…' : cs.length ? `${cs.length} conteneur${cs.length > 1 ? 's' : ''} lancé${cs.length > 1 ? 's' : ''} sans projet (docker run…) : on en tire un compose et un .env.` : 'Aucun conteneur lancé hors projet sur ce nœud.'}
            onClick={() => setMode('container')} />
          <Card id="git" icon={<GitFork size={16} />} title="Cloner un dépôt Git" text="Récupère un dépôt (HTTPS ou SSH) dans un dossier de projets." onClick={() => { onClose(); onClone() }} />
          {traefikMissing && <Card id="traefik" icon={<Rocket size={16} />} title="Traefik de base" text="Aucun Traefik détecté : un projet prêt à démarrer, avec les options de votre choix." onClick={() => { onClose(); onTraefik() }} />}
        </div>)}
    </Modal>
  )
}

export function PickContainer({ onPick, onClose }) {
  const list = useQuery({ queryKey: ['convertible'], queryFn: () => api.get('/projects/from-container/list') })
  return (
    <Modal title="Quel conteneur convertir ?" onClose={onClose} footer={<Btn onClick={onClose}>Annuler</Btn>}>
      {list.isLoading ? <Loading /> : <div className="col" style={{ gap: 6 }}>{(list.data?.containers || []).map((c) => (
        <button key={c.name} type="button" className="card np-card" onClick={() => onPick(c.name)}><b>{c.name}</b> <span className="muted small">{c.image} · {c.status}{c.protected ? ` · ${c.protected === 'traefik' ? 'Traefik' : 'Armadok'}` : ''}</span></button>))}</div>}
    </Modal>
  )
}


// « À partir d'un modèle » : choisir (modèles intégrés et enregistrés), remplir le nom et les variables du .env, créer. Rien n'est démarré.
function FromTemplate({ onBack, onClose }) {
  const { toast, confirm } = useUi(); const go = useGo(); const qc = useQueryClient()
  const list = useQuery({ queryKey: ['templates'], queryFn: () => api.get('/projects/templates/list') })
  const [pick, setPick] = useState(null); const [name, setName] = useState(''); const [vals, setVals] = useState({}); const [root, setRoot] = useState('')
  const det = useQuery({ queryKey: ['template', pick], queryFn: () => api.get(`/projects/templates/${encodeURIComponent(pick)}`), enabled: !!pick })
  const vis = useMutation({ mutationFn: ({ id, shared }) => api.put(`/projects/templates/${id}`, { shared }), onSuccess: (_, v) => { qc.invalidateQueries({ queryKey: ['templates'] }); toast(v.shared ? 'Modèle partagé (valeurs des secrets vidées)' : 'Modèle privé') }, onError: (e) => toast(e.message, 'err') })
  const del = useMutation({ mutationFn: (id) => api.del(`/projects/templates/${id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ['templates'] }); toast('Modèle supprimé') }, onError: (e) => toast(e.message, 'err') })
  const shown = (v) => (vals[v.key] ?? v.default).replace(/\{\{name\}\}/g, name)
  const create = useMutation({
    mutationFn: () => api.post(`/projects/templates/${encodeURIComponent(pick)}/create`, { name, root, values: Object.fromEntries((det.data?.vars || []).map((v) => [v.key, shown(v)])) }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['projects'] }); toast('Projet créé : relisez-le, puis déployez-le'); onClose(); if (r.id) go(`/projects/${r.id}`) }, onError: (e) => toast(e.message, 'err'),
  })
  const d = det.data
  if (pick) return (
    <Modal wide title={d ? `Nouveau projet · ${d.name}` : 'Modèle'} onClose={onClose} footer={<><Btn onClick={() => { setPick(null); setVals({}) }}>Retour</Btn><Btn kind="primary" disabled={!d || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$/.test(name)} busy={create.isPending} onClick={() => create.mutate()}>Créer le projet</Btn></>}>
      {det.isLoading ? <Loading /> : det.error ? <div className="banner err">{det.error.message}</div> : (
        <div className="col gap-lg">
          {d.description && <div className="muted">{d.description}</div>}
          <Field label="Nom du dossier du projet" help="Lettres, chiffres, « - », « _ » et « . ». Remplace {{name}} dans les fichiers du modèle."><input className="input mono" autoFocus style={{ maxWidth: 320 }} value={name} onChange={(e) => setName(e.target.value.trim())} placeholder="mon-serveur" /></Field>
          <RootPicker value={root} onChange={setRoot} />
          {d.vars.length > 0 && <div className="col" style={{ gap: 10 }}><b>Variables (.env)</b>
            {d.vars.map((v) => (
              <Field key={v.key} label={<span className="mono">{v.key}</span>} help={v.help}>
                <div className="row" style={{ gap: 6 }}><input className="input mono grow" type={v.secret ? 'password' : 'text'} autoComplete="off" value={shown(v)} onChange={(e) => setVals({ ...vals, [v.key]: e.target.value })} />
                  {v.secret && <Btn small title="Génère un secret aléatoire" onClick={() => { const a = new Uint8Array(18); crypto.getRandomValues(a); setVals({ ...vals, [v.key]: btoa(String.fromCharCode(...a)).replace(/[+/=]/g, 'x') }) }}><Wand2 size={13} /> Générer</Btn>}</div>
              </Field>))}</div>}
          <div className="row wrap" style={{ gap: 6 }}><span className="muted small">Fichiers créés :</span>{d.files.map((f) => <Badge key={f}>{f}</Badge>)}</div>
          {Object.entries(d.preview || {}).filter(([k]) => /compose/.test(k)).slice(0, 1).map(([k, v]) => <details key={k}><summary className="muted small" style={{ cursor: 'pointer' }}>Aperçu de {k}</summary><pre className="dk-code" style={{ maxHeight: '30vh', overflow: 'auto' }}>{v.replace(/\{\{name\}\}/g, name || '…')}</pre></details>)}
        </div>)}
    </Modal>)
  return (
    <Modal wide title="À partir d'un modèle" onClose={onClose} footer={<Btn onClick={onBack}>Retour</Btn>}>
      {list.isLoading ? <Loading /> : (
        <div className="col" style={{ gap: 10 }}>
          <div className="np-grid">{list.data.templates.map((t) => (
            <div key={t.id} className="card np-card" role="button" tabIndex={0} onClick={() => setPick(t.id)} onKeyDown={(e) => e.key === 'Enter' && setPick(t.id)}>
              <div className="row" style={{ gap: 8 }}><b className="grow">{t.name}</b>{t.builtin ? <Badge>intégré</Badge> : <Badge kind={t.shared ? 'info' : 'warn'} title={t.shared ? `Partagé par ${t.createdBy}` : `Privé (${t.createdBy})`}>{t.shared ? <><Globe size={10} /> {t.createdBy}</> : <><Lock size={10} /> privé</>}</Badge>}
                {t.mine && <Btn small kind="ghost" title={t.shared ? 'Le rendre privé : vous seul le voyez' : 'Le partager : visible de tous ceux qui créent des projets (les valeurs des secrets sont alors vidées)'} onClick={async (e) => { e.stopPropagation(); if (t.shared || await confirm({ title: `Partager « ${t.name} » ?`, message: 'Tous ceux qui peuvent créer des projets le verront et pourront l\'utiliser. Les valeurs des secrets du .env sont vidées pour de bon.', confirmLabel: 'Partager', danger: false })) vis.mutate({ id: t.id, shared: !t.shared }) }}>{t.shared ? <Lock size={13} /> : <Globe size={13} />}</Btn>}
                {t.mine && <Btn small kind="ghost" title="Supprimer ce modèle" onClick={async (e) => { e.stopPropagation(); if (await confirm({ title: `Supprimer le modèle « ${t.name} » ?`, message: 'Les projets déjà créés ne changent pas.', confirmLabel: 'Supprimer' })) del.mutate(t.id) }}><Trash2 size={13} /></Btn>}</div>
              <div className="muted small">{t.description || `${t.files.length} fichier${t.files.length > 1 ? 's' : ''}`}</div>
              <div className="muted small">{t.vars.length} variable{t.vars.length > 1 ? 's' : ''} à régler</div>
            </div>))}</div>
          <div className="muted small">Pour ajouter un modèle : ouvrez un projet existant → « Enregistrer comme modèle ».</div>
        </div>)}
    </Modal>
  )
}


// Gérer ses modèles : renommer, décrire, rendre privé ou partagé, supprimer.
export function ManageTemplates({ onClose }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const list = useQuery({ queryKey: ['templates'], queryFn: () => api.get('/projects/templates/list') })
  const mine = (list.data?.templates || []).filter((t) => t.mine)
  const refresh = () => qc.invalidateQueries({ queryKey: ['templates'] })
  const upd = useMutation({ mutationFn: ({ id, ...b }) => api.put(`/projects/templates/${id}`, b), onSuccess: () => { refresh(); toast('Modèle mis à jour') }, onError: (e) => toast(e.message, 'err') })
  const del = useMutation({ mutationFn: (id) => api.del(`/projects/templates/${id}`), onSuccess: () => { refresh(); toast('Modèle supprimé') }, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal wide title="Mes modèles de projet" onClose={onClose} footer={<Btn onClick={onClose}>Fermer</Btn>}>
      {list.isLoading ? <Loading /> : mine.length === 0 ? <div className="muted">Aucun modèle pour l'instant. « Enregistrer comme modèle » en crée un à partir de ce projet.</div> : (
        <div className="col" style={{ gap: 10 }}>{mine.map((t) => <TemplateRow key={t.id} t={t} busy={upd.isPending || del.isPending}
          onSave={(b) => upd.mutate({ id: t.id, ...b })}
          onShare={async (shared) => { if (!shared || await confirm({ title: `Partager « ${t.name} » ?`, message: 'Tous ceux qui peuvent créer des projets le verront et pourront l\'utiliser. Les valeurs des secrets du .env sont vidées pour de bon.', confirmLabel: 'Partager', danger: false })) upd.mutate({ id: t.id, shared }) }}
          onDelete={async () => { if (await confirm({ title: `Supprimer le modèle « ${t.name} » ?`, message: 'Les projets déjà créés ne changent pas.', confirmLabel: 'Supprimer' })) del.mutate(t.id) }} />)}</div>)}
    </Modal>
  )
}

function TemplateRow({ t, busy, onSave, onShare, onDelete }) {
  const [name, setName] = useState(t.name); const [desc, setDesc] = useState(t.description)
  const dirty = name.trim() !== t.name || desc.trim() !== t.description
  return (
    <div className="card col" style={{ gap: 8 }}>
      <div className="row wrap" style={{ gap: 8 }}>
        <input className="input grow" style={{ minWidth: 160 }} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} aria-label="Nom du modèle" />
        <Seg value={t.shared ? 'shared' : 'private'} onChange={(v) => onShare(v === 'shared')} options={[{ id: 'private', label: <><Lock size={12} /> Privé</>, hint: 'Vous seul le voyez' }, { id: 'shared', label: <><Globe size={12} /> Partagé</>, hint: 'Visible de ceux qui créent des projets ; secrets vidés' }]} />
        <Btn small kind="danger" title="Supprimer ce modèle (les projets déjà créés ne changent pas)" onClick={onDelete}><Trash2 size={13} /></Btn>
      </div>
      <input className="input" value={desc} maxLength={300} onChange={(e) => setDesc(e.target.value)} placeholder="Description (facultatif)" aria-label="Description" />
      <div className="row wrap" style={{ gap: 6 }}><span className="muted small">{t.files.length} fichier{t.files.length > 1 ? 's' : ''} :</span>{t.files.slice(0, 8).map((f) => <Badge key={f}>{f}</Badge>)}{t.files.length > 8 && <span className="muted small">+{t.files.length - 8}</span>}
        <span className="muted small">· {t.vars.length} variable{t.vars.length > 1 ? 's' : ''}{t.createdBy ? ` · par ${t.createdBy}` : ''}</span>
        {dirty && <Btn small kind="primary" className="right" busy={busy} disabled={!name.trim()} onClick={() => onSave({ name: name.trim(), description: desc.trim() })}>Enregistrer</Btn>}</div>
    </div>
  )
}
