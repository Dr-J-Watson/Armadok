import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Boxes, FileCode2, FolderPlus, GitFork, Rocket } from 'lucide-react'
import { api } from '../api.js'
import { Btn, Field, Loading, Modal, useUi } from './ui.jsx'
import ConvertContainer from './ConvertContainer.jsx'
import { useGo } from '../lib/nav.js'

const EMPTY = '# Projet vide : ajoutez vos services sous « services: ».\nservices: {}\n'
const template = (name) => ({
  'docker-compose.yml': `# Modèle de base : un service qui lit ses réglages dans le .env.\nservices:\n  app:\n    image: \${APP_IMAGE:-nginx:alpine}\n    container_name: \${APP_NAME:-${name}}\n    restart: unless-stopped\n    env_file: .env\n    ports:\n      - "\${APP_PORT:-8080}:80"\n    # Pour le publier avec Traefik (réseau partagé « proxy »), décommentez :\n    # networks: [proxy]\n    # labels:\n    #   - traefik.enable=true\n    #   - traefik.http.routers.${name}.rule=Host(\`${name}.exemple.fr\`)\n    #   - traefik.http.services.${name}.loadbalancer.server.port=80\n\n# networks:\n#   proxy:\n#     external: true\n`,
  '.env': `# Variables lues par le compose (droits 600 : ne pas versionner).\nAPP_IMAGE=nginx:alpine\nAPP_NAME=${name}\nAPP_PORT=8080\n`,
})

// « Nouveau projet » : vide, modèle de base, depuis un conteneur lancé à la main, depuis un dépôt Git, ou un Traefik de base.
export default function NewProject({ onClose, onClone, onTraefik, traefikMissing }) {
  const { toast } = useUi(); const go = useGo(); const qc = useQueryClient()
  const list = useQuery({ queryKey: ['convertible'], queryFn: () => api.get('/projects/from-container/list'), retry: false })
  const [mode, setMode] = useState(null); const [name, setName] = useState(''); const [conv, setConv] = useState(null)
  const create = useMutation({
    mutationFn: () => api.post('/projects/create', { name, files: mode === 'empty' ? { 'docker-compose.yml': EMPTY } : template(name) }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['projects'] }); toast('Projet créé'); onClose(); if (r.id) go(`/projects/${r.id}`) }, onError: (e) => toast(e.message, 'err'),
  })
  const cs = list.data?.containers || []
  if (mode === 'container' && !conv) return <PickContainer onPick={setConv} onClose={() => setMode(null)} />
  if (conv) return <ConvertContainer container={conv} onClose={() => { setConv(null); onClose() }} />
  const Card = ({ id, icon, title, text, onClick, off }) => (
    <button type="button" className={'card np-card ' + (mode === id ? 'on' : '')} disabled={off} onClick={onClick || (() => setMode(id))}><div className="row" style={{ gap: 8 }}>{icon}<b>{title}</b></div><div className="muted small">{text}</div></button>)
  return (
    <Modal wide title="Nouveau projet" onClose={onClose} footer={mode ? <><Btn onClick={() => setMode(null)}>Retour</Btn><Btn kind="primary" disabled={!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$/.test(name)} busy={create.isPending} onClick={() => create.mutate()}>Créer le projet</Btn></> : <Btn onClick={onClose}>Fermer</Btn>}>
      {mode ? (
        <div className="col">
          <div className="muted">{mode === 'empty' ? 'Un dossier avec un docker-compose.yml sans service : vous le remplissez dans l\'éditeur.' : 'Un dossier avec un docker-compose.yml d\'exemple (un service) et son .env : vous adaptez l\'image, le nom et le port.'}</div>
          <Field label="Nom du dossier" help="Lettres, chiffres, « - », « _ » et « . ». Créé dans le dossier des projets de ce nœud."><input className="input mono" autoFocus style={{ maxWidth: 320 }} value={name} onChange={(e) => setName(e.target.value.trim())} placeholder="mon-projet" /></Field>
        </div>
      ) : (
        <div className="np-grid">
          <Card id="empty" icon={<FolderPlus size={16} />} title="Projet vide" text="Un dossier et un compose sans service, à remplir vous-même." />
          <Card id="template" icon={<FileCode2 size={16} />} title="Modèle de base" text="Un docker-compose.yml et un .env d'exemple (image, nom, port), prêts à adapter." />
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
