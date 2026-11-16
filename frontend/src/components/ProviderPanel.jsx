import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cloud, Home, Network, ShieldCheck } from 'lucide-react'
import { api } from '../api.js'
import { certHelp } from '../lib/certs.js'
import { Badge, Btn, CopyBtn, Field, Info, Loading, Modal, useUi } from './ui.jsx'
import { useMeta } from '../pages/Traefik.jsx'

// Domaines sans enregistrements libres : DNS local (rien à gérer ici) et DuckDNS (une adresse + un TXT).
export function ProviderPanel({ zone, zinfo, canEdit, onChanged }) {
  const { toast } = useUi(); const qc = useQueryClient(); const meta = useMeta()
  const q = useQuery({ queryKey: ['provider', zone], queryFn: () => api.get(`/dns/zones/${zone}/provider`) })
  const [ip, setIp] = useState(''); const [txt, setTxt] = useState('')
  const save = useMutation({
    mutationFn: (body) => api.put(`/dns/zones/${zone}/provider/ip`, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['provider', zone] }); qc.invalidateQueries({ queryKey: ['sync'] }); onChanged?.(); toast('DuckDNS mis à jour'); setIp(''); setTxt('') },
    onError: (e) => toast(e.message, 'err'),
  })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const target = meta.data?.defaultTarget

  if (zinfo.provider === 'duckdns') {
    const ips = [...(q.data.ip4 || []), ...(q.data.ip6 || [])]
    return (
      <div className="card col gap-lg">
        <div className="row"><Cloud size={18} /><h2>{zone}</h2><Badge kind="accent">DuckDNS</Badge>
          <Info>{'DuckDNS donne à ce nom une seule adresse IP (IPv4 et/ou IPv6). Tous les sous-domaines (app.' + zone + ', photos.' + zone + '…) pointent vers cette même adresse : il n\'y a donc jamais d\'enregistrement à créer pour un nouveau site.'}</Info></div>
        <div className="col" style={{ gap: 6 }}>
          <div className="muted small">Adresse actuelle (telle que ce serveur la résout)</div>
          <div className="row wrap">{ips.length ? ips.map((a) => <span className="row" key={a}><code>{a}</code><CopyBtn text={a} /></span>) : <span className="muted">aucune pour l'instant</span>}</div>
        </div>
        {canEdit ? <>
          <Field label="Nouvelle adresse IP" tip="Adresse vers laquelle doit pointer ce nom et tous ses sous-domaines. Une adresse privée (192.168.x.x) est possible : utile pour un accès local avec un vrai certificat.">
            <div className="row wrap"><input className="input mono" style={{ maxWidth: 260 }} value={ip} onChange={(e) => setIp(e.target.value.trim())} placeholder="203.0.113.10 ou 192.168.1.20" />
              <Btn kind="primary" busy={save.isPending} disabled={!ip} onClick={() => save.mutate({ ip })}>Appliquer</Btn>
              <Btn busy={save.isPending} title="DuckDNS prend l'adresse publique de la connexion de ce serveur : pratique pour une IP qui change." onClick={() => save.mutate({ usePublic: true })}>Utiliser l'IP publique de ce serveur</Btn></div></Field>
          <Field label="Valeur TXT" tip="Un seul TXT par nom. Traefik le pose lui-même pendant un défi DNS ; à n'utiliser ici que pour une vérification manuelle." help={q.data.txt ? <>TXT actuel : <code>{q.data.txt}</code></> : 'Aucun TXT enregistré depuis cette interface.'}>
            <div className="row wrap"><input className="input mono" style={{ maxWidth: 260 }} value={txt} onChange={(e) => setTxt(e.target.value)} maxLength={255} />
              <Btn busy={save.isPending} disabled={!txt} onClick={() => save.mutate({ txt })}>Définir</Btn>
              <Btn kind="ghost" busy={save.isPending} onClick={() => save.mutate({ clearTxt: true })}>Effacer</Btn></div></Field>
        </> : <div className="muted">Vous avez un accès en lecture seule sur ce domaine.</div>}
      </div>
    )
  }

  return (
    <div className="card col gap-lg">
      <div className="row"><Home size={18} /><h2>{zone}</h2><Badge>DNS local</Badge>
        <Info>{'Ce domaine est résolu par votre propre DNS (Pi-hole, AdGuard, routeur…). Il sert ici de nom pour vos sites et pour les droits d\'accès ; aucun enregistrement n\'est créé chez un fournisseur et rien n\'est publié sur Internet.'}</Info></div>
      <div className="col" style={{ gap: 8 }}>
        <div className="row" style={{ alignItems: 'flex-start' }}><Network size={15} style={{ marginTop: 3 }} /><div>Dans votre DNS local, faites pointer les sites de ce domaine vers <b>l'adresse de ce serveur Traefik</b>{target ? <>, ici <code>{target}</code> <CopyBtn text={target} /></> : ''}. Un enregistrement joker <code>*.{zone}</code> suffit pour tous.</div></div>
        <div className="row" style={{ alignItems: 'flex-start' }}><ShieldCheck size={15} style={{ marginTop: 3 }} /><div>Aucun avertissement « DNS manquant » n'est affiché pour ce domaine, puisqu'il n'y a rien à vérifier chez un fournisseur.</div></div>
      </div>
    </div>
  )
}

// Ce que Traefik doit avoir pour obtenir un certificat sur ce domaine (cette interface ne délivre pas de certificats).
export function CertHelp({ zone, provider, onClose }) {
  const h = certHelp(provider, zone)
  const Code = ({ children }) => <div className="codebox"><pre>{children}</pre></div>
  return (
    <Modal wide title={h.title} onClose={onClose} footer={<Btn onClick={onClose}>Fermer</Btn>}>
      <div className="col gap-lg">
        <div>{h.intro}</div>
        {h.bullets && <ul className="col" style={{ margin: 0, paddingLeft: 18, gap: 6 }}>{h.bullets.map((b) => <li key={b}>{b}</li>)}</ul>}
        {h.env && <div className="col"><b>Variables du conteneur Traefik</b><Code>{h.env.join('\n')}</Code><div className="muted small">{h.envNote}</div></div>}
        <div className="col"><b>{h.generic ? 'Exemple de résolveur (défi DNS, ici avec OVH — remplacez le fournisseur)' : 'Résolveur'}</b><Code>{h.yaml}</Code></div>
        <div className="col"><b>Certificat joker (facultatif)</b><Code>{h.wildcard}</Code><div className="muted small">Un seul certificat pour tous les sous-domaines ; rien n'apparaît dans les journaux publics de certificats pour chaque site.</div></div>
        <div className="banner info" style={{ margin: 0 }}>Les clés de cette interface ne servent pas à Traefik : il lit les siennes dans son propre environnement.</div>
      </div>
    </Modal>
  )
}
