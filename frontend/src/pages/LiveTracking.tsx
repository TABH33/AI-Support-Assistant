/**
 * Live Tracking page (`/tracking`): polls `GET /route-plans/live` every
 * POLL_INTERVAL_MS and shows every actively-tracked route on one map
 * (`LiveTrackingMap`) beside a list giving the driver (or "Unassigned"),
 * origin -> destination, a progress bar, and the ETA.
 *
 * Positions are simulated server-side from each plan's created_at,
 * duration_min and geometry -- there is no GPS or device hardware anywhere
 * in this app, and nothing is stored per position. See
 * docs/superpowers/specs/2026-09-24-live-tracking-design.md.
 *
 * Polling with a plain `setInterval` (cleared on unmount) rather than a
 * WebSocket: every other page in this app fetches the same way, and push
 * delivery is explicitly out of scope for the approved design.
 *
 * A support_agent gets the same customer-ID filter input the Routes page
 * already has; a customer sees only their own routes (enforced by the
 * backend, which ignores any customer_id a customer sends).
 */
import { useEffect, useState } from 'react'
import { apiGet } from '../lib/apiClient'
import { useAuth } from '../context/AuthProvider'
import { LiveTrackingMap } from '../components/LiveTrackingMap'
import type { LiveRoutePlan } from '../types/routePlan'

const POLL_INTERVAL_MS = 5000

/** Local clock time for an ISO timestamp, em dash if it's unparseable --
 * same formatting convention as Routes.tsx's `formatTime`. */
function formatEta(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export default function LiveTracking() {
  const { user } = useAuth()
  const isSupportAgent = user?.role === 'support_agent'

  const [routes, setRoutes] = useState<LiveRoutePlan[]>([])
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [filterCustomerId, setFilterCustomerId] = useState('')

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const query =
          isSupportAgent && filterCustomerId
            ? `?customer_id=${encodeURIComponent(filterCustomerId)}`
            : ''
        const data = await apiGet<LiveRoutePlan[]>(`/route-plans/live${query}`)
        if (!cancelled) {
          setRoutes(data)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load live routes.')
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    }

    void load()
    const timer = setInterval(() => {
      void load()
    }, POLL_INTERVAL_MS)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [isSupportAgent, filterCustomerId])

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Live Tracking</h1>
      <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
        Simulated positions, refreshed every {POLL_INTERVAL_MS / 1000} seconds.
      </p>

      {isSupportAgent && (
        <div className="mt-4">
          <label
            htmlFor="live-filter-customer-id"
            className="block text-sm text-gray-600 dark:text-gray-300"
          >
            Filter by customer ID
          </label>
          <input
            id="live-filter-customer-id"
            value={filterCustomerId}
            onChange={(event) => setFilterCustomerId(event.target.value)}
            placeholder="All customers"
            className="mt-1 w-40 rounded border border-gray-300 px-2 py-1 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
          />
        </div>
      )}

      {error && (
        <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
          Failed to load live routes: {error}
        </p>
      )}

      {isLoading ? (
        <p className="mt-4 text-gray-600 dark:text-gray-300">Loading live routes…</p>
      ) : (
        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <LiveTrackingMap routes={routes} />
          </div>

          <div>
            <h2 className="text-sm font-semibold uppercase text-gray-500 dark:text-gray-400">
              Tracking {routes.length} route(s)
            </h2>
            {routes.length === 0 ? (
              <p className="mt-2 text-gray-600 dark:text-gray-300">
                No routes are being tracked right now.
              </p>
            ) : (
              <ul className="mt-2 divide-y divide-gray-200 dark:divide-gray-700 overflow-hidden rounded-lg bg-white dark:bg-gray-800 shadow">
                {routes.map((route) => (
                  <li
                    key={route.route_plan_id}
                    data-testid={`live-route-${route.route_plan_id}`}
                    className="px-4 py-3"
                  >
                    <span className="text-sm font-medium text-gray-900 dark:text-white">
                      {route.driver_name ?? 'Unassigned'}
                    </span>
                    <p className="text-xs text-gray-600 dark:text-gray-300">
                      {route.origin_label} → {route.destination_label}
                    </p>
                    <div
                      data-testid={`live-progress-${route.route_plan_id}`}
                      role="progressbar"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={Math.round(route.progress_percent)}
                      aria-label={`${route.origin_label} to ${route.destination_label} progress`}
                      className="mt-2 h-2 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700"
                    >
                      <div
                        className="h-full rounded-full bg-indigo-600"
                        style={{ width: `${Math.round(route.progress_percent)}%` }}
                      />
                    </div>
                    <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                      {Math.round(route.progress_percent)}% · ETA {formatEta(route.eta)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
