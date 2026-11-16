import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowRight, CheckCircle2, Info as InfoIcon, FileText, Folder, Layers, Lock, Link2, Route } from 'lucide-react'
import { api } from '../api.js'
import { baseName, dirOf, joinPath } from '../lib/tree.js'
import { Badge, Btn, Loading, Modal, useUi } from './ui.jsx'
import { Info } from './Tip.jsx'

const GROUPS = [
  { id: 'app', title: 'Applications', hint: 'Fichiers de sites (routeurs et services) laissés à la racine', icon: Route },
  { id: 'sites', title: 'Un fichier par site', hint: 'Chaque routeur est rangé avec le service qu\'il utilise (extrait des gros fichiers)', icon: Link2 },
  { id: 'routers', title: 'Un fichier par routeur', hint: 'Dans le dossier routers/', icon: Route },
  { id: 'services', title: 'Un fichier par service', hint: 'Dans le dossier services/', icon: Layers },
  { id: 'pairs', title: 'Services et routeurs séparés', hint: 'Un service rejoint le fichier de son routeur (ou l\'inverse)', icon: Link2 },
  { id: 'mw', title: 'Middlewares', hint: 'Regroupés dans leur dossier, un fichier par middleware quand ils étaient mélangés', icon: Layers },
  { id: 'tr', title: 'Transports serveur', hint: 'serversTransports (ex. insecureSkipVerify)', icon: Lock },
  { id: 'empty', title: 'Fichiers vides', hint: 'Aucun bloc dedans : supprimés (une copie reste dans l\'historique)', icon: FileText },
  { id: 'emptydirs', title: 'Dossiers vides', hint: 'Aucun fichier dedans : supprimés', icon: Folder },
  { id: 'tls', title: 'TLS', hint: 'Options et stores TLS', icon: Lock },
]

