import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { api, refreshSession, setLogoutHandler, setToken } from './api.js'
import { onLive } from './lib/live.js'

const Ctx = createContext(null)
export const useAuth = () => useContext(Ctx)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [ready, setReady] = useState(false)

  const logout = useCallback(async () => {
    try { await api.post('/auth/logout') } catch { /* ignore */ }
    setToken(null)
    setUser(null)
  }, [])

  useEffect(() => {
    setLogoutHandler(() => { setToken(null); setUser(null) })
    refreshSession().then((d) => setUser(d.user)).catch(() => {}).finally(() => setReady(true))
  }, [])

  // Temps réel : mon compte a changé (rôle, droits, désactivation…) -> je le relis tout de suite ; s'il n'existe plus, je suis déconnecté
  useEffect(() => onLive((topics) => {
    if (topics.some((t) => t.t === 'session')) refreshSession().then((d) => setUser(d.user)).catch(() => { setToken(null); setUser(null) })
  }), [])

  // renouvellement proactif de l'access token
  useEffect(() => {
    if (!user) return
    const t = setInterval(() => refreshSession().catch(() => {}), 10 * 60 * 1000)
    return () => clearInterval(t)
  }, [user])

  const start = (d) => { setToken(d.access_token); setUser(d.user) }
  // renvoie { mfaToken } si un second facteur est demandé
  const login = async (username, password) => {
    const d = await api.post('/auth/login', { username, password })
    if (d.mfa_required) return { mfaToken: d.mfa_token }
    start(d)
    return {}
  }
  const loginMfa = async (mfaToken, code) => start(await api.post('/auth/login/2fa', { mfaToken, code }))
  const reload = () => refreshSession().then((d) => setUser(d.user))

  return <Ctx.Provider value={{ user, ready, login, loginMfa, logout, reload }}>{children}</Ctx.Provider>
}
