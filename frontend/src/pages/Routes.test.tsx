import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import { AuthProvider, TOKEN_STORAGE_KEY } from '../context/AuthProvider'
import { makeFakeJwt } from '../test-support/jwt'
import RoutesPage from './Routes'
import type { RoutePlanListItem } from '../types/routePlan'

vi.mock('../components/RouteMap', () => ({
  RouteMap: () => <div data-testid="mock-route-map" />,
}))

function renderPage() {
  return render(
    <AuthProvider>
      <RoutesPage />
    </AuthProvider>
  )
}

function loginAsCustomer() {
  const token = makeFakeJwt({
    sub: '100',
    role: 'customer',
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 3600,
  })
  localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

function loginAsSupportAgent() {
  const token = makeFakeJwt({
    sub: '7',
    role: 'support_agent',
    access_level: 'admin',
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 3600,
  })
  localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

const activeRoute: RoutePlanListItem = {
  route_plan_id: 1,
  customer_id: 100,
  origin_label: 'Sydney CBD',
  destination_label: 'Parramatta',
  distance_km: 23.4,
  duration_min: 38.2,
  geometry: { type: 'LineString', coordinates: [] },
  warnings: [
    {
      location: { lat: -33.8, lon: 151.0 },
      distance_from_origin_km: 10,
      type: 'risk_zone',
      severity: 'high',
      description: 'x',
    },
  ],
  unavailable: false,
  unavailable_reason: null,
  status: 'active',
  created_at: '2026-09-18T08:00:00Z',
  completed_at: null,
}

const completedRoute: RoutePlanListItem = {
  ...activeRoute,
  route_plan_id: 2,
  origin_label: 'Sydney CBD',
  destination_label: 'Bondi Beach',
  status: 'completed',
  warnings: [],
  completed_at: '2026-09-18T09:00:00Z',
}

function mockRoutesFetch(routes: RoutePlanListItem[] = [activeRoute, completedRoute]) {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string, options?: RequestInit) => {
    if (options?.method === 'PATCH' && url.includes('/complete')) {
      return { ok: true, status: 200, json: async () => ({ route_plan_id: 1, status: 'completed', completed_at: '2026-09-18T10:00:00Z' }) }
    }
    if (options?.method === 'POST' && url.includes('/route-plan')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          route_plan_id: 3,
          distance_km: 5.0,
          duration_min: 10.0,
          geometry: { type: 'LineString', coordinates: [] },
          warnings: [],
          unavailable: false,
        }),
      }
    }
    if (url.includes('/route-plans')) {
      return { ok: true, status: 200, json: async () => routes }
    }
    throw new Error(`Unexpected fetch to ${url}`)
  })
}

describe('RoutesPage', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('lists active and completed routes fetched from GET /route-plans', async () => {
    loginAsCustomer()
    mockRoutesFetch()

    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('route-1')).toHaveTextContent('Sydney CBD → Parramatta')
    })
    expect(screen.getByTestId('route-2')).toHaveTextContent('Sydney CBD → Bondi Beach')
    expect(screen.getByText('Active (1)')).toBeInTheDocument()
    expect(screen.getByText('Completed (1)')).toBeInTheDocument()

    // distance/duration and per-warning severity/description now render
    // (final review Fix 4) -- activeRoute carries a high-severity warning.
    expect(screen.getByTestId('route-1')).toHaveTextContent('23.4 km · 38 min')
    expect(screen.getByTestId('route-1')).toHaveTextContent('high')
    expect(screen.getByTestId('route-1')).toHaveTextContent('x')
    expect(screen.getByTestId('route-2')).toHaveTextContent('No warnings')
  })

  it('marks an active route complete via the Mark complete button', async () => {
    loginAsCustomer()
    mockRoutesFetch()

    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('route-1')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /mark complete/i }))

    await waitFor(() => {
      const calledUrls = (fetch as unknown as Mock).mock.calls.map(([url, options]) => ({
        url,
        method: options?.method,
      }))
      expect(
        calledUrls.some((c) => c.url.includes('/route-plans/1/complete') && c.method === 'PATCH')
      ).toBe(true)
    })
  })

  it('plans a new route via the form and re-fetches the list', async () => {
    loginAsCustomer()
    mockRoutesFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('No active routes today.')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText(/origin/i), { target: { value: 'Sydney CBD' } })
    fireEvent.change(screen.getByLabelText(/destination/i), { target: { value: 'Bondi Beach' } })
    fireEvent.click(screen.getByRole('button', { name: /plan route/i }))

    await waitFor(() => {
      expect(screen.getByTestId('mock-route-map')).toBeInTheDocument()
    })

    const postCall = (fetch as unknown as Mock).mock.calls.find(
      ([url, options]) => options?.method === 'POST' && (url as string).includes('/route-plan')
    )
    expect(postCall).toBeTruthy()
  })

  it('shows a customer-ID filter only for a support_agent caller', async () => {
    loginAsSupportAgent()
    mockRoutesFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByLabelText(/filter by customer id/i)).toBeInTheDocument()
    })
  })

  it('does not show the customer-ID filter for a customer caller', async () => {
    loginAsCustomer()
    mockRoutesFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('No active routes today.')).toBeInTheDocument()
    })
    expect(screen.queryByLabelText(/filter by customer id/i)).not.toBeInTheDocument()
  })
})
