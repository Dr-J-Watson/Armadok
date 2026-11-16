import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api.js'
import { refreshZone } from '../lib/dns.js'
import { Badge, Btn, Modal, Seg, useUi } from './ui.jsx'

// Propose d'ajouter chez OVH les hôtes présents dans Traefik mais absents du DNS.
export default function MissingDns({ rows, onClose }) {
  const qc = useQueryClient(); const { toast } = useUi()
  const [sel, setSel] = useState(() => new Set(rows.map((r) => r.host)))
  const [mode, setMode] = useState('A')
  const [busy, setBusy] = useState(false)
  const canCname = rows.every((r) => r.suggestion?.cnameTarget)
  const targetOf = (r) => (mode === 'CNAME' ? r.suggestion.cnameTarget : r.suggestion.target)
  const invalid = rows.filter((r) => sel.has(r.host) && !targetOf(r))

  const go = async () => {
    setBusy(true)
    try {
      const byZone = {}
      rows.filter((r) => sel.has(r.host)).forEach((r) => (byZone[r.zone] ||= []).push({ fieldType: mode, subDomain: r.subDomain, target: targetOf(r), ttl: r.suggestion.ttl || 3600 }))
      let created = 0; const errors = []
      for (const [zone, create] of Object.entries(byZone)) {
        const res = await api.post(`/dns/zones/${zone}/bulk`, { create })
        created += res.created; errors.push(...res.errors)
        await refreshZone(api, toast, zone)
      }
      qc.invalidateQueries({ queryKey: ['sync'] }); qc.invalidateQueries({ queryKey: ['records'] }); qc.invalidateQueries({ queryKey: ['zones'] })
      if (errors.length) toast(errors.join('\n'), 'err'); else toast(`${created} enregistrement(s) ajouté(s) dans la zone DNS`)
      onClose()
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  return (
    <Modal title="Ajouter ces sous-domaines chez OVH ?" onClose={onClose}
      footer={<><Btn onClick={onClose}>Plus tard</Btn><Btn kind="primary" busy={busy} disabled={!sel.size || invalid.length > 0} onClick={go}>Ajouter {sel.size} enregistrement(s)</Btn></>}>
      <div className="col">
        <div className="row"><span className="muted">Type :</span>
          <Seg value={mode} onChange={setMode} options={[{ id: 'A', label: 'A (IP)' }, ...(canCname ? [{ id: 'CNAME', label: 'CNAME → racine' }] : [])]} />
          <span className="muted small">{mode === 'CNAME' ? 'Suit automatiquement l\'IP de la racine.' : 'Copie l\'IP de la racine de la zone.'}</span></div>
        {rows.map((r) => (
          <label key={r.host} className="row" style={{ cursor: 'pointer' }}>
            <input type="checkbox" checked={sel.has(r.host)} onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(r.host) : n.delete(r.host); setSel(n) }} />
            <code>{r.host}</code><span className="muted">→</span>
            {targetOf(r) ? <Badge kind="accent">{mode} {targetOf(r)}</Badge> : <Badge kind="err">pas d'IP connue — définissez DEFAULT_DNS_TARGET ou ajoutez-le à la main</Badge>}
          </label>
        ))}
      </div>
    </Modal>
  )
}
