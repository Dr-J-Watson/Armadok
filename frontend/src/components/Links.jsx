import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link2, Pencil, Plus, RefreshCw, ShieldCheck, Trash2, Unplug, Users } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Btn, CopyBtn, Empty, Field, Info, Loading, Modal, useUi } from './ui.jsx'
import { Countdown } from './Countdown.jsx'
import { fmtDate } from '../lib/util.js'

const STATE = {
  invited: ['info', 'invitation en attente'], expired: ['', 'invitation expirée'], to_confirm: ['warn', 'à confirmer ici'], wait_peer: ['warn', 'attend l\'autre hub'],
  online: ['ok', 'en ligne'], offline: ['', 'hors ligne'], incompatible: ['err', 'versions incompatibles'], revoked: ['err', 'révoquée'],
}
const okCode = (c) => c.replace(/\s/g, '').length >= 6

// Le champ de code 2FA, commun à toutes les actions qui engagent la sécurité d'une liaison.
function CodeField({ code, setCode, go }) {
  return <Field label="Code de double authentification" help="Le code à 6 chiffres de votre application d'authentification."><input className="input mono" autoFocus style={{ maxWidth: 180, letterSpacing: 3, textAlign: 'center' }} inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && okCode(code) && go) go() }} /></Field>
}