// Assistant de rangement : propose, l'utilisateur coche / ajuste, puis tout est appliqué d'un coup.
export default function Reorganize({ dirs, onClose, onDone }) {
  const { toast } = useUi()
  const [deep, setDeep] = useState(true)
  const [layout, setLayout] = useState('sites'); const [sub, setSub] = useState(true); const [clean, setClean] = useState(true)
  const plan = useQuery({ queryKey: ['reorganize', deep, layout, sub], queryFn: () => api.get(`/traefik/reorganize?scope=${deep ? 'all' : 'root'}&layout=${layout}&sub=${sub}`), staleTime: 0, gcTime: 0 })
  const [off, setOff] = useState(new Set())           // étapes décochées
  const [dest, setDest] = useState({})                // dossier choisi par étape (n -> dossier)
  const steps = plan.data?.steps || []
  const target = (s) => (s.group === 'pairs' || s.group === 'sites' || s.group === 'empty' || s.group === 'emptydirs' ? s.to : joinPath(dest[s.n] ?? dirOf(s.to), baseName(s.to)))
  const choices = useMemo(() => [...new Set([...dirs, ...Object.values(plan.data?.folders || {}), ...steps.map((s) => dirOf(s.to))].filter(Boolean))].sort(), [plan.data, dirs])
  const chosen = steps.filter((s) => !off.has(s.n))

  const apply = useMutation({
    mutationFn: () => api.post('/traefik/reorganize', { clean, steps: chosen.map((s) => ({ kind: s.kind, from: s.from, to: ['delete', 'rmdir'].includes(s.kind) ? '' : target(s), ids: s.ids })) }),
    onSuccess: (r) => { toast(`Rangement terminé : ${r.files} fichier(s) déplacé(s), ${r.blocks} bloc(s) regroupé(s)${r.deleted ? `, ${r.deleted} fichier(s) vide(s) supprimé(s)` : ''}${r.dirs ? `, ${r.dirs} dossier(s) vide(s) supprimé(s)` : ''}`); onDone() },
    onError: (e) => toast(e.message, 'err'),
  })
  const flip = (n) => setOff((o) => { const x = new Set(o); x.has(n) ? x.delete(n) : x.add(n); return x })

  return (
    <Modal wide title="Ranger mes fichiers" onClose={onClose}
      footer={<><span className="muted small" style={{ marginRight: 'auto' }}>Une copie de tous vos fichiers est gardée avant application.</span><Btn onClick={onClose}>Fermer</Btn>
        <Btn kind="primary" busy={apply.isPending} disabled={!chosen.length} onClick={() => apply.mutate()}>Appliquer {chosen.length ? `(${chosen.length})` : ''}</Btn></>}>
      <div className="col gap-lg">
        <div className="muted">Voici ce que je propose d'après le contenu de vos fichiers. Rien n'est écrit tant que vous n'avez pas validé ; décochez ce que vous voulez laisser en place. Les sites déjà rangés dans un dossier ne bougent pas.</div>
        <div className="card col" style={{ background: 'var(--panel2)', gap: 8 }}>
          <b>Vos gros fichiers de sites</b>
          {[['keep', 'Les laisser entiers', 'Un fichier entier est simplement déplacé dans apps/.'],
            ['sites', 'Un fichier par site (recommandé)', 'Chaque service est rangé avec TOUS les routeurs qui le visent, même s\'ils étaient dispersés ou dans un gros fichier. Si une partie du site est déjà rangée dans un dossier, le reste la rejoint.'],
            ['split', 'Un fichier par routeur et un par service', 'Le dossier routers/ contient un fichier par routeur, le dossier services/ un fichier par service.']].map(([id, label, hint]) => (
            <label key={id} className="row" style={{ gap: 8, alignItems: 'flex-start' }}><input type="radio" name="layout" checked={layout === id} onChange={() => { setLayout(id); setOff(new Set()); setDest({}) }} style={{ marginTop: 3 }} />
              <span><b className="small">{label}</b><div className="muted small">{hint}</div></span></label>))}
          <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={sub} onChange={(e) => { setSub(e.target.checked); setOff(new Set()); setDest({}) }} /> Sous-dossiers : middlewares par type (auth, headers, ip…) et sites par domaine
            <Info>{'Exemples : middlewares/auth/, middlewares/headers/, apps/exemple.fr/grafana-svc.yml. Avec « un fichier par routeur / service », les sous-dossiers ne concernent que les middlewares.'}</Info></label>
        </div>
        <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={clean} onChange={(e) => setClean(e.target.checked)} /> Supprimer les commentaires des fichiers rangés
          <Info>{'Les fichiers touchés par le rangement sont réécrits proprement, sans commentaires : les titres collés à un bloc ne se retrouvent plus dans le mauvais fichier. Les blocs désactivés sont conservés. Décochez pour garder les commentaires (les fichiers simplement déplacés les gardent déjà).'}</Info></label>
        <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={deep} onChange={(e) => { setDeep(e.target.checked); setOff(new Set()); setDest({}) }} /> Ranger aussi ce qui est déjà dans des dossiers (sites, middlewares, réglages TLS), pas seulement la racine
          <Info>{'Coché : TOUS les blocs sont listés et placés selon le mode choisi ci-dessus. Décoché : seuls les fichiers à la racine sont proposés, ce qui est déjà dans un dossier ne bouge pas.'}</Info></label>
        {plan.isLoading && <Loading />}
        {plan.isError && <div className="banner err">{plan.error.message}</div>}
        {plan.data && !steps.length && (!deep && plan.data.stats?.sitesInFolders > 0 && layout !== 'keep'
          ? <div className="banner info"><InfoIcon size={16} /> Rien à proposer à la racine : vos {plan.data.stats.sitesInFolders} routeurs et services sont déjà dans des dossiers. Cochez « Ranger aussi ce qui est déjà dans des dossiers » pour les ranger selon ce mode.</div>
          : <div className="banner ok"><CheckCircle2 size={16} /> Tout est déjà rangé selon ce mode : rien à proposer.</div>)}
        {steps.length > 0 && plan.data?.stats && <div className="muted small">{plan.data.stats.moved} bloc{plan.data.stats.moved > 1 ? 's' : ''} sur {plan.data.stats.blocks} {plan.data.stats.moved > 1 ? 'seraient déplacés' : 'serait déplacé'}, répartis en {steps.length} étape{steps.length > 1 ? 's' : ''} ci-dessous ; les autres sont déjà bien placés.</div>}
        {plan.data?.truncated && <div className="banner">Beaucoup de changements : seuls les premiers sont proposés. Appliquez-les puis relancez l'assistant.</div>}
        {GROUPS.map((g) => {
          const rows = steps.filter((s) => s.group === g.id)
          if (!rows.length) return null
          return (
            <div key={g.id} className="col">
              <div className="row"><g.icon size={15} /><h3>{g.title}</h3><Badge>{rows.length}</Badge><span className="muted small">{g.hint}</span></div>
              <div className="card flush">
                {rows.map((s) => (
                  <label key={s.n} className={'reorg-row ' + (off.has(s.n) ? 'off' : '')}>
                    <input type="checkbox" checked={!off.has(s.n)} onChange={() => flip(s.n)} />
                    <div className="grow" style={{ minWidth: 0 }}>
                      <div className="row wrap" style={{ gap: 6 }}>
                        <FileText size={13} /><code>{s.from}</code><ArrowRight size={13} className="muted" />
                        {s.group === 'empty' || s.group === 'emptydirs' ? <span className="muted">supprimé</span> : s.group === 'pairs' || s.group === 'sites' || off.has(s.n) ? <code>{target(s)}</code> : (
                          <span className="row" style={{ gap: 4 }}>
                            <select className="select sm" value={dest[s.n] ?? dirOf(s.to)} onClick={(e) => e.preventDefault()} onChange={(e) => setDest((d) => ({ ...d, [s.n]: e.target.value }))}>
                              {!choices.includes(dest[s.n] ?? dirOf(s.to)) && <option value={dest[s.n] ?? dirOf(s.to)}>{dest[s.n] ?? dirOf(s.to)} (nouveau)</option>}
                              {choices.map((d) => <option key={d} value={d}>{d}{dirs.includes(d) ? '' : ' (nouveau)'}</option>)}
                            </select>
                            <code>/{baseName(s.to)}</code>
                          </span>
                        )}
                      </div>
                      <div className="muted small">{s.reason} · {s.items.map((i) => i.name).join(', ')}</div>
                    </div>
                  </label>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </Modal>
  )
}
