import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Hourglass, Lock, RefreshCw, RotateCw, ShieldCheck, Trash2 } from 'lucide-react'
import { api } from '../api.js'
import { fmtDate, relTime } from '../lib/util.js'
import { useSearchParams } from 'react-router-dom'
import { Badge, Btn, CopyBtn, Copyable, Empty, Info, Loading, SearchBox, useSort, useUi } from '../components/ui.jsx'
import { useGo } from '../lib/nav.js'

export const certKind = (days) => (days < 0 ? 'err' : days <= 14 ? 'warn' : 'ok')
export const certLabel = (days) => (days < 0 ? `expiré depuis ${-days} j` : days === 0 ? 'expire aujourd\'hui' : `${days} j`)

const SITE_TEXT = {
  expired: ['err', 'Certificat expiré'],
  missing: ['warn', 'Aucun certificat obtenu'],
  expiring: ['warn', 'Expire bientôt'],
}

const SETUP = `# docker-compose.yml de l'interface (service « armadok »)
    environment:
      TRAEFIK_CERTS_FILE: /traefik-certs/acme.json
      TRAEFIK_CONTAINER: traefik                    # nom du conteneur de Traefik (pour le relancer)
    volumes:
      - /chemin/du/dossier/de/acme.json:/traefik-certs    # le DOSSIER qui contient acme.json
      - /var/run/docker.sock:/var/run/docker.sock         # facultatif : seulement pour supprimer / renouveler
    group_add: ["\${DOCKER_GID}"]                         # facultatif : groupe « docker » de la machine (getent group docker)`
const ROLLBACK = `# remettre la sauvegarde la plus récente (Traefik arrêté)
docker stop traefik
cp -p ./traefik_letsencrypt/$(ls -t ./traefik_letsencrypt | grep '^acme.json.bak-' | head -1) ./traefik_letsencrypt/acme.json
docker start traefik`
const Code = ({ text }) => <div className="codebox" style={{ position: 'relative' }}><div style={{ position: 'absolute', top: 4, right: 4 }}><CopyBtn text={text} /></div><pre style={{ margin: 0 }}>{text}</pre></div>

