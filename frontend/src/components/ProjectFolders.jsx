import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ChevronRight, CornerLeftUp, Folder, Home, Pencil, Trash2 } from 'lucide-react'
import { Badge, Btn, Field, Modal } from './ui.jsx'

// Dossiers d'organisation des projets : purement logiques (rien ne bouge sur le disque).
export const MIME = 'application/x-armadok-item'
export const FOLDER_COLORS = { red: '#ef4444', orange: '#f97316', yellow: '#eab308', green: '#22c55e', teal: '#14b8a6', blue: '#3b82f6', purple: '#a855f7', pink: '#ec4899', gray: '#6b7280' }

// Arbre à plat, en ordre d'affichage, avec la profondeur : pour les listes déroulantes.
export function flatTree(folders, skip = new Set()) {
  const kids = {}
  folders.forEach((f) => { (kids[f.parentId ?? 0] ||= []).push(f) })
  const out = []
  const walk = (pid, depth) => (kids[pid] || []).forEach((f) => { if (skip.has(f.id)) return; out.push({ ...f, depth }); walk(f.id, depth + 1) })
  walk(0, 0)
  return out
}

export function descendants(folders, id) {
  const out = new Set()
  const walk = (x) => folders.filter((f) => f.parentId === x).forEach((f) => { if (!out.has(f.id)) { out.add(f.id); walk(f.id) } })
  walk(id)
  return out
}

// Chiffres d'un dossier, sous-dossiers compris.
export function folderStats(folders, projects, id) {
  const ids = new Set([id, ...descendants(folders, id)])
  const ps = projects.filter((p) => ids.has(p.folderId))
  return { total: ps.length, running: ps.filter((p) => p.status === 'running').length, partial: ps.filter((p) => p.status === 'partial').length, stopped: ps.filter((p) => p.status === 'stopped').length, missing: ps.filter((p) => p.missing).length, sub: ids.size - 1 }
}

export function FolderTile({ f, st, canManage, dnd, onOpen, onEdit, onDelete, noun = 'projet' }) {
  const c = FOLDER_COLORS[f.color]
  return (
    <div className={'card folder-card' + (dnd.over ? ' drop-on' : '')} style={c ? { borderLeft: `4px solid ${c}` } : undefined} onClick={onOpen} {...dnd.props}>
      <div className="row"><Folder size={18} style={{ color: c || 'var(--muted)', flex: 'none' }} /><b className="grow ellipsis" title={f.name}>{f.name}</b>
        {canManage && <span className="row" style={{ gap: 2 }}>
          <Btn small kind="ghost" icon title="Modifier le dossier" aria-label="Modifier le dossier" onClick={(e) => { e.stopPropagation(); onEdit() }}><Pencil size={13} /></Btn>
          <Btn small kind="ghost" icon title="Supprimer le dossier (son contenu remonte d'un niveau)" aria-label="Supprimer le dossier" onClick={(e) => { e.stopPropagation(); onDelete() }}><Trash2 size={13} /></Btn></span>}</div>
      {f.description && <div className="muted small folder-desc">{f.description}</div>}
      <div className="row wrap" style={{ gap: 5, marginTop: 'auto' }}>
        <Badge>{st.total} {noun}{st.total > 1 ? 's' : ''}</Badge>
        {st.sub > 0 && <Badge>{st.sub} sous-dossier{st.sub > 1 ? 's' : ''}</Badge>}
        {st.running > 0 && noun !== 'site' && <Badge kind="ok">{st.running} en marche</Badge>}{st.stopped > 0 && noun === 'site' && <Badge kind="warn">{st.stopped} en pause</Badge>}
        {st.partial > 0 && <Badge kind="warn">{st.partial} partiel{st.partial > 1 ? 's' : ''}</Badge>}
        {st.missing > 0 && <Badge kind="err">{st.missing} introuvable{st.missing > 1 ? 's' : ''}</Badge>}
      </div>
    </div>
  )
}

// « .. » : remonte d'un niveau (et reçoit ce qu'on y dépose).
export function UpTile({ parent, dnd, onOpen }) {
  return (
    <div className={'card folder-card up' + (dnd.over ? ' drop-on' : '')} onClick={onOpen} {...dnd.props} title="Remonter d'un niveau (déposez ici pour sortir un élément du dossier)">
      <div className="row"><CornerLeftUp size={18} className="muted" /><b className="grow">..</b></div>
      <div className="muted small">{parent ? `Retour à « ${parent.name} »` : 'Retour à la racine'}</div>
    </div>
  )
}

export function Breadcrumb({ path, onOpen, dropFor }) {
  const seg = (id, label, icon) => { const d = dropFor(id); return <span key={id ?? 'root'} className={'crumb' + (d.over ? ' drop-on' : '')} {...d.props}><button type="button" className="crumb-b" onClick={() => onOpen(id)}>{icon}{label}</button></span> }
  return <div className="row wrap crumbs" style={{ gap: 2 }}>{seg(null, 'Projets', <Home size={13} />)}{path.map((f) => <span key={f.id} className="row" style={{ gap: 2 }}><ChevronRight size={13} className="muted" />{seg(f.id, f.name)}</span>)}</div>
}

