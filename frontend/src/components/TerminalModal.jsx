import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { getToken } from '../api.js'
import { useNode } from '../node.jsx'
import { Badge, Btn, Modal } from './ui.jsx'

const CLOSES = { 4401: 'Session expirée ou jeton refusé : rouvrez le terminal.', 4403: 'Accès refusé (droit « terminal », conteneur introuvable ou nœud inconnu).', 4501: 'Terminal indisponible : conteneur arrêté, nœud sans Docker, ou worker qui ne l\'autorise pas (WORKER_ALLOW_EXEC=true) ou hors ligne.', 1013: 'Trop de terminaux ouverts.' }

// Un terminal dans un conteneur (docker exec). Tout ce qui est tapé passe par la connexion chiffrée du navigateur ; seule l'ouverture est journalisée, jamais le contenu.
export default function TerminalModal({ container, onClose }) {
  const box = useRef(null); const { node } = useNode(); const [state, setState] = useState('connexion…'); const [kind, setKind] = useState('info')
  useEffect(() => {
    const term = new Terminal({ cursorBlink: true, fontSize: 13, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', theme: { background: '#0b0d12', foreground: '#d8dbe3' }, scrollback: 4000 })
    const fit = new FitAddon(); term.loadAddon(fit); term.open(box.current); fit.fit()
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/docker/exec`); ws.binaryType = 'arraybuffer'
    let live = false
    ws.onopen = () => ws.send(JSON.stringify({ token: getToken(), node: node.id, container, cols: term.cols, rows: term.rows }))
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') { if (JSON.parse(ev.data).t === 'ready') { live = true; setState('connecté'); setKind('ok'); term.focus() } return }
      term.write(new Uint8Array(ev.data))
    }
    ws.onclose = (ev) => { setState(live ? 'terminé' : (ev.reason || CLOSES[ev.code] || 'connexion impossible')); setKind(live ? '' : 'err'); term.write('\r\n\x1b[90m[session terminée]\x1b[0m\r\n') }
    const sub = term.onData((d) => ws.readyState === 1 && live && ws.send(JSON.stringify({ i: d })))
    const resize = () => { try { fit.fit(); if (ws.readyState === 1 && live) ws.send(JSON.stringify({ r: [term.cols, term.rows] })) } catch { /* */ } }
    const ro = new ResizeObserver(resize); ro.observe(box.current)
    return () => { ro.disconnect(); sub.dispose(); try { ws.close() } catch { /* */ } term.dispose() }
  }, [container]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Modal wide title={<span className="row" style={{ gap: 8 }}>Terminal · {container}<Badge kind={kind}>{state}</Badge></span>} onClose={onClose} footer={<Btn onClick={onClose}>Fermer</Btn>}>
      <div className="col"><div className="muted small">Un shell dans le conteneur (bash s'il existe, sinon sh). Fermer cette fenêtre met fin à la session.</div>
        <div className="dk-term" ref={box} /></div>
    </Modal>
  )
}