// Opération en cours (Traefik arrêté puis relancé) et résultat de la dernière.
function Pending({ running, onDone }) {
  const q = useQuery({ queryKey: ['certs', 'pending'], queryFn: () => api.get('/traefik/certs/pending'), refetchInterval: (qq) => (qq.state.data?.running || running ? 1500 : 60000) })
  const res = q.data?.result; const busy = q.data?.running
  const was = useRef(false)
  useEffect(() => { if (was.current && !busy) onDone(); was.current = !!busy }, [busy])   // eslint-disable-line react-hooks/exhaustive-deps
  if (!busy && !res) return null
  return (
    <div className="card col" style={{ borderColor: busy ? 'var(--warn)' : res.error ? 'var(--err)' : undefined }}>
      {busy ? <div className="row"><Hourglass size={16} /><h3>Traefik redémarre…</h3><span className="muted small">Retrait du certificat en cours : quelques secondes de coupure.</span></div> : (
        <div className="col" style={{ gap: 4 }}>
          <div className="row"><b>Dernière opération</b><span className="muted small">{fmtDate(res.at * 1000)}</span></div>
          {res.error ? <div className="banner err" style={{ margin: 0 }}><AlertTriangle size={16} />{res.error}</div> : <div className="small">{res.removed?.length || 0} retiré(s){res.removed?.length ? ` : ${res.removed.map((r) => r.main).join(', ')}` : ''}{res.missing?.length ? ` · introuvable(s) : ${res.missing.map((r) => r.main).join(', ')}` : ''} · Traefik a redémarré. Les certificats à renouveler sont redemandés par Traefik (en général en moins d'une minute).</div>}
          {res.backup && <details><summary className="muted small" style={{ cursor: 'pointer' }}>Sauvegarde {res.backup} · revenir en arrière</summary><div style={{ marginTop: 6 }}><Code text={ROLLBACK} /></div></details>}
        </div>)}
    </div>
  )
}

// Les certificats HTTPS obtenus par Traefik : on n'y lit jamais une clé privée.
export default function Certs() {
  const { toast, confirm } = useUi(); const qc = useQueryClient()
  const q = useQuery({ queryKey: ['certs'], queryFn: () => api.get('/traefik/certs'), staleTime: 30000 })
  const refresh = () => { qc.invalidateQueries({ queryKey: ['certs'] }); qc.invalidateQueries({ queryKey: ['overview'] }) }
  const ask = useMutation({ mutationFn: (host) => api.post('/traefik/certs/request', { host }), onSuccess: (r) => { toast(`Demande envoyée à Traefik (résolveur ${r.resolver}) : le certificat arrive en général en moins d'une minute`); setTimeout(refresh, 15000) }, onError: (e) => toast(e.message, 'err') })
  const [sel, setSel] = useState(() => new Set())
  const [hideInfo, setHideInfo] = useState(() => { try { return localStorage.getItem('hide-certs-info') === '1' } catch { return false } })
  const [params] = useSearchParams(); const go = useGo(); const [fq, setFq] = useState(params.get('host') || '')
  const { rows: sorted, th } = useSort(q.data?.certs, null, { names: (c) => c.main, issuer: (c) => c.issuer || '', until: (c) => c.notAfter, left: (c) => c.daysLeft })
  const shown = sorted.filter((c) => !fq || [c.main, ...(c.names || []), ...(c.usedBy || []), c.resolver || ''].join(' ').toLowerCase().includes(fq.toLowerCase()))
  const keyOf = (c) => c.resolver + '|' + c.main
  const purge = useMutation({ mutationFn: ({ list, mode }) => api.post('/traefik/certs/purge', { mode, items: list.map((c) => ({ resolver: c.resolver, main: c.main })) }), onSuccess: () => { setSel(new Set()); qc.invalidateQueries({ queryKey: ['certs', 'pending'] }); toast('Traefik redémarre : quelques secondes de coupure') }, onError: (e) => toast(e.message, 'err') })
  const coverOf = (host) => (q.data?.certs || []).filter((c) => (c.names.length ? c.names : [c.main]).some((n) => n === host || (n.startsWith('*.') && host.endsWith(n.slice(1)) && !host.slice(0, -(n.length - 1)).includes('.')))).sort((a, b) => b.notAfter - a.notAfter)[0]
  const doPurge = async (list, mode) => {
    const renew = mode === 'renew'; const many = list.length > 1
    const names = (c) => (c.names.length ? c.names : [c.main]).join(', ')
    const sole = [...new Set(list.flatMap((c) => c.soleFor || []))]
    const msg = `${many ? list.map((c) => '• ' + names(c)).join('\n') : names(list[0])}\n\n${renew ? `${many ? 'Ces certificats seront retirés' : 'Le certificat sera retiré'} d'acme.json, puis Traefik en obtiendra ${many ? 'des neufs' : 'un neuf'} (pour les sites qui les demandent toujours).` : `${many ? 'Ces certificats seront retirés' : 'Le certificat sera retiré'} d'acme.json.`}${sole.length ? `\n\nATTENTION : ${many ? 'certains sont' : 'il est'} les seuls à couvrir ${sole.join(', ')}. Ces sites utiliseront le certificat par défaut de Traefik (non valide) jusqu'à ce que Traefik en obtienne un autre. Let's Encrypt limite à 5 certificats identiques par semaine${renew && list.length > 5 ? ' : renouveler plus de 5 d\'un coup peut atteindre cette limite' : ''}.` : renew && list.length > 5 ? '\n\nLet\'s Encrypt limite à 5 certificats identiques par semaine : en renouveler plus de 5 d\'un coup peut atteindre cette limite.' : ''}\n\nTraefik sera coupé quelques secondes, une seule fois (arrêté, puis relancé). Une sauvegarde d'acme.json est faite avant.`
    if (await confirm({ title: `${renew ? 'Renouveler' : 'Supprimer'} ${many ? `ces ${list.length} certificats` : 'ce certificat'} ?`, message: msg, confirmLabel: renew ? 'Renouveler' : 'Supprimer' })) purge.mutate({ list, mode })
  }

  if (q.isLoading) return <Loading />
  if (q.error) return <div className="banner err">{q.error.message}</div>
  const d = q.data
  if (!d.configured) return (
    <div className="card col gap-lg">
      <div className="row"><Lock size={18} /><h2>Certificats HTTPS</h2><Info>{'Traefik garde ses certificats (et leurs clés privées) dans acme.json. L\'interface en lit seulement les noms, l\'émetteur et les dates : vous voyez les échéances et les sites sans certificat, sans qu\'aucune clé n\'arrive jamais dans votre navigateur.'}</Info></div>
      <div className="muted">Cette fonction est facultative et désactivée. Elle montre les certificats de ce nœud, leurs échéances, et prévient quand un site n'a pas obtenu son certificat. Pour l'activer, donnez à l'interface le dossier qui contient acme.json :</div>
      <div className="codebox"><pre>{SETUP}</pre></div>
    </div>
  )
  const problems = d.sites.filter((s) => SITE_TEXT[s.worst])
  return (
    <div className="col gap-lg">
      {d.error && <div className="banner err"><AlertTriangle size={18} />{d.error}</div>}
      <Pending running={purge.isPending} onDone={refresh} />
      {d.canManage && !d.purgeSupported && d.purgeWhy && !hideInfo && <div className="banner info" style={{ alignItems: 'flex-start' }}><AlertTriangle size={16} /><div className="col" style={{ gap: 6 }}><div className="row" style={{ gap: 8 }}><span className="grow">Supprimer / renouveler depuis l'interface n'est pas activé : {d.purgeWhy}.</span><Btn small kind="ghost" title="Masquer ce message (dans ce navigateur)" onClick={() => { setHideInfo(true); try { localStorage.setItem('hide-certs-info', '1') } catch { /* ignoré */ } }}>Masquer</Btn></div><details><summary style={{ cursor: 'pointer' }}>Comment l'activer</summary><div style={{ marginTop: 6 }}><Code text={SETUP} /></div></details></div></div>}
      {!d.error && problems.length === 0 && <div className="banner ok"><CheckCircle2 size={18} /> Tous les sites en HTTPS ont un certificat valide.</div>}
      {problems.length > 0 && (
        <div className="card flush">
          <div className="row" style={{ padding: '12px 14px 4px' }}><AlertTriangle size={15} /><h3>À surveiller</h3><Info>Un site avec un « résolveur » devrait avoir obtenu son certificat. « Aucun certificat » veut souvent dire que le défi a échoué (DNS, limite de Let's Encrypt, clés du défi DNS manquantes dans Traefik).</Info></div>
          {problems.map((s) => { const [kind, label] = SITE_TEXT[s.worst]; return (
            <div className="issue" key={s.id} style={{ cursor: 'default' }}><Badge kind={kind}>{label}</Badge>
              <div className="grow"><b>{s.name}</b><div className="muted small">{s.hosts.filter((h) => h.status === s.worst).map((h) => h.host + (h.days !== undefined ? ` (${certLabel(h.days)})` : '')).join(', ')}</div></div>
              {d.canManage && s.worst === 'missing' && <Btn small title="Demande à Traefik de réessayer d'obtenir le certificat (sans le redémarrer). Il faut que le site ait un résolveur de certificats." busy={ask.isPending} onClick={() => ask.mutate(s.hosts.find((h) => h.status === s.worst).host)}><RotateCw size={13} /> Redemander</Btn>}
              {d.purgeSupported && ['expired', 'expiring'].includes(s.worst) && coverOf(s.hosts.find((h) => h.status === s.worst).host) && <Btn small title="Le certificat existe encore dans acme.json : Traefik ne le redemande pas tout seul. On le retire (Traefik arrêté) pour qu'il en obtienne un neuf." onClick={() => doPurge([coverOf(s.hosts.find((h) => h.status === s.worst).host)], 'renew')}><RefreshCw size={13} /> Renouveler</Btn>}</div>) })}
        </div>)}
      <div className="card flush">
        <div className="row" style={{ padding: '12px 14px 4px' }}><ShieldCheck size={15} /><h3>Certificats</h3><Badge>{d.certs.length}</Badge><SearchBox value={fq} onChange={setFq} placeholder="Filtrer (domaine, site…)" />
          {d.purgeSupported && d.certs.some((c) => c.daysLeft < 0) && <Btn small kind="ghost" title="Coche tous les certificats déjà expirés (souvent des restes de sites supprimés)" onClick={() => setSel(new Set(d.certs.filter((c) => c.daysLeft < 0).map(keyOf)))}>Sélectionner les expirés ({d.certs.filter((c) => c.daysLeft < 0).length})</Btn>}
          {d.purgeSupported && d.certs.some((c) => c.daysLeft >= 0 && c.daysLeft <= 14) && <Btn small kind="ghost" title="Coche les certificats qui expirent dans moins de 14 jours (un renouvellement automatique a échoué)" onClick={() => setSel(new Set(d.certs.filter((c) => c.daysLeft >= 0 && c.daysLeft <= 14).map(keyOf)))}>Sélectionner ceux qui expirent bientôt</Btn>}
          {d.updated && <span className="muted small right">relevé {relTime(new Date(d.updated * 1000))}</span>}</div>
        {d.certs.length === 0 ? <Empty>Aucun certificat pour l'instant.</Empty> : (
          <table className="t"><thead><tr>{d.purgeSupported && <th style={{ width: 30 }}><input type="checkbox" title="Tout sélectionner" checked={d.certs.length > 0 && sel.size === d.certs.length} onChange={(e) => setSel(e.target.checked ? new Set(d.certs.map(keyOf)) : new Set())} /></th>}<th>{th('names', 'Domaines')}</th><th>{th('issuer', 'Émetteur')}</th><th>{th('until', 'Valide jusqu\'au')}</th><th>{th('left', 'Reste')}<Info>Traefik renouvelle seul environ 30 jours avant l'échéance. En dessous de 14 jours, un renouvellement a probablement échoué.</Info></th><th>Résolveur</th>{d.purgeSupported && <th />}</tr></thead>
            <tbody>{shown.length === 0 && <tr><td colSpan={7}><Empty>Aucun certificat ne correspond à « {fq} ».</Empty></td></tr>}{shown.map((c) => (
              <tr key={c.fingerprint || c.serial + c.main}>
                {d.purgeSupported && <td><input type="checkbox" checked={sel.has(keyOf(c))} onChange={() => setSel((o) => { const n = new Set(o); n.has(keyOf(c)) ? n.delete(keyOf(c)) : n.add(keyOf(c)); return n })} /></td>}
                <td><div className="row wrap" style={{ gap: 5 }}>{(c.names.length ? c.names : [c.main]).slice(0, 6).map((n) => <Badge key={n} kind={n.startsWith('*.') ? 'accent' : ''} title={n.startsWith('*.') ? 'Certificat joker : couvre tous les sous-domaines directs' : undefined}>{n}</Badge>)}{c.names.length > 6 && <Badge>+{c.names.length - 6}</Badge>}</div></td>
                <td className="muted">{c.issuer || '—'}</td>
                <td>{fmtDate(c.notAfter * 1000)}</td>
                <td><Badge kind={certKind(c.daysLeft)}>{certLabel(c.daysLeft)}</Badge></td>
                <td className="muted small">{c.resolver || '—'}{c.usedBy?.length > 0 && <div className="row wrap" style={{ gap: 4, marginTop: 2 }}>{c.usedBy.slice(0, 3).map((n) => <a key={n} href={`/sites?open=${encodeURIComponent(n)}`} className="linkcell" title="Ouvrir ce site" onClick={(e) => { e.preventDefault(); go(`/sites?open=${encodeURIComponent(n)}`) }}>{n}</a>)}{c.usedBy.length > 3 && <span title={c.usedBy.slice(3).join(', ')}>+{c.usedBy.length - 3}</span>}</div>}</td>
                {d.purgeSupported && <td className="actions"><Btn small kind="ghost" icon title="Renouveler : retirer ce certificat pour que Traefik en obtienne un neuf" onClick={() => doPurge([c], 'renew')}><RefreshCw size={14} /></Btn><Btn small kind="ghost" icon title="Supprimer ce certificat d'acme.json" onClick={() => doPurge([c], 'delete')}><Trash2 size={14} /></Btn></td>}
              </tr>))}</tbody></table>)}
      </div>
      {sel.size > 0 && d.purgeSupported && <div className="sel-bar"><b>{sel.size} sélectionné{sel.size > 1 ? 's' : ''}</b>
        <Btn small className="right" busy={purge.isPending} onClick={() => doPurge(d.certs.filter((c) => sel.has(keyOf(c))), 'renew')}><RefreshCw size={14} /> Renouveler</Btn>
        <Btn small kind="danger" busy={purge.isPending} onClick={() => doPurge(d.certs.filter((c) => sel.has(keyOf(c))), 'delete')}><Trash2 size={14} /> Supprimer</Btn>
        <Btn small onClick={() => setSel(new Set())}>Désélectionner</Btn></div>}
    </div>
  )
}
