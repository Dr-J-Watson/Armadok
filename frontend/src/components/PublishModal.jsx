import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Globe, TriangleAlert } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Loading, Modal } from './ui.jsx'

// Publier un conteneur sur Traefik : on vérifie que Traefik peut l'atteindre, puis on ouvre l'assistant de site déjà rempli (nom et adresse cible).
export default function PublishModal({ container, onClose, onGo }) {
  const q = useQuery({ queryKey: ['publish-hint', container], queryFn: () => api.get(`/docker/containers/${encodeURIComponent(container)}/publish-hint`) })
  const [url, setUrl] = useState(null)
  if (q.isLoading) return <Modal small title="Publier sur Traefik" onClose={onClose}><Loading /></Modal>
  if (q.error) return <Modal small title="Publier sur Traefik" onClose={onClose}><div className="banner err">{q.error.message}</div></Modal>
  const h = q.data; const t = h.traefik; const target = url ?? h.url
  const go = () => { window.dispatchEvent(new CustomEvent('new-site', { detail: { name: h.name, url: target, dns: true } })); (onGo || onClose)() }
  return (
    <Modal small title={`Publier « ${h.container} » sur Traefik`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" disabled={!h.canCreate || !h.port && !url} onClick={go}><Globe size={14} /> Ouvrir l'assistant de site</Btn></>}>
      <div className="col">
        <div className="muted">L'assistant s'ouvre avec le nom et l'adresse cible déjà remplis ; vous choisissez l'adresse publique, les protections et le DNS.</div>
        <label>Adresse que Traefik utilisera pour joindre ce conteneur<input className="input mono" value={target} onChange={(e) => setUrl(e.target.value)} /></label>
        {h.ports.length > 1 && <div className="row wrap" style={{ gap: 6 }}><span className="muted small">Ports exposés :</span>{h.ports.map((p) => <Badge key={p} style={{ cursor: 'pointer' }} onClick={() => setUrl(`http://${h.container}:${p}`)}>{p}</Badge>)}</div>}
        {!h.port && <div className="banner warn"><TriangleAlert size={14} /> Aucun port exposé par ce conteneur : indiquez-le dans l'adresse (http://nom:port).</div>}
        {t.found && t.reachable === false && <div className="banner err"><TriangleAlert size={14} /> <span>Traefik et ce conteneur ne partagent aucun réseau : Traefik ne pourra pas l'atteindre. Réseau(x) de Traefik : <b>{t.networks.join(', ') || '—'}</b> ; de ce conteneur : <b>{h.networks.join(', ') || '—'}</b>. Connectez le conteneur à un réseau de Traefik (dans son compose : <code>networks: [{t.networks[0] || 'proxy'}]</code>).</span></div>}
        {t.found && t.reachable && <div className="banner ok">Traefik et ce conteneur partagent le réseau <b>{t.sharedNetworks.join(', ')}</b> : le nom du conteneur suffit.</div>}
        {!t.found && <div className="muted small">Le conteneur de Traefik n'est pas sur ce nœud (ou porte un autre nom) : la joignabilité n'est pas vérifiée.</div>}
        {h.linked && <div className="banner info">Une règle Traefik mène déjà à ce conteneur ; vous pouvez en ajouter une autre.</div>}
        {t.docker === true && <div className="muted small">Traefik lit aussi les labels Docker{t.exposedByDefault === false ? ' (seulement ceux avec traefik.enable=true)' : ''} : vous pouvez aussi publier depuis le compose, dans l'onglet « Fichiers » du projet.</div>}
      </div>
    </Modal>
  )
}
