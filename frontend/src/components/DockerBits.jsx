import { useEffect, useRef, useState } from 'react'
import { Pause, Play, Trash2 } from 'lucide-react'
import { api } from '../api.js'
import { Btn, Modal } from './ui.jsx'
import { enc, stripAnsi } from '../lib/docker.js'

// Confirmation « retapez le nom » : pour agir sur le conteneur de l'interface ou celui de Traefik.
export function NameConfirm({ name, what, onCancel, onConfirm }) {
  const [v, setV] = useState('')
  return (
    <Modal small title="Action sensible" onClose={onCancel} footer={<><Btn onClick={onCancel}>Annuler</Btn><Btn kind="danger" disabled={v !== name} onClick={() => onConfirm(v)}>{what}</Btn></>}>
      <div className="col">
        <div>« <b>{name}</b> » est un conteneur dont dépend l'accès à vos sites ou à cette interface. Retapez son nom pour confirmer.</div>
        <input className="input mono" autoFocus value={v} onChange={(e) => setV(e.target.value)} placeholder={name} onKeyDown={(e) => e.key === 'Enter' && v === name && onConfirm(v)} />
      </div>
    </Modal>
  )
}

// Barre fine (CPU, mémoire).
export function MiniBar({ pct, warn = 80, label }) {
  const p = Math.max(0, Math.min(100, pct || 0))
  return <div className="dk-bar" title={label}><i style={{ width: `${p}%`, background: p >= warn ? 'var(--warn)' : undefined }} /></div>
}

// Journaux en direct : on interroge le serveur toutes les 2,5 s avec un curseur (le même mécanisme marche pour un nœud distant).
export function LogViewer({ name }) {
  const [lines, setLines] = useState([]); const [live, setLive] = useState(true); const [filter, setFilter] = useState(''); const [err, setErr] = useState('')
  const since = useRef(0); const box = useRef(null); const stick = useRef(true)
  useEffect(() => { since.current = 0; setLines([]); setErr('') }, [name])
  useEffect(() => {
    if (!live) return undefined
    let stop = false; let timer
    const tick = async () => {
      try {
        const r = await api.get(`/docker/containers/${enc(name)}/logs?tail=${since.current ? 1000 : 300}&since=${since.current}`)
        if (stop) return
        setErr(''); since.current = r.since
        if (r.lines.length) setLines((l) => [...l, ...r.lines].slice(-3000))
      } catch (e) { if (!stop) setErr(e.message) }
      if (!stop) timer = setTimeout(tick, 2500)
    }
    tick()
    return () => { stop = true; clearTimeout(timer) }
  }, [name, live])
  useEffect(() => { if (stick.current && box.current) box.current.scrollTop = box.current.scrollHeight }, [lines])
  const f = filter.trim().toLowerCase()
  const shown = f ? lines.filter((l) => l.m.toLowerCase().includes(f)) : lines
  return (
    <div className="col">
      <div className="row" style={{ gap: 8 }}>
        <input className="input grow" placeholder="Filtrer les lignes…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <Btn small onClick={() => setLive((v) => !v)}>{live ? <><Pause size={13} /> Pause</> : <><Play size={13} /> Reprendre</>}</Btn>
        <Btn small kind="ghost" onClick={() => setLines([])}><Trash2 size={13} /> Vider</Btn>
        <span className="muted small">{live ? 'En direct' : 'En pause'}</span>
      </div>
      {err && <div className="banner err">{err}</div>}
      <div className="dk-logs" ref={box} onScroll={(e) => { const t = e.currentTarget; stick.current = t.scrollHeight - t.scrollTop - t.clientHeight < 30 }}>
        {shown.length === 0 ? <div className="muted">Aucune ligne{f ? ' ne correspond au filtre' : ' pour l\'instant'}.</div>
          : shown.map((l, i) => <div key={i} className={l.s === 'err' ? 'dk-err' : ''}>{stripAnsi(l.m)}</div>)}
      </div>
    </div>
  )
}
