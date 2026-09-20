import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import Drivers from './Drivers'
import type { Driver } from '../types/telematics'

function renderDrivers() {
  return render(
    <MemoryRouter>
      <Drivers />
    </MemoryRouter>
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

function mockDriverFetch() {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string) => {
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

  it('renders a link to each driver\'s own page', async () => {
    mockDriverFetch()

    renderDrivers()

    await waitFor(() => {
      expect(screen.queryByText(/loading drivers/i)).not.toBeInTheDocument()
    })

    const janeLink = screen.getByRole('link', { name: /Jane Cooper/ })
    const robertLink = screen.getByRole('link', { name: /Robert Fox/ })
    expect(janeLink).toHaveAttribute('href', '/drivers/1')
    expect(robertLink).toHaveAttribute('href', '/drivers/2')
    expect(janeLink).toHaveTextContent('LN-001')
    expect(robertLink).toHaveTextContent('LN-002')
  })

  it('shows an empty-state message when there are no drivers', async () => {
    ;(fetch as unknown as Mock).mockImplementation(async () => ({
      ok: true,
      status: 200,
      json: async () => [],
    }))

    renderDrivers()

    await waitFor(() => {
      expect(screen.getByText('No drivers recorded yet.')).toBeInTheDocument()
    })
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
