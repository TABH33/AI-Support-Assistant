import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import Overview from './Overview'
import type { Driver, Trip, Vehicle } from '../types/telematics'

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

const vehicles: Vehicle[] = [
  {
    vehicle_id: 10,
    customer_id: 100,
    registration_number: 'ABC-123',
    make: 'Ford',
    model: 'Transit',
    year: 2021,
    created_at: '2024-01-01T00:00:00Z',
  },
]

const trips: Trip[] = [
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
    driver_id: 2,
    vehicle_id: 10,
    start_time: '2024-03-06T10:00:00Z',
    end_time: null,
    start_location: 'Depot B',
    end_location: null,
    distance_km: null,
    created_at: '2024-03-06T10:00:00Z',
  },
]

/** Routes the mocked `fetch` calls made by `apiGet` to seeded-style fixture data by URL. */
function mockFleetFetch() {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string) => {
    if (url.includes('/drivers')) {
      return { ok: true, status: 200, json: async () => drivers }
    }
    if (url.includes('/vehicles')) {
      return { ok: true, status: 200, json: async () => vehicles }
    }
    if (url.includes('/trips')) {
      return { ok: true, status: 200, json: async () => trips }
    }
    throw new Error(`Unexpected fetch to ${url}`)
  })
}

describe('Overview', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows a loading state before the fleet data arrives', () => {
    ;(fetch as unknown as Mock).mockImplementation(() => new Promise(() => {}))

    render(<Overview />)

    expect(screen.getByText(/loading fleet data/i)).toBeInTheDocument()
  })

  it('renders summary counts and the trip list from the real API response shape', async () => {
    mockFleetFetch()

    render(<Overview />)

    await waitFor(() => {
      expect(screen.queryByText(/loading fleet data/i)).not.toBeInTheDocument()
    })

    // Summary panel.
    expect(screen.getByText('Drivers').nextSibling).toHaveTextContent('2')
    expect(screen.getByText('Vehicles').nextSibling).toHaveTextContent('1')
    expect(screen.getByText('Total distance').nextSibling).toHaveTextContent('42.5 km')

    // Trip list -- specific values pulled through from driver/vehicle joins.
    expect(screen.getByText('Jane Cooper')).toBeInTheDocument()
    expect(screen.getByText('Robert Fox')).toBeInTheDocument()
    expect(screen.getAllByText('Ford Transit (ABC-123)')).toHaveLength(2)
    // "42.5 km" appears twice: once in the summary panel's total distance
    // (there's only one trip with a distance in this fixture) and once in
    // that trip's row.
    expect(screen.getAllByText('42.5 km')).toHaveLength(2)

    // Completed trip's end time renders; the still-in-progress trip's
    // missing end_time renders as an em dash rather than blank/"null".
    const rows = screen.getAllByRole('row')
    // rows[0] is the header row.
    expect(rows[1]).toHaveTextContent('Jane Cooper')
    expect(rows[1]).toHaveTextContent('42.5 km')
    expect(rows[2]).toHaveTextContent('Robert Fox')
    expect(rows[2]).toHaveTextContent('—')

    // Requests hit the real Task 7 endpoints.
    const calledUrls = (fetch as unknown as Mock).mock.calls.map(([url]) => url)
    expect(calledUrls.some((url: string) => url.endsWith('/drivers'))).toBe(true)
    expect(calledUrls.some((url: string) => url.endsWith('/vehicles'))).toBe(true)
    expect(calledUrls.some((url: string) => url.endsWith('/trips'))).toBe(true)
  })

  it('shows an error message instead of a blank screen when the API call fails', async () => {
    ;(fetch as unknown as Mock).mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: async () => ({ detail: 'Database unavailable' }),
    })

    render(<Overview />)

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Database unavailable')
    })
    expect(screen.queryByText(/loading fleet data/i)).not.toBeInTheDocument()
  })
})
