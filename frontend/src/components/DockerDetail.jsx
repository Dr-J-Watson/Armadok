import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, File, Folder, FolderOpen, Link2, Link2Off, Plus } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Copyable, Field, Loading, Modal, useUi } from './ui.jsx'
import { enc, fmtSize } from '../lib/docker.js'
import { ago } from '../lib/features.js'

const Row = ({ k, children, tip }) => (children == null || children === false || children === '' ? null : <div className="kv" title={tip}><span className="muted">{k}</span><span style={{ minWidth: 0, wordBreak: 'break-all' }}>{children}</span></div>)
const when = (s) => { const t = typeof s === 'number' ? s * 1000 : Date.parse(s); return t ? `${new Date(t).toLocaleString('fr-FR')} (${ago(t / 1000)})` : '' }

// ── image ───────────────────────────────────────────────────────────────────────────────────────────────────────────
export function ImageModal({ id, onClose, onOpenContainer }) {
  const q = useQuery({ queryKey: ['docker-image', id], queryFn: () => api.get(`/docker/images/${enc(id)}/detail`) })
  const d = q.data
  return (
    <Modal wide title={d ? (d.tags[0] || `Image ${d.id}`) : 'Image'} onClose={onClose} footer={<Btn onClick={onClose}>Fermer</Btn>}>
      {q.isLoading ? <Loading /> : q.error ? <div className="banner err">{q.error.message}</div> : (
        <div className="col gap-lg">
          <div className="col" style={{ gap: 4 }}>
            <Row k="Étiquettes">{d.tags.length ? <span className="row wrap" style={{ gap: 4 }}>{d.tags.map((t) => <Copyable key={t} text={t}><Badge>{t}</Badge></Copyable>)}</span> : <span className="muted">sans nom (image orpheline)</span>}</Row>
            <Row k="Identifiant"><span className="mono small">{d.fullId}</span></Row>
            <Row k="Taille">{fmtSize(d.size)}</Row>
            <Row k="Créée">{when(d.created)}</Row>
            <Row k="Plateforme">{[d.os, d.arch].filter(Boolean).join(' / ')}</Row>
            <Row k="Auteur">{d.author}</Row>
            <Row k="Utilisateur">{d.user && <span className="mono">{d.user}</span>}</Row>
            <Row k="Dossier de travail">{d.workdir && <span className="mono">{d.workdir}</span>}</Row>
            <Row k="Point d'entrée">{d.entrypoint.length > 0 && <span className="mono small">{d.entrypoint.join(' ')}</span>}</Row>
            <Row k="Commande">{d.cmd.length > 0 && <span className="mono small">{d.cmd.join(' ')}</span>}</Row>
            <Row k="Ports déclarés">{d.exposed.length > 0 && <span className="row wrap" style={{ gap: 4 }}>{d.exposed.map((p) => <Badge key={p}>{p}</Badge>)}</span>}</Row>
            <Row k="Volumes déclarés">{d.volumes.length > 0 && <span className="mono small">{d.volumes.join(', ')}</span>}</Row>
            <Row k="Variables" tip="Seuls les noms sont affichés, jamais les valeurs.">{d.envNames.length > 0 && <span className="mono small">{d.envNames.join(', ')}</span>}</Row>
          </div>
          <div className="col" style={{ gap: 6 }}>
            <h3>Utilisée par</h3>
            {d.usedBy.length ? <div className="row wrap" style={{ gap: 4 }}>{d.usedBy.map((n) => <Badge key={n} style={{ cursor: 'pointer' }} onClick={() => onOpenContainer?.(n)} title="Ouvrir ce conteneur">{n}</Badge>)}</div>
              : <div className="muted small">Aucun conteneur : cette image peut être supprimée sans conséquence (elle sera retéléchargée au besoin).</div>}
          </div>
          {d.digests.length > 0 && <div className="col" style={{ gap: 4 }}><h3>Empreintes</h3>{d.digests.map((x) => <Copyable key={x} text={x}><span className="mono small" style={{ wordBreak: 'break-all' }}>{x}</span></Copyable>)}</div>}
          {Object.keys(d.labels).length > 0 && <div className="col" style={{ gap: 4 }}><h3>Labels</h3>{Object.entries(d.labels).map(([k, v]) => <Row key={k} k={k}><span className="small">{v}</span></Row>)}</div>}
          <div className="col" style={{ gap: 6 }}>
            <h3>Couches ({d.layers.length})</h3>
            <div className="card flush"><table className="t"><thead><tr><th>Instruction</th><th>Taille</th></tr></thead><tbody>
              {d.layers.map((l, i) => <tr key={i}><td className="mono small" style={{ wordBreak: 'break-all' }}>{l.by || '—'}</td><td className="nowrap">{l.size ? fmtSize(l.size) : '—'}</td></tr>)}</tbody></table></div>
          </div>
        </div>)}
    </Modal>
  )
}

