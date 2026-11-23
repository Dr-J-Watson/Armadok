// Télécharge un tableau en CSV (séparateur « ; » et BOM : s'ouvre correctement dans Excel / LibreOffice en français).
export function downloadCsv(filename, header, rows) {
  const cell = (v) => { let s = v == null ? '' : String(v); if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; return `"${s.replace(/"/g, '""')}"` }   // pas d'injection de formule
  const text = '﻿' + [header, ...rows].map((r) => r.map(cell).join(';')).join('\r\n') + '\r\n'
  downloadBlob(filename, text, 'text/csv;charset=utf-8')
}

// Fait télécharger un texte au navigateur sous ce nom de fichier.
export function downloadBlob(filename, text, type = 'text/plain;charset=utf-8') {
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = filename
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}