// Liaisons avec d'autres hubs : chaque hub garde ses utilisateurs, ses droits et ses nœuds ; ici on établit seulement « qui est l'autre » et si le lien est vivant.
export default function Links() {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const q = useQuery({ queryKey: ['links'], queryFn: () => api.get('/admin/links'), refetchInterval: 5000 })
  const [modal, setModal] = useState(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['links'] })
  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data
  const del = async (l) => { if (await confirm({ title: 'Retirer cette liaison de la liste ?', message: 'Elle est déjà coupée : seul son historique disparaît.', confirmLabel: 'Retirer' })) { try { await api.del(`/admin/links/${l.id}`); refresh() } catch (e) { toast(e.message, 'err') } } }
  return (
    <div className="col" style={{ gap: 12, marginTop: 22 }}>
      <div className="row wrap" style={{ gap: 8, alignItems: 'center' }}>
        <h2 style={{ margin: 0, fontSize: 17 }}><Link2 size={16} style={{ verticalAlign: -2 }} /> Liaisons avec d'autres hubs</h2>
        <Info>Chaque hub garde ses propres utilisateurs, droits et nœuds. Une liaison établit seulement qui est l'autre hub (clés échangées avec un code à usage unique, puis empreinte comparée par les deux administrateurs) et si la connexion est vivante. Un seul des deux hubs doit être joignable (chemin /api/link, comme /api/worker).</Info>
        <span className="right row wrap" style={{ gap: 8 }}>
          <Btn small onClick={() => setModal({ kind: 'invite' })}><Plus size={13} /> Inviter un hub</Btn>
          <Btn small onClick={() => setModal({ kind: 'join' })}><Link2 size={13} /> Rejoindre un hub</Btn>
        </span>
      </div>
      <div className="muted small">Ce hub s'appelle <b>{d.identity.name}</b> <button type="button" className="linkcell" style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer' }} title="Renommer ce hub (l'autre administrateur le verra)" onClick={() => setModal({ kind: 'name' })}><Pencil size={11} /></button> · version {d.identity.version} · protocole {d.identity.minProtocol}–{d.identity.protocol}</div>
      {d.links.length === 0 ? <div className="card"><Empty><Link2 size={30} /><div>Aucune liaison. Invitez un autre hub, ou rejoignez-en un avec son invitation.</div></Empty></div> : (
        <div className="card flush"><table className="t"><thead><tr><th>Hub</th><th>État</th><th>Version<Info>Version de l'autre hub et protocole commun négocié. Si les versions ne se recouvrent plus, la liaison le dit : mettez à jour le plus ancien des deux.</Info></th><th>Délai de grâce<Info>Si la connexion est coupée, les effets de la liaison (comptes invités…) restent valables ce temps-là, puis cessent. 0 = tout de suite.</Info></th><th /></tr></thead>
          <tbody>{d.links.map((l) => {
            const [kind, label] = STATE[l.state] || ['', l.state]
            return (
              <tr key={l.id} style={{ opacity: l.status === 'revoked' ? .65 : 1 }}>
                <td><b>{l.label || l.peerName || 'Hub en attente'}</b>{l.label && l.peerName && <span className="muted"> · {l.peerName}</span>}<div className="muted small">{l.status === 'invited' ? 'invitation émise ici' : l.weCall ? `appelé : ${l.peerUrl}` : 'ce hub reçoit l\'appel'}</div></td>
                <td><Badge kind={kind}>{label}</Badge>{l.state === 'invited' && l.codeExpires && <div className="muted small">expire dans <Countdown until={l.codeExpires} onEnd={refresh} /></div>}
                  {l.status === 'revoked' && <div className="muted small">{l.revokedBy ? `par ${l.revokedBy}` : ''} {fmtDate((l.revokedAt || 0) * 1000)}</div>}
                  {l.lastError && l.status === 'paired' && l.state !== 'online' && <div className="muted small" style={{ maxWidth: 340 }}>{l.lastError}</div>}
                  {l.status === 'paired' && !l.lastSeen ? null : l.status === 'paired' && l.state !== 'online' && <div className="muted small">vu {fmtDate(l.lastSeen * 1000)}</div>}</td>
                <td className="small">{l.status === 'invited' ? '—' : <>{l.peerVersion || '?'}{l.protocol ? <div className="muted">protocole {l.protocol}{l.features.length ? ` · ${l.features.join(', ')}` : ''}</div> : null}</>}</td>
                <td>{l.status === 'paired' ? <button type="button" className="linkcell" style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer' }} title="Modifier le délai de grâce" onClick={() => setModal({ kind: 'grace', l })}>{l.graceHours} h <Pencil size={11} /></button> : '—'}</td>
                <td className="actions">
                  {l.state === 'to_confirm' && <Btn small kind="primary" onClick={() => setModal({ kind: 'confirm', l })}><ShieldCheck size={13} /> Comparer et confirmer</Btn>}
                  {l.status === 'invited' && <Btn small onClick={() => setModal({ kind: 'renew', l })}><RefreshCw size={13} /> Nouveau code</Btn>}
                  {['online', 'offline', 'incompatible'].includes(l.state) && <Btn small onClick={() => setModal({ kind: 'teams', l })}><Users size={13} /> Équipes</Btn>}
                  {l.status === 'paired' && <Btn small kind="danger" onClick={() => setModal({ kind: 'revoke', l })}><Unplug size={13} /> Révoquer</Btn>}
                  {l.status !== 'paired' && <Btn small kind="ghost" icon title="Retirer de la liste" aria-label="Retirer" onClick={() => del(l)}><Trash2 size={13} /></Btn>}
                </td>
              </tr>)
          })}</tbody></table></div>)}
      {modal?.kind === 'invite' && <InviteModal onClose={() => { setModal(null); refresh() }} />}
      {modal?.kind === 'renew' && <InviteModal link={modal.l} onClose={() => { setModal(null); refresh() }} />}
      {modal?.kind === 'join' && <JoinModal onClose={() => { setModal(null); refresh() }} />}
      {modal?.kind === 'confirm' && <ConfirmModal l={modal.l} onClose={() => { setModal(null); refresh() }} />}
      {modal?.kind === 'revoke' && <RevokeModal l={modal.l} onClose={() => { setModal(null); refresh() }} />}
      {modal?.kind === 'grace' && <GraceModal l={modal.l} max={d.maxGraceHours} onClose={() => { setModal(null); refresh() }} />}
      {modal?.kind === 'teams' && <TeamsModal l={modal.l} onClose={() => { setModal(null); refresh() }} />}
      {modal?.kind === 'name' && <NameModal name={d.identity.name} onClose={() => { setModal(null); refresh() }} />}
    </div>
  )
}

