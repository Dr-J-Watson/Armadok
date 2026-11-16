import { useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, KeyRound, Plus, Trash2, X, ChevronRight, RotateCcw } from 'lucide-react'
import YAML from 'yaml'
import { Btn, Field, Modal, Switch } from './ui.jsx'
import { api } from '../api.js'
import { useUi } from './ui.jsx'
import HostDns from './HostDns.jsx'
import { extractHosts } from '../lib/util.js'

const isEmpty = (v) => v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length) || (typeof v === 'object' && !Array.isArray(v) && !Object.keys(v).length)
const setKey = (obj, k, v) => { const c = { ...(obj || {}) }; if (v === undefined) delete c[k]; else c[k] = v; return c }

// ---------- saisies de base ----------
export function Chips({ value = [], onChange, placeholder, listId, ordered, bad = () => false }) {
  const [txt, setTxt] = useState('')
  const add = (raw) => {
    const parts = raw.split(/[,\n]/).map((x) => x.trim()).filter(Boolean)
    if (parts.length) onChange([...value, ...parts.filter((p) => !value.includes(p))])
    setTxt('')
  }
  const move = (i, d) => { const a = [...value]; [a[i], a[i + d]] = [a[i + d], a[i]]; onChange(a) }
  return (
    <div className="chips">
      {value.map((v, i) => (
        <span key={v + i} className={'chip ' + (bad(v) ? 'bad' : '')}>
          {ordered && i > 0 && <button type="button" onClick={() => move(i, -1)} title="Monter"><ArrowLeft size={11} /></button>}
          {ordered && <span className="muted small">{i + 1}.</span>}
          {v}
          {ordered && i < value.length - 1 && <button type="button" onClick={() => move(i, 1)} title="Descendre"><ArrowRight size={11} /></button>}
          <button type="button" onClick={() => onChange(value.filter((_, j) => j !== i))}><X size={12} /></button>
        </span>
      ))}
      <input value={txt} list={listId} placeholder={value.length ? '' : placeholder}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(txt) } else if (e.key === 'Backspace' && !txt && value.length) onChange(value.slice(0, -1)) }}
        onBlur={() => txt && add(txt)} onChange={(e) => { const v = e.target.value; if (listId && value !== undefined && e.nativeEvent.inputType === 'insertReplacementText') add(v); else setTxt(v) }} />
    </div>
  )
}

function KV({ value = {}, onChange }) {
  const rows = Object.entries(value)
  const set = (i, k, v) => { const r = rows.map(([a, b]) => [a, b]); r[i] = [k, v]; onChange(Object.fromEntries(r)) }
  return (
    <div>
      {rows.map(([k, v], i) => (
        <div className="kvrow" key={i}>
          <input className="input" value={k} placeholder="clé" onChange={(e) => set(i, e.target.value, v)} />
          <input className="input" value={v} placeholder="valeur" onChange={(e) => set(i, k, e.target.value)} />
          <Btn kind="ghost" icon onClick={() => onChange(Object.fromEntries(rows.filter((_, j) => j !== i)))}><Trash2 size={14} /></Btn>
        </div>
      ))}
      <Btn small onClick={() => onChange({ ...value, [`clé${rows.length + 1}`]: '' })}><Plus size={13} /> Ajouter</Btn>
    </div>
  )
}

function HtpasswdModal({ onClose, onDone }) {
  const [u, setU] = useState(''); const [p, setP] = useState(''); const [busy, setBusy] = useState(false); const { toast } = useUi()
  const go = async () => {
    setBusy(true)
    try { const r = await api.post('/traefik/htpasswd', { username: u, password: p }); onDone(r.entry); onClose() } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  return (
    <Modal small title="Générer un utilisateur bcrypt" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={!u || !p} onClick={go}>Générer & ajouter</Btn></>}>
      <div className="col"><Field label="Utilisateur"><input className="input" value={u} onChange={(e) => setU(e.target.value)} autoFocus /></Field>
        <Field label="Mot de passe" help="Le mot de passe n'est jamais stocké, seul le hash bcrypt est écrit dans le fichier."><input className="input" type="password" value={p} onChange={(e) => setP(e.target.value)} /></Field></div>
    </Modal>
  )
}

