import { useQuery } from '@tanstack/react-query'
import { ExternalLink, Globe, Plus, Workflow } from 'lucide-react'
import { api } from '../api.js'
import { useNode } from '../node.jsx'
import { Badge, Btn, Empty, Loading } from '../components/ui.jsx'
import { useGo } from '../lib/nav.js'

// Un nœud sans Traefik dont les sites passent par celui d'un autre nœud : on voit ici les sites de ce Traefik qui mènent aux conteneurs de CETTE machine.
export default function ViaSites() {
  const { node, setNode } = useNode(); const go = useGo()
  const q = useQuery({ queryKey: ['via-sites', node?.id], queryFn: () => api.get('/docker/via-sites'), refetchInterval: 20000 })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data
  const open = (s) => { setNode(s.node.id); go(`/sites?open=${encodeURIComponent(s.router)}`) }
  const create = () => { if (!d.via) return; setNode(d.via.id); setTimeout(() => window.dispatchEvent(new CustomEvent('new-site')), 600) }
  return (
    <>
      <div className="row wrap" style={{ marginBottom: 14, gap: 10 }}>
        <div className="grow"><h1 style={{ margin: 0 }}>Sites</h1>
          <div className="muted">Les sites de {d.via ? <>« <b>{d.via.name}</b> »</> : 'un autre nœud'} qui mènent aux conteneurs de « {node.name} ». Ils sont publiés par le Traefik de ce nœud, pas par cette machine.</div></div>
        {d.via?.canCreate && <Btn kind="primary" onClick={create} title={`Ouvre l'assistant de site sur « ${d.via.name} »`}><Plus size={14} /> Nouveau site</Btn>}
      </div>
      {!d.via && <div className="banner warn">Le nœud choisi pour publier les sites n'est plus accessible : choisissez-en un autre dans « Ce nœud » → Docker.</div>}
      {d.via && d.addresses.length === 0 && <div className="banner warn">Aucune adresse n'est connue pour cette machine : sans elle, les sites ne peuvent pas être reliés à ses conteneurs. Ajoutez-la dans « Ce nœud » → Docker (ou <code>WORKER_ADDRESSES</code> dans le worker).</div>}
      {d.sites.length === 0 ? <div className="card"><Empty><Workflow size={28} /><div>Aucun site de « {d.via?.name || 'ce Traefik'} » ne mène aux conteneurs de cette machine pour l'instant.<br /><span className="muted small">Publiez-en un depuis la fiche d'un conteneur (« Publier sur Traefik ») : il doit publier son port sur la machine.</span></div></Empty></div>
        : <div className="col">{d.sites.map((s) => (
          <div key={s.node.id + s.router} className="card col" style={{ gap: 6 }}>
            <div className="row wrap" style={{ gap: 8 }}><b className="mono">{s.router}</b><Badge kind={s.sure ? 'ok' : 'warn'} title={s.sure ? 'Lien certain' : 'Lien probable : le nom ou le port ne correspond pas exactement'}>{s.sure ? 'certain' : 'probable'}</Badge><Badge>{s.provider === 'docker' ? 'labels' : 'fichier YAML'}</Badge>
              <span className="muted small">→ {s.containers.join(', ')}</span>
              <Btn small className="right" onClick={() => open(s)} title={`Ouvrir ce site sur « ${s.node.name} »`}><ExternalLink size={13} /> Ouvrir sur « {s.node.name} »</Btn></div>
            <div className="row wrap" style={{ gap: 6 }}>{s.hosts.map((h) => <a key={h} className="dk-chip" href={`https://${h}`} target="_blank" rel="noreferrer"><Globe size={11} /> {h}</a>)}</div>
            <div className="small mono muted" style={{ wordBreak: 'break-all' }}>{s.rule}</div>
          </div>))}</div>}
    </>
  )
}
