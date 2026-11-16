// Arborescence des fichiers Traefik d'un nœud (dossiers optionnels, jusqu'à 4 niveaux).
export const baseName = (p) => p.split('/').pop()
export const dirOf = (p) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '')
export const joinPath = (d, n) => (d ? `${d}/${n}` : n)
export const withExt = (n) => (/\.ya?ml$/i.test(n) ? n : n + '.yml')
export const MAX_DEPTH = 4

// Organisation proposée sur un nœud « à plat » (rien d'obligatoire : c'est un simple point de départ).
export const SUGGESTED_DIRS = [
  { name: 'apps', hint: 'vos applications' },
  { name: 'infra', hint: 'infrastructure (dashboard, portainer…)' },
  { name: 'middlewares', hint: 'authentification, en-têtes, chaînes…' },
]

export function buildTree(files, dirs = []) {
  const root = { name: '', path: '', dirs: new Map(), files: [] }
  const ensure = (path) => path.split('/').reduce((n, part) => {
    if (!n.dirs.has(part)) n.dirs.set(part, { name: part, path: joinPath(n.path, part), dirs: new Map(), files: [] })
    return n.dirs.get(part)
  }, root)
  dirs.forEach((d) => ensure(d))
  files.forEach((f) => (dirOf(f.path) ? ensure(dirOf(f.path)) : root).files.push(f))
  return root
}

export const countFiles = (n) => n.files.length + [...n.dirs.values()].reduce((a, d) => a + countFiles(d), 0)
export const sorted = (n) => ({
  dirs: [...n.dirs.values()].sort((a, b) => a.name.localeCompare(b.name)),
  files: [...n.files].sort((a, b) => baseName(a.path).localeCompare(baseName(b.path))),
})
