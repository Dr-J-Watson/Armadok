import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api.js'
import { useNode } from '../node.jsx'
import { refreshZone } from '../lib/dns.js'
import { extractHosts } from '../lib/util.js'
import { useOverview } from '../pages/Traefik.jsx'
import { Badge, Btn, Modal, useUi } from './ui.jsx'

// Suppression d'un site (ou d'un conteneur qui en sert) avec UNE seule confirmation : on propose en plus, avec des cases à cocher, de supprimer
// les sites reliés et les enregistrements DNS de leurs adresses (seulement ceux que la personne a le droit de supprimer ; rien n'est coché d'office).
//   const removal = useSiteRemoval()
//   const choice = await removal.confirmRemoval({ title, message, confirmLabel, sites })   // null si on annule
//   … l'action principale …  puis  await removal.apply(choice)  ;  et ne pas oublier d'afficher {removal.dialog}
// sites : [{ name, hosts, locked }]   locked : ce site est supprimé de toute façon (c'est l'action demandée)
//         [{ name, hosts, labels: true }]   site défini par les labels du conteneur : il disparaît avec lui, on ne propose que son DNS
//         [{ name, hosts }]   site relié (fichier Traefik) : case à cocher
// Les liens Traefik d'un conteneur → les sites à proposer : ceux des labels (ils partent avec lui) et ceux des fichiers dont le lien est CERTAIN.
// Un site servi par un autre nœud (lien « node ») ou seulement « probable » n'est jamais proposé.
export const sitesFromLinks = (links) => [...new Map((links || []).filter((l) => !l.node && (l.provider === 'docker' || (l.item && l.sure)))
  .map((l) => [l.router, l.provider === 'docker' ? { name: l.router, hosts: l.hosts || [], labels: true } : { name: l.router, hosts: l.hosts || [] }])).values()]

export function useSiteRemoval() {
  const qc = useQueryClient(); const { toast } = useUi()
  const [ask, setAsk] = useState(null)
  const confirmRemoval = (plan) => new Promise((resolve) => setAsk({ plan, resolve }))
  const done = (v) => { ask.resolve(v); setAsk(null) }

  // supprime les sites cochés puis les enregistrements DNS cochés ; l'action principale (le conteneur, par exemple) a déjà été faite par l'appelant
  const apply = async (choice) => {
    if (!choice) return
    const ok = { sites: 0, records: 0 }; const errors = []
    for (const name of choice.sites) {
      try { await api.post('/traefik/site/delete', { name, deleteService: true }); ok.sites++ } catch (e) { errors.push(`${name} : ${e.message}`) }
    }
    for (const z of choice.dns) {
      try { const r = await api.post(`/dns/zones/${z.zone}/bulk`, { delete: z.ids }); ok.records += r.deleted || 0; errors.push(...(r.errors || [])); await refreshZone(api, toast, z.zone) } catch (e) { errors.push(`${z.zone} : ${e.message}`) }
    }
    qc.invalidateQueries()
    const parts = [ok.sites && `${ok.sites} site${ok.sites > 1 ? 's' : ''}`, ok.records && `${ok.records} enregistrement${ok.records > 1 ? 's' : ''} DNS`].filter(Boolean)
    if (parts.length) toast(`Supprimé : ${parts.join(' et ')}`)
    if (errors.length) toast(errors.join('\n'), 'err')
  }

  const dialog = ask ? <RemovalModal plan={ask.plan} onCancel={() => done(null)} onConfirm={done} /> : null
  return { confirmRemoval, apply, dialog }
}

