import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import Drivers from './Drivers'
import { SelectionProvider } from '../context/SelectionContext'
import type { Driver, DrivingEvent, Trip } from '../types/telematics'

/** Drivers reads/writes `SelectionContext` (Task 21) -- provide it in every render. */
function renderDrivers() {
  return render(
    <SelectionProvider>
      <Drivers />
    </SelectionProvider>
  )
}

const drivers: Driver[] = [
  {
    driver_id: 1,
    customer_id: 100,
    full_name: 'Jane Cooper',
    license_number: 'LN-001',
    email: 'jane@example.com',
    phone_number: null,
    created_at: '2024-01-01T00:00:00Z',
  },
  {
    driver_id: 2,
    customer_id: 100,
    full_name: 'Robert Fox',
    license_number: 'LN-002',
    email: null,
    phone_number: null,
    created_at: '2024-01-01T00:00:00Z',
  },
]

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
    {
      driving_event_id: 1,
      trip_id: 500,
      event_type: 'speeding',
      event_time: '2024-03-05T08:10:00Z',
      location: null,
      details: null,
      created_at: '2024-03-05T08:10:00Z',
    },
    {
      driving_event_id: 2,
      trip_id: 500,
      event_type: 'speeding',
      event_time: '2024-03-05T08:15:00Z',
      location: null,
      details: null,
      created_at: '2024-03-05T08:15:00Z',
    },
    {
      driving_event_id: 3,
      trip_id: 500,
      event_type: 'speeding',
      event_time: '2024-03-05T08:20:00Z',
      location: null,
      details: null,
      created_at: '2024-03-05T08:20:00Z',
    },
    {
      driving_event_id: 4,
      trip_id: 500,
      event_type: 'harsh_braking',
      event_time: '2024-03-05T08:25:00Z',
      location: null,
      details: null,
      created_at: '2024-03-05T08:25:00Z',
    },
  ],
  501: [
    {
      driving_event_id: 5,
      trip_id: 501,
      event_type: 'idling',
      event_time: '2024-03-06T10:10:00Z',
      location: null,
      details: null,
      created_at: '2024-03-06T10:10:00Z',
    },
    {
      driving_event_id: 6,
      trip_id: 501,
      event_type: 'idling',
      event_time: '2024-03-06T10:15:00Z',
      location: null,
      details: null,
      created_at: '2024-03-06T10:15:00Z',
    },
    {
      driving_event_id: 7,
      trip_id: 501,
      event_type: 'route_deviation',
      event_time: '2024-03-06T10:20:00Z',
      location: null,
      details: null,
      created_at: '2024-03-06T10:20:00Z',
    },
    {
      driving_event_id: 8,
      trip_id: 501,
      event_type: 'speeding',
      event_time: '2024-03-06T10:25:00Z',
      location: null,
      details: null,
      created_at: '2024-03-06T10:25:00Z',
    },
  ],
}

/** Routes mocked `fetch` calls made by `apiGet` to fixture data by URL, mirroring Overview.test.tsx. */
function mockDriverFetch() {
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
    if (url.includes('/drivers')) {
      return { ok: true, status: 200, json: async () => drivers }
    }
    throw new Error(`Unexpected fetch to ${url}`)
  })
}

describe('Drivers', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows a loading state before the driver list arrives', () => {
    ;(fetch as unknown as Mock).mockImplementation(() => new Promise(() => {}))

    renderDrivers()

    expect(screen.getByText(/loading drivers/i)).toBeInTheDocument()
  })

  it('renders the driver list and defaults to the first driver selected', async () => {
    mockDriverFetch()

    renderDrivers()

    await waitFor(() => {
      expect(screen.queryByText(/loading drivers/i)).not.toBeInTheDocument()
    })

    const janeButton = screen.getByRole('button', { name: /Jane Cooper/ })
    const robertButton = screen.getByRole('button', { name: /Robert Fox/ })
    expect(janeButton).toBeInTheDocument()
    expect(robertButton).toBeInTheDocument()
    expect(janeButton).toHaveAttribute('aria-pressed', 'true')
    expect(robertButton).toHaveAttribute('aria-pressed', 'false')
  })

  it('renders event-type badges with the exact counts and colors from mocked event data', async () => {
    mockDriverFetch()

    renderDrivers()

    // Driver 1 (Jane Cooper) is auto-selected: trip 500 has 3 speeding + 1
    // harsh_braking; trip 501 has 2 idling + 1 route_deviation + 1 speeding.
    // Combined counts: speeding=4, harsh_braking=1, idling=2, route_deviation=1.
    await waitFor(() => {
      expect(screen.getByTestId('event-badge-speeding')).toBeInTheDocument()
    })

    const speedingBadge = screen.getByTestId('event-badge-speeding')
    expect(speedingBadge).toHaveTextContent('Speeding')
    expect(speedingBadge).toHaveTextContent('4')
    expect(speedingBadge.className).toContain('bg-red-100')
    expect(speedingBadge.className).toContain('text-red-800')

    const harshBrakingBadge = screen.getByTestId('event-badge-harsh_braking')
    expect(harshBrakingBadge).toHaveTextContent('Harsh braking')
    expect(harshBrakingBadge).toHaveTextContent('1')
    expect(harshBrakingBadge.className).toContain('bg-orange-100')

    const idlingBadge = screen.getByTestId('event-badge-idling')
    expect(idlingBadge).toHaveTextContent('Idling')
    expect(idlingBadge).toHaveTextContent('2')
    expect(idlingBadge.className).toContain('bg-yellow-100')

    const routeDeviationBadge = screen.getByTestId('event-badge-route_deviation')
    expect(routeDeviationBadge).toHaveTextContent('Route deviation')
    expect(routeDeviationBadge).toHaveTextContent('1')
    expect(routeDeviationBadge.className).toContain('bg-purple-100')

    expect(screen.getByText(/Based on 2 trips/)).toBeInTheDocument()
  })

  it('shows zero-count badges for a driver with no trips', async () => {
    mockDriverFetch()

    renderDrivers()

    await waitFor(() => {
      expect(screen.queryByText(/loading drivers/i)).not.toBeInTheDocument()
    })

    const robertButton = screen.getByRole('button', { name: /Robert Fox/ })
    robertButton.click()

    await waitFor(() => {
      expect(robertButton).toHaveAttribute('aria-pressed', 'true')
    })
    await waitFor(() => {
      expect(screen.getByText(/Based on 0 trips/)).toBeInTheDocument()
    })

    expect(screen.getByTestId('event-badge-speeding')).toHaveTextContent('0')
    expect(screen.getByTestId('event-badge-harsh_braking')).toHaveTextContent('0')
    expect(screen.getByTestId('event-badge-idling')).toHaveTextContent('0')
    expect(screen.getByTestId('event-badge-route_deviation')).toHaveTextContent('0')
  })

  it('shows an error message instead of a blank screen when the driver list fails to load', async () => {
    ;(fetch as unknown as Mock).mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: async () => ({ detail: 'Database unavailable' }),
    })

    renderDrivers()

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Database unavailable')
    })
    expect(screen.queryByText(/loading drivers/i)).not.toBeInTheDocument()
  })
})
