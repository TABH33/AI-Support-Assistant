import { useEffect } from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import { ChatWidget } from './ChatWidget'
import { AuthProvider, TOKEN_STORAGE_KEY } from '../context/AuthProvider'
import { SelectionProvider, useSelection } from '../context/SelectionContext'
import { makeFakeJwt } from '../test-support/jwt'
import type { Device } from '../types/telematics'

// Customer 100's devices -- what an unfiltered `GET /devices` (a `customer`
// caller's own tenant-scoped view) or an explicit `?customer_id=100` returns.
const customerADevices: Device[] = [
  {
    device_id: 1,
    customer_id: 100,
    serial_number: 'SN-OLD',
    device_type: 'obd',
    battery_status: 'ok',
    signal_strength: 70,
    last_seen: '2024-01-01T00:00:00Z',
    device_status: 'active',
    installed_at: null,
    created_at: '2024-01-01T00:00:00Z',
  },
  {
    device_id: 2,
    customer_id: 100,
    serial_number: 'SN-NEW',
    device_type: 'obd',
    battery_status: 'ok',
    signal_strength: 90,
    // Most recently active among customer 100's devices.
    last_seen: '2024-06-01T00:00:00Z',
    device_status: 'active',
    installed_at: null,
    created_at: '2024-01-01T00:00:00Z',
  },
]

// Customer 200's devices -- a DIFFERENT customer, returned only from an
// explicit `GET /devices?customer_id=200`. Deliberately given a MORE
// recent `last_seen` than anything in customerADevices, so a test asserting
// "the widget picked a customer-200 device" can't pass by accident if the
// widget actually queried the unfiltered/wrong-customer list.
const customerBDevices: Device[] = [
  {
    device_id: 30,
    customer_id: 200,
    serial_number: 'SN-B',
    device_type: 'obd',
    battery_status: 'ok',
    signal_strength: 85,
    last_seen: '2024-08-01T00:00:00Z',
    device_status: 'active',
    installed_at: null,
    created_at: '2024-01-01T00:00:00Z',
  },
]

/**
 * Seeds `SelectionContext` with a driver/trip/vehicle (and optionally
 * customer) selection on mount, mimicking what Overview.tsx/Drivers.tsx
 * (Task 21 wiring) would have already set before the widget is opened.
 * `customerId` defaults to `undefined` (left unset -- `selectedCustomerId`
 * stays `null`) so tests can exercise the "support_agent hasn't selected
 * anything yet" path deliberately.
 */
function SelectionSeeder({
  tripId = 77,
  driverId = 5,
  vehicleId = 9,
  customerId,
}: {
  tripId?: number
  driverId?: number
  vehicleId?: number
  customerId?: number
}) {
  const { selectTrip } = useSelection()
  useEffect(() => {
    selectTrip({ tripId, driverId, vehicleId, customerId })
  }, [selectTrip, tripId, driverId, vehicleId, customerId])
  return null
}

function renderWidget({
  seedSelection = true,
  selectionCustomerId,
}: { seedSelection?: boolean; selectionCustomerId?: number } = {}) {
  return render(
    <AuthProvider>
      <SelectionProvider>
        {seedSelection && <SelectionSeeder customerId={selectionCustomerId} />}
        <ChatWidget />
      </SelectionProvider>
    </AuthProvider>
  )
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

/**
 * Routes mocked `fetch` calls to `GET /devices` (customer-100-scoped by
 * default, customer-200-scoped when `?customer_id=200` is present) and
 * `POST /chat` fixture responses.
 */
function mockChatFetch({ escalated = false }: { escalated?: boolean } = {}) {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string) => {
    if (url.includes('/devices')) {
      const match = url.match(/customer_id=(\d+)/)
      if (match && Number(match[1]) === 200) {
        return { ok: true, status: 200, json: async () => customerBDevices }
      }
      return { ok: true, status: 200, json: async () => customerADevices }
    }
    if (url.includes('/chat')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          session_id: 42,
          answer: escalated
            ? "I'm not confident enough to answer that -- a human agent will follow up."
            : 'Your vehicle traveled 42.5 km on its last trip.',
          confidence: escalated ? 0.1 : 0.95,
          escalated,
        }),
      }
    }
    throw new Error(`Unexpected fetch to ${url}`)
  })
}

