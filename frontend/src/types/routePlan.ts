/**
 * TypeScript mirrors of `RoutePlanResponse`/`WarningOut` in
 * `backend/app/api/route_plan.py` (also reused, unchanged, as
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
   *
   * Optional here because the chat path already renders this text through
   * `ChatResponse.answer`, so no component reads these yet -- they are
   * mirrored to keep this file's "keep in sync with the backend schema"
   * contract honest.
   */
  unavailable_reason?: 'geocoding_failed' | 'service_unavailable' | null
  unavailable_message?: string | null
}
