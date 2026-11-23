import { api } from '../api.js'

// Fichiers du poste <-> projet. Un fichier voyage en morceaux de 2 Mo (base64) : l'API refuse les corps de plus de 4 Mo et le lien avec un worker les messages de plus de 8 Mo.
export const CHUNK = 2 * 1024 * 1024
export const MAX_UPLOAD = 100 * 1024 * 1024

const toBytes = (b64) => { const s = atob(b64); const u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u }
const readB64 = (blob) => new Promise((ok, ko) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1] || ''); r.onerror = () => ko(r.error); r.readAsDataURL(blob) })

function save(name, blob) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000)
}

// Télécharge un fichier (tel quel) ou une sélection (archive zip, arborescence conservée). `onProgress(0..1)` facultatif.
export async function downloadPaths(id, rels, onProgress) {
  const m = await api.post(`/projects/${id}/pack`, { rels })
  const parts = []; let off = 0
  for (;;) {
    const r = await api.post(`/projects/${id}/pack/read`, { token: m.token, offset: off })
    const b = toBytes(r.b64); parts.push(b); off += b.length
    onProgress?.(m.size ? Math.min(1, off / m.size) : 1)
    if (r.done) break
  }
  save(m.name, new Blob(parts))
  return m
}

// Envoie un fichier du poste vers `rel` (chemin dans le projet). Lève une erreur lisible ; un envoi interrompu est nettoyé côté serveur.
export async function uploadFile(id, file, rel, overwrite, onProgress, unlimited = false) {
  if (!unlimited && file.size > MAX_UPLOAD) throw new Error(`« ${file.name} » dépasse ${MAX_UPLOAD / 1048576} Mo`)
  let off = 0
  try {
    do {
      const part = file.slice(off, off + CHUNK); const last = off + part.size >= file.size
      await api.post(`/projects/${id}/upload`, { rel, b64: await readB64(part), offset: off, last, overwrite })
      off += part.size; onProgress?.(file.size ? off / file.size : 1)
      if (last) return
    } while (true) // eslint-disable-line no-constant-condition
  } catch (e) {
    api.post(`/projects/${id}/upload`, { rel, abort: true }).catch(() => {})
    throw e
  }
}

// Ce qu'on dépose (glisser-déposer) : fichiers ET dossiers, dossiers parcourus en profondeur -> [{ file, path }] avec path relatif (« dossier/sous/fichier »).
export async function collectDrop(dt) {
  const items = [...(dt.items || [])].map((i) => i.webkitGetAsEntry?.()).filter(Boolean)
  if (!items.length) return [...(dt.files || [])].map((f) => ({ file: f, path: f.name }))
  const out = []
  const readAll = (reader) => new Promise((ok, ko) => { const all = []; const next = () => reader.readEntries((b) => (b.length ? (all.push(...b), next()) : ok(all)), ko); next() })
  const walk = async (en, prefix) => {
    if (en.isFile) out.push({ file: await new Promise((ok, ko) => en.file(ok, ko)), path: prefix + en.name })
    else if (en.isDirectory) for (const c of await readAll(en.createReader())) await walk(c, prefix + en.name + '/')
  }
  for (const en of items) await walk(en, '')
  return out
}

// Ce qu'on choisit avec un <input type="file"> (avec ou sans « dossier »)
export const fromInput = (files) => [...files].map((f) => ({ file: f, path: f.webkitRelativePath || f.name }))
