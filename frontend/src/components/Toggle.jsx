import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api.js'
import { useUi } from './ui.jsx'

const list = (xs, max = 6) => xs.slice(0, max).map((x) => `« ${x} »`).join(', ') + (xs.length > max ? ` et ${xs.length - max} autre(s)` : '')

// Résume ce qu'un (dés)activation va toucher, pour que personne ne coupe quelque chose sans le savoir.
function impacts(plan, enable) {
  const out = []
  const cascade = plan.cascade.map((b) => b.name)
  if (cascade.length) out.push(enable ? `Seront aussi réactivés (ils étaient tombés avec lui ou en dépendent) : ${list(cascade)}.` : `Seront aussi désactivés, car ils reposent dessus : ${list(cascade)}.`)
  const removed = plan.refs.filter((r) => r.removed.length), restored = plan.refs.filter((r) => r.restored.length)
  if (removed.length) out.push(`Retiré de la configuration de ${list([...new Set(removed.map((r) => r.id.split('/').pop()))])} (rétabli à la réactivation).`)
  if (restored.length) out.push(`Rétabli dans la configuration de ${list([...new Set(restored.map((r) => r.id.split('/').pop()))])}.`)
  if (plan.skipped.length) out.push(`⚠ Non modifié (mise en forme du fichier non gérée) : ${list(plan.skipped.map((s) => s.id.split('/').pop()))}. À corriger à la main.`)
  return out
}

// Active / désactive des éléments Traefik, des fichiers ou tous les sites d'un domaine, avec aperçu des conséquences.
export function useToggler() {
  const qc = useQueryClient(); const { toast, confirm } = useUi()
  const [busy, setBusy] = useState(false)

  const run = async ({ url, body, enable, title, what, always }) => {
    setBusy(true)
    try {
      const plan = await api.post(url, { ...body, dry: true })
      if (!plan.blocks.length && !plan.files.length) { toast('Rien à modifier'); return null }
      const notes = impacts(plan, enable)
      if ((notes.length || always) && !(await confirm({ title, message: [what, ...notes].filter(Boolean).join('\n\n'), confirmLabel: enable ? 'Réactiver' : 'Désactiver', danger: false }))) return null
      const res = await api.post(url, { ...body, dry: false })
      qc.invalidateQueries()
      const flip = 'paused' in body ? { ...body, paused: !body.paused } : { ...body, enable: !enable }
      const undo = { label: 'Annuler', run: () => api.post(url, { ...flip, dry: false }).then(() => { qc.invalidateQueries(); toast('Annulé') }).catch((e) => toast(e.message, 'err')) }
      toast(enable ? 'Réactivé' : 'Désactivé — réversible à tout moment', 'ok', undo)
      // Éléments désactivés qui utilisent ce qu'on vient de réactiver (coupés avant que l'application s'en souvienne, ou à la main) : on propose, on ne devine pas
      if (enable && res.dependents?.length && await confirm({ title: 'Réactiver aussi ce qui l\'utilise ?', message: `Ces éléments sont désactivés et utilisent ce que vous venez de réactiver : ${list(res.dependents.map((d) => d.name))}.\n\nLes réactiver aussi ?`, confirmLabel: 'Réactiver aussi', danger: false })) {
        await api.post('/traefik/toggle', { ids: res.dependents.map((d) => d.id), files: [], enable: true, dry: false })
        qc.invalidateQueries()
        toast(`${res.dependents.length} élément(s) réactivé(s) aussi`)
      }
      return res
    } catch (e) { toast(e.message, 'err'); return null } finally { setBusy(false) }
  }

  return {
    busy,
    // ids : éléments (ex. « http/routers/blog ») · files : chemins de fichiers
    toggle: ({ ids = [], files = [], enable, label }) => run({ url: '/traefik/toggle', body: { ids, files, enable }, enable,
      title: `${enable ? 'Réactiver' : 'Désactiver'} ${label} ?`, what: files.length && !enable ? 'Traefik ne lira plus ce fichier tant qu\'il sera désactivé.' : '' }),
    pauseDomain: (domain, paused) => run({ url: '/traefik/toggle/domain', body: { domain, paused }, enable: !paused, always: true,
      title: `${paused ? 'Mettre en pause' : 'Reprendre'} les sites de ${domain} ?`,
      what: paused ? 'Tous les sites (routeurs) de ce domaine sont désactivés ; les enregistrements DNS restent en place.' : 'Seuls les sites mis en pause avec le domaine reprennent ; ceux que vous aviez coupés à la main restent coupés.' }),
  }
}
