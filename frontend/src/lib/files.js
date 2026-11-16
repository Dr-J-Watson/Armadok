import { Archive, Braces, Container, Database, File as FileIcon, FileCode, FileCog, FileImage, FileJson, FileKey, FileText, FileTerminal, Hammer, Package, Settings, GitBranch, Lock } from 'lucide-react'

// Types de fichiers d'un projet : icône, couleur (classe .fk-<id>), langage Monaco. « key » = les fichiers qui comptent dans un projet Docker.
const K = {
  compose: { id: 'compose', label: 'compose', Icon: Container, lang: 'yaml', key: true, hint: 'Fichier compose : définit les services du projet' },
  env: { id: 'env', label: 'secrets', Icon: FileKey, lang: 'dotenv', key: true, hint: 'Variables d\'environnement (secrets)' },
  make: { id: 'make', label: 'makefile', Icon: Hammer, lang: 'makefile', key: true, hint: 'Makefile : commandes du projet' },
  docker: { id: 'docker', label: 'dockerfile', Icon: Package, lang: 'dockerfile', key: true, hint: 'Dockerfile : construction d\'une image' },
  yaml: { id: 'yaml', label: 'yaml', Icon: FileCog, lang: 'yaml' },
  json: { id: 'json', label: 'json', Icon: FileJson, lang: 'json' },
  md: { id: 'md', label: 'texte', Icon: FileText, lang: 'markdown' },
  shell: { id: 'shell', label: 'script', Icon: FileTerminal, lang: 'shell' },
  conf: { id: 'conf', label: 'config', Icon: Settings, lang: 'ini' },
  py: { id: 'py', label: 'python', Icon: FileCode, lang: 'python' },
  js: { id: 'js', label: 'javascript', Icon: FileCode, lang: 'javascript' },
  ts: { id: 'ts', label: 'typescript', Icon: FileCode, lang: 'typescript' },
  web: { id: 'web', label: 'web', Icon: FileCode, lang: 'html' },
  css: { id: 'css', label: 'css', Icon: FileCode, lang: 'css' },
  code: { id: 'code', label: 'code', Icon: FileCode, lang: 'plaintext' },
  sql: { id: 'sql', label: 'sql', Icon: Database, lang: 'sql' },
  image: { id: 'image', label: 'image', Icon: FileImage, lang: 'plaintext', binary: true },
  archive: { id: 'archive', label: 'archive', Icon: Archive, lang: 'plaintext', binary: true },
  lock: { id: 'lock', label: 'verrou', Icon: Lock, lang: 'plaintext' },
  git: { id: 'git', label: 'git', Icon: GitBranch, lang: 'ini' },
  data: { id: 'data', label: 'données', Icon: Braces, lang: 'plaintext', binary: true },
  file: { id: 'file', label: 'fichier', Icon: FileIcon, lang: 'plaintext' },
}
const EXT = {
  yml: 'yaml', yaml: 'yaml', json: 'json', md: 'md', txt: 'md', rst: 'md', sh: 'shell', bash: 'shell', zsh: 'shell', conf: 'conf', ini: 'conf', cfg: 'conf', toml: 'conf', properties: 'conf',
  py: 'py', js: 'js', mjs: 'js', cjs: 'js', jsx: 'js', ts: 'ts', tsx: 'ts', html: 'web', htm: 'web', xml: 'web', css: 'css', scss: 'css', php: 'code', go: 'code', rs: 'code', rb: 'code', java: 'code', c: 'code', h: 'code', lua: 'code',
  sql: 'sql', png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', svg: 'image', webp: 'image', ico: 'image', zip: 'archive', gz: 'archive', tgz: 'archive', tar: 'archive', bz2: 'archive', xz: 'archive', '7z': 'archive', pdf: 'data',
  lock: 'lock', db: 'data', sqlite: 'data', pem: 'lock', key: 'lock', crt: 'lock',
}

export const baseName = (p) => String(p || '').split('/').pop()

export function kindOf(path) {
  const n = baseName(path).toLowerCase()
  if (/^(docker-)?compose(\.[\w-]+)?\.ya?ml$/.test(n)) return K.compose
  if (n.startsWith('.env')) return K.env
  if (n === 'makefile' || n === 'gnumakefile' || n.endsWith('.mk')) return K.make
  if (n.startsWith('dockerfile') || n.endsWith('.dockerfile')) return K.docker
  if (n === '.gitignore' || n === '.gitattributes' || n === '.dockerignore') return K.git
  const ext = n.includes('.') ? n.split('.').pop() : ''
  return K[EXT[ext]] || K.file
}

export const langOf = (path) => kindOf(path).lang
export const isCompose = (path) => kindOf(path).id === 'compose'
// Ordre d'affichage dans un dossier : les fichiers clés d'abord.
export const keyRank = (path) => ({ compose: 0, env: 1, make: 2, docker: 3 })[kindOf(path).id] ?? 9
