import type { ReactElement } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../context/AuthProvider'
import type { UserRole } from '../context/AuthProvider'

/**
 * Wraps a subtree that requires a specific role, on top of `ProtectedRoute`'s
 * authentication check. Used for fleet-management-only pages (Overview,
 * Drivers) that a `customer` should never reach, including by typing the URL
 * directly. Redirects to `redirectTo` rather than rendering nothing, so a
 * customer lands on a real page instead of a blank one.
 */
export function RequireRole({
  role,
  redirectTo,
  children,
}: {
  role: UserRole
  redirectTo: string
  children: ReactElement
}): ReactElement {
  const { user } = useAuth()

  if (user?.role !== role) {
    return <Navigate to={redirectTo} replace />
  }

  return children
}