function RemovalModal({ plan, onCancel, onConfirm }) {
  const { can } = useNode(); const ov = useOverview()
  const sites = plan.sites || []
  const [pickSites, setPickSites] = useState(new Set()); const [pickRecs, setPickRecs] = useState(new Set())
  const names = new Set(sites.map((s) => s.name))
  const hosts = [...new Set(sites.flatMap((s) => s.hosts || []))].sort()
  const canDns = can('dns.records.delete') && hosts.length > 0
  const check = useQuery({ queryKey: ['removal-dns', hosts.join(',')], queryFn: () => api.post('/sync/check', { hosts }), enabled: canDns, staleTime: 0, gcTime: 0, retry: false })
  const sharedBy = (h) => (ov.data?.items || []).some((i) => i.kind === 'routers' && i.proto === 'http' && !names.has(i.name) && extractHosts(i.spec?.rule || '').includes(h))   // un autre site sert la même adresse : son DNS reste
  const ownersGo = (h) => sites.filter((s) => (s.hosts || []).includes(h)).every((s) => s.locked || s.labels || pickSites.has(s.name))                                       // le DNS d'une adresse ne part que si ses sites partent aussi
  const recs = (check.data || []).filter((r) => r.status === 'ok' && r.canEdit && r.records?.length && !sharedBy(r.host))
    .flatMap((r) => r.records.map((x) => ({ key: `${r.zone}#${x.id}`, zone: r.zone, id: x.id, host: r.host, label: `${x.fieldType} ${r.host} → ${x.target}` })))
  const optional = sites.filter((s) => !s.locked && !s.labels && can('sites.delete'))
  const toggle = (set, setSet, k) => { const n = new Set(set); n.has(k) ? n.delete(k) : n.add(k); setSet(n) }
  const go = () => {
    const sel = recs.filter((r) => pickRecs.has(r.key) && ownersGo(r.host))
    const byZone = {}
    sel.forEach((r) => { (byZone[r.zone] ||= []).push(r.id) })
    onConfirm({ sites: [...sites.filter((s) => s.locked).map((s) => s.name), ...optional.filter((s) => pickSites.has(s.name)).map((s) => s.name)], dns: Object.entries(byZone).map(([zone, ids]) => ({ zone, ids })) })
  }
  const extras = optional.length > 0 || recs.length > 0
  return (
    <Modal small title={plan.title} onClose={onCancel} footer={<><Btn onClick={onCancel}>Annuler</Btn><Btn kind="danger" busy={canDns && check.isLoading} onClick={go}>{plan.confirmLabel || 'Supprimer'}</Btn></>}>
      <div className="col gap-lg">
        <div style={{ whiteSpace: 'pre-wrap' }}>{plan.message}</div>
        {sites.some((s) => s.labels) && <div className="muted small">Les règles Traefik de ce conteneur sont dans ses labels : elles disparaissent avec lui.</div>}
        {extras && <div className="col" style={{ gap: 8 }}>
          <b>En même temps (facultatif)</b>
          {optional.map((s) => <label key={s.name} className="row" style={{ gap: 8, alignItems: 'flex-start' }}><input type="checkbox" checked={pickSites.has(s.name)} onChange={() => toggle(pickSites, setPickSites, s.name)} />
            <span>Supprimer aussi le site <b className="mono">{s.name}</b>{(s.hosts || []).length > 0 && <span className="muted small"> ({s.hosts.join(', ')})</span>}</span></label>)}
          {recs.map((r) => { const on = ownersGo(r.host); return <label key={r.key} className="row" style={{ gap: 8, alignItems: 'flex-start', opacity: on ? 1 : .55 }} title={on ? undefined : 'Cochez d\'abord la suppression du site qui utilise cette adresse'}>
            <input type="checkbox" disabled={!on} checked={on && pickRecs.has(r.key)} onChange={() => toggle(pickRecs, setPickRecs, r.key)} />
            <span>Supprimer l'enregistrement DNS <b className="mono">{r.label}</b> <Badge>{r.zone}</Badge></span></label> })}
          <div className="muted small">Un enregistrement supprimé n'est plus résolu : l'adresse devient injoignable. Restaurable via l'historique de la zone chez OVH.</div>
        </div>}
        {canDns && check.isLoading && <div className="muted small">Recherche des enregistrements DNS de ces adresses…</div>}
      </div>
    </Modal>
  )
}