// ---------- éditeur de règle ----------
const RULE_SNIPPETS = [['Host', 'Host(``)'], ['PathPrefix', 'PathPrefix(``)'], ['Path', 'Path(``)'], ['Method', 'Method(``)'], ['Header', 'Header(``, ``)'], ['Query', 'Query(``, ``)'], ['ClientIP', 'ClientIP(``)'], ['HostRegexp', 'HostRegexp(``)'], ['&&', ' && '], ['||', ' || '], ['!', '!'], ['( )', '()']]
export function RuleEditor({ value = '', onChange, tcp }) {
  const ref = useRef()
  const snippets = tcp ? [['HostSNI', 'HostSNI(``)'], ['HostSNI(*)', 'HostSNI(`*`)'], ['ClientIP', 'ClientIP(``)'], ['ALPN', 'ALPN(``)'], ['&&', ' && '], ['||', ' || '], ['!', '!']] : RULE_SNIPPETS
  const insert = (snip) => {
    const el = ref.current; const a = el.selectionStart ?? value.length, z = el.selectionEnd ?? value.length
    const needsJoin = /^[A-Z]/.test(snip) && value.trim() && !/(&&|\|\||\(|!)\s*$/.test(value.slice(0, a))
    const text = (needsJoin ? ' && ' : '') + snip
    onChange(value.slice(0, a) + text + value.slice(z))
    const pos = a + text.indexOf('``') + 1
    requestAnimationFrame(() => { el.focus(); const p = text.includes('``') ? pos : a + text.length; el.setSelectionRange(p, p) })
  }
  const hosts = extractHosts(value)
  return (
    <div className="col" style={{ gap: 8 }}>
      <textarea ref={ref} className="textarea" style={{ minHeight: 56 }} value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} placeholder="Host(`app.exemple.fr`) && PathPrefix(`/api`)" />
      <div className="row wrap" style={{ gap: 5 }}>{snippets.map(([l, s]) => <Btn key={l} small className="mono" onClick={() => insert(s)}>{l}</Btn>)}</div>
      {!tcp && hosts.length > 0 && <HostDns hosts={hosts} />}
    </div>
  )
}

// ---------- rendu récursif ----------
function RefInput({ field, value, onChange, refs }) {
  const id = 'dl-' + field.ref
  return (
    <>
      <input className="input" list={id} value={value || ''} onChange={(e) => onChange(e.target.value || undefined)} placeholder={field.ph || 'nom ou nom@provider'} />
      <datalist id={id}>{(refs[field.ref] || []).map((n) => <option key={n} value={n} />)}</datalist>
    </>
  )
}

function Control({ field, value, onChange, ctx }) {
  const { refs } = ctx
  const [ht, setHt] = useState(false)
  switch (field.t) {
    case 's': case 'd':
      return <input className="input" value={value ?? ''} placeholder={field.ph} onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value)} />
    case 'n':
      return <input className="input" type="number" value={value ?? ''} placeholder={field.ph} onChange={(e) => onChange(e.target.value === '' ? undefined : Number(e.target.value))} />
    case 'e':
      return <select className="select" value={value ?? ''} onChange={(e) => onChange(e.target.value || undefined)}><option value="">— par défaut —</option>{field.opts.map((o) => <option key={o}>{o}</option>)}</select>
    case 'b': {
      const cur = value ?? field.def ?? false
      return <div className="row"><Switch value={cur} onChange={(v) => onChange(v)} />{value !== undefined && <Btn kind="ghost" small icon title="Revenir à la valeur par défaut" onClick={() => onChange(undefined)}><RotateCcw size={12} /></Btn>}<span className="muted small">{value === undefined ? 'défaut' : ''}</span></div>
    }
    case 'sl':
      return <div className="col" style={{ gap: 6 }}><Chips value={value || []} onChange={(v) => onChange(v.length ? v : undefined)} placeholder={field.ph || 'Entrée pour ajouter'} />
        {field.htpasswd && <div><Btn small onClick={() => setHt(true)}><KeyRound size={13} /> Générer un utilisateur</Btn></div>}
        {ht && <HtpasswdModal onClose={() => setHt(false)} onDone={(e) => onChange([...(value || []), e])} />}</div>
    case 'kv':
      return <KV value={value} onChange={(v) => onChange(Object.keys(v).length ? v : undefined)} />
    case 'ref':
      return <RefInput field={field} value={value} onChange={onChange} refs={refs} />
    case 'refl': {
      const id = 'dl-' + field.ref
      const known = refs[field.ref] || []
      return <><Chips value={value || []} ordered={field.ref === 'middlewares'} listId={id} placeholder="Choisir ou saisir…" bad={(v) => !v.includes('@') && field.ref !== 'entrypoints' && !known.includes(v)} onChange={(v) => onChange(v.length ? v : undefined)} />
        <datalist id={id}>{known.map((n) => <option key={n} value={n} />)}</datalist></>
    }
    case 'rule':
      return <RuleEditor value={value} onChange={(v) => onChange(v || undefined)} tcp={field.tcp} />
    case 'raw':
      return <RawYaml value={value} onChange={onChange} />
    default:
      return null
  }
}