function InviteModal({ link, onClose }) {
  const [label, setLabel] = useState(link?.label || ''); const [code, setCode] = useState(''); const [busy, setBusy] = useState(false); const [err, setErr] = useState(''); const [out, setOut] = useState(null)
  const [url, setUrl] = useState(window.location.origin)
  const go = async () => {
    setBusy(true); setErr('')
    try { const r = link ? await api.post(`/admin/links/${link.id}/invite/renew`, { code }) : await api.post('/admin/links/invite', { code, label, url }); setOut(r) } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  const invitation = out ? `${url.replace(/\/+$/, '')}#${out.code}` : ''
  return (
    <Modal small title={link ? 'Nouveau code d\'invitation' : 'Inviter un hub'} onClose={onClose} footer={out ? <Btn kind="primary" onClick={onClose}>Fermer</Btn> : <><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={!okCode(code)} onClick={go}>Créer l'invitation</Btn></>}>
      {out ? (
        <div className="col"><div className="banner info">Donnez cette invitation à l'administrateur de l'autre hub, <b>par un canal sûr</b>. Elle ne sert qu'<b>une fois</b> et expire dans 15 minutes. Ce hub doit être joignable par l'autre à cette adresse (chemin <span className="mono">/api/link</span>).</div>
          <Field label="Adresse de ce hub, telle que l'autre le joint"><input className="input mono" value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
          <div className="row" style={{ gap: 8 }}><input className="input mono grow" readOnly value={invitation} onFocus={(e) => e.target.select()} /><CopyBtn text={invitation} /></div>
          <div className="muted small">Ensuite, comparez l'<b>empreinte</b> qui s'affichera ici et chez lui, et confirmez des deux côtés.</div></div>
      ) : (
        <div className="col">
          {!link && <Field label="Nom pour vous reconnaître (facultatif)"><input className="input" maxLength={80} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Hub de l'agence" /></Field>}
          <CodeField code={code} setCode={setCode} go={go} />{err && <div className="banner err">{err}</div>}</div>)}
    </Modal>
  )
}

function JoinModal({ onClose }) {
  const [inv, setInv] = useState(''); const [label, setLabel] = useState(''); const [code, setCode] = useState(''); const [busy, setBusy] = useState(false); const [err, setErr] = useState(''); const [url, setUrl] = useState('')
  const hasUrl = inv.includes('#')
  const go = async () => {
    setBusy(true); setErr('')
    try { await api.post('/admin/links/join', { code, invitation: inv.trim(), url: url.trim(), label }); onClose() } catch (e) { setErr(e.message); setBusy(false) }
  }
  return (
    <Modal small title="Rejoindre un hub" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={!okCode(code) || inv.trim().length < 10 || (!hasUrl && !url.trim())} onClick={go}>Rejoindre</Btn></>}>
      <div className="col">
        <Field label="Invitation reçue" help="Collez l'invitation complète (adresse#code), ou seulement le code et renseignez l'adresse en dessous."><input className="input mono" autoFocus value={inv} onChange={(e) => setInv(e.target.value)} placeholder="https://armadok.exemple.fr#lnk_…" /></Field>
        {!hasUrl && <Field label="Adresse du hub (HTTPS)"><input className="input mono" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://armadok.exemple.fr" /></Field>}
        <Field label="Nom pour vous reconnaître (facultatif)"><input className="input" maxLength={80} value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
        <CodeField code={code} setCode={setCode} go={go} />{err && <div className="banner err">{err}</div>}</div>
    </Modal>
  )
}

function ConfirmModal({ l, onClose }) {
  const { toast } = useUi(); const [code, setCode] = useState(''); const [busy, setBusy] = useState(false); const [err, setErr] = useState('')
  const go = async () => { setBusy(true); setErr(''); try { await api.post(`/admin/links/${l.id}/confirm`, { code }); toast('Confirmé de ce côté'); onClose() } catch (e) { setErr(e.message); setBusy(false) } }
  return (
    <Modal small title="Comparer l'empreinte" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={!okCode(code)} onClick={go}><ShieldCheck size={14} /> Elle est identique : confirmer</Btn></>}>
      <div className="col">
        <div className="banner info">Lisez cette empreinte à l'administrateur de <b>{l.peerName || 'l\'autre hub'}</b> (de vive voix, ou par un canal que vous connaissez déjà), ou comparez-la avec ce qu'il voit. <b>Elle doit être strictement identique des deux côtés.</b> Sinon, quelqu'un s'est glissé entre vous : révoquez.</div>
        <div className="mono" style={{ fontSize: 22, letterSpacing: 2, textAlign: 'center', padding: 10 }}>{l.fingerprint}</div>
        <div className="muted small">Rien n'est actif tant que les <b>deux</b> administrateurs n'ont pas confirmé.</div>
        <CodeField code={code} setCode={setCode} go={go} />{err && <div className="banner err">{err}</div>}</div>
    </Modal>
  )
}

function RevokeModal({ l, onClose }) {
  const { toast } = useUi(); const [code, setCode] = useState(''); const [busy, setBusy] = useState(false); const [err, setErr] = useState('')
  const go = async () => { setBusy(true); setErr(''); try { await api.post(`/admin/links/${l.id}/revoke`, { code }); toast('Liaison révoquée'); onClose() } catch (e) { setErr(e.message); setBusy(false) } }
  return (
    <Modal small title={`Révoquer la liaison avec ${l.peerName || 'ce hub'}`} onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="danger" busy={busy} disabled={!okCode(code)} onClick={go}><Unplug size={14} /> Révoquer</Btn></>}>
      <div className="col"><div className="banner warn">La connexion est coupée tout de suite et l'autre hub est prévenu. Pour relier à nouveau ces deux hubs, il faudra refaire une invitation et comparer les empreintes.</div>
        <CodeField code={code} setCode={setCode} go={go} />{err && <div className="banner err">{err}</div>}</div>
    </Modal>
  )
}

