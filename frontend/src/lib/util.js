import YAML from 'yaml'

export const HOST_RE = /Host(?:SNI)?\(([^)]*)\)/g
export function extractHosts(rule = '') {
  const out = []
  for (const m of rule.matchAll(HOST_RE)) for (const h of m[1].matchAll(/[`"']([^`"']+)[`"']/g)) if (!/[*{]/.test(h[1])) out.push(h[1].toLowerCase())
  return out
}
export const toYaml = (obj) => (obj && Object.keys(obj).length ? YAML.stringify(obj, { indent: 2, lineWidth: 0 }) : '')
export const fromYaml = (txt) => YAML.parse(txt || '') ?? {}
export const fmtDate = (d) => (d ? new Date(d).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }) : '—')

// Retire récursivement valeurs vides pour produire un YAML propre
export function clean(v) {
  if (Array.isArray(v)) { const a = v.map(clean).filter((x) => x !== undefined); return a.length ? a : undefined }
  if (v && typeof v === 'object') {
    const o = {}
    for (const [k, x] of Object.entries(v)) { const c = clean(x); if (c !== undefined) o[k] = c }
    return Object.keys(o).length ? o : undefined
  }
  if (v === '' || v === null || v === undefined) return undefined
  return v
}

// YAML « joli » proche du style des fichiers existants : chaînes spéciales entre guillemets
export function toYamlPretty(obj, header = '') {
  const doc = new YAML.Document(obj)
  YAML.visit(doc, { Scalar(key, node) { if (key !== 'key' && typeof node.value === 'string' && !/^[A-Za-z0-9_./-]+$/.test(node.value)) node.type = 'QUOTE_DOUBLE' } })
  return (header ? header + '\n' : '') + doc.toString({ indent: 2, lineWidth: 0 })
}

export function relTime(d) {
  const s = Math.round((Date.now() - d.getTime()) / 1000)
  if (s < 60) return 'à l\'instant'
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`
  return `il y a ${Math.round(s / 86400)} j`
}
export const fmtSize = (n) => (n < 1024 ? `${n} o` : `${(n / 1024).toFixed(1).replace('.', ',')} Ko`)
