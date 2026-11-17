import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Boxes, TriangleAlert } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Field, Loading, Modal, useUi } from './ui.jsx'
import { JobOutput } from './ComposeBar.jsx'
import { useGo } from '../lib/nav.js'

// Un conteneur lancé à la main (docker run) devient un projet : docker-compose.yml + .env (les variables d'environnement y vont, le compose y renvoie).
export default function ConvertContainer({ container, onClose }) {
  const { toast } = useUi(); const go = useGo()
  const pv = useQuery({ queryKey: ['convert-preview', container], queryFn: () => api.post('/projects/from-container/preview', { container }), retry: false })
  const [name, setName] = useState(null); const [replace, setReplace] = useState(false); const [confirm, setConfirm] = useState(''); const [made, setMade] = useState(null)
  const d = pv.data; const folder = name ?? d?.name ?? ''
  const run = useMutation({ mutationFn: () => api.post('/projects/from-container', { container, name: folder, replace, confirm: confirm || folder }), onSuccess: (r) => { setMade(r); if (!r.job) { toast('Projet créé : relisez-le, puis déployez-le'); go(`/projects/${r.id}`); onClose() } }, onError: (e) => toast(e.message, 'err') })
  if (made?.job) return <JobOutput id={made.id} job={{ ...made.job, action: 'up' }} onClose={() => { go(`/projects/${made.id}`); onClose() }} />
  return (
    <Modal wide title={`Convertir « ${container} » en projet`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn>
      <Btn kind="primary" disabled={!d || !folder.trim() || (replace && d.protected && confirm !== folder)} busy={run.isPending} onClick={() => run.mutate()} title="Écrit docker-compose.yml et .env dans un nouveau dossier de projet"><Boxes size={14} /> {replace ? 'Créer et remplacer le conteneur' : 'Créer le projet'}</Btn></>}>
      {pv.isLoading ? <Loading /> : pv.error ? <div className="banner err">{pv.error.message}</div> : (
        <div className="col gap-lg">
          <div className="muted">Ce conteneur a été lancé sans projet compose (docker run, ou un autre outil). Armadok relit sa configuration et écrit un <code>docker-compose.yml</code> et un <code>.env</code> équivalents dans un nouveau dossier. <b>Rien n'est démarré ni arrêté</b> tant que vous ne le demandez pas ci-dessous.</div>
          <Field label="Nom du dossier du projet" help="Créé dans le dossier des projets de ce nœud."><input className="input mono" style={{ maxWidth: 320 }} value={folder} onChange={(e) => setName(e.target.value.trim())} /></Field>
          {d.warnings.map((w, i) => <div key={i} className="banner warn" style={{ margin: 0 }}><TriangleAlert size={14} /> {w}</div>)}
          <div className="row wrap" style={{ gap: 6 }}>
            <Badge>{d.envKeys.length} variable{d.envKeys.length > 1 ? 's' : ''} → .env</Badge>
            {d.volumes.length > 0 && <Badge title="Repris en « external » : vos données sont gardées">{d.volumes.length} volume{d.volumes.length > 1 ? 's' : ''} conservé{d.volumes.length > 1 ? 's' : ''}</Badge>}
            {d.networks.length > 0 && <Badge>réseau{d.networks.length > 1 ? 'x' : ''} : {d.networks.join(', ')}</Badge>}
            <span className="muted small">Les valeurs des variables ne sont écrites que dans le .env (droits 600), jamais dans le compose ci-dessous.</span>
          </div>
          <pre className="dk-code" style={{ maxHeight: '38vh', overflow: 'auto' }}>{d.compose}</pre>
          {d.canReplace && (
            <div className="col" style={{ gap: 6 }}>
              <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /><b>Remplacer maintenant le conteneur par le projet</b></label>
              <div className="muted small" style={{ marginLeft: 24 }}>L'ancien conteneur est <b>arrêté puis renommé</b> « {container}-avant-armadok » (jamais supprimé : vous pouvez revenir en arrière), puis le projet est déployé sous le nom d'origine. Sans cette case, le projet est seulement créé : tant que l'ancien conteneur existe sous le même nom, le déploiement échouera.</div>
              {replace && d.protected === 'traefik' && <Field label="C'est le conteneur de Traefik : tous vos sites seront coupés quelques secondes. Retapez le nom du dossier pour confirmer"><input className="input mono" style={{ maxWidth: 320 }} value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder={folder} /></Field>}
            </div>)}
        </div>)}
    </Modal>
  )
}