function GraceModal({ l, max, onClose }) {
  const { toast } = useUi(); const [h, setH] = useState(String(l.graceHours)); const [code, setCode] = useState(''); const [busy, setBusy] = useState(false); const [err, setErr] = useState('')
  const n = Number(h); const ok = Number.isInteger(n) && n >= 0 && n <= max
  const go = async () => { setBusy(true); setErr(''); try { await api.patch(`/admin/links/${l.id}`, { graceHours: n, code }); toast('Délai enregistré'); onClose() } catch (e) { setErr(e.message); setBusy(false) } }
  return (
    <Modal small title="Délai de grâce" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={!ok || !okCode(code)} onClick={go}>Enregistrer</Btn></>}>
      <div className="col"><div className="muted small">Si la connexion avec <b>{l.peerName || 'cet hub'}</b> est coupée, ce qu'elle apporte (comptes invités…) reste valable ce nombre d'heures, puis cesse. <b>0</b> : dès la coupure. Plus c'est court, plus c'est sûr.</div>
        <Field label={`Heures (0 à ${max})`}><input className="input mono" style={{ maxWidth: 120 }} inputMode="numeric" value={h} onChange={(e) => setH(e.target.value)} /></Field>
        <CodeField code={code} setCode={setCode} go={ok ? go : null} />{err && <div className="banner err">{err}</div>}</div>
    </Modal>
  )
}

