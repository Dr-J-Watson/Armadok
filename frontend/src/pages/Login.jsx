import { useState } from 'react'
import { Lock, ShieldCheck } from 'lucide-react'
import { useAuth } from '../auth.jsx'
import { Btn, Field } from '../components/ui.jsx'

export default function Login() {
  const { login, loginMfa } = useAuth()
  const [u, setU] = useState(''); const [p, setP] = useState(''); const [err, setErr] = useState(''); const [busy, setBusy] = useState(false)
  const [mfa, setMfa] = useState(null); const [code, setCode] = useState('')
  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setErr('')
    try {
      if (mfa) await loginMfa(mfa, code)
      else { const r = await login(u, p); if (r.mfaToken) setMfa(r.mfaToken) }
    } catch (x) { setErr(x.message); if (mfa && x.status === 401 && /expir|invalide/i.test(x.message)) { setMfa(null); setCode('') } } finally { setBusy(false) }
  }
  return (
    <div className="login">
      <form className="card col gap-lg" onSubmit={submit}>
        <div className="brand" style={{ padding: 0 }}><div className="logo"><b style={{ fontSize: 18, lineHeight: 1 }}>A</b></div>Armadok</div>
        {!mfa ? (<>
          <Field label="Utilisateur"><input className="input" value={u} onChange={(e) => setU(e.target.value)} autoFocus autoComplete="username" /></Field>
          <Field label="Mot de passe"><input className="input" type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" /></Field>
        </>) : (
          <Field label="Code de vérification" help="Entrez le code à 6 chiffres de votre application d'authentification, ou un code de secours.">
            <input className="input mono" style={{ fontSize: 20, letterSpacing: 4, textAlign: 'center' }} value={code} onChange={(e) => setCode(e.target.value)} autoFocus autoComplete="one-time-code" inputMode="text" placeholder="000000" />
          </Field>)}
        {err && <div className="banner err" style={{ margin: 0 }}>{err}</div>}
        <Btn kind="primary" busy={busy} type="submit">{mfa ? <><ShieldCheck size={14} /> Vérifier</> : <><Lock size={14} /> Connexion</>}</Btn>
        {mfa && <Btn kind="ghost" type="button" onClick={() => { setMfa(null); setCode(''); setErr('') }}>Retour</Btn>}
      </form>
    </div>
  )
}
