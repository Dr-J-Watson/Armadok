// Petits utilitaires partagés par les écrans Docker.
export const fmtSize = (b) => {
  if (b == null) return '—'
  if (b < 1024) return `${b} o`
  const u = ['Ko', 'Mo', 'Go', 'To']; let v = b / 1024; let i = 0
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1 }
  return `${v.toFixed(v >= 100 ? 0 : 1).replace('.', ',')} ${u[i]}`
}

// Les journaux de certains outils contiennent des codes couleur ANSI : on les retire.
// eslint-disable-next-line no-control-regex
export const stripAnsi = (s) => String(s || '').replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '')

export const STATE = {
  running: ['En marche', 'ok'], exited: ['Arrêté', ''], created: ['Créé', ''], paused: ['En pause', 'warn'], restarting: ['Redémarre', 'warn'], dead: ['Mort', 'err'], removing: ['Suppression', 'warn'],
}
export const stateOf = (c) => {
  const [label, kind] = STATE[c.state] || [c.state || '?', '']
  if (c.state === 'running' && c.health === 'unhealthy') return ['En marche · défaillant', 'err']
  if (c.state === 'running' && c.health === 'starting') return ['Démarrage', 'warn']
  return [label, kind]
}

export const portLabel = (p) => (p.public ? `${p.public}→${p.private}/${p.type}` : `${p.private}/${p.type}`)
export const PROTECTED = { self: 'Interface', traefik: 'Traefik' }
export const enc = encodeURIComponent
