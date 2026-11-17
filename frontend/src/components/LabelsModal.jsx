import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Check, Copy, TriangleAlert } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Empty, Loading, Modal, Tabs, useUi } from './ui.jsx'

const copy = (t, toast) => navigator.clipboard?.writeText(t).then(() => toast('Copié'), () => toast('Copie impossible', 'err'))

// Les labels Traefik d'un compose et le fichier YAML de Traefik disent la même chose : ici, on passe de l'un à l'autre (dans les deux sens).
export default function LabelsModal({ id, onClose }) {
  const [tab, setTab] = useState('labels')
  const q = useQuery({ queryKey: ['project-labels', id], queryFn: () => api.get(`/projects/${id}/labels`) })
  return (
    <Modal wide title="Labels Traefik et fichier de Traefik" onClose={onClose} footer={<Btn onClick={onClose}>Fermer</Btn>}>
      <div className="col">
        <Tabs value={tab} onChange={setTab} tabs={[{ id: 'labels', label: 'Du compose vers Traefik' }, { id: 'site', label: 'D\'un site vers le compose' }]} />
        {tab === 'labels' && (q.isLoading ? <Loading /> : q.error ? <div className="banner err">{q.error.message}</div> : (
          <div className="col">
            {q.data.checks.map((c, i) => <div key={i} className={'banner ' + (c.level === 'err' ? 'err' : 'warn')}><TriangleAlert size={14} /> <b>{c.service}</b> : {c.text}</div>)}
            {q.data.services.length === 0 && <div className="card"><Empty><div>Aucun service de ce compose n'a de labels <code>traefik.*</code>.</div></Empty></div>}
            {q.data.services.map((s) => <ServiceCard key={s.service} id={id} s={s} data={q.data} />)}
          </div>))}
        {tab === 'site' && <FromSite id={id} />}
      </div>
    </Modal>
  )
}

const NEW_FILE = '__new__'

// Un service du compose : ses labels vus comme un fichier de Traefik, et de quoi les écrire dans Traefik (fichier au choix, labels retirés ensuite si on veut).
function ServiceCard({ id, s, data }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const [dest, setDest] = useState(NEW_FILE); const [name, setName] = useState(''); const [remove, setRemove] = useState(false)
  const apply = useMutation({
    mutationFn: () => api.post(`/projects/${id}/labels/apply`, { service: s.service, file: dest === NEW_FILE ? name.trim() : dest, removeLabels: remove }),
    onSuccess: (r) => { for (const k of ['project-labels', 'project', 'overview', 'project-tree']) qc.invalidateQueries({ queryKey: [k, id] }); qc.invalidateQueries({ queryKey: ['overview'] }); toast(`Créé dans Traefik : ${r.file}${r.removedLabels?.length ? ' — labels retirés du compose (redéployez le projet)' : ''}`) },
    onError: (e) => toast(e.message, 'err'),
  })
  const go = async () => {
    const where = dest === NEW_FILE ? (name.trim() || `zz-…-${s.service}.yml`) : dest
    if (await confirm({ title: 'Créer ces règles dans Traefik ?', message: `Les ${s.items.length} élément(s) de « ${s.service} » sont écrits dans « ${where} » du dossier de Traefik.${remove ? ' Les labels traefik.* sont ensuite retirés du compose (son historique est gardé) : redéployez le projet pour que Docker les oublie.' : ' Les labels restent dans le compose : sans les retirer, le même site existe deux fois (en @docker et en @file).'}`, confirmLabel: 'Créer', danger: false })) apply.mutate()
  }
  return (
    <div className="card col" style={{ gap: 8 }}>
      <div className="row" style={{ gap: 8 }}><b>{s.service}</b>{s.disabled && <Badge kind="warn">traefik.enable=false</Badge>}<span className="muted small">{Object.keys(s.labels).length} label{Object.keys(s.labels).length > 1 ? 's' : ''}</span>
        <span className="right"><Btn small title="Copie le fichier YAML équivalent" onClick={() => copy(s.yaml, toast)}><Copy size={13} /> Copier le YAML</Btn></span></div>
      {s.warnings.map((w, i) => <div key={i} className="small" style={{ color: 'var(--warn)' }}>⚠ {w}</div>)}
      <pre className="dk-code">{s.yaml || '(rien à convertir)'}</pre>
      {data.canApply && s.items.length > 0 && (
        <div className="col" style={{ gap: 6 }}>
          <div className="row wrap" style={{ gap: 8 }}>
            <span className="muted small">Écrire dans</span>
            <select className="input" style={{ maxWidth: 300 }} value={dest} onChange={(e) => setDest(e.target.value)}>
              <option value={NEW_FILE}>un nouveau fichier…</option>
              {(data.traefikFiles || []).map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
            {dest === NEW_FILE && <input className="input mono" style={{ maxWidth: 260 }} value={name} onChange={(e) => setName(e.target.value)} placeholder={`zz-…-${s.service}.yml (vide : nom automatique)`} />}
          </div>
          {data.canEditCompose && <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={remove} onChange={(e) => setRemove(e.target.checked)} />Retirer ensuite les labels traefik.* de ce service dans le compose<span className="muted">(sinon la règle existerait deux fois)</span></label>}
          <div><Btn small kind="primary" busy={apply.isPending} onClick={go}><ArrowRight size={13} /> Créer dans Traefik</Btn></div>
        </div>)}
    </div>
  )
}

// Un site de Traefik (fichier) → des labels dans un service du compose : on propose le service d'après la cible du site, on laisse choisir le fichier compose.
function FromSite({ id }) {
  const { toast } = useUi(); const qc = useQueryClient()
  const [name, setName] = useState(''); const [plan, setPlan] = useState(null); const [pick, setPick] = useState(''); const [overwrite, setOverwrite] = useState(false); const [disable, setDisable] = useState(true); const [done, setDone] = useState(null)
  const ov = useQuery({ queryKey: ['overview'], queryFn: () => api.get('/traefik/overview') })
  const sites = (ov.data?.items || []).filter((i) => i.proto === 'http' && i.kind === 'routers' && !i.disabled)
  const key = (c) => `${c.file}::${c.service}`
  const analyse = useMutation({ mutationFn: (router) => api.post(`/projects/${id}/labels/from-site/plan`, { router }), onSuccess: (r) => { setPlan(r); setDone(null); setPick(r.suggest ? key(r.suggest) : '') }, onError: (e) => toast(e.message, 'err') })
  const chosen = plan?.candidates.find((c) => key(c) === pick)
  const apply = useMutation({
    mutationFn: () => api.post(`/projects/${id}/labels/from-site/apply`, { router: plan.router, service: chosen.service, file: chosen.file, overwrite, disableSite: disable && plan.canDisable }),
    onSuccess: (r) => { setDone(r); for (const k of ['project-labels', 'project', 'project-tree']) qc.invalidateQueries({ queryKey: [k, id] }); qc.invalidateQueries({ queryKey: ['overview'] }); toast(`Labels écrits dans ${r.file}`) },
    onError: (e) => toast(e.message, 'err'),
  })
  return (
    <div className="col">
      <div className="muted">Choisissez un site de Traefik : on propose le service de ce projet qui correspond à sa cible, on écrit ses labels (routeur, service, protections) dans le fichier compose que vous choisissez, et on peut mettre le site en pause côté Traefik pour qu'il ne soit pas servi deux fois.</div>
      <div className="row" style={{ gap: 8 }}>
        <select className="input grow" value={name} onChange={(e) => { setName(e.target.value); setPlan(null); setDone(null) }}><option value="">Choisir un site…</option>{sites.map((s) => <option key={s.id} value={s.name}>{s.name} — {s.spec?.rule}</option>)}</select>
        <Btn kind="primary" disabled={!name} busy={analyse.isPending} onClick={() => analyse.mutate(name)} title="Calcule les labels et propose le service">Analyser</Btn>
      </div>
      {plan && (
        <div className="card col" style={{ gap: 10 }}>
          {plan.checks.map((c, i) => <div key={i} className={'banner ' + (c.level === 'err' ? 'err' : c.level === 'info' ? 'info' : 'warn')} style={{ margin: 0 }}><TriangleAlert size={14} /> {c.text}</div>)}
          {plan.warnings.map((w, i) => <div key={i} className="small" style={{ color: 'var(--warn)' }}>⚠ {w}</div>)}
          <div><b>Dans quel service ?</b> <span className="muted small">La cible du site est <code>{plan.targets.join(', ') || '—'}</code>.</span></div>
          {plan.candidates.length === 0 ? <div className="muted small">Aucun service dans les fichiers compose de ce projet.</div> : (
            <div className="col" style={{ gap: 4 }}>
              {plan.candidates.map((c) => (
                <label key={key(c)} className="row wrap" style={{ gap: 8, cursor: 'pointer' }}>
                  <input type="radio" name="svc" checked={pick === key(c)} onChange={() => setPick(key(c))} />
                  <b>{c.service}</b><span className="muted small mono">{c.file}</span>{c.containerName && <span className="muted small">({c.containerName})</span>}
                  {plan.suggest && key(plan.suggest) === key(c) && <Badge kind="ok" title={plan.suggest.reason}>suggéré</Badge>}
                  {c.hasTraefik && <Badge kind="warn" title="Ce service a déjà des labels traefik.* : ils sont complétés, pas écrasés (sauf si vous cochez « remplacer »)">a déjà des labels</Badge>}
                  {plan.suggest && key(plan.suggest) === key(c) && <span className="muted small">— {c.reason}</span>}
                </label>))}
            </div>)}
          <div className="row"><b className="grow">Labels qui seront écrits</b><Btn small onClick={() => copy(Object.entries(plan.labels).map(([k, v]) => `${k}=${v}`).join('\n'), toast)}><Copy size={13} /> Copier</Btn></div>
          <pre className="dk-code">{Object.entries(plan.labels).map(([k, v]) => `${k}=${v}`).join('\n')}</pre>
          <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />Remplacer les labels qui existent déjà avec une autre valeur</label>
          {plan.canDisable && <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={disable} onChange={(e) => setDisable(e.target.checked)} />Mettre le site en pause dans Traefik <span className="muted">(réversible : sans cela, il est servi deux fois, en @file et en @docker)</span></label>}
          {done ? <div className="banner ok" style={{ margin: 0 }}><Check size={14} /> Labels écrits dans <b>{done.file}</b> (service {done.service}){done.disabled ? ' ; site mis en pause dans Traefik' : ''}. {done.note}</div>
            : <div><Btn kind="primary" disabled={!chosen || !plan.canApply} busy={apply.isPending} onClick={() => apply.mutate()} title={plan.canApply ? 'Écrit les labels dans le fichier compose choisi (son historique est gardé)' : 'Droit de modifier les fichiers du projet requis'}><ArrowRight size={13} /> Écrire dans le compose</Btn></div>}
        </div>)}
    </div>
  )
}
