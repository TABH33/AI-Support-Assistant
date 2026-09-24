/**
 * TypeScript mirrors of `RoutePlanResponse`/`WarningOut`/`RoutePlanListItem`
 * in `backend/app/api/route_plan.py` (also reused, unchanged, as
 * `ChatResponse.route_plan`'s shape in `backend/app/api/chat.py`). Field
 * names/nullability were copied directly from those Pydantic models, not
 * guessed -- keep in sync if the backend schema changes.
 */
export interface RouteGeometry {
  type: string
  coordinates: [number, number][]
}

export interface RouteWarning {
  location: { lat: number; lon: number }
  distance_from_origin_km: number
  type: 'weather' | 'risk_zone'
  severity: string
  description: string
}

export interface RoutePlanResult {
  /** Optional here only so pre-existing test fixtures that predate this
   * field keep compiling -- every real response includes it. */
  route_plan_id?: number
  distance_km: number | null
  duration_min: number | null
  geometry: RouteGeometry | null
  warnings: RouteWarning[]
  unavailable: boolean
  /**
   * Populated only when `unavailable` is true (final-review Fix 5).
   * `unavailable_reason` is a stable machine-readable code --
   * `'geocoding_failed'` (the place name could not be resolved; retrying
   * will not help, the user must fix the spelling) or
   * `'service_unavailable'` (a downstream outage; retrying shortly is the
   * right advice). `unavailable_message` is the matching display text.
   */
  unavailable_reason?: 'geocoding_failed' | 'service_unavailable' | null
  unavailable_message?: string | null
}

/** `GET /route-plans` list item -- a saved RoutePlan row, mirroring
 * `RoutePlanListItem` in `backend/app/api/route_plan.py`. */
export interface RoutePlanListItem {
  route_plan_id: number
  customer_id: number
  origin_label: string
  destination_label: string
  distance_km: number | null
  duration_min: number | null
  geometry: RouteGeometry | null
  warnings: RouteWarning[]
  unavailable: boolean
  unavailable_reason: string | null
  status: 'active' | 'completed'
  created_at: string
  completed_at: string | null
}

/** `GET /route-plans/live` row -- mirrors `LiveRoutePlan` in
 * `backend/app/api/route_plan.py`: everything `GET /route-plans` returns
 * plus the assigned driver and the simulated current position. The
 * position fields are recomputed by the backend on every request and are
 * never stored, so two polls a few seconds apart legitimately return
 * different `current_lat`/`current_lon` for the same `route_plan_id`.
 * `eta` is fixed at `created_at + duration_min` and does not move. */
export interface LiveRoutePlan extends RoutePlanListItem {
  driver_id: number | null
  /** Joined from the driver's `full_name`; null when unassigned. */
  driver_name: string | null
  current_lat: number
  current_lon: number
  /** 0-100 inclusive, already clamped server-side. */
  progress_percent: number
  eta: string
}