function RawYaml({ value, onChange }) {
  const [txt, setTxt] = useState(() => (isEmpty(value) ? '' : YAML.stringify(value)))
  const [err, setErr] = useState(null)
  return <><textarea className="textarea" style={{ minHeight: 120 }} value={txt} onChange={(e) => { setTxt(e.target.value); try { const v = YAML.parse(e.target.value); setErr(null); onChange(isEmpty(v) ? undefined : v) } catch (x) { setErr(x.message.split('\n')[0]) } }} />
    {err && <div className="small" style={{ color: 'var(--err)' }}>{err}</div>}</>
}

function ObjectList({ field, value = [], onChange, ctx }) {
  const add = () => onChange([...value, {}])
  return (
    <div>
      {value.map((row, i) => (
        <div className="listrow" key={i}>
          <div className="grow"><Fields fields={field.f} value={row} onChange={(r) => onChange(value.map((x, j) => (j === i ? r : x)))} ctx={ctx} /></div>
          <Btn kind="ghost" icon onClick={() => onChange(value.filter((_, j) => j !== i))}><Trash2 size={14} /></Btn>
        </div>
      ))}
      <Btn small onClick={add}><Plus size={13} /> Ajouter</Btn>
    </div>
  )
}

export function Fields({ fields, value, onChange, ctx }) {
  const val = value || {}
  return (
    <>
      {fields.map((fd) => {
        if (fd.t === 'raw' && fd.k === '') {
          return <Field key="raw" label={fd.label} help={fd.help}><RawYaml value={val} onChange={(v) => onChange(v || {})} /></Field>
        }
        if (fd.t === 'o' && fd.flat) {
          const open = fd.f.some((x) => !isEmpty(val[x.k]))
          return (
            <details className="group" key={fd.k} open={open}>
              <summary><ChevronRight size={14} />{fd.label}</summary>
              <div className="inner"><div className="fgrid"><Fields fields={fd.f} value={val} onChange={onChange} ctx={ctx} /></div></div>
            </details>
          )
        }
        if (fd.t === 'o') {
          return (
            <details className="group" key={fd.k} open={!isEmpty(val[fd.k])}>
              <summary><ChevronRight size={14} />{fd.label}{fd.help && <span className="muted small"> — {fd.help}</span>}</summary>
              <div className="inner"><div className="fgrid"><Fields fields={fd.f} value={val[fd.k]} onChange={(v) => onChange(setKey(val, fd.k, isEmpty(v) ? undefined : v))} ctx={ctx} /></div></div>
            </details>
          )
        }
        const wide = ['ol', 'kv', 'sl', 'refl', 'rule', 'raw'].includes(fd.t)
        return (
          <Field key={fd.k} label={fd.label} required={fd.required} help={fd.help} className={wide ? 'wide' : ''}>
            {fd.t === 'ol'
              ? <ObjectList field={fd} value={val[fd.k]} onChange={(v) => onChange(setKey(val, fd.k, v.length ? v : undefined))} ctx={ctx} />
              : <Control field={fd} value={val[fd.k]} onChange={(v) => onChange(setKey(val, fd.k, v))} ctx={ctx} />}
          </Field>
        )
      })}
    </>
  )
}

const hasValue = (f, val) => (f.t === 'o' && f.flat ? f.f.some((x) => !isEmpty((val || {})[x.k])) : !isEmpty((val || {})[f.k]))

// `show` : clés des champs à mettre en avant ; les autres passent sous « Plus d'options » (ouvert d'office si l'un d'eux est déjà réglé).
export default function SchemaForm({ fields, value, onChange, ctx, show }) {
  const main = show ? fields.filter((f) => show.includes(f.k)) : fields
  const rest = show ? fields.filter((f) => !show.includes(f.k)) : []
  return (
    <div className="col gap-lg"><style>{`.field.wide{grid-column:1/-1}`}</style>
      {main.length > 0 && <div className="fgrid"><Fields fields={main} value={value} onChange={onChange} ctx={ctx} /></div>}
      {show && main.length === 0 && <div className="muted">Rien à régler ici : les valeurs par défaut conviennent.</div>}
      {rest.length > 0 && (
        <details className="group" open={rest.some((f) => hasValue(f, value))}>
          <summary><ChevronRight size={14} />Plus d'options</summary>
          <div className="inner"><div className="fgrid"><Fields fields={rest} value={value} onChange={onChange} ctx={ctx} /></div></div>
        </details>)}
    </div>
  )
}
