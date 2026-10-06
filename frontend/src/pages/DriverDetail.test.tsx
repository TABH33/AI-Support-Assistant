import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import DriverDetail from './DriverDetail'
import { SelectionProvider } from '../context/SelectionContext'
import type { Driver, DrivingEvent, Trip } from '../types/telematics'

/** DriverDetail reads driverId from the URL and writes SelectionContext -- provide both. */
function renderDriverDetail(driverId = '1') {
  return render(
    <SelectionProvider>
      <MemoryRouter initialEntries={[`/drivers/${driverId}`]}>
        <Routes>
          <Route path="/drivers/:driverId" element={<DriverDetail />} />
        </Routes>
      </MemoryRouter>
    </SelectionProvider>
  )
}

const driversById: Record<number, Driver> = {
  1: {
    driver_id: 1,
    customer_id: 100,
    full_name: 'Jane Cooper',
    license_number: 'LN-001',
    email: 'jane@example.com',
    phone_number: null,
    created_at: '2024-01-01T00:00:00Z',
  },
  2: {
    driver_id: 2,
    customer_id: 100,
    full_name: 'Robert Fox',
    license_number: 'LN-002',
    email: null,
    phone_number: null,
    created_at: '2024-01-01T00:00:00Z',
  },
}

// Driver 1 has two trips; driver 2 has none.
const tripsByDriver: Record<number, Trip[]> = {
  1: [
    {
      trip_id: 500,
      driver_id: 1,
      vehicle_id: 10,
      start_time: '2024-03-05T08:00:00Z',
      end_time: '2024-03-05T09:30:00Z',
      start_location: 'Depot A',
      end_location: 'Depot B',
      distance_km: 42.5,
      created_at: '2024-03-05T09:30:00Z',
    },
    {
      trip_id: 501,
      driver_id: 1,
      vehicle_id: 10,
      start_time: '2024-03-06T10:00:00Z',
      end_time: '2024-03-06T11:00:00Z',
      start_location: 'Depot B',
      end_location: 'Depot A',
      distance_km: 20,
      created_at: '2024-03-06T11:00:00Z',
    },
  ],
  2: [],
}

// Trip 500 has 3 speeding + 1 harsh_braking; trip 501 has 2 idling + 1 route_deviation + 1 more speeding.
const eventsByTrip: Record<number, DrivingEvent[]> = {
  500: [
    { driving_event_id: 1, trip_id: 500, event_type: 'speeding', event_time: '2024-03-05T08:10:00Z', location: null, details: null, created_at: '2024-03-05T08:10:00Z' },
    { driving_event_id: 2, trip_id: 500, event_type: 'speeding', event_time: '2024-03-05T08:15:00Z', location: null, details: null, created_at: '2024-03-05T08:15:00Z' },
    { driving_event_id: 3, trip_id: 500, event_type: 'speeding', event_time: '2024-03-05T08:20:00Z', location: null, details: null, created_at: '2024-03-05T08:20:00Z' },
    { driving_event_id: 4, trip_id: 500, event_type: 'harsh_braking', event_time: '2024-03-05T08:25:00Z', location: null, details: null, created_at: '2024-03-05T08:25:00Z' },
  ],
  501: [
    { driving_event_id: 5, trip_id: 501, event_type: 'idling', event_time: '2024-03-06T10:10:00Z', location: null, details: null, created_at: '2024-03-06T10:10:00Z' },
    { driving_event_id: 6, trip_id: 501, event_type: 'idling', event_time: '2024-03-06T10:15:00Z', location: null, details: null, created_at: '2024-03-06T10:15:00Z' },
    { driving_event_id: 7, trip_id: 501, event_type: 'route_deviation', event_time: '2024-03-06T10:20:00Z', location: null, details: null, created_at: '2024-03-06T10:20:00Z' },
    { driving_event_id: 8, trip_id: 501, event_type: 'speeding', event_time: '2024-03-06T10:25:00Z', location: null, details: null, created_at: '2024-03-06T10:25:00Z' },
  ],
}

function mockDriverDetailFetch() {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string) => {
    if (url.includes('/trips?driver_id=')) {
      const driverId = Number(new URL(url).searchParams.get('driver_id'))
      return { ok: true, status: 200, json: async () => tripsByDriver[driverId] ?? [] }
    }
    const tripEventsMatch = url.match(/\/trips\/(\d+)\/events/)
    if (tripEventsMatch) {
      const tripId = Number(tripEventsMatch[1])
      return { ok: true, status: 200, json: async () => eventsByTrip[tripId] ?? [] }
    }
    const driverMatch = url.match(/\/drivers\/(\d+)$/)
    if (driverMatch) {
      const driverId = Number(driverMatch[1])
      return { ok: true, status: 200, json: async () => driversById[driverId] }
    }
    throw new Error(`Unexpected fetch to ${url}`)
  })
}

describe('DriverDetail', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows a back link to the drivers list', async () => {
    mockDriverDetailFetch()

    renderDriverDetail('1')

    await waitFor(() => {
      expect(screen.getByRole('link', { name: /back to drivers/i })).toHaveAttribute(
        'href',
        '/drivers'
      )
    })
  })

  it('renders the driver name and license number', async () => {
    mockDriverDetailFetch()

    renderDriverDetail('1')

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Jane Cooper' })).toBeInTheDocument()
    })
    expect(screen.getByText('License LN-001')).toBeInTheDocument()
  })

  it('renders event-type badges with the exact counts and colors from mocked event data', async () => {
    mockDriverDetailFetch()

    renderDriverDetail('1')

    // Trip 500 has 3 speeding + 1 harsh_braking; trip 501 has 2 idling + 1
    // route_deviation + 1 speeding. Combined: speeding=4, harsh_braking=1,
    // idling=2, route_deviation=1.
    await waitFor(() => {
      expect(screen.getByTestId('event-badge-speeding')).toBeInTheDocument()
    })

    const speedingBadge = screen.getByTestId('event-badge-speeding')
    expect(speedingBadge).toHaveTextContent('Speeding')
    expect(speedingBadge).toHaveTextContent('4')
    expect(speedingBadge.className).toContain('bg-status-danger-surface')

    const harshBrakingBadge = screen.getByTestId('event-badge-harsh_braking')
    expect(harshBrakingBadge).toHaveTextContent('1')

    const idlingBadge = screen.getByTestId('event-badge-idling')
    expect(idlingBadge).toHaveTextContent('2')

    const routeDeviationBadge = screen.getByTestId('event-badge-route_deviation')
    expect(routeDeviationBadge).toHaveTextContent('1')

    expect(screen.getByText(/Based on 2 trips/)).toBeInTheDocument()
  })

  it('shows zero-count badges for a driver with no trips', async () => {
    mockDriverDetailFetch()

    renderDriverDetail('2')

    await waitFor(() => {
      expect(screen.getByText(/Based on 0 trips/)).toBeInTheDocument()
    })

    expect(screen.getByTestId('event-badge-speeding')).toHaveTextContent('0')
    expect(screen.getByTestId('event-badge-harsh_braking')).toHaveTextContent('0')
    expect(screen.getByTestId('event-badge-idling')).toHaveTextContent('0')
    expect(screen.getByTestId('event-badge-route_deviation')).toHaveTextContent('0')
  })

  it('shows an error message instead of a blank screen when the driver fails to load', async () => {
    ;(fetch as unknown as Mock).mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: async () => ({ detail: 'Database unavailable' }),
    })

    renderDriverDetail('1')

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Database unavailable')
    })
  })
})