function NameModal({ name, onClose }) {
  const { toast } = useUi(); const [v, setV] = useState(name); const [busy, setBusy] = useState(false); const [err, setErr] = useState('')
  const go = async () => { setBusy(true); setErr(''); try { await api.put('/admin/links/identity', { name: v }); toast('Nom enregistré'); onClose() } catch (e) { setErr(e.message); setBusy(false) } }
  return (
    <Modal small title="Nom de ce hub" onClose={onClose} footer={<><Btn onClick={onClose}>Annuler</Btn><Btn kind="primary" busy={busy} disabled={!v.trim()} onClick={go}>Enregistrer</Btn></>}>
      <div className="col"><Field label="Comment les autres hubs vous voient"><input className="input" autoFocus maxLength={80} value={v} onChange={(e) => setV(e.target.value)} /></Field>{err && <div className="banner err">{err}</div>}</div>
    </Modal>
  )
}

// Équipes d'une liaison : celles de CE hub que l'on partage, l'accueil de celles de l'autre hub, et les invités qui en résultent.
function TeamsModal({ l: l0, onClose }) {
  const { toast } = useUi(); const qc = useQueryClient()
  const all = useQuery({ queryKey: ['links'], queryFn: () => api.get('/admin/links'), refetchInterval: 5000 })
  const l = (all.data?.links || []).find((x) => x.id === l0.id) || l0   // toujours l'état à jour de la liaison
  const q = useQuery({ queryKey: ['link-teams', l.id], queryFn: () => api.get(`/admin/links/${l.id}/teams`) })
  const gq = useQuery({ queryKey: ['link-guests-of', l.id], queryFn: () => api.get(`/admin/links/${l.id}/guests`), refetchInterval: 10000 })
  const [sel, setSel] = useState(null); const [code, setCode] = useState(''); const [busy, setBusy] = useState(''); const [err, setErr] = useState('')
  const refresh = () => { q.refetch(); gq.refetch(); qc.invalidateQueries({ queryKey: ['links'] }); qc.invalidateQueries({ queryKey: ['link-guests'] }) }
  if (q.isLoading) return <Modal title="Équipes" onClose={onClose}><Loading /></Modal>
  const d = q.data; const shared = sel || new Set(d.mine.filter((t) => t.shared).map((t) => t.id))
  const toggle = (id) => { const n = new Set(shared); n.has(id) ? n.delete(id) : n.add(id); setSel(n) }
  const run = async (what, fn, ok) => { setBusy(what); setErr(''); try { await fn(); toast(ok); setCode(''); setSel(null); refresh() } catch (e) { setErr(e.message) } finally { setBusy('') } }
  const peer = l.peerName || 'l\'autre hub'
  return (
    <Modal wide title={`Équipes · ${peer}`} onClose={onClose} footer={<Btn onClick={onClose}>Fermer</Btn>}>
      <div className="col" style={{ gap: 18 }}>
        <div>
          <h3 style={{ margin: '0 0 6px' }}>Équipes de ce hub que vous partagez avec {peer}</h3>
          <div className="muted small" style={{ marginBottom: 8 }}>Ces équipes (et leurs membres) sont annoncées à {peer}. Ses administrateurs leur donnent des droits <b>chez lui</b> : vous ne décidez pas de ce qu'elles peuvent y faire, seulement qui a la possibilité d'y aller. {d.peerHosting ? <Badge kind="ok">{peer} accueille vos équipes</Badge> : <Badge kind="warn">{peer} n'accueille pas encore vos équipes</Badge>}</div>
          {d.mine.length === 0 ? <div className="muted small">Aucune équipe ici. Créez-en dans « Droits → Groupes ».</div> : d.mine.map((t) => (
            <label key={t.id} className="row" style={{ gap: 8, padding: '3px 0' }}><input type="checkbox" checked={shared.has(t.id)} onChange={() => toggle(t.id)} /> <b>{t.name}</b> <span className="muted small">{t.members} membre{t.members > 1 ? 's' : ''}</span></label>))}
          <div style={{ marginTop: 8 }}><Btn kind="primary" small busy={busy === 'teams'} disabled={!okCode(code) || !sel} onClick={() => run('teams', () => api.put(`/admin/links/${l.id}/teams`, { groupIds: [...shared], code }), 'Équipes partagées enregistrées')}>Enregistrer le partage</Btn></div>
        </div>
        <div>
          <h3 style={{ margin: '0 0 6px' }}>Accueillir les équipes de {peer} sur ce hub</h3>
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            <Btn small kind={d.hostEnabled ? 'danger' : 'primary'} busy={busy === 'host'} disabled={!okCode(code)} onClick={() => run('host', () => api.patch(`/admin/links/${l.id}`, { hostEnabled: !d.hostEnabled, code }), d.hostEnabled ? 'Accueil retiré : les invités ont été supprimés' : 'Accueil autorisé')}>{d.hostEnabled ? 'Retirer l\'accueil (supprime les invités)' : 'Autoriser l\'accueil'}</Btn>
            <span className="muted small">{d.hostEnabled ? 'Les équipes reçues deviennent des groupes, leurs membres des invités sans mot de passe, plafonnés, qui disparaissent avec la liaison.' : 'Tant que ce n\'est pas autorisé, rien n\'est créé ici.'}</span>
          </div>
          {d.hostEnabled && <div className="row" style={{ gap: 8, alignItems: 'center', marginTop: 8 }}>
            <Btn small kind={l.directEnabled ? 'danger' : undefined} busy={busy === 'direct'} disabled={!okCode(code)} onClick={() => run('direct', () => api.patch(`/admin/links/${l.id}`, { directEnabled: !l.directEnabled, code }), l.directEnabled ? 'Connexion directe retirée : mots de passe des invités effacés' : 'Connexion directe autorisée')}>{l.directEnabled ? 'Retirer la connexion directe' : 'Autoriser la connexion directe'}</Btn>
            <span className="muted small">{l.directEnabled ? 'Les invités peuvent définir un mot de passe (et une 2FA obligatoire) propre à ce hub, avec un lien à usage unique.' : 'Les invités passent uniquement par leur hub d\'origine.'}</span></div>}
          {d.received.length > 0 && <div style={{ marginTop: 8 }}>{d.received.map((t) => <div key={t.id} className="small"><b>{t.name}</b> · {t.members.length} invité{t.members.length > 1 ? 's' : ''} · {t.grants ? `${t.grants} attribution${t.grants > 1 ? 's' : ''}` : <span className="muted">aucun droit encore : donnez-lui un rôle dans « Droits »</span>}</div>)}</div>}
        </div>
        {gq.data && gq.data.length > 0 && <div>
          <h3 style={{ margin: '0 0 6px' }}>Invités</h3>
          <table className="t"><tbody>{gq.data.map((g) => (
            <tr key={g.id}><td><b>{g.username}</b></td><td>{g.teams.map((t) => <Badge key={t}>{t}</Badge>)}</td>
              <td>{g.blocked ? <Badge kind="err">écarté</Badge> : g.suspended ? <Badge kind="warn">suspendu</Badge> : <Badge kind="ok">actif</Badge>}</td>
              <td className="actions"><Btn small kind="ghost" disabled={g.blocked && !okCode(code)} onClick={() => run('b' + g.id, () => api.post(`/admin/links/${l.id}/guests/${g.id}/block`, { blocked: !g.blocked, code }), g.blocked ? 'Invité rétabli' : 'Invité écarté')}>{g.blocked ? 'Rétablir' : 'Écarter'}</Btn></td></tr>))}</tbody></table>
        </div>}
        <div><CodeField code={code} setCode={setCode} />{err && <div className="banner err">{err}</div>}<div className="muted small">Le code sert au partage, à l'accueil et au rétablissement d'un invité écarté.</div></div>
      </div>
    </Modal>
  )
}
