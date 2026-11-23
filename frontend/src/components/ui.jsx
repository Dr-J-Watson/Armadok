import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, ChevronUp, Copy, Loader2, Search, X } from 'lucide-react'
import { Info, TooltipLayer } from './Tip.jsx'

export { Info }

const UiCtx = createContext(null)
export const useUi = () => useContext(UiCtx)

export function UiProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const [dialog, setDialog] = useState(null)

  const toast = useCallback((message, kind = 'ok', action = null) => {   // action = { label, run } : bouton « Annuler » pendant quelques secondes
    // jamais de notification vide : on accepte texte, Error ou liste ; sans contenu, une erreur reste visible avec un texte de secours
    let text = message instanceof Error ? message.message : Array.isArray(message) ? message.filter(Boolean).join('\n') : message
    text = typeof text === 'string' ? text.trim() : text == null ? '' : String(text).trim()
    if (!text || text === '[object Object]') {
      if (kind !== 'err') return
      text = 'Une erreur est survenue (le serveur n\'a donné aucun détail)'
    }
    message = text
    const id = Math.random()
    setToasts((t) => [...t, { id, message, kind, action }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === 'err' ? 7000 : action ? 8000 : 3500)
  }, [])
  const confirm = useCallback(
    (opts) => new Promise((resolve) => setDialog({ ...(typeof opts === 'string' ? { message: opts } : opts), resolve })),
    [],
  )
  const done = (v) => { dialog.resolve(v); setDialog(null) }

  return (
    <UiCtx.Provider value={{ toast, confirm }}>
      {children}
      <TooltipLayer />
      <div className="toasts">{toasts.map((t) => <div key={t.id} className={'toast ' + t.kind}>{t.message}{t.action && <button type="button" className="toast-act" onClick={() => { setToasts((l) => l.filter((x) => x.id !== t.id)); t.action.run() }}>{t.action.label}</button>}</div>)}</div>
      {dialog && (
        <Modal small title={dialog.title || 'Confirmation'} onClose={() => done(false)}
          footer={<>
            <Btn onClick={() => done(false)}>Annuler</Btn>
            <Btn kind={dialog.danger === false ? 'primary' : 'danger'} onClick={() => done(true)}>{dialog.confirmLabel || 'Confirmer'}</Btn>
          </>}>
          <div style={{ whiteSpace: 'pre-wrap' }}>{dialog.message}</div>
        </Modal>
      )}
    </UiCtx.Provider>
  )
}

export function Btn({ kind, small, icon, busy, children, className = '', ...p }) {
  return (
    <button {...p} disabled={p.disabled || busy} className={`btn ${kind || ''} ${small ? 'sm' : ''} ${icon ? 'icon' : ''} ${className}`}>
      {busy && <Loader2 size={14} className="spin" />}
      {children}
    </button>
  )
}

export function Modal({ title, onClose, children, footer, wide, small, side }) {
  useEffect(() => {
    const h = (e) => e.key === 'Escape' && onClose?.()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])
  const down = useRef(false)
  return (
    <div className={'overlay ' + (side ? 'right' : '')} onMouseDown={(e) => (down.current = e.target === e.currentTarget)} onClick={(e) => down.current && e.target === e.currentTarget && onClose?.()}>
      <div className={side ? 'drawer' : `modal ${wide ? 'wide' : ''} ${small ? 'sm' : ''}`}>
        <div className="mhead"><h2 className="grow">{title}</h2><Btn kind="ghost" icon onClick={onClose}><X size={16} /></Btn></div>
        <div className="mbody">{children}</div>
        {footer && <div className="mfoot">{footer}</div>}
      </div>
    </div>
  )
}

export function Field({ label, help, tip, required, children, className = '' }) {
  return (
    <div className={'field ' + className}>
      {label && <label>{label}{required && <span className="req">*</span>}{tip && <Info>{tip}</Info>}</label>}
      {children}
      {help && <div className="help">{help}</div>}
    </div>
  )
}

