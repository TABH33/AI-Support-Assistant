import type { ReactElement } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthProvider'

/**
 * Wraps a subtree that requires an authenticated session. `AuthProvider`
 * rehydrates any persisted session synchronously (see
 * `readPersistedSession` in `AuthProvider.tsx`), so `isAuthenticated` is
 * already accurate on the very first render -- no separate loading state
 * needed here. Renders children when authenticated; otherwise redirects to
 * `/login`, remembering the attempted location so `Login` can send the
 * user back where they meant to go.
 */
export function ProtectedRoute({ children }: { children: ReactElement }): ReactElement {
  const { isAuthenticated } = useAuth()
  const location = useLocation()

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return children
}
