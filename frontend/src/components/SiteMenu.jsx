import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, CheckCircle2, Copy, Copy as CopyIcon, ExternalLink, Globe, Lock, MoreHorizontal, Stethoscope, Users, XCircle } from 'lucide-react'
import { api, qs } from '../api.js'
import { Badge, Btn, Loading, Modal, useUi } from './ui.jsx'
import { useGo } from '../lib/nav.js'

// Menu « ⋯ » d'une carte de site : les raccourcis qu'on cherchait sinon dans trois pages différentes.
export default function SiteMenu({ site, host, dnsRow, statsService, canCerts, canDuplicate, onDuplicate }) {
  const [open, setOpen] = useState(false); const [check, setCheck] = useState(false)
  const ref = useRef(null); const go = useGo(); const { toast } = useUi()
  useEffect(() => {
    if (!open) return
    const off = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    const esc = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', off); document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', off); document.removeEventListener('keydown', esc) }
  }, [open])
  const item = (icon, label, run) => <button type="button" onClick={() => { setOpen(false); run() }}>{icon}{label}</button>
  return (
    <span className="menu-wrap" ref={ref} onClick={(e) => e.stopPropagation()}>
      <Btn small kind="ghost" icon title="Plus d'actions" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}><MoreHorizontal size={15} /></Btn>
      {open && <div className="menu-pop" role="menu">
        {host && item(<Copy size={14} />, 'Copier l\'adresse', () => { try { navigator.clipboard?.writeText(`https://${host}`) } catch { /* indisponible */ } toast('Adresse copiée') })}
        {host && item(<ExternalLink size={14} />, 'Ouvrir dans un onglet', () => window.open(`https://${host}`, '_blank', 'noopener'))}
        {host && item(<Stethoscope size={14} />, 'Tester le site', () => setCheck(true))}
        {statsService && item(<Activity size={14} />, 'Statistiques', () => go(`/monitoring?tab=services&service=${encodeURIComponent(statsService)}`))}
        {host && statsService && item(<Users size={14} />, 'Voir ses visiteurs', () => go(`/monitoring?tab=ips&s=${encodeURIComponent(host)}`))}
        {host && canCerts && item(<Lock size={14} />, 'Son certificat', () => go(`/traefik?tab=certs&host=${encodeURIComponent(host)}`))}
        {dnsRow?.zone && item(<Globe size={14} />, 'Son enregistrement DNS', () => go(`/dns?zone=${encodeURIComponent(dnsRow.zone)}&q=${encodeURIComponent(dnsRow.subDomain || '')}`))}
        {canDuplicate && item(<CopyIcon size={14} />, 'Dupliquer ce site', onDuplicate)}
      </div>}
      {check && <CheckModal name={site.name} onClose={() => setCheck(false)} />}
    </span>
  )
}

const Step = ({ r, label }) => (
  <div className="row" style={{ gap: 8 }}>{r?.ok ? <CheckCircle2 size={16} style={{ color: 'var(--ok)' }} /> : <XCircle size={16} style={{ color: 'var(--err)' }} />}<b style={{ minWidth: 90 }}>{label}</b><span className="muted small">{r?.detail}</span></div>)

export function CheckModal({ name, onClose }) {
  const q = useQuery({ queryKey: ['site-check', name], queryFn: () => api.get(`/traefik/site/check?${qs({ name })}`), retry: false, staleTime: 0, gcTime: 0 })
  return (
    <Modal small title={`Test de « ${name} »`} onClose={onClose} footer={<><Btn onClick={() => q.refetch()} busy={q.isFetching}>Relancer</Btn><Btn kind="primary" onClick={onClose}>Fermer</Btn></>}>
      {q.isLoading ? <div className="col"><Loading /><div className="muted small" style={{ textAlign: 'center' }}>Résolution du nom, certificat, réponse du site…</div></div> : q.error ? <div className="banner err">{q.error.message}</div> : (
        <div className="col gap-lg">{q.data.results.map((r) => (
          <div key={r.host} className="col" style={{ gap: 6 }}>
            <div className="row"><b className="mono grow">{r.host}</b><Badge kind={r.ok ? 'ok' : 'err'}>{r.ok ? 'tout va bien' : 'à vérifier'}</Badge></div>
            <Step r={r.dns} label="Nom (DNS)" /><Step r={r.tls} label="Certificat" /><Step r={r.http} label="Réponse" />
          </div>))}
          <div className="muted small">Une réponse 401 ou 403 est normale pour un site protégé : il répond, il demande simplement de s'identifier. Le test part de ce serveur, pas de votre navigateur.</div></div>)}
    </Modal>
  )
}
