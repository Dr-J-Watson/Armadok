import { useEffect, useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { api } from '../api.js'
import { Btn, CopyBtn, Field } from '../components/ui.jsx'

// Page publique : un invité d'un hub lié définit SON mot de passe et sa double authentification sur CE hub (le lien à usage unique est la seule preuve).
// Le mot de passe n'est tapé qu'ici : le hub d'origine ne voit que le lien.
export default function GuestSetup() {
  const token = decodeURIComponent((window.location.hash || '').replace(/^#/, ''))
  const [st, setSt] = useState(null); const [err, setErr] = useState(''); const [busy, setBusy] = useState(false)
  const [pw, setPw] = useState(''); const [pw2, setPw2] = useState(''); const [code, setCode] = useState(''); const [done, setDone] = useState(null)
  useEffect(() => { if (token) api.post('/guest/setup/start', { token }).then(setSt).catch((e) => setErr(e.message)) }, [token])
  const submit = async (e) => {
    e.preventDefault(); setErr('')
    if (pw !== pw2) { setErr('Les deux mots de passe ne sont pas identiques'); return }
    setBusy(true)
    try { setDone(await api.post('/guest/setup/finish', { token, password: pw, code })); window.history.replaceState(null, '', window.location.pathname) } catch (x) { setErr(x.message) } finally { setBusy(false) }
  }
  return (
    <div className="login">
      <form className="card col gap-lg" onSubmit={submit} style={{ maxWidth: 420 }}>
        <div className="brand" style={{ padding: 0 }}><div className="logo"><b style={{ fontSize: 18, lineHeight: 1 }}>A</b></div>Armadok</div>
        {!token ? <div className="banner err" style={{ margin: 0 }}>Il manque le lien d'activation. Redemandez-en un depuis votre hub d'origine.</div>
          : done ? (<>
            <div className="banner ok" style={{ margin: 0 }}>Mot de passe et double authentification enregistrés pour <b>{done.username}</b>. Vous pouvez maintenant vous connecter directement sur ce hub.</div>
            <Field label="Codes de secours" help="À garder en lieu sûr : chacun sert une fois si vous perdez votre application d'authentification. Ils ne seront plus affichés."><div className="mono small" style={{ columns: 2 }}>{done.recoveryCodes.map((c) => <div key={c}>{c}</div>)}</div></Field>
            <div className="row"><CopyBtn text={done.recoveryCodes.join('\n')} /><Btn kind="primary" type="button" className="right" onClick={() => { window.location.href = '/' }}>Se connecter</Btn></div></>)
            : !st ? (err ? <div className="banner err" style={{ margin: 0 }}>{err}</div> : <div className="muted">Vérification du lien…</div>) : (<>
              <div className="muted small">Compte invité <b>{st.username}</b>. Ce mot de passe n'est valable que sur ce hub ; il n'est jamais communiqué à votre hub d'origine.</div>
              <Field label="1. Application d'authentification" help="Scannez ce code (ou saisissez la clé), puis tapez le code à 6 chiffres affiché."><div className="col" style={{ alignItems: 'center', gap: 6 }}><div style={{ width: 160, background: '#fff', padding: 6, borderRadius: 6 }} dangerouslySetInnerHTML={{ __html: st.qr }} /><span className="mono small">{st.secret}</span></div></Field>
              <Field label="2. Mot de passe (12 caractères au moins)"><input className="input" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus /></Field>
              <Field label="Confirmer le mot de passe"><input className="input" type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} /></Field>
              <Field label="3. Code de l'application"><input className="input mono" style={{ fontSize: 18, letterSpacing: 4, textAlign: 'center' }} inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} /></Field>
              {err && <div className="banner err" style={{ margin: 0 }}>{err}</div>}
              <Btn kind="primary" busy={busy} type="submit" disabled={!pw || !code.trim()}><ShieldCheck size={14} /> Enregistrer</Btn></>)}
      </form>
    </div>
  )
}
