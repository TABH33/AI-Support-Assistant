/**
 * Route selector + daily route/risk tracking, for a customer -- who IS the
 * driver logging in (see RequireRole in App.tsx, which keeps this page
 * customer-only, and the driver-login design in the brainstorming for this
 * feature). Reuses the route-planning feature's `POST /route-plan` and
 * `RouteMap`, and lists today's routes via `GET /route-plans`, with a
 * "Mark complete" action (`PATCH /route-plans/{id}/complete`).
 */
import { useEffect, useState, type FormEvent } from 'react'
import { apiGet, apiPatch, apiPost } from '../lib/apiClient'
import { RouteMap } from '../components/RouteMap'
import type { RoutePlanListItem, RoutePlanResult, RouteWarning } from '../types/routePlan'

const STATUS_BADGE: Record<'active' | 'completed', string> = {
  active: 'bg-status-warning-surface text-status-warning-text dark:bg-status-warning-text/30 dark:text-status-warning-surface',
  completed: 'bg-status-success-surface text-status-success-text dark:bg-status-success-text/30 dark:text-status-success-surface',
}

/** Label + color-coded badge classes per warning severity, same
 * badge-styling convention as Alerts.tsx's ticket-status badges.
 * `severity` isn't a strict union on the backend, so anything not
 * recognized falls back to the neutral/gray style below. */
