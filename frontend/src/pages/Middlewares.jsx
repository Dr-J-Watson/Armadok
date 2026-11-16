import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Copy, Pause, Pencil, Plus, Trash2 } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Empty, Info, Loading, SearchBox, Switch, useUi } from '../components/ui.jsx'
import { useToggler } from '../components/Toggle.jsx'
import { ItemEditor, useMeta, useOverview } from './Traefik.jsx'
import { humanOf, techName } from '../traefik/human.js'

const CATEGORIES = [
  ['security', 'Sécurité & accès', 'Mot de passe, liste d\'IP, authentification externe, en-têtes de sécurité…'],
  ['path', 'Redirection & chemins', 'Rediriger, ajouter ou retirer un préfixe, réécrire le chemin.'],
  ['content', 'Requêtes & réponses', 'Compression, pages d\'erreur, type de contenu, mise en tampon.'],
  ['traffic', 'Résilience & trafic', 'Limiter le débit, réessayer, couper un service qui répond mal.'],
  ['group', 'Groupes', 'Plusieurs protections appliquées ensemble, dans l\'ordre, sous un seul nom.'],
  ['other', 'Autres', ''],
]

// Toutes les protections (middlewares) du nœud au même endroit : on les crée, on les coupe, on voit qui les utilise.
export default function Middlewares() {
  const ov = useOverview(); const meta = useMeta(); const qc = useQueryClient(); const { toast, confirm } = useUi()
  const { toggle, busy } = useToggler()
  const [q, setQ] = useState(''); const [edit, setEdit] = useState(null)
  const items = ov.data?.items || []

  const list = useMemo(() => items.filter((i) => i.kind === 'middlewares' && i.proto !== 'tls')
    .filter((i) => !q || `${i.name} ${humanOf(i.type)[0]} ${techName(i.type)} ${i.type}`.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => a.name.localeCompare(b.name)), [items, q])
  const sections = CATEGORIES.map(([id, title, hint]) => ({ id, title, hint, rows: list.filter((m) => humanOf(m.type)[2] === id || (id === 'other' && !CATEGORIES.some(([c]) => c === humanOf(m.type)[2]))) })).filter((s) => s.rows.length)
  const total = items.filter((i) => i.kind === 'middlewares' && i.proto !== 'tls').length
  const off = items.filter((i) => i.kind === 'middlewares' && i.disabled).length

  const del = useMutation({
    mutationFn: (m) => api.del(`/traefik/item?proto=${m.proto}&kind=${m.kind}&name=${encodeURIComponent(m.name)}`),
    onSuccess: () => { qc.invalidateQueries(); toast('Supprimé') }, onError: (e) => toast(e.message, 'err'),
  })
  const askDelete = async (m) => {
    const users = m.usedBy?.length ? `\n\n⚠ Utilisé par : ${m.usedBy.map((u) => u.split('/').pop()).join(', ')}` : ''
    if (await confirm({ title: `Supprimer « ${m.name} » ?`, message: `Retire la protection de ${m.file}.${users}\nUne sauvegarde est conservée.`, confirmLabel: 'Supprimer' })) del.mutate(m)
  }

  if (ov.isLoading) return <Loading />
  return (
    <>
      <div className="page-head">
        <div><h1>Middlewares <Info>{'Un middleware est une protection ou une transformation placée entre le routeur et l\'application : mot de passe, liste d\'IP autorisées, en-têtes de sécurité, redirection…\nCréez-le une fois, branchez-le sur autant de sites que vous voulez (depuis l\'assistant de site). L\'interrupteur le retire de tous les sites qui l\'utilisent, et le remet au même endroit.'}</Info></h1>
          <p>{total} au total{off > 0 && <> · <b>{off}</b> désactivé(s)</>} · les protections appliquées avant d'envoyer la requête à l'application.</p></div>
        <Btn kind="primary" className="right" onClick={() => setEdit({ isNew: true })}><Plus size={15} /> Nouveau middleware</Btn>
      </div>
      <div className="row" style={{ marginBottom: 14 }}><SearchBox value={q} onChange={setQ} placeholder="Chercher un middleware ou un type…" /></div>
      {sections.length === 0 && <div className="card"><Empty>{total ? 'Aucun résultat.' : <>Aucun middleware pour l'instant.<br /><Btn kind="primary" style={{ marginTop: 12 }} onClick={() => setEdit({ isNew: true })}><Plus size={14} /> Créer le premier</Btn></>}</Empty></div>}
      <div className="col" style={{ gap: 22 }}>
        {sections.map((s) => (
          <section key={s.id} className="col" style={{ gap: 10 }}>
            <div className="mw-cat"><h3>{s.title}</h3><Badge>{s.rows.length}</Badge>{s.hint && <Info>{s.hint}</Info>}</div>
            <div className="mw-grid">
              {s.rows.map((m) => {
                const sites = (m.usedBy || []).map((u) => u.split('/').pop())
                return (
                  <div key={m.id} className={'mw-card ' + (m.disabled ? 'off' : '')} onClick={() => (m.disabled ? toggle({ ids: [m.id], enable: true, label: `« ${m.name} »` }) : setEdit({ item: m }))}>
                    <div className="row">
                      <div className="grow" style={{ minWidth: 0 }}>
                        <b className="ellipsis" style={{ display: 'block' }}>{m.name}{m.proto === 'tcp' && <Badge className="mw-proto">TCP</Badge>}</b>
                        <span className="muted small"><b>{techName(m.type)}</b> · {humanOf(m.type)[0]}</span>
                      </div>
                      {m.disabled && <Badge kind="warn" className="badge-off"><Pause size={10} /> désactivé</Badge>}
                      <Switch value={!m.disabled} busy={busy} title={m.disabled ? 'Réactiver' : 'Désactiver : le retire de tous les sites qui l\'utilisent, réversible'} onChange={(on) => toggle({ ids: [m.id], enable: on, label: `« ${m.name} »` })} />
                    </div>
                    <div className="row wrap small" style={{ gap: 5 }} title="Les routeurs et groupes qui utilisent ce middleware">
                      <span className="muted">Utilisé par</span>
                      {m.disabled ? <span className="muted">—</span> : sites.length === 0 ? <Badge kind="warn">personne</Badge>
                        : <>{sites.slice(0, 4).map((n) => <Badge key={n} kind="accent">{n}</Badge>)}{sites.length > 4 && <Badge>+{sites.length - 4}</Badge>}</>}
                    </div>
                    <div className="row mw-actions" onClick={(e) => e.stopPropagation()}>
                      <span className="muted small ellipsis grow" title={m.file}>{m.file}</span>
                      {!m.disabled && <Btn kind="ghost" small icon title="Dupliquer" onClick={() => setEdit({ isNew: true, template: { ...m, name: m.name + '-copie' } })}><Copy size={14} /></Btn>}
                      {!m.disabled && <Btn kind="ghost" small icon title="Modifier" onClick={() => setEdit({ item: m })}><Pencil size={14} /></Btn>}
                      <Btn kind="ghost" small icon title="Supprimer" onClick={() => askDelete(m)}><Trash2 size={14} /></Btn>
                    </div>
                  </div>
                )
              })}
            </div>
          </section>
        ))}
      </div>
      {edit && <ItemEditor key={edit.item?.id || edit.template?.name || 'new'} {...edit} proto="http" kind="middlewares" items={items} files={(ov.data?.files || []).filter((f) => !f.disabled).map((f) => f.path)} meta={meta.data}
        onClose={() => setEdit(null)} onOpenItem={(it) => setEdit({ item: it })} />}
    </>
  )
}
