import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../context/AuthProvider'
import { ChatWidget } from './ChatWidget'
import { getProfilePhoto, onProfilePhotoChange } from '../lib/avatar'
import ctrackLogo from '../assets/ctrack-logo.png'

/** Fleet-management tools (fleet-wide summary, driver roster) that stay
 * support_agent-only -- a customer's nav/routing is scoped to their own
 * route-planning and alerts workflow. See RequireRole for the matching
 * route guard in App.tsx. */
const CUSTOMER_NAV_LINKS = [
  { to: '/routes', label: 'Routes' },
  { to: '/tracking', label: 'Live Tracking' },
  { to: '/alerts', label: 'Alerts' },
]

/** Routes (plan-a-route + today's list) is customer-only: a support_agent's
 * view of active routes and their drivers is already covered by Live
 * Tracking, so the redundant list -- and the plan-on-a-customer's-behalf
 * form that came with it -- are deliberately not offered here. See
 * RequireRole for the matching route guard in App.tsx. */
const SUPPORT_AGENT_NAV_LINKS = [
  { to: '/overview', label: 'Overview' },
  { to: '/tracking', label: 'Live Tracking' },
  { to: '/drivers', label: 'Drivers' },
  { to: '/alerts', label: 'Alerts' },
]

/** Top-level app shell: nav bar + routed page content via <Outlet />. */
export function Layout() {
  const { user, logout } = useAuth()
  const navLinks = user?.role === 'support_agent' ? SUPPORT_AGENT_NAV_LINKS : CUSTOMER_NAV_LINKS
  const [isMenuOpen, setIsMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const [photo, setPhoto] = useState<string | null>(() => (user ? getProfilePhoto(user) : null))

  // Keeps the avatar in sync with a photo change made on the Profile page,
  // without a full reload -- see lib/avatar.ts's module docs.
  useEffect(() => {
    setPhoto(user ? getProfilePhoto(user) : null)
    return onProfilePhotoChange(() => setPhoto(user ? getProfilePhoto(user) : null))
  }, [user])

  // The role/log-out menu closes on an outside click or Escape -- it does
  // not live inside a <details>/<dialog>, so this listener is how it
  // behaves like a native dropdown.
  useEffect(() => {
    if (!isMenuOpen) return

    function handlePointerDown(event: MouseEvent) {
      if (!menuRef.current?.contains(event.target as Node)) {
        setIsMenuOpen(false)
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setIsMenuOpen(false)
      }
    }

    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [isMenuOpen])

  return (
    <div className="min-h-screen bg-brand-dark dark:bg-brand-darker-blue">
      <nav className="flex items-center justify-between px-6 py-4 bg-brand-dark dark:bg-brand-darker-blue shadow-card">
        <div className="flex items-center gap-6">
          <div className="relative" ref={menuRef}>
            <button
              type="button"
              onClick={() => setIsMenuOpen((open) => !open)}
              aria-haspopup="menu"
              aria-expanded={isMenuOpen}
              className="flex items-center rounded focus:outline-none focus:ring-2 focus:ring-brand-teal"
            >
              <img src={ctrackLogo} alt="Ctrack" className="h-8 w-auto" />
            </button>
            {isMenuOpen && user && (
              <div
                role="menu"
                className="absolute left-0 top-full mt-2 w-48 rounded-lg bg-brand-darker-blue shadow-card py-2 z-10"
              >
                <p className="px-4 py-1.5 text-xs font-medium text-white/50 capitalize">
                  {user.role.replace('_', ' ')}
                </p>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setIsMenuOpen(false)
                    logout()
                  }}
                  className="w-full text-left px-4 py-1.5 text-sm font-medium text-white/80 hover:text-accent-orange"
                >
                  Log out
                </button>
              </div>
            )}
          </div>
          <div className="flex items-center gap-4">
            {navLinks.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                className={({ isActive }) =>
                  `text-sm font-medium ${
                    isActive
                      ? 'text-brand-teal'
                      : 'text-white/80 hover:text-brand-teal'
                  }`
                }
              >
                {link.label}
              </NavLink>
            ))}
          </div>
        </div>
        <Link
          to="/profile"
          aria-label="Profile"
          className="flex h-9 w-9 items-center justify-center overflow-hidden rounded-full bg-brand-darker-blue text-white/80 hover:text-brand-teal focus:outline-none focus:ring-2 focus:ring-brand-teal"
        >
          {photo ? (
            <img src={photo} alt="Your profile" className="h-full w-full object-cover" />
          ) : (
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-5 w-5"
              aria-hidden="true"
            >
              <circle cx="12" cy="8" r="3" />
              <path d="M5 21a7 7 0 0 1 14 0" />
            </svg>
          )}
        </Link>
      </nav>
      <main className="p-6 font-body">
        <Outlet />
      </main>
      <ChatWidget />
    </div>
  )
}
