/**
 * Renders a computed route (route-planning + warnings feature) as a
 * Leaflet polyline over OpenStreetMap tiles, with a color-coded marker per
 * flagged warning (weather = blue, risk_zone = red). Coordinates in
 * `RoutePlanResult.geometry.coordinates` are GeoJSON order ([longitude,
 * latitude]) -- Leaflet's LatLngExpression is [latitude, longitude], so
 * every coordinate is swapped below before being handed to a Leaflet
 * component.
 */
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import type { RoutePlanResult, RouteWarning } from '../types/routePlan'

interface RouteMapProps {
  routePlan: RoutePlanResult
}

const WARNING_COLORS: Record<RouteWarning['type'], string> = {
  weather: '#2563eb',
  risk_zone: '#dc2626',
}

export function RouteMap({ routePlan }: RouteMapProps) {
  if (!routePlan.geometry) {
    return null
  }

  const positions: [number, number][] = routePlan.geometry.coordinates.map(([lon, lat]) => [lat, lon])
  const center = positions[Math.floor(positions.length / 2)] ?? positions[0]

  return (
    <div
      data-testid="route-map"
      className="h-64 w-full overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700"
    >
      <MapContainer center={center} zoom={11} style={{ height: '100%', width: '100%' }} scrollWheelZoom={false}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <Polyline positions={positions} pathOptions={{ color: '#4f46e5', weight: 4 }} />
        {routePlan.warnings.map((warning, index) => (
          <CircleMarker
            key={`${warning.type}-${index}`}
            center={[warning.location.lat, warning.location.lon]}
            radius={8}
            pathOptions={{
              color: WARNING_COLORS[warning.type],
              fillColor: WARNING_COLORS[warning.type],
              fillOpacity: 0.8,
            }}
          >
            <Popup>
              <strong>{warning.type === 'weather' ? 'Weather warning' : 'Risk zone'}</strong> ({warning.severity})
              <p>{warning.description}</p>
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  )
}

export default RouteMap
