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
}
