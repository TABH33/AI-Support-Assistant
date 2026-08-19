import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../context/AuthProvider'

const NAV_LINKS = [
  { to: '/overview', label: 'Overview' },
  { to: '/routes', label: 'Routes' },
  { to: '/drivers', label: 'Drivers' },
  { to: '/alerts', label: 'Alerts' },
]

/** Top-level app shell: nav bar + routed page content via <Outlet />. */
export function Layout() {
  const { user, logout } = useAuth()

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <nav className="flex items-center justify-between px-6 py-4 bg-white dark:bg-gray-800 shadow">
        <div className="flex items-center gap-6">
          <span className="font-bold text-lg text-gray-900 dark:text-white">
            Telematics AI Assistant
          </span>
          <div className="flex items-center gap-4">
            {NAV_LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                className={({ isActive }) =>
                  `text-sm font-medium ${
                    isActive
                      ? 'text-indigo-600 dark:text-indigo-400'
                      : 'text-gray-600 dark:text-gray-300 hover:text-indigo-600 dark:hover:text-indigo-400'
                  }`
                }
              >
                {link.label}
              </NavLink>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-4">
          {user && (
            <span className="text-sm text-gray-500 dark:text-gray-400">{user.role}</span>
          )}
          <button
            type="button"
            onClick={logout}
            className="text-sm font-medium text-gray-600 dark:text-gray-300 hover:text-red-600 dark:hover:text-red-400"
          >
            Log out
          </button>
        </div>
      </nav>
      <main className="p-6">
        <Outlet />
      </main>
    </div>
  )
}
