/**
 * Route selector + daily route/risk tracking. Lets a customer (or, on
 * behalf of a customer, a support_agent) plan a route -- reusing the
 * route-planning feature's `POST /route-plan` and `RouteMap` -- and lists
 * everyone's routes planned today via `GET /route-plans`, with a "Mark
 * complete" action (`PATCH /route-plans/{id}/complete`). A support_agent
 * additionally sees every customer's routes and can filter by customer ID
 * -- see docs/superpowers/specs/2026-09-18-route-selector-and-daily-tracking-design.md.
 */
import { useEffect, useState, type FormEvent } from 'react'
import { apiGet, apiPatch, apiPost } from '../lib/apiClient'
import { useAuth } from '../context/AuthProvider'
import { RouteMap } from '../components/RouteMap'
import type { RoutePlanListItem, RoutePlanResult, RouteWarning } from '../types/routePlan'

const STATUS_BADGE: Record<'active' | 'completed', string> = {
  active: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-300',
  completed: 'bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300',
}

/** Label + color-coded badge classes per warning severity, same
 * badge-styling convention as Alerts.tsx's ticket-status badges.
 * `severity` isn't a strict union on the backend, so anything not
 * recognized falls back to the neutral/gray style below. */
const WARNING_SEVERITY_BADGE: Record<string, string> = {
  high: 'bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300',
  moderate: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-300',
  low: 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300',
}

function warningBadgeClass(severity: string): string {
  return WARNING_SEVERITY_BADGE[severity] ?? WARNING_SEVERITY_BADGE.low
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(undefined, { hour: '2-digit', minute: '2-digit' })
}

/** e.g. "23.4 km · 38 min" -- either half renders as an em dash when its
 * value is null (a route saved with `unavailable=true` has no distance/
 * duration since ORS/geocoding never returned one). */
function formatDistanceDuration(distanceKm: number | null, durationMin: number | null): string {
  const distance = distanceKm === null ? '—' : `${distanceKm.toFixed(1)} km`
  const duration = durationMin === null ? '—' : `${Math.round(durationMin)} min`
  return `${distance} · ${duration}`
}

/** "now" + `durationMin`, formatted as a local clock time -- the estimated
 * arrival time for a route just planned (assumes departure now, since this
 * app has no separate "planned departure time" concept). Em dash if the
 * duration is unknown (an `unavailable` plan). */
