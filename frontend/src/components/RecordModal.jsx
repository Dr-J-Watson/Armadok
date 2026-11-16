import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api.js'
import { refreshZone } from '../lib/dns.js'
import { Btn, Field, Modal, useUi } from './ui.jsx'

const TYPE_HELP = {
  A: 'Adresse IPv4, ex. 203.0.113.10', AAAA: 'Adresse IPv6', CNAME: 'Nom cible terminé par un point, ex. cible.exemple.fr.',
  MX: 'Priorité + serveur', TXT: 'Texte libre (SPF, vérification…)', NS: 'Serveur de noms', SRV: 'Priorité poids port cible', CAA: 'flag tag "valeur"',
}
const split = (t) => (t || '').trim().split(/\s+/)

export default function RecordModal({ zone, zones, record, initial, onClose, onSaved }) {
  const qc = useQueryClient()
  const { toast } = useUi()
  const meta = useQuery({ queryKey: ['meta'], queryFn: () => api.get('/meta') })
  const zq = useQuery({ queryKey: ['zones'], queryFn: () => api.get('/dns/zones') })
  const edit = !!record?.id
  const base = record || initial || {}
  const [z, setZ] = useState(zone || zones?.[0])
  const [type, setType] = useState(base.fieldType || 'A')
  const [sub, setSub] = useState(base.subDomain || '')
  const [ttl, setTtl] = useState(base.ttl ?? 3600)
  const [target, setTarget] = useState(base.target || '')
  const [mx, setMx] = useState(() => { const [p, ...r] = split(base.target); return { prio: p || '10', host: r.join(' ') } })
  const [srv, setSrv] = useState(() => { const [a, b, c, ...r] = split(base.target); return { prio: a || '0', weight: b || '5', port: c || '', host: r.join(' ') } })
  const [caa, setCaa] = useState(() => { const [a, b, ...r] = split(base.target); return { flag: a || '0', tag: b || 'issue', value: r.join(' ') } })

  useEffect(() => { if (type === 'MX') setTarget(`${mx.prio} ${mx.host}`); if (type === 'SRV') setTarget(`${srv.prio} ${srv.weight} ${srv.port} ${srv.host}`); if (type === 'CAA') setTarget(`${caa.flag} ${caa.tag} ${caa.value}`) }, [type, mx, srv, caa])

  const save = useMutation({
    mutationFn: () => {
      const body = { fieldType: type, subDomain: sub.trim() === '@' ? '' : sub.trim(), target: target.trim(), ttl: Number(ttl) || 0 }
      return edit ? api.put(`/dns/zones/${z}/records/${record.id}`, body) : api.post(`/dns/zones/${z}/records`, body)
    },
    onSuccess: async () => {
      await refreshZone(api, toast, z)
      qc.invalidateQueries({ queryKey: ['records'] }); qc.invalidateQueries({ queryKey: ['zones'] }); qc.invalidateQueries({ queryKey: ['sync'] })
      toast(edit ? 'Enregistrement modifié' : 'Enregistrement ajouté'); onSaved?.(); onClose()
    },
    onError: (e) => toast(e.message, 'err'),
  })

  const cap = zq.data?.find((x) => x.name === z)?.capabilities || {}   // AdGuard Home / Pi-hole : seulement A / AAAA / CNAME, sans TTL
  const types = cap.types || meta.data?.recordTypes || ['A', 'AAAA', 'CNAME', 'MX', 'TXT']
  const fqdn = `${sub && sub !== '@' ? sub + '.' : ''}${z}`
  return (
    <Modal title={edit ? 'Modifier l\'enregistrement' : 'Nouvel enregistrement'} onClose={onClose}
      footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={save.isPending} disabled={!target.trim()} onClick={() => save.mutate()}>{edit ? 'Enregistrer' : 'Ajouter'}</Btn></>}>
      <div className="col">
        {!zone && zones && <Field label="Zone"><select className="select" value={z} onChange={(e) => setZ(e.target.value)}>{zones.map((x) => <option key={x}>{x}</option>)}</select></Field>}
        <div className="fgrid">
          <Field label="Type" tip={'A / AAAA : pointe vers une adresse IP (IPv4 / IPv6).\nCNAME : alias vers un autre nom.\nMX : serveur de messagerie. TXT : texte (SPF, vérifications).\nSRV, CAA, NS… : usages techniques.'}><select className="select" value={type} onChange={(e) => setType(e.target.value)} disabled={edit}>{types.map((t) => <option key={t}>{t}</option>)}</select></Field>
          <Field label="Sous-domaine" tip="Vide : le domaine lui-même. « blog » : blog.votredomaine. « * » : joker, tous les sous-domaines qui n'ont pas leur propre enregistrement." help={<>Résultat : <b className="mono">{fqdn}</b></>}><input className="input" value={sub} onChange={(e) => setSub(e.target.value.toLowerCase())} placeholder="@ (racine)" autoFocus={!edit} /></Field>
          {cap.ttl !== false && <Field label="TTL (s)" tip="Durée pendant laquelle les serveurs DNS gardent la réponse en mémoire. Plus court = changements visibles plus vite (mais plus de requêtes). 0 = valeur par défaut de la zone." help="0 = défaut de la zone"><input className="input" type="number" value={ttl} onChange={(e) => setTtl(e.target.value)} /></Field>}
        </div>
        {type === 'MX' ? (
          <div className="fgrid"><Field label="Priorité"><input className="input" value={mx.prio} onChange={(e) => setMx({ ...mx, prio: e.target.value })} /></Field><Field label="Serveur mail"><input className="input" value={mx.host} onChange={(e) => setMx({ ...mx, host: e.target.value })} placeholder="mx1.mail.ovh.net." /></Field></div>
        ) : type === 'SRV' ? (
          <div className="fgrid">{['prio', 'weight', 'port', 'host'].map((k) => <Field key={k} label={{ prio: 'Priorité', weight: 'Poids', port: 'Port', host: 'Cible' }[k]}><input className="input" value={srv[k]} onChange={(e) => setSrv({ ...srv, [k]: e.target.value })} /></Field>)}</div>
        ) : type === 'CAA' ? (
          <div className="fgrid"><Field label="Flag"><input className="input" value={caa.flag} onChange={(e) => setCaa({ ...caa, flag: e.target.value })} /></Field><Field label="Tag"><select className="select" value={caa.tag} onChange={(e) => setCaa({ ...caa, tag: e.target.value })}><option>issue</option><option>issuewild</option><option>iodef</option></select></Field><Field label="Valeur"><input className="input" value={caa.value} onChange={(e) => setCaa({ ...caa, value: e.target.value })} placeholder={'"letsencrypt.org"'} /></Field></div>
        ) : type === 'TXT' ? (
          <Field label="Valeur"><textarea className="textarea" value={target} onChange={(e) => setTarget(e.target.value)} /></Field>
        ) : (
          <Field label="Cible" help={cap.ttl === false && type === 'CNAME' ? 'Nom cible, ex. cible.exemple.fr' : TYPE_HELP[type]}><input className="input mono" value={target} onChange={(e) => setTarget(e.target.value)} /></Field>
        )}
      </div>
    </Modal>
  )
}
