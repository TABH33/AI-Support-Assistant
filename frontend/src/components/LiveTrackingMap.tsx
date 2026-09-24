/**
 * Multi-route live tracking map: draws every actively-tracked route's line
 * plus a marker at its simulated current position, using the same
 * `react-leaflet` primitives and OpenStreetMap tiles `RouteMap.tsx` uses.
 *
 * Coordinate order, the one easy thing to get wrong here: values in
 * `geometry.coordinates` are GeoJSON order ([longitude, latitude]) and are
 * swapped below before reaching Leaflet, whose LatLngExpression is
 * [latitude, longitude] -- while `current_lat`/`current_lon` already
 * arrive from the backend as separate named fields and are used as-is.
 *
 * Positions are simulated server-side from each plan's created_at,
 * duration_min and geometry; no GPS hardware exists anywhere in this app
 * and nothing is stored per position. See
 * docs/superpowers/specs/2026-09-24-live-tracking-design.md.
 */
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import type { LiveRoutePlan } from '../types/routePlan'

interface LiveTrackingMapProps {
  routes: LiveRoutePlan[]
}

/** One color per tracked route, cycled by index, so two routes sharing a
 * stretch of road stay tellable apart. Deliberately not keyed off severity
 * or status -- every route drawn here is active and available. */
const ROUTE_COLORS = ['#4f46e5', '#0891b2', '#c2410c', '#15803d', '#a21caf']

function routeColor(index: number): string {
  return ROUTE_COLORS[index % ROUTE_COLORS.length]
}

export function LiveTrackingMap({ routes }: LiveTrackingMapProps) {
  const drawable = routes
    .map((route) => ({
      route,
      positions: (route.geometry?.coordinates ?? []).map(
        ([lon, lat]) => [lat, lon] as [number, number]
      ),
    }))
    .filter((entry) => entry.positions.length > 0)

  if (drawable.length === 0) {
    return (
      <div
        data-testid="live-tracking-map-empty"
        className="flex h-96 w-full items-center justify-center rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800"
      >
        <p className="text-sm text-gray-600 dark:text-gray-300">
          No routes are being tracked right now.
        </p>
      </div>
    )
  }

  const center: [number, number] = [drawable[0].route.current_lat, drawable[0].route.current_lon]

  return (
    <div
      data-testid="live-tracking-map"
      className="h-96 w-full overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700"
    >
      <MapContainer center={center} zoom={11} style={{ height: '100%', width: '100%' }} scrollWheelZoom={false}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {drawable.map(({ route, positions }, index) => (
          <Polyline
            key={`line-${route.route_plan_id}`}
            positions={positions}
            pathOptions={{ color: routeColor(index), weight: 4 }}
          />
        ))}
        {drawable.map(({ route }, index) => (
          <CircleMarker
            key={`position-${route.route_plan_id}`}
            center={[route.current_lat, route.current_lon]}
            radius={9}
            pathOptions={{
              color: routeColor(index),
              fillColor: routeColor(index),
              fillOpacity: 0.9,
            }}
          >
            <Popup>
              <strong>{route.driver_name ?? 'Unassigned'}</strong>
              <p>
                {route.origin_label} → {route.destination_label}
              </p>
              <p>{Math.round(route.progress_percent)}% complete</p>
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  )
}

export default LiveTrackingMap
