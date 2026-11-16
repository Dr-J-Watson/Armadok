import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { CircleHelp } from 'lucide-react'

const DELAY = 900   // ms avant l'infobulle d'un élément (bouton, badge…) quand on reste dessus
const FAST = 120    // ms pour une icône « ? » (c'est elle qu'on vise pour avoir l'explication)

// Icône d'aide : explication au survol (ou au clic / au clavier / au toucher). Texte sur plusieurs lignes accepté.
export function Info({ children, tip, className = '' }) {
  const text = tip ?? (typeof children === 'string' ? children : '')
  return <button type="button" className={'help-i ' + className} data-tip={text} data-tip-fast aria-label={'Aide : ' + text} onClick={(e) => e.preventDefault()}><CircleHelp size={14} /></button>
}

// Une seule couche d'infobulles pour toute l'application : elle remplace aussi les « title » natifs (délai long, laids, absents au clavier).
export function TooltipLayer() {
  const [tip, setTip] = useState(null)
  const ref = useRef(null)

  useEffect(() => {
    let timer, cur = null, stash = null, pinned = false
    const hide = () => {
      clearTimeout(timer)
      if (cur && stash) cur.setAttribute('title', stash) // React reste cohérent avec le DOM
      cur = null; stash = null; pinned = false
      setTip(null)
    }
    const find = (t) => (t instanceof Element ? t.closest('[data-tip],[title]') : null)
    const arm = (el, delay) => {
      const title = el.getAttribute('title')
      if (title) { stash = title; el.removeAttribute('title') }
      const text = el.getAttribute('data-tip') || title
      if (!text) return false
      cur = el
      timer = setTimeout(() => setTip({ text, rect: el.getBoundingClientRect() }), delay)
      return true
    }
    const over = (e) => {
      const el = find(e.target)
      if (!el || el === cur || pinned) return
      if (cur) hide()
      arm(el, el.hasAttribute('data-tip-fast') ? FAST : DELAY)
    }
    const out = (e) => {
      if (!cur || pinned) return
      if (e.relatedTarget instanceof Node && cur.contains(e.relatedTarget)) return
      hide()
    }
    const focus = (e) => { const el = find(e.target); if (el && el.hasAttribute('data-tip') && !pinned) { if (cur) hide(); arm(el, 0) } }
    const click = (e) => {
      const el = find(e.target)
      if (el?.hasAttribute('data-tip-fast')) { // clic sur « ? » : l'infobulle reste jusqu'au prochain clic (utile au toucher)
        const same = pinned && cur === el
        hide()
        if (!same && arm(el, 0)) pinned = true
      } else if (cur) hide()
    }
    const key = (e) => e.key === 'Escape' && cur && hide()
    const leave = () => cur && !pinned && hide()
    const opts = { capture: true }
    document.addEventListener('mouseover', over, opts); document.addEventListener('mouseout', out, opts)
    document.addEventListener('focusin', focus, opts); document.addEventListener('focusout', leave, opts)
    document.addEventListener('click', click, opts); document.addEventListener('keydown', key, opts)
    window.addEventListener('scroll', leave, opts); window.addEventListener('blur', hide)
    return () => {
      hide()
      document.removeEventListener('mouseover', over, opts); document.removeEventListener('mouseout', out, opts)
      document.removeEventListener('focusin', focus, opts); document.removeEventListener('focusout', leave, opts)
      document.removeEventListener('click', click, opts); document.removeEventListener('keydown', key, opts)
      window.removeEventListener('scroll', leave, opts); window.removeEventListener('blur', hide)
    }
  }, [])

  useLayoutEffect(() => {
    const el = ref.current
    if (!tip || !el) return
    const b = el.getBoundingClientRect(), r = tip.rect
    let top = r.bottom + 8
    if (top + b.height > window.innerHeight - 8) top = Math.max(8, r.top - b.height - 8)
    el.style.top = top + 'px'
    el.style.left = Math.max(8, Math.min(r.left + r.width / 2 - b.width / 2, window.innerWidth - b.width - 8)) + 'px'
    el.style.visibility = 'visible'
  }, [tip])

  return tip ? <div ref={ref} role="tooltip" className="tip-layer" style={{ visibility: 'hidden' }}>{tip.text}</div> : null
}