// ── réseau ──────────────────────────────────────────────────────────────────────────────────────────────────────────
export function NetworkModal({ name, onClose, onOpenContainer }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const q = useQuery({ queryKey: ['docker-network', name], queryFn: () => api.get(`/docker/networks/${enc(name)}/detail`), refetchInterval: 10000 })
  const all = useQuery({ queryKey: ['docker-list'], queryFn: () => api.get('/docker/containers') })
  const [pick, setPick] = useState(''); const [alias, setAlias] = useState('')
  const refresh = () => { qc.invalidateQueries({ queryKey: ['docker-network', name] }); qc.invalidateQueries({ queryKey: ['docker-networks'] }); qc.invalidateQueries({ queryKey: ['docker-list'] }) }
  const link = useMutation({ mutationFn: () => api.post(`/docker/networks/${enc(name)}/connect`, { container: pick, aliases: alias.split(/[,\s]+/).filter(Boolean) }), onSuccess: () => { toast('Conteneur relié au réseau'); setPick(''); setAlias(''); refresh() }, onError: (e) => toast(e.message, 'err') })
  const unlink = useMutation({ mutationFn: (c) => api.post(`/docker/networks/${enc(name)}/disconnect`, { container: c }), onSuccess: () => { toast('Conteneur détaché du réseau'); refresh() }, onError: (e) => toast(e.message, 'err') })
  const d = q.data; const here = new Set((d?.containers || []).map((c) => c.name))
  const free = (all.data?.containers || []).filter((c) => !here.has(c.name) && c.state === 'running' && !c.protected)
  const detach = async (c) => { if (await confirm({ title: `Détacher « ${c.name} » de « ${name} » ?`, message: 'Le conteneur perd l\'accès aux autres conteneurs de ce réseau (et Traefik ne pourra plus l\'atteindre par ce réseau, s\'il en dépend).', confirmLabel: 'Détacher' })) unlink.mutate(c.name) }
  return (
    <Modal wide title={`Réseau « ${name} »`} onClose={onClose} footer={<Btn onClick={onClose}>Fermer</Btn>}>
      {q.isLoading ? <Loading /> : q.error ? <div className="banner err">{q.error.message}</div> : (
        <div className="col gap-lg">
          <div className="col" style={{ gap: 4 }}>
            <Row k="Identifiant"><span className="mono small">{d.id}</span></Row>
            <Row k="Pilote">{d.driver}{d.builtin && <Badge style={{ marginLeft: 6 }}>intégré</Badge>}{d.internal && <Badge style={{ marginLeft: 6 }} kind="warn" title="Les conteneurs de ce réseau n'ont pas d'accès à l'extérieur">interne</Badge>}</Row>
            <Row k="Portée">{d.scope}</Row>
            <Row k="Créé">{when(d.created)}</Row>
            <Row k="Sous-réseaux">{d.subnets.length > 0 && d.subnets.map((s) => <div key={s.subnet} className="mono small">{s.subnet}{s.gateway && <span className="muted"> · passerelle {s.gateway}</span>}</div>)}</Row>
            {Object.keys(d.options).length > 0 && <Row k="Options"><span className="mono small">{Object.entries(d.options).map(([k, v]) => `${k}=${v}`).join(', ')}</span></Row>}
          </div>
          <div className="col" style={{ gap: 6 }}>
            <h3>Conteneurs sur ce réseau ({d.containers.length})</h3>
            {d.containers.length === 0 ? <div className="muted small">Aucun conteneur n'est relié à ce réseau.</div> : (
              <div className="card flush"><table className="t"><thead><tr><th>Conteneur</th><th>Adresse</th><th /></tr></thead><tbody>
                {d.containers.map((c) => <tr key={c.id}><td><b style={{ cursor: 'pointer' }} onClick={() => onOpenContainer?.(c.name)} title="Ouvrir ce conteneur">{c.name}</b>{c.self && <Badge style={{ marginLeft: 6 }} kind="warn">cette interface</Badge>}</td><td className="mono small">{c.ip}{c.ip6 && <div className="muted">{c.ip6}</div>}</td>
                  <td className="actions">{d.canControl && !c.self && !d.builtin && <Btn small kind="ghost" title="Retirer ce conteneur de ce réseau" onClick={() => detach(c)} busy={unlink.isPending}><Link2Off size={13} /> Détacher</Btn>}</td></tr>)}</tbody></table></div>)}
          </div>
          {d.canControl && !['host', 'none'].includes(d.name) && (
            <div className="col" style={{ gap: 6 }}>
              <h3>Relier un conteneur</h3>
              <div className="muted small">Un conteneur relié peut joindre les autres par leur nom. Un alias facultatif lui donne un nom de plus sur ce réseau.</div>
              <div className="row wrap" style={{ gap: 8 }}>
                <select className="input" style={{ maxWidth: 260 }} value={pick} onChange={(e) => setPick(e.target.value)}><option value="">— choisir un conteneur en marche —</option>{free.map((c) => <option key={c.id} value={c.name}>{c.name}</option>)}</select>
                <input className="input mono" style={{ maxWidth: 200 }} value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="alias (facultatif)" />
                <Btn kind="primary" disabled={!pick} busy={link.isPending} onClick={() => link.mutate()}><Link2 size={14} /> Relier</Btn>
              </div>
            </div>)}
        </div>)}
    </Modal>
  )
}

