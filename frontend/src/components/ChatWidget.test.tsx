import { useEffect } from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import { ChatWidget } from './ChatWidget'
import { AuthProvider, TOKEN_STORAGE_KEY } from '../context/AuthProvider'
import { SelectionProvider, useSelection } from '../context/SelectionContext'
import { makeFakeJwt } from '../test-support/jwt'
import type { Device } from '../types/telematics'

const devices: Device[] = [
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
    // Most recently active -- ChatWidget should auto-select this one.
    last_seen: '2024-06-01T00:00:00Z',
    device_status: 'active',
    installed_at: null,
    created_at: '2024-01-01T00:00:00Z',
  },
]

/** Seeds `SelectionContext` with a driver/trip/vehicle selection on mount, mimicking what Overview.tsx/Drivers.tsx (Task 21 wiring) would have already set before the widget is opened. */
function SelectionSeeder() {
  const { selectTrip } = useSelection()
  useEffect(() => {
    selectTrip({ tripId: 77, driverId: 5, vehicleId: 9 })
  }, [selectTrip])
  return null
}

function renderWidget({ seedSelection = true }: { seedSelection?: boolean } = {}) {
  return render(
    <AuthProvider>
      <SelectionProvider>
        {seedSelection && <SelectionSeeder />}
        <ChatWidget />
      </SelectionProvider>
    </AuthProvider>
  )
}

/** Routes mocked `fetch` calls to `GET /devices` and `POST /chat` fixture responses. Captures the last `/chat` request body for assertions. */
function mockChatFetch({ escalated = false }: { escalated?: boolean } = {}) {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string) => {
    if (url.includes('/devices')) {
      return { ok: true, status: 200, json: async () => devices }
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

  it('includes customer_id (derived from the resolved device) for a support_agent caller starting a new session', async () => {
    const token = makeFakeJwt({
      sub: '7',
      role: 'support_agent',
      access_level: 'admin',
      iat: Math.floor(Date.now() / 1000),
      exp: Math.floor(Date.now() / 1000) + 3600,
    })
    localStorage.setItem(TOKEN_STORAGE_KEY, token)

    mockChatFetch()
    renderWidget()

    await openWidget()
    await sendMessage('Checking on a customer device')

    await waitFor(() => {
      expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
    })

    const chatCall = (fetch as unknown as Mock).mock.calls.find(([url]) => (url as string).includes('/chat'))
    const [, options] = chatCall as [string, RequestInit]
    const body = JSON.parse(options.body as string)
    // customer_id comes from the auto-selected device (device_id 2 -> customer_id 100).
    expect(body.customer_id).toBe(100)
    expect(body.device_id).toBe(2)
  })
})
