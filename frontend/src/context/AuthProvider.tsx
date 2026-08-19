/**
 * Auth context: owns the JWT issued by `POST /auth/login` (see
 * `backend/app/api/auth.py`), the derived current-user info, and
 * login/logout actions.
 *
 * Backend contract mirrored here (`LoginResponse` in `backend/app/api/auth.py`):
 *   { access_token: string, token_type: "bearer", role: "customer" | "support_agent", access_level: string | null }
 * The JWT payload itself (`backend/app/auth/security.py::create_access_token`)
 * carries `sub` (user id), `role`, `access_level?`, `iat`, `exp`.
 *
 * Token storage: localStorage (see TOKEN_STORAGE_KEY below), not memory-only.
 *   - Tradeoff accepted: a token in localStorage is readable by any script
 *     that achieves XSS on this origin (unlike an httpOnly cookie, or
 *     memory-only storage which at least limits the blast radius to the
 *     current tab's lifetime). For this POC there is no XSS-hardening
 *     requirement in the plan and the alternative (memory-only) throws the
 *     user back to /login on every page refresh, which is a poor
 *     experience during manual QA of a dashboard app. If this ever ships
 *     past POC, prefer an httpOnly cookie set by the backend instead.
 *
 * No `/auth/me` endpoint exists yet (checked `backend/app/api/*.py`), and
 * the plan doesn't call for adding one in this task. Rather than pull in a
 * JWT library, we base64url-decode the JWT payload ourselves purely to
 * read `sub`/`role`/`access_level`/`exp` for UI purposes (e.g. which nav
 * links to show, when to treat the session as expired). This decode is
 * UNVERIFIED (no signature check) -- that's fine because we only ever
 * decode a token our own backend just issued to us (at login) or one we
 * previously stored ourselves (on reload); we never accept a token from
 * an untrusted source. No security-sensitive decision is made on the
 * client from this decode -- the backend independently verifies the
 * signature + expiry (`decode_access_token`) on every authenticated
 * request, which is the real security boundary.
 */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { apiPost, setAuthToken } from '../lib/apiClient'

export type UserRole = 'customer' | 'support_agent'

export interface AuthUser {
  id: string
  role: UserRole
  accessLevel: string | null
}

interface LoginResponse {
  access_token: string
  token_type: string
  role: UserRole
  access_level: string | null
}

interface JwtPayload {
  sub: string
  role: UserRole
  access_level?: string
  iat: number
  exp: number
}

export interface AuthContextValue {
  user: AuthUser | null
  token: string | null
  isAuthenticated: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
}

export const TOKEN_STORAGE_KEY = 'telematics_auth_token'

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

/** Decodes a JWT payload without verifying its signature. See module docs above. */
function decodeJwtPayload(token: string): JwtPayload | null {
  try {
    const segments = token.split('.')
    if (segments.length !== 3) return null
    const base64 = segments[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4)
    const json = atob(padded)
    return JSON.parse(json) as JwtPayload
  } catch {
    return null
  }
}

/** Returns the AuthUser encoded in `token`, or null if it's malformed or expired. */
function userFromToken(token: string): AuthUser | null {
  const payload = decodeJwtPayload(token)
  if (!payload || typeof payload.exp !== 'number') return null
  if (payload.exp * 1000 <= Date.now()) return null
  return {
    id: payload.sub,
    role: payload.role,
    accessLevel: payload.access_level ?? null,
  }
}

/**
 * Reads and validates a persisted token from localStorage, synchronously,
 * for use as a `useState` lazy initializer. Runs once per mount (React
 * only invokes a lazy initializer on the first render, even under
 * StrictMode's double-invoke -- both calls are idempotent here since
 * they're pure reads plus, at worst, a `removeItem` of the same key).
 * Doing this synchronously during initial state -- rather than in a
 * `useEffect` that runs after the first paint -- means there's no
 * "loading" flash where `ProtectedRoute` doesn't yet know whether a
 * session exists, and it keeps `apiClient`'s module-level auth header in
 * sync with the session before any component has a chance to fire a
 * request.
 */
function readPersistedSession(): { token: string | null; user: AuthUser | null } {
  const stored = localStorage.getItem(TOKEN_STORAGE_KEY)
  if (!stored) return { token: null, user: null }

  const rehydratedUser = userFromToken(stored)
  if (!rehydratedUser) {
    localStorage.removeItem(TOKEN_STORAGE_KEY)
    return { token: null, user: null }
  }

  setAuthToken(stored)
  return { token: stored, user: rehydratedUser }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => readPersistedSession().token)
  const [user, setUser] = useState<AuthUser | null>(() => readPersistedSession().user)

  const login = useCallback(async (email: string, password: string) => {
    // Let apiPost's rejection propagate to the caller on failure (e.g. 401
    // "Incorrect email or password") -- we deliberately do not catch it
    // here so the error is surfaced to the UI, not silently swallowed.
    const response = await apiPost<LoginResponse>('/auth/login', { email, password })

    const nextUser = userFromToken(response.access_token) ?? {
      id: '',
      role: response.role,
      accessLevel: response.access_level ?? null,
    }

    localStorage.setItem(TOKEN_STORAGE_KEY, response.access_token)
    setAuthToken(response.access_token)
    setToken(response.access_token)
    setUser(nextUser)
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_STORAGE_KEY)
    setAuthToken(null)
    setToken(null)
    setUser(null)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      token,
      isAuthenticated: user !== null && token !== null,
      login,
      logout,
    }),
    [user, token, login, logout]
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return ctx
}
