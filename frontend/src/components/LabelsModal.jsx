import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Copy, TriangleAlert } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Empty, Loading, Modal, Tabs, useUi } from './ui.jsx'

const copy = (t, toast) => navigator.clipboard?.writeText(t).then(() => toast('Copié'), () => toast('Copie impossible', 'err'))

// Les labels Traefik d'un compose et le fichier YAML de Traefik disent la même chose : ici, on passe de l'un à l'autre (dans les deux sens).
export default function LabelsModal({ id, onClose }) {
  const [tab, setTab] = useState('labels'); const { toast, confirm } = useUi(); const qc = useQueryClient()
  const q = useQuery({ queryKey: ['project-labels', id], queryFn: () => api.get(`/projects/${id}/labels`) })
  const apply = useMutation({ mutationFn: (service) => api.post(`/projects/${id}/labels/apply`, { service }), onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['project-labels', id] }); qc.invalidateQueries({ queryKey: ['project', id] }); qc.invalidateQueries({ queryKey: ['overview'] }); toast(`Créé dans Traefik : ${r.file}`) }, onError: (e) => toast(e.message, 'err') })
  const create = async (s) => { if (await confirm({ title: `Créer ces règles dans Traefik ?`, message: `Les ${s.items.length} élément(s) de « ${s.service} » sont écrits dans un fichier zz-… du dossier de Traefik. Les labels restent dans le compose : retirez-les ensuite si vous ne voulez qu'une définition (sinon le même site existe deux fois, en @docker et en @file).`, confirmLabel: 'Créer', danger: false })) apply.mutate(s.service) }
  return (
    <Modal wide title="Labels Traefik et fichier de Traefik" onClose={onClose} footer={<Btn onClick={onClose}>Fermer</Btn>}>
      <div className="col">
        <Tabs value={tab} onChange={setTab} tabs={[{ id: 'labels', label: 'Du compose vers Traefik' }, { id: 'site', label: 'D\'un site vers des labels' }]} />
        {tab === 'labels' && (q.isLoading ? <Loading /> : q.error ? <div className="banner err">{q.error.message}</div> : (
          <div className="col">
            {q.data.checks.map((c, i) => <div key={i} className={'banner ' + (c.level === 'err' ? 'err' : 'warn')}><TriangleAlert size={14} /> <b>{c.service}</b> : {c.text}</div>)}
            {q.data.services.length === 0 && <div className="card"><Empty><div>Aucun service de ce compose n'a de labels <code>traefik.*</code>.</div></Empty></div>}
            {q.data.services.map((s) => (
              <div key={s.service} className="card col" style={{ gap: 8 }}>
                <div className="row" style={{ gap: 8 }}><b>{s.service}</b>{s.disabled && <Badge kind="warn">traefik.enable=false</Badge>}<span className="muted small">{Object.keys(s.labels).length} label{Object.keys(s.labels).length > 1 ? 's' : ''}</span>
                  <span className="right row" style={{ gap: 6 }}><Btn small onClick={() => copy(s.yaml, toast)}><Copy size={13} /> Copier le YAML</Btn>{q.data.canApply && <Btn small kind="primary" busy={apply.isPending} onClick={() => create(s)}><ArrowRight size={13} /> Créer dans Traefik</Btn>}</span></div>
                {s.warnings.map((w, i) => <div key={i} className="small" style={{ color: 'var(--warn)' }}>⚠ {w}</div>)}
                <pre className="dk-code">{s.yaml || '(rien à convertir)'}</pre>
              </div>))}
          </div>))}
        {tab === 'site' && <FromSite id={id} />}
      </div>
    </Modal>
  )
}

function FromSite() {
  const { toast } = useUi(); const [name, setName] = useState(''); const [res, setRes] = useState(null)
  const ov = useQuery({ queryKey: ['overview'], queryFn: () => api.get('/traefik/overview') })
  const sites = (ov.data?.items || []).filter((i) => i.proto === 'http' && i.kind === 'routers' && !i.disabled)
  const go = useMutation({ mutationFn: (router) => api.post('/projects/labels/from-site', { router }), onSuccess: setRes, onError: (e) => toast(e.message, 'err') })
  return (
    <div className="col">
      <div className="muted">Choisissez un site : on écrit les labels équivalents (routeur, service, protections), à coller dans <code>labels:</code> d'un service du compose.</div>
      <div className="row" style={{ gap: 8 }}><select className="input grow" value={name} onChange={(e) => { setName(e.target.value); setRes(null) }}><option value="">Choisir un site…</option>{sites.map((s) => <option key={s.id} value={s.name}>{s.name} — {s.spec?.rule}</option>)}</select><Btn kind="primary" disabled={!name} busy={go.isPending} onClick={() => go.mutate(name)}>Convertir</Btn></div>
      {res && <div className="card col" style={{ gap: 8 }}>
        {res.warnings.map((w, i) => <div key={i} className="small" style={{ color: 'var(--warn)' }}>⚠ {w}</div>)}
        <div className="row"><b className="grow">À coller dans le compose</b><Btn small onClick={() => copy(res.text, toast)}><Copy size={13} /> Copier</Btn></div>
        <pre className="dk-code">{res.text}</pre></div>}
    </div>
  )
}