async function openWidget() {
  fireEvent.click(screen.getByRole('button', { name: /open chat/i }))
}

async function sendMessage(text: string) {
  fireEvent.change(screen.getByLabelText(/chat message/i), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: /^send$/i }))
}

describe('ChatWidget', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    localStorage.clear()
    sessionStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows the required AI disclosure banner the first time it is opened', async () => {
    renderWidget()

    await openWidget()

    const banner = screen.getByTestId('chat-disclosure-banner')
    expect(banner).toHaveTextContent('You are talking to an AI assistant')
  })

  it('does not re-show the disclosure banner on a second open in the same session', async () => {
    renderWidget()

    await openWidget()
    expect(screen.getByTestId('chat-disclosure-banner')).toBeInTheDocument()

    // Close, then reopen.
    fireEvent.click(screen.getByRole('button', { name: /minimize chat/i }))
    await openWidget()

    expect(screen.queryByTestId('chat-disclosure-banner')).not.toBeInTheDocument()
  })

  it('sends the currently-selected driver/trip/vehicle context and an auto-resolved device_id, and renders the assistant response', async () => {
    mockChatFetch()
    renderWidget()

    await openWidget()
    await sendMessage('How is my fleet doing?')

    await waitFor(() => {
      expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
    })

    // The user's own message renders too.
    expect(screen.getByText('How is my fleet doing?')).toBeInTheDocument()

    const chatCall = (fetch as unknown as Mock).mock.calls.find(([url]) => (url as string).includes('/chat'))
    expect(chatCall).toBeDefined()
    const [, options] = chatCall as [string, RequestInit]
    const body = JSON.parse(options.body as string)

    expect(body.query).toBe('How is my fleet doing?')
    expect(body.driver_id).toBe(5)
    expect(body.trip_id).toBe(77)
    expect(body.vehicle_id).toBe(9)
    // No session yet -- device_id must be resolved from GET /devices, picking
    // the device with the most recent last_seen (device_id 2), not just the
    // first one returned.
    expect(body.device_id).toBe(2)
    expect(body.session_id).toBeNull()
    // customer role: customer_id is never sent (backend ignores/doesn't need it).
    expect(body.customer_id).toBeUndefined()

    // GET /devices was actually called to resolve that device_id.
    const devicesCall = (fetch as unknown as Mock).mock.calls.find(([url]) => (url as string).includes('/devices'))
    expect(devicesCall).toBeDefined()
  })

  it('reuses the session_id from the first response on the next message instead of re-resolving a device', async () => {
    mockChatFetch()
    renderWidget()

    await openWidget()
    await sendMessage('First question')
    await waitFor(() => {
      expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
    })

    const devicesCallsAfterFirst = (fetch as unknown as Mock).mock.calls.filter(([url]) =>
      (url as string).includes('/devices')
    ).length

    await sendMessage('Second question')
    await waitFor(() => {
      const calledUrls = (fetch as unknown as Mock).mock.calls.map(([url]) => url as string)
      expect(calledUrls.filter((u) => u.includes('/chat')).length).toBe(2)
    })

    const chatCalls = (fetch as unknown as Mock).mock.calls.filter(([url]) => (url as string).includes('/chat'))
    const secondBody = JSON.parse((chatCalls[1][1] as RequestInit).body as string)
    expect(secondBody.session_id).toBe(42)

    const devicesCallsAfterSecond = (fetch as unknown as Mock).mock.calls.filter(([url]) =>
      (url as string).includes('/devices')
    ).length
    expect(devicesCallsAfterSecond).toBe(devicesCallsAfterFirst)
  })

  it('visually and textually distinguishes an escalated response from a normal answer', async () => {
    mockChatFetch({ escalated: true })
    renderWidget()

    await openWidget()
    await sendMessage('This is a complicated billing dispute')

    await waitFor(() => {
      expect(screen.getByTestId('chat-escalation-label')).toBeInTheDocument()
    })

    expect(screen.getByTestId('chat-escalation-label')).toHaveTextContent('Escalated to human support')

    const assistantMessage = screen.getByTestId('chat-message-assistant')
    expect(assistantMessage).toHaveTextContent(
      "I'm not confident enough to answer that -- a human agent will follow up."
    )
    // Distinct styling from a normal assistant bubble (amber, not the plain gray bubble).
    expect(assistantMessage.className).toContain('bg-amber-50')
    expect(assistantMessage.className).not.toContain('bg-gray-100')
  })

  it('does not show the escalation styling for a normal (non-escalated) response', async () => {
    mockChatFetch({ escalated: false })
    renderWidget()

    await openWidget()
    await sendMessage('How many drivers do I have?')

    await waitFor(() => {
      expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
    })

    expect(screen.queryByTestId('chat-escalation-label')).not.toBeInTheDocument()
    const assistantMessage = screen.getByTestId('chat-message-assistant')
    expect(assistantMessage.className).toContain('bg-gray-100')
    expect(assistantMessage.className).not.toContain('bg-amber-50')
  })

  describe('support_agent customer_id/device_id resolution', () => {
    it("derives customer_id from the agent's actual selection (not an arbitrary device) and resolves device_id scoped to that same customer", async () => {
      loginAsSupportAgent()
      mockChatFetch()
      // The agent selected a driver belonging to customer 200 -- a
      // DIFFERENT customer than customerADevices (100), which would have
      // been picked by the old (buggy) "most-recently-active device across
      // every customer" logic, since customerBDevices' device has a later
      // last_seen than anything in customerADevices too.
      renderWidget({ selectionCustomerId: 200 })

      await openWidget()
      await sendMessage('What is this customer doing?')

      await waitFor(() => {
        expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
      })

      const chatCall = (fetch as unknown as Mock).mock.calls.find(([url]) => (url as string).includes('/chat'))
      expect(chatCall).toBeDefined()
      const [, options] = chatCall as [string, RequestInit]
      const body = JSON.parse(options.body as string)

      // customer_id comes from the agent's selection (200), never from an
      // unrelated device's own customer_id (which would have been 100).
      expect(body.customer_id).toBe(200)
      expect(body.driver_id).toBe(5)
      // device_id must come from customer 200's own device list (30), not
      // customer 100's most-recently-active device (2).
      expect(body.device_id).toBe(30)

      // GET /devices was called scoped to customer 200, not unfiltered.
      const devicesCall = (fetch as unknown as Mock).mock.calls.find(([url]) =>
        (url as string).includes('/devices')
      )
      expect(devicesCall).toBeDefined()
      const [devicesUrl] = devicesCall as [string]
      expect(devicesUrl).toContain('customer_id=200')
    })

    it('shows a "select a driver or vehicle first" state instead of the message form when a support_agent has made no selection yet, and never guesses a customer', async () => {
      loginAsSupportAgent()
      mockChatFetch()
      // No SelectionSeeder at all -- selectedCustomerId stays null.
      renderWidget({ seedSelection: false })

      await openWidget()

      expect(screen.getByTestId('chat-needs-selection')).toHaveTextContent(
        /select a driver or vehicle/i
      )
      // The message form must not be usable while unresolved.
      expect(screen.queryByLabelText(/chat message/i)).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /^send$/i })).not.toBeInTheDocument()

      // Nothing was silently guessed: no /chat or /devices call happened.
      expect(fetch).not.toHaveBeenCalled()
    })
  })
})
