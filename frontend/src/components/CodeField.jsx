import { ShieldCheck } from 'lucide-react'
import { useAuth } from '../auth.jsx'
import { Btn, Field } from './ui.jsx'

export const okCode = (c) => (c || '').replace(/\s/g, '').length >= 6

// Le champ de code 2FA des actions sensibles. Sans double authentification activée, le code ne peut pas exister : on le dit et on propose de l'activer, comme le bandeau des nœuds.
export function CodeField({ code, setCode, go, help = 'Le code à 6 chiffres de votre application d\'authentification.', autoFocus, wide }) {
  const { user } = useAuth()
  if (!user?.twoFactor) return (
    <div className="banner info" style={{ margin: 0 }}><ShieldCheck size={18} /><span className="grow">Cette action demande un code de double authentification, mais elle n'est pas activée sur votre compte. Activez-la pour continuer.</span>
      <Btn small kind="primary" onClick={() => window.dispatchEvent(new Event('open-profile'))}>Activer la 2FA</Btn></div>)
  return (
    <Field label="Code de double authentification" help={help}>
      <input className="input mono" autoFocus={autoFocus} style={{ maxWidth: wide ? undefined : 180, letterSpacing: 3, textAlign: 'center' }} inputMode="numeric" autoComplete="one-time-code" placeholder="000000"
        value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && okCode(code) && go) go() }} /></Field>)
}