export const Switch = ({ value, onChange, title, disabled, busy }) => <button type="button" role="switch" title={title} disabled={disabled || busy} className={'switch ' + (value ? 'on ' : '') + (busy ? 'busy' : '')} onClick={(e) => { e.stopPropagation(); onChange(!value) }} aria-checked={!!value} aria-label={title} />

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs">
      {tabs.map((t) => (
        <button key={t.id} data-tip={t.hint} className={'tab ' + (value === t.id ? 'active' : '')} onClick={() => onChange(t.id)}>
          {t.icon}{t.label}{t.count !== undefined && <span className="badge">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function Seg({ options, value, onChange }) {
  return <div className="seg">{options.map((o) => <button key={o.id ?? o} data-tip={o.hint} className={(o.id ?? o) === value ? 'active' : ''} onClick={() => onChange(o.id ?? o)}>{o.label ?? o}</button>)}</div>
}

export function SearchBox({ value, onChange, placeholder = 'Rechercher…' }) {
  return <div className="search"><Search size={15} /><input className="input" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} /></div>
}

// Copie dans le presse-papiers ; sans lui (page non sécurisée, refus du navigateur), on ne casse rien.
export function copyText(text) {
  try { navigator.clipboard?.writeText(String(text))?.catch?.(() => {}) } catch { /* presse-papiers indisponible */ }
}

// [copié, copier] : « copié » reste vrai 1,2 s après la copie (pour changer l'icône du bouton).
function useCopied() {
  const [ok, setOk] = useState(false)
  return [ok, (text) => { copyText(text); setOk(true); setTimeout(() => setOk(false), 1200) }]
}

export function CopyBtn({ text }) {
  const [ok, copy] = useCopied()
  return <Btn kind="ghost" small icon onClick={(e) => { e.stopPropagation(); copy(text) }}>{ok ? <Check size={14} /> : <Copy size={14} />}</Btn>
}

export const Empty = ({ children }) => <div className="empty">{children}</div>
export const Loading = () => <div className="empty"><Loader2 className="spin" /></div>
export const Badge = ({ kind = '', children, ...p }) => <span className={'badge ' + kind} {...p}>{children}</span>

// Valeur + petit bouton « copier » (adresse IP, nom d'hôte, empreinte, commande…)
export function Copyable({ text, children, className = '', title = 'Copier' }) {
  const [ok, copy] = useCopied()
  const go = (e) => { e.stopPropagation(); e.preventDefault(); copy(text) }
  return <span className={'copyable ' + className}>{children ?? text}<button type="button" className="copy-mini" title={ok ? 'Copié' : title} aria-label={title} onClick={go}>{ok ? <Check size={12} /> : <Copy size={12} />}</button></span>
}

// Tri d'un tableau en cliquant sur les en-têtes : const { rows, th } = useSort(data, { key: 'name' }) ; <th>{th('name', 'Nom')}</th>
export function useSort(data, init = null, getters = {}) {
  const [sort, setSort] = useState(init)   // { key, dir: 1 | -1 }
  const rows = (() => {
    if (!sort || !data) return data || []
    const get = getters[sort.key] || ((r) => r[sort.key])
    return [...data].sort((a, b) => {
      const x = get(a), y = get(b)
      if (x == null && y == null) return 0
      if (x == null) return 1
      if (y == null) return -1
      return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), 'fr', { numeric: true })) * sort.dir
    })
  })()
  const th = (key, label) => (
    <button type="button" className={'th-sort ' + (sort?.key === key ? 'on' : '')} title="Trier" onClick={() => setSort((o) => (o?.key === key ? { key, dir: -o.dir } : { key, dir: 1 }))}>
      {label}{sort?.key === key ? (sort.dir > 0 ? <ChevronUp size={12} /> : <ChevronDown size={12} />) : null}</button>)
  return { rows, th, sort }
}
