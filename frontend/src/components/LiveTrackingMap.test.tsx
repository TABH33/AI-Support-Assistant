import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { LiveTrackingMap } from './LiveTrackingMap'
import type { LiveRoutePlan } from '../types/routePlan'

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
    <div data-testid="position-marker" data-lat={center[0]} data-lon={center[1]} data-color={pathOptions.color}>
      {children}
    </div>
  ),
  Popup: ({ children }: { children: React.ReactNode }) => <div data-testid="position-popup">{children}</div>,
}))

const ASSIGNED_ROUTE: LiveRoutePlan = {
  route_plan_id: 1,
  customer_id: 100,
  origin_label: 'Sydney CBD',
  destination_label: 'Parramatta',
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
  warnings: [],
  unavailable: false,
  unavailable_reason: null,
  status: 'active',
  created_at: '2026-09-24T09:00:00Z',
  completed_at: null,
  driver_id: 11,
  driver_name: 'Alice Driver',
  current_lat: -33.84,
  current_lon: 151.15,
  progress_percent: 50,
  eta: '2026-09-24T09:38:00Z',
}

const UNASSIGNED_ROUTE: LiveRoutePlan = {
  ...ASSIGNED_ROUTE,
  route_plan_id: 2,
  destination_label: 'Bondi Beach',
  driver_id: null,
  driver_name: null,
  current_lat: -33.87,
  current_lon: 151.2,
  progress_percent: 10,
}

describe('LiveTrackingMap', () => {
  it('draws one polyline per tracked route', () => {
    render(<LiveTrackingMap routes={[ASSIGNED_ROUTE, UNASSIGNED_ROUTE]} />)

    expect(screen.getAllByTestId('route-polyline')).toHaveLength(2)
  })

  it('draws the polyline with one point per geometry coordinate', () => {
    render(<LiveTrackingMap routes={[ASSIGNED_ROUTE]} />)

    expect(screen.getByTestId('route-polyline')).toHaveAttribute('data-point-count', '3')
  })

  it('places a marker at each route current position in Leaflet lat/lon order', () => {
    render(<LiveTrackingMap routes={[ASSIGNED_ROUTE]} />)

    const marker = screen.getByTestId('position-marker')
    expect(marker).toHaveAttribute('data-lat', '-33.84')
    expect(marker).toHaveAttribute('data-lon', '151.15')
  })

  it('color-codes each tracked route differently', () => {
    render(<LiveTrackingMap routes={[ASSIGNED_ROUTE, UNASSIGNED_ROUTE]} />)

    const colors = screen.getAllByTestId('position-marker').map((m) => m.getAttribute('data-color'))
    expect(new Set(colors).size).toBe(2)
  })

  it('names the assigned driver in the marker popup', () => {
    render(<LiveTrackingMap routes={[ASSIGNED_ROUTE]} />)

    expect(screen.getByText('Alice Driver')).toBeInTheDocument()
    expect(screen.getByText('Sydney CBD → Parramatta')).toBeInTheDocument()
  })

  it('labels an unassigned route as Unassigned in its popup', () => {
    render(<LiveTrackingMap routes={[UNASSIGNED_ROUTE]} />)

    expect(screen.getByText('Unassigned')).toBeInTheDocument()
  })

  it('skips a route whose geometry has no coordinates', () => {
    const empty: LiveRoutePlan = {
      ...ASSIGNED_ROUTE,
      route_plan_id: 3,
      geometry: { type: 'LineString', coordinates: [] },
    }

    render(<LiveTrackingMap routes={[ASSIGNED_ROUTE, empty]} />)

    expect(screen.getAllByTestId('route-polyline')).toHaveLength(1)
  })

  it('renders an empty state instead of a map when nothing is tracked', () => {
    render(<LiveTrackingMap routes={[]} />)

    expect(screen.queryByTestId('map-container')).not.toBeInTheDocument()
    expect(screen.getByTestId('live-tracking-map-empty')).toBeInTheDocument()
  })
})
