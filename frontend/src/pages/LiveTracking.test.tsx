import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import { AuthProvider, TOKEN_STORAGE_KEY } from '../context/AuthProvider'
import { makeFakeJwt } from '../test-support/jwt'
import LiveTracking from './LiveTracking'
import type { LiveRoutePlan } from '../types/routePlan'

vi.mock('../components/LiveTrackingMap', () => ({
  LiveTrackingMap: ({ routes }: { routes: LiveRoutePlan[] }) => (
    <div data-testid="mock-live-tracking-map" data-route-count={routes.length} />
  ),
}))

function renderPage() {
  return render(
    <AuthProvider>
      <LiveTracking />
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

const assignedRoute: LiveRoutePlan = {
  route_plan_id: 1,
  customer_id: 100,
  origin_label: 'Sydney CBD',
  destination_label: 'Parramatta',
  distance_km: 23.4,
  duration_min: 38.2,
  geometry: { type: 'LineString', coordinates: [[151.2093, -33.8688], [151.0011, -33.815]] },
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

const unassignedRoute: LiveRoutePlan = {
  ...assignedRoute,
  route_plan_id: 2,
  destination_label: 'Bondi Beach',
  driver_id: null,
  driver_name: null,
  progress_percent: 10,
}

function mockLiveFetch(routes: LiveRoutePlan[] = [assignedRoute, unassignedRoute]) {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string) => {
    if (url.includes('/route-plans/live')) {
      return { ok: true, status: 200, json: async () => routes }
    }
    throw new Error(`Unexpected fetch to ${url}`)
  })
}

describe('LiveTracking', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    localStorage.clear()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('lists one row per tracked route fetched from GET /route-plans/live', async () => {
    loginAsCustomer()
    mockLiveFetch()

    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('live-route-1')).toHaveTextContent('Sydney CBD → Parramatta')
    })
    expect(screen.getByTestId('live-route-1')).toHaveTextContent('Alice Driver')
    expect(screen.getByTestId('live-route-2')).toHaveTextContent('Unassigned')
    expect(screen.getByText('Tracking 2 route(s)')).toBeInTheDocument()
  })

  it('shows each route progress as a labelled progress bar', async () => {
    loginAsCustomer()
    mockLiveFetch()

    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('live-progress-1')).toBeInTheDocument()
    })
    expect(screen.getByTestId('live-progress-1')).toHaveAttribute('aria-valuenow', '50')
    expect(screen.getByTestId('live-progress-2')).toHaveAttribute('aria-valuenow', '10')
  })

  it('passes the tracked routes to the map', async () => {
    loginAsCustomer()
    mockLiveFetch()

    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('mock-live-tracking-map')).toHaveAttribute('data-route-count', '2')
    })
  })

  it('shows an empty state when nothing is being tracked', async () => {
    loginAsCustomer()
    mockLiveFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('No routes are being tracked right now.')).toBeInTheDocument()
    })
  })

  it('shows a customer-ID filter only for a support_agent caller', async () => {
    loginAsSupportAgent()
    mockLiveFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByLabelText(/filter by customer id/i)).toBeInTheDocument()
    })
  })

  it('does not show the customer-ID filter for a customer caller', async () => {
    loginAsCustomer()
    mockLiveFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('No routes are being tracked right now.')).toBeInTheDocument()
    })
    expect(screen.queryByLabelText(/filter by customer id/i)).not.toBeInTheDocument()
  })

  it('refetches with the customer_id a support agent typed', async () => {
    loginAsSupportAgent()
    mockLiveFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByLabelText(/filter by customer id/i)).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText(/filter by customer id/i), { target: { value: '200' } })

    await waitFor(() => {
      const urls = (fetch as unknown as Mock).mock.calls.map(([url]) => url as string)
      expect(urls.some((url) => url.includes('/route-plans/live?customer_id=200'))).toBe(true)
    })
  })

  it('polls the live endpoint again after the poll interval elapses', async () => {
    loginAsCustomer()
    mockLiveFetch()
    // `shouldAdvanceTime` keeps the real clock running underneath the fake
    // timers, so Testing Library's own `waitFor` polling still works while
    // the page's setInterval is driven forward explicitly below.
    vi.useFakeTimers({ shouldAdvanceTime: true })

    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('live-route-1')).toBeInTheDocument()
    })
    const callsAfterFirstLoad = (fetch as unknown as Mock).mock.calls.length

    await vi.advanceTimersByTimeAsync(5000)

    expect((fetch as unknown as Mock).mock.calls.length).toBeGreaterThan(callsAfterFirstLoad)
  })
})
