import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { getToken } from '../api.js'
import { useNode } from '../node.jsx'
import { Badge, Btn, Modal, useUi } from './ui.jsx'

const CLOSES = { 4401: 'Session expirée ou jeton refusé : relancez la commande.', 4403: 'Accès refusé (droits « déployer » et « terminal » requis, ou projet introuvable).', 4501: 'Impossible de lancer ici : une commande tourne déjà sur ce projet, le nœud n\'a pas Docker, ou le worker n\'autorise pas les commandes (WORKER_ALLOW_EXEC=true) ou est hors ligne.', 1013: 'Trop de terminaux ouverts.' }

// Une cible du Makefile dans un vrai terminal : on y voit la sortie ET on peut répondre aux questions de la commande (confirmation, saisie, mot de passe…).
// Fermer la fenêtre arrête la commande. Seule l'ouverture est journalisée, jamais ce qui est tapé.
export default function MakeTerminal({ project, target, vars, onClose }) {
  const box = useRef(null); const { node } = useNode(); const { confirm } = useUi()
  const [state, setState] = useState('connexion…'); const [kind, setKind] = useState('info'); const [running, setRunning] = useState(true); const [seed, setSeed] = useState(0)
  const live = useRef(false)
  useEffect(() => {
    setRunning(true); setState('connexion…'); setKind('info'); live.current = false
    const term = new Terminal({ cursorBlink: true, fontSize: 13, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', theme: { background: '#0b0d12', foreground: '#d8dbe3' }, scrollback: 6000, convertEol: false })
    const fit = new FitAddon(); term.loadAddon(fit); term.open(box.current); fit.fit()
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/projects/make/term`); ws.binaryType = 'arraybuffer'
    let code = null
    ws.onopen = () => ws.send(JSON.stringify({ token: getToken(), node: node.id, project, target, vars: vars || {}, cols: term.cols, rows: term.rows }))
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        const m = JSON.parse(ev.data)
        if (m.t === 'ready') { live.current = true; setState('en cours'); setKind('warn'); term.focus() }
        else if (m.t === 'exit') code = m.code
        return
      }
      term.write(new Uint8Array(ev.data))
    }
    ws.onclose = (ev) => {
      const was = live.current; live.current = false; setRunning(false)
      if (!was) { setState(ev.reason || CLOSES[ev.code] || 'connexion impossible'); setKind('err'); return }
      if (code === 0) { setState('terminé · succès'); setKind('ok') } else if (code != null) { setState(`terminé · code ${code}`); setKind('err') } else { setState('interrompu'); setKind('err') }
      term.write(`\r\n\x1b[90m[${code === 0 ? 'terminé' : code != null ? 'terminé avec le code ' + code : 'connexion perdue'}]\x1b[0m\r\n`)
    }
    const sub = term.onData((d) => ws.readyState === 1 && live.current && ws.send(JSON.stringify({ i: d })))
    const resize = () => { try { fit.fit(); if (ws.readyState === 1 && live.current) ws.send(JSON.stringify({ r: [term.cols, term.rows] })) } catch { /* */ } }
    const ro = new ResizeObserver(resize); ro.observe(box.current)
    return () => { ro.disconnect(); sub.dispose(); try { ws.close() } catch { /* */ } term.dispose() }
  }, [project, target, seed]) // eslint-disable-line react-hooks/exhaustive-deps
  const close = async () => {
    if (running && !(await confirm({ title: 'Arrêter la commande ?', message: `« make ${target} » tourne encore : fermer cette fenêtre l'arrête.`, confirmLabel: 'Arrêter et fermer' }))) return
    onClose()
  }
  return (
    <Modal wide title={<span className="row" style={{ gap: 8 }}>make {target}<Badge kind={kind}>{state}</Badge></span>} onClose={close} footer={<>{!running && <Btn onClick={() => setSeed((n) => n + 1)}>Relancer</Btn>}<Btn kind={running ? 'danger' : 'primary'} onClick={close}>{running ? 'Arrêter et fermer' : 'Fermer'}</Btn></>}>
      <div className="col"><div className="muted small">Un vrai terminal : si la commande pose une question, répondez ici (Ctrl+C l'interrompt). Fermer la fenêtre arrête la commande.</div>
        <div className="dk-term" ref={box} /></div>
    </Modal>
  )
}