export function NewNetworkModal({ onClose }) {
  const { toast } = useUi(); const qc = useQueryClient(); const [f, setF] = useState({ name: '', subnet: '', internal: false })
  const m = useMutation({ mutationFn: () => api.post('/docker/networks', f), onSuccess: () => { qc.invalidateQueries({ queryKey: ['docker-networks'] }); toast('Réseau créé'); onClose() }, onError: (e) => toast(e.message, 'err') })
  return (
    <Modal small title="Nouveau réseau" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" disabled={!f.name.trim()} busy={m.isPending} onClick={() => m.mutate()}><Plus size={14} /> Créer</Btn></>}>
      <div className="col">
        <div className="muted small">Un réseau « bridge » : les conteneurs qui le rejoignent se joignent par leur nom, sans publier de port.</div>
        <Field label="Nom" help="Lettres, chiffres, « - », « _ » et « . »."><input className="input mono" autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="proxy" /></Field>
        <Field label="Sous-réseau" help="Facultatif. Ex : 172.30.0.0/24. Vide : Docker choisit."><input className="input mono" value={f.subnet} onChange={(e) => setF({ ...f, subnet: e.target.value.trim() })} placeholder="172.30.0.0/24" /></Field>
        <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={f.internal} onChange={(e) => setF({ ...f, internal: e.target.checked })} />Réseau interne (sans accès à l'extérieur)</label>
      </div>
    </Modal>
  )
}

// ── volume ──────────────────────────────────────────────────────────────────────────────────────────────────────────
function VolumeFiles({ name }) {
  const [path, setPath] = useState('/'); const [file, setFile] = useState(null)
  const ls = useQuery({ queryKey: ['docker-vol-ls', name, path], queryFn: () => api.get(`/docker/volumes/${enc(name)}/files?path=${enc(path)}`), retry: false, staleTime: 15000 })
  const txt = useQuery({ queryKey: ['docker-vol-file', name, file], queryFn: () => api.get(`/docker/volumes/${enc(name)}/file?path=${enc(file)}`), enabled: !!file, retry: false })
  const join = (n) => (path === '/' ? '/' + n : path + '/' + n)
  const parts = path.split('/').filter(Boolean)
  const crumbs = [['/', '/'], ...parts.map((p, i) => [p, '/' + parts.slice(0, i + 1).join('/')])]
  if (file) {
    const d = txt.data
    return (
      <div className="col" style={{ gap: 8 }}>
        <div className="row" style={{ gap: 8 }}><Btn small onClick={() => setFile(null)}><ArrowLeft size={13} /> Retour</Btn><b className="mono small ellipsis grow" title={file}>{file}</b>{d && <span className="muted small">{fmtSize(d.size)}</span>}</div>
        {txt.isLoading ? <Loading /> : txt.error ? <div className="banner err">{txt.error.message}</div> : d.binary ? <div className="banner info">Fichier binaire ({fmtSize(d.size)}) : non affiché.</div> : (<>
          {d.truncated && <div className="banner warn">Seul le début du fichier est affiché ({fmtSize(256 * 1024)} sur {fmtSize(d.size)}).</div>}
          <pre className="rule-preview mono small" style={{ maxHeight: '50vh', overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>{d.text || '(fichier vide)'}</pre></>)}
      </div>)
  }
  return (
    <div className="col" style={{ gap: 8 }}>
      <div className="row wrap" style={{ gap: 4 }}>{crumbs.map(([l, p], i) => <span key={p} className="row" style={{ gap: 4 }}>{i > 1 && <span className="muted">/</span>}<button type="button" className="btn sm ghost" onClick={() => setPath(p)}>{i === 0 ? <FolderOpen size={13} /> : l}</button></span>)}</div>
      {ls.isLoading ? <Loading /> : ls.error ? <div className="banner err">{ls.error.message}</div> : ls.data.entries.length === 0 ? <div className="muted small">Dossier vide.</div> : (
        <div className="card flush"><table className="t"><thead><tr><th>Nom</th><th>Taille</th><th>Modifié</th><th>Droits</th></tr></thead><tbody>
          {ls.data.entries.map((e) => (
            <tr key={e.name} style={{ cursor: e.type === 'link' ? 'default' : 'pointer' }} onClick={() => (e.type === 'dir' ? setPath(join(e.name)) : e.type === 'file' ? setFile(join(e.name)) : null)}>
              <td className="row" style={{ gap: 6 }}>{e.type === 'dir' ? <Folder size={14} /> : <File size={14} />}<span className="mono small">{e.name}{e.type === 'link' ? ' →' : ''}</span></td>
              <td className="nowrap small">{e.type === 'dir' ? '—' : fmtSize(e.size)}</td><td className="small muted">{e.mtime ? ago(e.mtime) : ''}</td><td className="mono small muted">{e.mode} {e.uid}:{e.gid}</td></tr>))}</tbody></table></div>)}
      {ls.data?.more && <div className="muted small">Seules les 500 premières entrées sont listées.</div>}
    </div>
  )
}

export function VolumeModal({ name, onClose, onOpenContainer }) {
  const q = useQuery({ queryKey: ['docker-volume', name], queryFn: () => api.get(`/docker/volumes/${enc(name)}/detail`) })
  const d = q.data
  return (
    <Modal wide title="Volume" onClose={onClose} footer={<Btn onClick={onClose}>Fermer</Btn>}>
      {q.isLoading ? <Loading /> : q.error ? <div className="banner err">{q.error.message}</div> : (
        <div className="col gap-lg">
          <div className="col" style={{ gap: 4 }}>
            <Row k="Nom"><Copyable text={d.name}><span className="mono small">{d.name}</span></Copyable></Row>
            <Row k="Pilote">{d.driver}</Row>
            <Row k="Emplacement"><span className="mono small">{d.mountpoint}</span></Row>
            <Row k="Créé">{when(d.created)}</Row>
            {Object.keys(d.options).length > 0 && <Row k="Options"><span className="mono small">{Object.entries(d.options).map(([k, v]) => `${k}=${v}`).join(', ')}</span></Row>}
            <Row k="Utilisé par">{d.usedBy.length > 0 ? <span className="row wrap" style={{ gap: 4 }}>{d.usedBy.map((n) => <Badge key={n} style={{ cursor: 'pointer' }} onClick={() => onOpenContainer?.(n)}>{n}</Badge>)}</span> : <span className="muted">aucun conteneur (supprimable)</span>}</Row>
          </div>
          <div className="col" style={{ gap: 6 }}>
            <h3>Contenu</h3>
            {d.canBrowse ? <><div className="muted small">Lecture seule, par un conteneur temporaire sans réseau. Les fichiers ne se modifient pas d'ici.</div><VolumeFiles name={d.name} /></>
              : <div className="banner info">Parcourir l'intérieur d'un volume demande le droit « terminal dans les conteneurs » (et, sur un worker, WORKER_ALLOW_EXEC=true) : c'est un accès aux données de vos applications.</div>}
          </div>
        </div>)}
    </Modal>
  )
}
