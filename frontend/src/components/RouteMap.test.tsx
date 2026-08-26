import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { RouteMap } from './RouteMap'
import type { RoutePlanResult } from '../types/routePlan'

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="map-container">{children}</div>
  ),
  TileLayer: () => <div data-testid="tile-layer" />,
  Polyline: ({ positions }: { positions: [number, number][] }) => (
    <div data-testid="route-polyline" data-point-count={positions.length} />
  ),
  CircleMarker: ({
    center,
    pathOptions,
    children,
  }: {
    center: [number, number]
    pathOptions: { color: string }
    children: React.ReactNode
  }) => (
    <div data-testid="warning-marker" data-lat={center[0]} data-lon={center[1]} data-color={pathOptions.color}>
      {children}
    </div>
  ),
  Popup: ({ children }: { children: React.ReactNode }) => <div data-testid="warning-popup">{children}</div>,
}))

const ROUTE_PLAN: RoutePlanResult = {
  distance_km: 23.4,
  duration_min: 38.2,
  geometry: {
    type: 'LineString',
    coordinates: [
      [151.2093, -33.8688],
      [151.15, -33.84],
      [151.0011, -33.815],
    ],
  },
  warnings: [
    {
      location: { lat: -33.84, lon: 151.15 },
      distance_from_origin_km: 12.0,
      type: 'risk_zone',
      severity: 'high',
      description: '4 harsh-braking events recorded near this point.',
    },
    {
      location: { lat: -33.8, lon: 151.0 },
      distance_from_origin_km: 20.0,
      type: 'weather',
      severity: 'moderate',
      description: 'Heavy rain forecast near this segment.',
    },
  ],
  unavailable: false,
}

describe('RouteMap', () => {
  it('renders the polyline with one point per geometry coordinate', () => {
    render(<RouteMap routePlan={ROUTE_PLAN} />)

    expect(screen.getByTestId('route-polyline')).toHaveAttribute('data-point-count', '3')
  })

  it('renders one marker per warning', () => {
    render(<RouteMap routePlan={ROUTE_PLAN} />)

    expect(screen.getAllByTestId('warning-marker')).toHaveLength(2)
  })

  it('color-codes risk_zone and weather markers differently', () => {
    render(<RouteMap routePlan={ROUTE_PLAN} />)

    const markers = screen.getAllByTestId('warning-marker')
    const colors = markers.map((marker) => marker.getAttribute('data-color'))
    expect(new Set(colors).size).toBe(2)
  })

  it('shows the warning description in its popup', () => {
    render(<RouteMap routePlan={ROUTE_PLAN} />)

    expect(screen.getByText('4 harsh-braking events recorded near this point.')).toBeInTheDocument()
  })

  it('renders nothing when geometry is null', () => {
    const { container } = render(
      <RouteMap
        routePlan={{ distance_km: null, duration_min: null, geometry: null, warnings: [], unavailable: true }}
      />
    )

    expect(container).toBeEmptyDOMElement()
  })
})
