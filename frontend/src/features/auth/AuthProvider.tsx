/**
 * Session state for the whole app.
 *
 * `permissions` here is a *mirror* of what the backend decided. It is used
 * only to choose what to render — hiding a button the user cannot use is a
 * courtesy, not a control. Every request is authorised again server-side, and
 * the authorization tests call the API directly to prove it.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import {
  api,
  clearTokens,
  getRefreshToken,
  setSessionExpiredHandler,
  setTokens,
} from '@/lib/api'
import type { CurrentUser, TokenResponse } from '@/lib/types'

export type AuthStatus = 'initialising' | 'authenticated' | 'anonymous'

interface AuthValue {
  status: AuthStatus
  user: CurrentUser | null
  /** Resolved permission codes for the active membership. */
  permissions: Set<string>
  /** True when the signed-in user holds the permission. */
  can: (permission: string) => boolean
  canAny: (...permissions: string[]) => boolean
  signIn: (input: {
    email: string
    password: string
    organizationId?: string
  }) => Promise<void>
  signOut: () => Promise<void>
  switchOrganization: (organizationId: string) => Promise<void>
  refreshUser: () => Promise<void>
  /** Set when the session ended while the app was open. */
  expiredNotice: boolean
  dismissExpiredNotice: () => void
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [status, setStatus] = useState<AuthStatus>('initialising')
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [expiredNotice, setExpiredNotice] = useState(false)

  const loadUser = useCallback(async () => {
    const me = await api.get<CurrentUser>('/auth/me')
    setUser(me)
    setStatus('authenticated')
  }, [])

  // On first load, a refresh token in sessionStorage means a reload rather
  // than a fresh visit: exchange it rather than asking for the password again.
  useEffect(() => {
    let cancelled = false
    const bootstrap = async () => {
      const refresh = getRefreshToken()
      if (!refresh) {
        setStatus('anonymous')
        return
      }
      try {
        const tokens = await api.post<TokenResponse>(
          '/auth/refresh',
          { refresh_token: refresh },
          { skipAuth: true },
        )
        if (cancelled) return
        setTokens(tokens.access_token, tokens.refresh_token)
        await loadUser()
      } catch {
        if (cancelled) return
        clearTokens()
        setStatus('anonymous')
      }
    }
    void bootstrap()
    return () => {
      cancelled = true
    }
  }, [loadUser])

  // The client calls this when a refresh fails mid-session.
  useEffect(() => {
    setSessionExpiredHandler(() => {
      setUser(null)
      setStatus('anonymous')
      setExpiredNotice(true)
      queryClient.clear()
      navigate('/', { replace: true })
    })
  }, [navigate, queryClient])

  const signIn = useCallback(
    async ({
      email,
      password,
      organizationId,
    }: {
      email: string
      password: string
      organizationId?: string
    }) => {
      const tokens = await api.post<TokenResponse>(
        '/auth/login',
        {
          email,
          password,
          ...(organizationId ? { organization_id: organizationId } : {}),
        },
        { skipAuth: true },
      )
      setTokens(tokens.access_token, tokens.refresh_token)
      setExpiredNotice(false)
      await loadUser()
      // Start at the dashboard rather than wherever the previous session was.
      // A different person may now be signed in, and the old route may be one
      // they have no permission to see.
      navigate('/', { replace: true })
    },
    [loadUser, navigate],
  )

  const signOut = useCallback(async () => {
    const refresh = getRefreshToken()
    if (refresh) {
      // Tell the backend so the session is revoked, not merely forgotten.
      try {
        await api.post<void>('/auth/logout', { refresh_token: refresh })
      } catch {
        /* the local session is cleared regardless */
      }
    }
    clearTokens()
    setUser(null)
    setStatus('anonymous')
    queryClient.clear()
    navigate('/', { replace: true })
  }, [navigate, queryClient])

  const switchOrganization = useCallback(
    async (organizationId: string) => {
      const refresh = getRefreshToken()
      if (!refresh) throw new Error('No active session')
      const tokens = await api.post<TokenResponse>('/auth/switch-organization', {
        organization_id: organizationId,
        refresh_token: refresh,
      })
      setTokens(tokens.access_token, tokens.refresh_token)
      // Cached data belongs to the previous organization; drop all of it.
      queryClient.clear()
      await loadUser()
      navigate('/', { replace: true })
    },
    [loadUser, navigate, queryClient],
  )

  const permissions = useMemo(
    () => new Set(user?.permissions ?? []),
    [user?.permissions],
  )

  const value = useMemo<AuthValue>(
    () => ({
      status,
      user,
      permissions,
      can: (permission) => permissions.has(permission),
      canAny: (...candidates) => candidates.some((p) => permissions.has(p)),
      signIn,
      signOut,
      switchOrganization,
      refreshUser: loadUser,
      expiredNotice,
      dismissExpiredNotice: () => setExpiredNotice(false),
    }),
    [
      status,
      user,
      permissions,
      signIn,
      signOut,
      switchOrganization,
      loadUser,
      expiredNotice,
    ],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}