const WARNING_SEVERITY_BADGE: Record<string, string> = {
  high: 'bg-status-danger-surface text-status-danger-text dark:bg-status-danger-text/30 dark:text-status-danger-surface',
  moderate: 'bg-status-warning-surface text-status-warning-text dark:bg-status-warning-text/30 dark:text-status-warning-surface',
  low: 'bg-surface-page text-muted dark:bg-white/10 dark:text-white/70',
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
    return <p className="mt-1 text-xs text-muted dark:text-white/60">No warnings</p>
  }
  return (
    <ul className="mt-1 space-y-1">
      {warnings.map((warning, index) => (
        <li
          key={`${warning.type}-${index}`}
          className="flex items-start gap-2 text-xs text-muted dark:text-white/70"
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
  const [origin, setOrigin] = useState('')
  const [destination, setDestination] = useState('')
  const [planResult, setPlanResult] = useState<RoutePlanResult | null>(null)
  const [planError, setPlanError] = useState<string | null>(null)
  const [isPlanning, setIsPlanning] = useState(false)

  const [routes, setRoutes] = useState<RoutePlanListItem[]>([])
  const [listError, setListError] = useState<string | null>(null)
  const [isLoadingList, setIsLoadingList] = useState(true)

  async function loadRoutes() {
    setIsLoadingList(true)
    setListError(null)
    try {
      const data = await apiGet<RoutePlanListItem[]>('/route-plans')
      setRoutes(data)
    } catch (err) {
      setListError(err instanceof Error ? err.message : 'Failed to load routes.')
    } finally {
      setIsLoadingList(false)
    }
  }

  useEffect(() => {
    void loadRoutes()
  }, [])

  async function handlePlanRoute(event: FormEvent) {
    event.preventDefault()
    setPlanError(null)
    setIsPlanning(true)
    setPlanResult(null)
    try {
      const result = await apiPost<RoutePlanResult>('/route-plan', { origin, destination })
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
      <h1 className="font-heading text-2xl font-bold text-white">Routes</h1>

      <form onSubmit={handlePlanRoute} className="mt-4 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="route-origin" className="block text-sm text-white/70">
            Origin
          </label>
          <input
            id="route-origin"
            value={origin}
            onChange={(event) => setOrigin(event.target.value)}
            required
            className="mt-1 rounded border border-line bg-white px-2 py-1 text-brand-dark dark:border-white/20 dark:bg-white/10 dark:text-white"
          />
        </div>
        <div>
          <label htmlFor="route-destination" className="block text-sm text-white/70">
            Destination
          </label>
          <input
            id="route-destination"
            value={destination}
            onChange={(event) => setDestination(event.target.value)}
            required
            className="mt-1 rounded border border-line bg-white px-2 py-1 text-brand-dark dark:border-white/20 dark:bg-white/10 dark:text-white"
          />
        </div>
        <button
          type="submit"
          disabled={isPlanning}
          className="rounded bg-brand-teal px-4 py-1.5 text-sm font-medium text-white hover:bg-brand-darker-teal disabled:opacity-50"
        >
          {isPlanning ? 'Planning…' : 'Plan route'}
        </button>
      </form>

      {planError && (
        <p role="alert" className="mt-2 text-sm text-accent-pink dark:text-accent-pink">
          {planError}
        </p>
      )}

      {planResult && (
        <div className="mt-4">
          {planResult.unavailable ? (
            <p className="text-sm text-accent-yellow dark:text-accent-yellow">
              {planResult.unavailable_message ?? 'Route data is currently unavailable.'}
            </p>
          ) : (
            <>
              <p data-testid="plan-result-stats" className="mb-2 text-sm text-muted dark:text-white/70">
                {formatDistanceDuration(planResult.distance_km, planResult.duration_min)}
                {' · Estimated arrival '}
                <span className="font-semibold text-brand-dark dark:text-white">
                  {formatEstimatedArrival(planResult.duration_min)}
                </span>
              </p>
              <RouteMap routePlan={planResult} />
            </>
          )}
        </div>
      )}

      <h2 className="mt-8 font-heading text-lg font-semibold text-white">
        Today's routes
      </h2>
      {isLoadingList ? (
        <p className="mt-2 text-white/70">Loading routes…</p>
      ) : listError ? (
        <p role="alert" className="mt-2 text-sm text-accent-pink dark:text-accent-pink">
          Failed to load routes: {listError}
        </p>
      ) : (
        <>
          <h3 className="mt-4 text-sm font-semibold uppercase text-white/70">
            Active ({active.length})
          </h3>
          {active.length === 0 ? (
            <p className="mt-1 text-white/70">No active routes today.</p>
          ) : (
            <ul className="mt-2 divide-y divide-line dark:divide-white/10 overflow-hidden rounded-lg bg-surface-card dark:bg-brand-darker-blue shadow-card">
              {active.map((route) => (
                <li
                  key={route.route_plan_id}
                  data-testid={`route-${route.route_plan_id}`}
                  className="px-4 py-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-brand-dark dark:text-white">
                      {route.origin_label} → {route.destination_label}
                    </span>
                    <span
                      data-testid={`route-status-${route.route_plan_id}`}
                      className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-medium ${STATUS_BADGE[route.status]}`}
                    >
                      {route.status}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-muted dark:text-white/60">
                    {formatDistanceDuration(route.distance_km, route.duration_min)}
                    {' · '}
                    {formatTime(route.created_at)}
                  </p>
                  <RouteWarningsList warnings={route.warnings} />
                  <button
                    type="button"
                    onClick={() => void handleMarkComplete(route.route_plan_id)}
                    className="mt-2 rounded border border-line px-3 py-1 text-xs font-medium text-muted dark:border-white/20 dark:text-white/80"
                  >
                    Mark complete
                  </button>
                </li>
              ))}
            </ul>
          )}

          <h3 className="mt-6 text-sm font-semibold uppercase text-white/70">
            Completed ({completed.length})
          </h3>
          {completed.length === 0 ? (
            <p className="mt-1 text-white/70">No completed routes today.</p>
          ) : (
            <ul className="mt-2 divide-y divide-line dark:divide-white/10 overflow-hidden rounded-lg bg-surface-card dark:bg-brand-darker-blue shadow-card">
              {completed.map((route) => (
                <li
                  key={route.route_plan_id}
                  data-testid={`route-${route.route_plan_id}`}
                  className="px-4 py-3"
                >
                  <span className="text-sm font-medium text-brand-dark dark:text-white">
                    {route.origin_label} → {route.destination_label}
                  </span>
                  <p className="mt-1 text-xs text-muted dark:text-white/60">
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