export function FolderModal({ folder, folders, parentId, busy, onSave, onClose }) {
  const [name, setName] = useState(folder?.name || ''); const [desc, setDesc] = useState(folder?.description || ''); const [color, setColor] = useState(folder?.color || '')
  const [parent, setParent] = useState(folder ? folder.parentId : parentId ?? null)
  const blocked = folder ? new Set([folder.id, ...descendants(folders, folder.id)]) : new Set()
  return (
    <Modal small title={folder ? 'Modifier le dossier' : 'Nouveau dossier'} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={!name.trim()} onClick={() => onSave({ name: name.trim(), description: desc, color, parentId: parent })}>{folder ? 'Enregistrer' : 'Créer'}</Btn></>}>
      <div className="col">
        <div className="muted small">Un dossier range la liste des projets ; rien n'est déplacé sur le disque et les conteneurs ne sont pas touchés.</div>
        <Field label="Nom"><input className="input" autoFocus maxLength={60} value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && name.trim()) onSave({ name: name.trim(), description: desc, color, parentId: parent }) }} placeholder="Production, Tests, Famille…" /></Field>
        <Field label="Description (facultatif)"><textarea className="input" rows={3} maxLength={500} value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="À quoi sert ce dossier ?" /></Field>
        <Field label="Couleur">
          <div className="row wrap" style={{ gap: 8 }}>
            <button type="button" className={'swatch none' + (color === '' ? ' on' : '')} title="Aucune" aria-label="Aucune couleur" onClick={() => setColor('')} />
            {Object.entries(FOLDER_COLORS).map(([k, v]) => <button key={k} type="button" className={'swatch' + (color === k ? ' on' : '')} style={{ background: v }} title={k} aria-label={k} onClick={() => setColor(k)} />)}
          </div></Field>
        <Field label="Rangé dans"><FolderSelect folders={folders} value={parent} onChange={setParent} skip={blocked} /></Field>
      </div>
    </Modal>
  )
}

export function FolderSelect({ folders, value, onChange, skip }) {
  return (
    <select className="select" value={value ?? ''} onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}>
      <option value="">Racine (aucun dossier)</option>
      {flatTree(folders, skip).map((f) => <option key={f.id} value={f.id}>{'  '.repeat(f.depth)}{f.depth ? '↳ ' : ''}{f.name}</option>)}
    </select>
  )
}

export function MoveModal({ projects, folders, current, busy, onMove, onClose, noun = 'projet' }) {
  const [to, setTo] = useState(current ?? null)
  return (
    <Modal small title={`Ranger ${projects.length > 1 ? `${projects.length} ${noun}s` : projects[0].name}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} onClick={() => onMove(to)}>Ranger</Btn></>}>
      <Field label="Dans le dossier"><FolderSelect folders={folders} value={to} onChange={setTo} /></Field>
    </Modal>
  )
}

// Navigation et glisser-déposer communs (projets, sites) : dossier courant dans l'adresse (?g=), onDrop({ kind, id }, dossierCible | null).
export function useFolderNav({ folders, enabled, onDrop }) {
  const [params, setParams] = useSearchParams(); const [over, setOver] = useState(null)
  const byId = Object.fromEntries(folders.map((f) => [f.id, f]))
  const cur = byId[Number(params.get('g'))] ? Number(params.get('g')) : null
  const crumbs = []; for (let x = cur; x != null && crumbs.length < 20; x = byId[x]?.parentId ?? null) crumbs.unshift(byId[x])
  const openFolder = (id) => { setOver(null); setParams((p) => { const n = new URLSearchParams(p); if (id == null) n.delete('g'); else n.set('g', String(id)); return n }) }
  const dragOf = (kind, id) => (enabled ? { draggable: true, onDragStart: (e) => { e.dataTransfer.setData(MIME, JSON.stringify({ kind, id })); e.dataTransfer.effectAllowed = 'move' } } : {})
  const dropFor = (target) => {
    const key = target ?? 'root'
    return { over: over === key, props: enabled ? {
      onDragOver: (e) => { if (!e.dataTransfer.types.includes(MIME)) return; e.preventDefault(); e.dataTransfer.dropEffect = 'move'; if (over !== key) setOver(key) },
      onDragLeave: (e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOver((o) => (o === key ? null : o)) },
      onDrop: (e) => { e.preventDefault(); setOver(null); let x; try { x = JSON.parse(e.dataTransfer.getData(MIME)) } catch { return } if (!x || (x.kind === 'folder' && x.id === target)) return; onDrop(x, target) } } : {} }
  }
  const parentOfCur = byId[cur]?.parentId != null && byId[byId[cur].parentId] ? byId[cur].parentId : null
  return { cur, byId, crumbs, openFolder, dragOf, dropFor, parentOfCur, inCur: (fid) => (byId[fid] ? fid : null) === cur }
}