function formatEstimatedArrival(durationMin: number | null): string {
  if (durationMin === null) return '—'
  const eta = new Date(Date.now() + durationMin * 60_000)
  return eta.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

/** Per-warning severity badge + description, shown under a route's
 * summary line so a manager can see whether a route's warnings were
 * actually severe, not just how many there were. */
function RouteWarningsList({ warnings }: { warnings: RouteWarning[] }) {
  if (warnings.length === 0) {
    return <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">No warnings</p>
  }
  return (
    <ul className="mt-1 space-y-1">
      {warnings.map((warning, index) => (
        <li
          key={`${warning.type}-${index}`}
          className="flex items-start gap-2 text-xs text-gray-600 dark:text-gray-300"
        >
          <span
            className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase ${warningBadgeClass(warning.severity)}`}
          >
            {warning.severity}
          </span>
          <span>{warning.description}</span>
        </li>
      ))}
    </ul>
  )
}

export default function RoutesPage() {
  const { user } = useAuth()
  const isSupportAgent = user?.role === 'support_agent'

  const [origin, setOrigin] = useState('')
  const [destination, setDestination] = useState('')
  const [planCustomerId, setPlanCustomerId] = useState('')
  const [planResult, setPlanResult] = useState<RoutePlanResult | null>(null)
  const [planError, setPlanError] = useState<string | null>(null)
  const [isPlanning, setIsPlanning] = useState(false)

  const [routes, setRoutes] = useState<RoutePlanListItem[]>([])
  const [listError, setListError] = useState<string | null>(null)
  const [isLoadingList, setIsLoadingList] = useState(true)
  const [filterCustomerId, setFilterCustomerId] = useState('')

  async function loadRoutes() {
    setIsLoadingList(true)
    setListError(null)
    try {
      const query =
        isSupportAgent && filterCustomerId
          ? `?customer_id=${encodeURIComponent(filterCustomerId)}`
          : ''
      const data = await apiGet<RoutePlanListItem[]>(`/route-plans${query}`)
      setRoutes(data)
    } catch (err) {
      setListError(err instanceof Error ? err.message : 'Failed to load routes.')
    } finally {
      setIsLoadingList(false)
    }
  }

  useEffect(() => {
    void loadRoutes()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterCustomerId])

  async function handlePlanRoute(event: FormEvent) {
    event.preventDefault()
    setPlanError(null)

    if (isSupportAgent && !planCustomerId) {
      setPlanError('Customer ID is required for a support agent to save a route plan.')
      return
    }

    setIsPlanning(true)
    setPlanResult(null)
    try {
      const body: Record<string, unknown> = { origin, destination }
      if (isSupportAgent) {
        body.customer_id = Number(planCustomerId)
      }
      const result = await apiPost<RoutePlanResult>('/route-plan', body)
      setPlanResult(result)
      await loadRoutes()
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : 'Failed to plan route.')
    } finally {
      setIsPlanning(false)
    }
  }

  async function handleMarkComplete(routePlanId: number) {
    try {
      await apiPatch(`/route-plans/${routePlanId}/complete`, {})
      await loadRoutes()
    } catch (err) {
      setListError(err instanceof Error ? err.message : 'Failed to mark route complete.')
    }
  }

  const active = routes.filter((route) => route.status === 'active')
  const completed = routes.filter((route) => route.status === 'completed')

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Routes</h1>

      <form onSubmit={handlePlanRoute} className="mt-4 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="route-origin" className="block text-sm text-gray-600 dark:text-gray-300">
            Origin
          </label>
          <input
            id="route-origin"
            value={origin}
            onChange={(event) => setOrigin(event.target.value)}
            required
            className="mt-1 rounded border border-gray-300 px-2 py-1 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
          />
        </div>
        <div>
          <label
            htmlFor="route-destination"
            className="block text-sm text-gray-600 dark:text-gray-300"
          >
            Destination
          </label>
          <input
            id="route-destination"
            value={destination}
            onChange={(event) => setDestination(event.target.value)}
            required
            className="mt-1 rounded border border-gray-300 px-2 py-1 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
          />
        </div>
        {isSupportAgent && (
          <div>
            <label
              htmlFor="route-plan-customer-id"
              className="block text-sm text-gray-600 dark:text-gray-300"
            >
              Customer ID
            </label>
            <input
              id="route-plan-customer-id"
              value={planCustomerId}
              onChange={(event) => setPlanCustomerId(event.target.value)}
              className="mt-1 w-24 rounded border border-gray-300 px-2 py-1 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
            />
          </div>
        )}
        <button
          type="submit"
          disabled={isPlanning}
          className="rounded bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {isPlanning ? 'Planning…' : 'Plan route'}
        </button>
      </form>

      {planError && (
        <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
          {planError}
        </p>
      )}

      {planResult && (
        <div className="mt-4">
          {planResult.unavailable ? (
            <p className="text-sm text-yellow-700 dark:text-yellow-400">
              {planResult.unavailable_message ?? 'Route data is currently unavailable.'}
            </p>
          ) : (
            <>
              <p data-testid="plan-result-stats" className="mb-2 text-sm text-gray-700 dark:text-gray-300">
                {formatDistanceDuration(planResult.distance_km, planResult.duration_min)}
                {' · Estimated arrival '}
                <span className="font-semibold text-gray-900 dark:text-white">
                  {formatEstimatedArrival(planResult.duration_min)}
                </span>
              </p>
              <RouteMap routePlan={planResult} />
            </>
          )}
        </div>
      )}

      {isSupportAgent && (
        <div className="mt-6">
          <label
            htmlFor="route-filter-customer-id"
            className="block text-sm text-gray-600 dark:text-gray-300"
          >
            Filter by customer ID
          </label>
          <input
            id="route-filter-customer-id"
            value={filterCustomerId}
            onChange={(event) => setFilterCustomerId(event.target.value)}
            placeholder="All customers"
            className="mt-1 w-40 rounded border border-gray-300 px-2 py-1 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
          />
        </div>
      )}

      <h2 className="mt-8 text-lg font-semibold text-gray-900 dark:text-white">
        Today's routes
      </h2>
      {isLoadingList ? (
        <p className="mt-2 text-gray-600 dark:text-gray-300">Loading routes…</p>
      ) : listError ? (
        <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
          Failed to load routes: {listError}
        </p>
      ) : (
        <>
          <h3 className="mt-4 text-sm font-semibold uppercase text-gray-500 dark:text-gray-400">
            Active ({active.length})
          </h3>
          {active.length === 0 ? (
            <p className="mt-1 text-gray-600 dark:text-gray-300">No active routes today.</p>
          ) : (
            <ul className="mt-2 divide-y divide-gray-200 dark:divide-gray-700 overflow-hidden rounded-lg bg-white dark:bg-gray-800 shadow">
              {active.map((route) => (
                <li
                  key={route.route_plan_id}
                  data-testid={`route-${route.route_plan_id}`}
                  className="px-4 py-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-gray-900 dark:text-white">
                      {route.origin_label} → {route.destination_label}
                    </span>
                    <span
                      data-testid={`route-status-${route.route_plan_id}`}
                      className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-medium ${STATUS_BADGE[route.status]}`}
                    >
                      {route.status}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    {formatDistanceDuration(route.distance_km, route.duration_min)}
                    {' · '}
                    {formatTime(route.created_at)}
                  </p>
                  <RouteWarningsList warnings={route.warnings} />
                  <button
                    type="button"
                    onClick={() => void handleMarkComplete(route.route_plan_id)}
                    className="mt-2 rounded border border-gray-300 px-3 py-1 text-xs font-medium text-gray-700 dark:border-gray-600 dark:text-gray-200"
                  >
                    Mark complete
                  </button>
                </li>
              ))}
            </ul>
          )}

          <h3 className="mt-6 text-sm font-semibold uppercase text-gray-500 dark:text-gray-400">
            Completed ({completed.length})
          </h3>
          {completed.length === 0 ? (
            <p className="mt-1 text-gray-600 dark:text-gray-300">No completed routes today.</p>
          ) : (
            <ul className="mt-2 divide-y divide-gray-200 dark:divide-gray-700 overflow-hidden rounded-lg bg-white dark:bg-gray-800 shadow">
              {completed.map((route) => (
                <li
                  key={route.route_plan_id}
                  data-testid={`route-${route.route_plan_id}`}
                  className="px-4 py-3"
                >
                  <span className="text-sm font-medium text-gray-900 dark:text-white">
                    {route.origin_label} → {route.destination_label}
                  </span>
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    {formatDistanceDuration(route.distance_km, route.duration_min)}
                    {' · completed '}
                    {formatTime(route.completed_at)}
                  </p>
                  <RouteWarningsList warnings={route.warnings} />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
