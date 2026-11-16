import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Bot, Eye, Pencil, Play, Plus, Search, ShieldOff, Trash2, X } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, Empty, Field, Info, Loading, Modal, Switch, useUi } from './ui.jsx'
import { COUNTRY_CODES, GROUPS, countryName } from '../lib/countries.js'
import { ago } from '../lib/features.js'

// Automod : des règles « si l'adresse remplit ces conditions, alors on la bannit ». Toujours commencer en observation.
const HOURS = [{ v: 1, l: '1 heure' }, { v: 24, l: '24 heures' }, { v: 168, l: '7 jours' }, { v: 720, l: '30 jours' }, { v: '', l: 'définitivement' }]

const TEMPLATES = [
  { name: 'Suspectes venant de certains pays', icon: '🌍', text: 'Bannit les adresses détectées comme suspectes (balayage, erreurs en rafale…) qui viennent des pays choisis.', conditions: { country: { mode: 'in', codes: ['RU', 'CN'] }, suspicious: true }, hours: 24 },
  { name: 'Tout visiteur hors de mes pays', icon: '🛂', text: 'Bannit toute adresse dont le pays n\'est pas dans votre liste. Large : vérifiez l\'aperçu (visiteurs en voyage, services externes).', conditions: { country: { mode: 'not_in', codes: ['FR'] }, min_hits: 1 }, hours: 24 },
  { name: 'Attaques graves', icon: '🛑', text: 'Bannit les adresses qui tentent une injection, une exécution de commandes, une remontée de répertoires ou qui lisent un fichier sensible.', conditions: { attacks: ['sqli', 'rce', 'traversal', 'leak'] }, hours: 168 },
  { name: 'Balayeurs insistants', icon: '🧹', text: 'Bannit les adresses suspectes qui accumulent beaucoup d\'erreurs (404, 403, 401).', conditions: { suspicious: true, min_errors: 40 }, hours: 24 },
  { name: 'Scanners connus', icon: '🔎', text: 'Bannit les outils d\'attaque identifiés par leur signature (sqlmap, nikto, zgrab…).', conditions: { attacks: ['scanner_ua'] }, hours: 168 },
]

const ACTION = { ban: ['err', 'bannie'], would_ban: ['warn', 'serait bannie'], limit: ['err', 'règle suspendue'], skip: ['', 'ignorée'], error: ['err', 'erreur'] }

function CountryPicker({ value, onChange }) {
  const [q, setQ] = useState('')
  const all = useMemo(() => COUNTRY_CODES.map((c) => ({ c, n: countryName(c) })).sort((a, b) => a.n.localeCompare(b.n, 'fr')), [])
  const list = all.filter((x) => !q || x.n.toLowerCase().includes(q.toLowerCase()) || x.c.toLowerCase() === q.toLowerCase())
  const set = new Set(value)
  const toggle = (c) => onChange(set.has(c) ? value.filter((x) => x !== c) : [...value, c])
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row wrap" style={{ gap: 6 }}>{value.length === 0 ? <span className="muted small">Aucun pays choisi</span> : value.map((c) => <span className="chip" key={c}>{countryName(c)}<button type="button" onClick={() => toggle(c)} title="Retirer"><X size={11} /></button></span>)}</div>
      <div className="row wrap" style={{ gap: 6 }}>{GROUPS.map(([n, codes]) => <Btn key={n} small kind="ghost" onClick={() => onChange([...new Set([...value, ...codes])])}>+ {n}</Btn>)}{value.length > 0 && <Btn small kind="ghost" onClick={() => onChange([])}>Vider</Btn>}</div>
      <div style={{ position: 'relative' }}><Search size={14} style={{ position: 'absolute', left: 10, top: 10, opacity: .5 }} /><input className="input" style={{ paddingLeft: 30 }} placeholder="Chercher un pays…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <div className="metric-list" style={{ maxHeight: 160 }}>{list.map((x) => <button type="button" key={x.c} className={'metric-item' + (set.has(x.c) ? ' on' : '')} onClick={() => toggle(x.c)}><span>{x.n}</span><span className="muted small mono">{x.c}</span></button>)}</div>
    </div>
  )
}

