import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import Alerts from './Alerts'
import type { Notification, SupportTicket } from '../types/telematics'

const tickets: SupportTicket[] = [
  {
    support_ticket_id: 900,
    chat_session_id: 1,
    customer_id: 100,
    device_id: 10,
    assigned_support_agent_id: null,
    ticket_status: 'open',
    priority: 'high',
    subject: 'Device offline',
    description: 'Device stopped reporting.',
    created_at: '2024-03-01T08:00:00Z',
    resolved_at: null,
  },
  {
    support_ticket_id: 901,
    chat_session_id: 2,
    customer_id: 100,
    device_id: 11,
    assigned_support_agent_id: 5,
    ticket_status: 'resolved',
    priority: 'low',
    subject: 'Battery low warning',
    description: 'Battery dropped below threshold.',
    created_at: '2024-02-15T10:00:00Z',
    resolved_at: '2024-02-16T09:00:00Z',
  },
]

const notifications: Notification[] = [
  {
    notification_id: 700,
    support_ticket_id: 900,
    customer_id: 100,
    notification_type: 'email',
    message: 'We are investigating your offline device.',
    sent_at: '2024-03-01T08:05:00Z',
    created_at: '2024-03-01T08:05:00Z',
  },
  {
    notification_id: 701,
    support_ticket_id: 901,
    customer_id: 100,
    notification_type: 'sms',
    message: 'Your battery issue has been resolved.',
    sent_at: null,
    created_at: '2024-02-16T09:01:00Z',
  },
]

/** Routes mocked `fetch` calls made by `apiGet` to fixture data by URL, mirroring Overview.test.tsx. */
function mockAlertsFetch() {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string) => {
    if (url.includes('/tickets')) {
      return { ok: true, status: 200, json: async () => tickets }
    }
    if (url.includes('/notifications')) {
      return { ok: true, status: 200, json: async () => notifications }
    }
    throw new Error(`Unexpected fetch to ${url}`)
  })
}

describe('Alerts', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows a loading state before the alerts data arrives', () => {
    ;(fetch as unknown as Mock).mockImplementation(() => new Promise(() => {}))

    render(<Alerts />)

    expect(screen.getByText(/loading alerts/i)).toBeInTheDocument()
  })

  it('renders ticket rows with correct subject, status badge, and priority', async () => {
    mockAlertsFetch()

    render(<Alerts />)

    await waitFor(() => {
      expect(screen.queryByText(/loading alerts/i)).not.toBeInTheDocument()
    })

    // "Device offline" and "Battery low warning" each appear twice: once as
    // the ticket's own subject cell, and once inside the corresponding
    // notification card (which looks the subject up by support_ticket_id).
    expect(screen.getAllByText('Device offline')).toHaveLength(2)
    expect(screen.getAllByText('Battery low warning')).toHaveLength(2)

    const openBadge = screen.getByTestId('ticket-status-900')
    expect(openBadge).toHaveTextContent('Open')
    expect(openBadge.className).toContain('bg-red-100')
    expect(openBadge.className).toContain('text-red-800')

    const resolvedBadge = screen.getByTestId('ticket-status-901')
    expect(resolvedBadge).toHaveTextContent('Resolved')
    expect(resolvedBadge.className).toContain('bg-green-100')
    expect(resolvedBadge.className).toContain('text-green-800')

    const rows = screen.getAllByRole('row')
    // rows[0] is the header row.
    expect(rows[1]).toHaveTextContent('Device offline')
    expect(rows[1]).toHaveTextContent('high')
    expect(rows[2]).toHaveTextContent('Battery low warning')
    expect(rows[2]).toHaveTextContent('low')
  })

  it('renders notifications with the linked ticket subject and message', async () => {
    mockAlertsFetch()

    render(<Alerts />)

    await waitFor(() => {
      expect(screen.queryByText(/loading alerts/i)).not.toBeInTheDocument()
    })

    const notif700 = screen.getByTestId('notification-700')
    expect(notif700).toHaveTextContent('Device offline')
    expect(notif700).toHaveTextContent('We are investigating your offline device.')
    expect(notif700).toHaveTextContent('email')

    const notif701 = screen.getByTestId('notification-701')
    expect(notif701).toHaveTextContent('Battery low warning')
    expect(notif701).toHaveTextContent('Your battery issue has been resolved.')
    expect(notif701).toHaveTextContent('Not yet sent')

    // Requests hit the real Task 20 endpoints.
    const calledUrls = (fetch as unknown as Mock).mock.calls.map(([url]) => url)
    expect(calledUrls.some((url: string) => url.endsWith('/tickets'))).toBe(true)
    expect(calledUrls.some((url: string) => url.endsWith('/notifications'))).toBe(true)
  })

  it('shows an error message instead of a blank screen when the API call fails', async () => {
    ;(fetch as unknown as Mock).mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: async () => ({ detail: 'Database unavailable' }),
    })

    render(<Alerts />)

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Database unavailable')
    })
    expect(screen.queryByText(/loading alerts/i)).not.toBeInTheDocument()
  })

  it('shows empty-state messages when there are no tickets or notifications', async () => {
    ;(fetch as unknown as Mock).mockImplementation(async () => ({
      ok: true,
      status: 200,
      json: async () => [],
    }))

    render(<Alerts />)

    await waitFor(() => {
      expect(screen.getByText('No support tickets.')).toBeInTheDocument()
    })
    expect(screen.getByText('No notifications.')).toBeInTheDocument()
  })
})