function RuleModal({ init, kinds, geoOk, reveal, onClose }) {
  const { toast } = useUi(); const qc = useQueryClient()
  const c0 = init.conditions || {}
  const [name, setName] = useState(init.name || '')
  const [useCountry, setUseCountry] = useState(!!c0.country); const [cMode, setCMode] = useState(c0.country?.mode || 'in'); const [codes, setCodes] = useState(c0.country?.codes || [])
  const [susp, setSusp] = useState(!!c0.suspicious)
  const [useAtk, setUseAtk] = useState(!!c0.attacks); const [atk, setAtk] = useState(c0.attacks === 'any' ? ['any'] : c0.attacks || [])
  const [minHits, setMinHits] = useState(c0.min_hits || ''); const [minErr, setMinErr] = useState(c0.min_errors || '')
  const [mode, setMode] = useState(init.mode || 'observe'); const [hours, setHours] = useState(init.hours === undefined ? 24 : init.hours === null ? '' : init.hours)
  const [enabled] = useState(init.enabled === undefined ? true : init.enabled)
  const [pv, setPv] = useState(null)
  const conditions = useMemo(() => ({
    ...(useCountry ? { country: { mode: cMode, codes } } : {}), ...(susp ? { suspicious: true } : {}), ...(useAtk && atk.length ? { attacks: atk.includes('any') ? 'any' : atk } : {}),
    ...(minHits ? { min_hits: Number(minHits) } : {}), ...(minErr ? { min_errors: Number(minErr) } : {}) }), [useCountry, cMode, codes, susp, useAtk, atk, minHits, minErr])
  const body = { name, conditions, mode, hours: hours === '' ? null : Number(hours), enabled }
  const save = useMutation({ mutationFn: () => (init.id ? api.put(`/obs/automod/${init.id}`, body) : api.post('/obs/automod', body)), onSuccess: () => { qc.invalidateQueries({ queryKey: ['obs-automod'] }); toast(mode === 'block' ? 'Règle enregistrée : elle bannit réellement' : 'Règle enregistrée en observation : rien n\'est banni'); onClose() }, onError: (e) => toast(e.message, 'err') })
  const prev = useMutation({ mutationFn: () => api.post('/obs/automod/preview', { conditions }), onSuccess: setPv, onError: (e) => toast(e.message, 'err') })
  const toggleAtk = (k) => setAtk((a) => (k === 'any' ? (a.includes('any') ? [] : ['any']) : a.filter((x) => x !== 'any').includes(k) ? a.filter((x) => x !== k) : [...a.filter((x) => x !== 'any'), k]))
  const empty = Object.keys(conditions).length === 0
  return (
    <Modal wide title={init.id ? 'Modifier la règle' : 'Nouvelle règle d\'automod'} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={save.isPending} disabled={!name.trim() || empty} onClick={() => save.mutate()}>Enregistrer</Btn></>}>
      <div className="col gap-lg">
        <Field label="Nom de la règle"><input className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="Suspectes venant de Russie" autoFocus /></Field>

        <div className="col" style={{ gap: 10 }}>
          <div className="row"><h3>Si l'adresse…</h3><Info>Toutes les conditions cochées doivent être vraies en même temps. Le réseau local et les adresses validées ne sont jamais visés.</Info></div>

          <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={useCountry} disabled={!geoOk} onChange={(e) => setUseCountry(e.target.checked)} /><b>vient d'un pays…</b>{!geoOk && <span className="muted small">(demande la base de localisation : onglet « Adresses IP », bouton « Télécharger la base »)</span>}</label>
          {useCountry && <div className="col" style={{ paddingLeft: 26, gap: 8 }}>
            <Seg2 value={cMode} onChange={setCMode} options={[['in', 'est dans la liste'], ['not_in', 'n\'est PAS dans la liste']]} />
            <CountryPicker value={codes} onChange={setCodes} />
            <div className="muted small">Une adresse dont le pays est inconnu n'est jamais visée par une condition de pays.</div></div>}

          <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={susp} onChange={(e) => setSusp(e.target.checked)} /><b>est détectée comme suspecte</b><Info>Beaucoup d'erreurs 401 / 403 / 404, ou des chemins typiques de balayage (.env, wp-login, phpmyadmin…).</Info></label>

          <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={useAtk} onChange={(e) => setUseAtk(e.target.checked)} /><b>a tenté une attaque…</b></label>
          {useAtk && <div className="row wrap" style={{ paddingLeft: 26, gap: 6 }}>
            <button type="button" className={'xchip' + (atk.includes('any') ? ' on' : '')} onClick={() => toggleAtk('any')}>n'importe laquelle</button>
            {kinds.map((k) => <button type="button" key={k.kind} className={'xchip' + (atk.includes(k.kind) ? ' on' : '')} title={k.why} onClick={() => toggleAtk(k.kind)}>{k.label}</button>)}</div>}

          <div className="row wrap" style={{ gap: 14 }}>
            <label className="row" style={{ gap: 6 }}><span>a fait au moins</span><input className="input" type="number" min="1" style={{ width: 90 }} value={minHits} onChange={(e) => setMinHits(e.target.value)} placeholder="—" /><span>requêtes</span></label>
            <label className="row" style={{ gap: 6 }}><span>dont au moins</span><input className="input" type="number" min="1" style={{ width: 90 }} value={minErr} onChange={(e) => setMinErr(e.target.value)} placeholder="—" /><span>erreurs (401 / 403 / 404…)</span></label>
          </div>
          <div className="muted small">Les chiffres portent sur les dernières 24 heures.</div>
        </div>

        <div className="col" style={{ gap: 10 }}>
          <h3>Alors…</h3>
          <Seg2 value={mode} onChange={setMode} options={[['observe', 'Observer seulement (recommandé au début)'], ['block', 'Bannir réellement']]} />
          {mode === 'block' && <div className="row" style={{ gap: 8 }}><span>Bannir pendant</span><select className="select" style={{ width: 'auto' }} value={hours} onChange={(e) => setHours(e.target.value)}>{HOURS.map((h) => <option key={h.l} value={h.v}>{h.l}</option>)}</select></div>}
          <div className="muted small">{mode === 'observe' ? 'La règle ne bannit rien : elle note dans le journal ci-dessous ce qu\'elle AURAIT banni. Quand la liste vous convient, passez en « Bannir réellement ».' : 'Les adresses visées reçoivent une erreur 403 sur tous les sites de ce nœud (comme un bannissement manuel). Au plus 25 adresses par passe : au-delà, la règle se suspend d\'elle-même.'}</div>
          {mode === 'block' && Object.keys(conditions).length === 1 && conditions.country?.mode === 'not_in' && <div className="banner err" style={{ margin: 0 }}><AlertTriangle size={16} /> Cette règle bannit <b>tous</b> les visiteurs hors des pays choisis (y compris un service externe, un client en voyage). Vérifiez bien l'aperçu.</div>}
        </div>

        <div className="col" style={{ gap: 8 }}>
          <div className="row"><Btn disabled={empty} busy={prev.isPending} onClick={() => prev.mutate()}><Eye size={14} /> Qui serait visé maintenant ?</Btn><span className="muted small">Aucune action : simple aperçu sur les dernières 24 heures.</span></div>
          {pv && (pv.count === 0 ? <div className="muted">Personne en ce moment.</div> : (
            <div className="card flush"><div style={{ padding: '8px 14px' }}><b>{pv.count} adresse{pv.count > 1 ? 's' : ''}</b>{pv.count > pv.rows.length ? ` (les ${pv.rows.length} premières)` : ''}</div>
              <table className="t"><tbody>{pv.rows.map((r) => <tr key={r.ip}><td className="mono">{r.ip}</td><td>{r.country ? countryName(r.country) : '—'}</td><td className="small">{r.hits} req. · {r.errors} err.</td><td className="small">{r.attacks.join(', ')}{r.suspicious && !r.attacks.length ? 'suspecte' : ''}</td></tr>)}</tbody></table></div>))}
        </div>
      </div>
    </Modal>
  )
}

const Seg2 = ({ value, onChange, options }) => <div className="seg">{options.map(([v, l]) => <button type="button" key={v} className={value === v ? 'active' : ''} onClick={() => onChange(v)}>{l}</button>)}</div>

export default function Automod({ f }) {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const q = useQuery({ queryKey: ['obs-automod'], queryFn: () => api.get('/obs/automod'), refetchInterval: 30000, retry: false })
  const [edit, setEdit] = useState(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['obs-automod'] })
  const upd = useMutation({ mutationFn: ({ r, patch }) => api.put(`/obs/automod/${r.id}`, { name: r.name, conditions: r.conditions, mode: r.mode, hours: r.hours, enabled: r.enabled, ...patch }), onSuccess: refresh, onError: (e) => toast(e.message, 'err') })
  const del = useMutation({ mutationFn: (id) => api.del(`/obs/automod/${id}`), onSuccess: () => { refresh(); toast('Règle supprimée') }, onError: (e) => toast(e.message, 'err') })
  const run = useMutation({ mutationFn: () => api.post('/obs/automod/run', {}), onSuccess: (r) => { refresh(); qc.invalidateQueries({ queryKey: ['obs-bans'] }); toast(r.matched ? `${r.matched} adresse(s) visée(s)` : 'Personne n\'est visé en ce moment') }, onError: (e) => toast(e.message, 'err') })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data; const can = d.canManage
  const kindLabel = Object.fromEntries(d.attackKinds.map((k) => [k.kind, k.label]))
  const ruleText = (r) => {
    const c = r.conditions; const p = []
    if (c.country) p.push(`pays ${c.country.mode === 'in' ? 'dans' : 'hors de'} ${c.country.codes.map(countryName).join(', ')}`)
    if (c.suspicious) p.push('détectée comme suspecte')
    if (c.attacks) p.push(c.attacks === 'any' ? 'attaque détectée' : `attaque : ${c.attacks.map((k) => kindLabel[k] || k).join(', ')}`)
    if (c.min_hits) p.push(`≥ ${c.min_hits} requêtes`)
    if (c.min_errors) p.push(`≥ ${c.min_errors} erreurs`)
    return p.join(' ET ')
  }
  const action = (r) => (r.mode === 'observe' ? 'Observe' : `Bannit ${r.hours == null ? 'définitivement' : HOURS.find((h) => h.v === r.hours)?.l || `${r.hours} h`}`)
  return (
    <div className="col gap-lg">
      <div className="muted">L'automod bannit tout seul les adresses qui remplissent vos conditions : un pays, « suspecte », un type d'attaque, un volume de requêtes ou d'erreurs. Il regarde le journal d'accès toutes les 30 secondes. <b>Commencez toujours en observation</b> : la règle note qui elle aurait banni, sans rien bloquer. Le réseau local et les adresses validées ne sont jamais visés, et une adresse sans pays connu n'est jamais bannie par une condition de pays.</div>
      {!d.available && <div className="banner err"><AlertTriangle size={16} /><span>Le journal d'accès de Traefik n'est pas branché : l'automod n'a rien à lire (voir l'aide, « Fonctionnalités facultatives »).</span></div>}
      {d.available && !d.geo.available && <div className="banner info"><AlertTriangle size={16} /><span>Sans la base de localisation, les conditions de pays sont indisponibles (onglet « Adresses IP », bouton « Télécharger la base »). Les autres conditions marchent.</span></div>}

      {can && <div className="card col"><div className="row"><h3>Partir d'un modèle</h3></div>
        <div className="grid two" style={{ gap: 10 }}>{TEMPLATES.map((t) => (
          <button type="button" key={t.name} className="card tpl" onClick={() => setEdit({ name: t.name, conditions: t.conditions, mode: 'observe', hours: t.hours })} disabled={t.conditions.country && !d.geo.available} title={t.conditions.country && !d.geo.available ? 'Demande la base de localisation' : undefined}>
            <b>{t.icon} {t.name}</b><div className="muted small">{t.text}</div>{t.conditions.country && !d.geo.available && <div className="small" style={{ color: 'var(--warn)', fontWeight: 600 }}>Nécessite la base de localisation (Surveillance → Adresses IP → « Télécharger la base »).</div>}</button>))}</div>
        <div><Btn kind="primary" onClick={() => setEdit({})}><Plus size={14} /> Règle personnalisée</Btn></div></div>}

      <div className="card flush">
        <div className="row" style={{ padding: '12px 14px 4px' }}><Bot size={15} /><h3>Règles</h3><Badge>{d.rules.length}</Badge>{can && d.rules.some((r) => r.enabled) && <Btn small className="right" busy={run.isPending} onClick={() => run.mutate()} title="Applique les règles tout de suite, sans attendre"><Play size={13} /> Lancer une passe</Btn>}</div>
        {d.rules.length === 0 ? <Empty><Bot size={28} /><div>Aucune règle. Choisissez un modèle ci-dessus pour commencer.</div></Empty> : (
          <table className="t"><thead><tr><th>Règle</th><th>Si l'adresse…</th><th>Alors</th><th>Visées</th><th>Active</th><th /></tr></thead><tbody>
            {d.rules.map((r) => (
              <tr key={r.id} style={{ opacity: r.enabled ? 1 : .55 }}>
                <td><b>{r.name}</b>{!r.enabled && r.mode === 'block' && <div className="small" style={{ color: 'var(--err)' }}>suspendue ou désactivée</div>}</td>
                <td className="small">{ruleText(r)}</td>
                <td><Badge kind={r.mode === 'block' ? 'err' : 'info'}>{action(r)}</Badge></td>
                <td className="small">{r.hits ? <>{r.hits} bannie{r.hits > 1 ? 's' : ''}{r.last_hit && <div className="muted">{ago(r.last_hit)}</div>}</> : <span className="muted">—</span>}</td>
                <td>{can ? <Switch value={r.enabled} title={r.enabled ? 'Désactiver' : 'Activer'} busy={upd.isPending} onChange={(v) => upd.mutate({ r, patch: { enabled: v } })} /> : <Badge kind={r.enabled ? 'ok' : ''}>{r.enabled ? 'oui' : 'non'}</Badge>}</td>
                <td className="actions">{can && <><Btn small kind="ghost" icon title="Modifier" onClick={() => setEdit(r)}><Pencil size={14} /></Btn><Btn small kind="ghost" icon title="Supprimer" onClick={async () => { if (await confirm({ title: `Supprimer « ${r.name} » ?`, message: 'Les bannissements déjà posés restent (onglet Bannies).', confirmLabel: 'Supprimer' })) del.mutate(r.id) }}><Trash2 size={14} /></Btn></>}</td>
              </tr>))}</tbody></table>)}
      </div>

      <div className="card flush">
        <div className="row" style={{ padding: '12px 14px 4px' }}><ShieldOff size={15} /><h3>Journal</h3><Info>30 jours : ce que chaque règle a banni ou aurait banni (observation).</Info></div>
        {d.log.length === 0 ? <Empty>Rien pour l'instant.</Empty> : (
          <table className="t"><tbody>{d.log.map((l) => { const [k, t] = ACTION[l.action] || ['', l.action]; return (
            <tr key={l.id}><td className="muted small" style={{ width: 120 }}>{ago(l.at)}</td><td><Badge kind={k}>{t}</Badge></td><td className="mono">{l.ip || '—'}</td><td className="small">{l.rule_name}</td><td className="muted small">{l.detail}</td></tr>) })}</tbody></table>)}
      </div>

      {edit && <RuleModal init={edit} kinds={d.attackKinds} geoOk={d.geo.available} reveal={f.ips.reveal} onClose={() => setEdit(null)} />}
    </div>
  )
}
