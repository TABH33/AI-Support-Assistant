import { useEffect } from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import { ChatWidget } from './ChatWidget'
import { AuthProvider, TOKEN_STORAGE_KEY } from '../context/AuthProvider'
import { SelectionProvider, useSelection } from '../context/SelectionContext'
import { makeFakeJwt } from '../test-support/jwt'
import type { Device } from '../types/telematics'

vi.mock('./RouteMap', () => ({
  RouteMap: ({ routePlan }: { routePlan: { distance_km: number | null } }) => (
    <div data-testid="mock-route-map" data-distance={routePlan.distance_km ?? undefined} />
  ),
}))

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
 * default, customer-200-scoped when `?customer_id=200` is present),
 * `POST /chat`, `PATCH /chat/messages/{id}/feedback`, and
 * `POST /chat/sessions/{id}/survey` fixture responses.
 *
 * `/feedback`/`/survey` are checked BEFORE the generic `/chat` check below,
 * since both of those URLs (`/chat/messages/{id}/feedback`,
 * `/chat/sessions/{id}/survey`) also contain the substring `/chat`.
 */
function mockChatFetch({ escalated = false }: { escalated?: boolean } = {}) {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string, options?: RequestInit) => {
    if (url.includes('/feedback')) {
      const body = JSON.parse((options?.body as string) ?? '{}')
      return {
        ok: true,
        status: 200,
        json: async () => ({
          chat_message_id: 555,
          feedback: body.feedback,
          escalated: body.feedback === false,
          support_ticket_id: body.feedback === false ? 901 : null,
        }),
      }
    }
    if (url.includes('/survey')) {
      const body = JSON.parse((options?.body as string) ?? '{}')
      return {
        ok: true,
        status: 200,
        json: async () => ({ chat_session_id: 42, ces_score: body.score }),
      }
    }
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
          message_id: 555,
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

  describe('Task 22: message feedback', () => {
    it('clicking thumbs-down on an assistant message PATCHes /chat/messages/{message_id}/feedback with feedback:false', async () => {
      mockChatFetch()
      renderWidget()

      await openWidget()
      await sendMessage('Why is my device offline?')
      await waitFor(() => {
        expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
      })

      fireEvent.click(screen.getByRole('button', { name: /thumbs down/i }))

      await waitFor(() => {
        const feedbackCall = (fetch as unknown as Mock).mock.calls.find(([url]) =>
          (url as string).includes('/feedback')
        )
        expect(feedbackCall).toBeDefined()
      })

      const feedbackCall = (fetch as unknown as Mock).mock.calls.find(([url]) =>
        (url as string).includes('/feedback')
      ) as [string, RequestInit]
      const [feedbackUrl, options] = feedbackCall
      expect(feedbackUrl).toContain('/chat/messages/555/feedback')
      expect(options.method).toBe('PATCH')
      expect(JSON.parse(options.body as string)).toEqual({ feedback: false })

      // Optimistic UI reflects the selection.
      expect(screen.getByRole('button', { name: /thumbs down/i })).toHaveAttribute('aria-pressed', 'true')
    })

    it('clicking thumbs-up PATCHes feedback:true', async () => {
      mockChatFetch()
      renderWidget()

      await openWidget()
      await sendMessage('How many drivers do I have?')
      await waitFor(() => {
        expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
      })

      fireEvent.click(screen.getByRole('button', { name: /thumbs up/i }))

      await waitFor(() => {
        const feedbackCall = (fetch as unknown as Mock).mock.calls.find(([url]) =>
          (url as string).includes('/feedback')
        )
        expect(feedbackCall).toBeDefined()
      })

      const [, options] = (fetch as unknown as Mock).mock.calls.find(([url]) =>
        (url as string).includes('/feedback')
      ) as [string, RequestInit]
      expect(JSON.parse(options.body as string)).toEqual({ feedback: true })
    })

    it('shows the escalation acknowledgement when a thumbs-down PATCH response has escalated:true', async () => {
      // Final-review Fix 7 regression test: the PATCH response
      // (ChatMessageFeedbackResponse, carrying escalated/support_ticket_id)
      // was previously awaited and discarded -- nothing told the user a
      // support ticket had been created on their behalf. mockChatFetch's
      // /feedback handler already returns `escalated: body.feedback ===
      // false`, i.e. `true` for a thumbs-down click, matching the real
      // backend's contract.
      mockChatFetch()
      renderWidget()

      await openWidget()
      await sendMessage('Why is my device offline?')
      await waitFor(() => {
        expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
      })

      // Not shown yet -- this response was NOT auto-escalated (confidence
      // 0.95, per mockChatFetch's non-escalated branch).
      expect(screen.queryByTestId('chat-escalation-label')).not.toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: /thumbs down/i }))

      // The SAME visual acknowledgement the auto-escalation path uses now
      // appears for the message the user just downvoted.
      await waitFor(() => {
        expect(screen.getByTestId('chat-escalation-label')).toBeInTheDocument()
      })
      expect(screen.getByTestId('chat-escalation-label')).toHaveTextContent('Escalated to human support')
    })

    it('rolls back the optimistic thumbs-down state and shows an error when the PATCH fails', async () => {
      mockChatFetch()
      renderWidget()

      await openWidget()
      await sendMessage('Why is my device offline?')
      await waitFor(() => {
        expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
      })

      // The next fetch call (the feedback PATCH) fails; everything else
      // (already resolved by this point) keeps using mockChatFetch's normal
      // responses.
      ;(fetch as unknown as Mock).mockImplementationOnce(async () => ({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
        json: async () => ({}),
      }))

      const thumbsDownButton = screen.getByRole('button', { name: /thumbs down/i })
      fireEvent.click(thumbsDownButton)

      // Optimistic UI applies immediately...
      expect(thumbsDownButton).toHaveAttribute('aria-pressed', 'true')

      // ...then reverts once the PATCH rejects, and the failure is surfaced.
      await waitFor(() => {
        expect(thumbsDownButton).toHaveAttribute('aria-pressed', 'false')
      })
      expect(screen.getByRole('alert')).toBeInTheDocument()

      // Thumbs-up must also read as un-set -- the rollback restores "no
      // feedback yet," not some other stuck state.
      expect(screen.getByRole('button', { name: /thumbs up/i })).toHaveAttribute('aria-pressed', 'false')
    })
  })

  describe('Task 22: CES survey trigger', () => {
    it('shows the CES survey instead of closing when the widget is dismissed after a message was sent, and closes it after submitting a score', async () => {
      mockChatFetch()
      renderWidget()

      await openWidget()
      await sendMessage('How is my fleet doing?')
      await waitFor(() => {
        expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
      })

      // Attempt to close -- the survey should appear instead of the widget
      // actually closing.
      fireEvent.click(screen.getByRole('button', { name: /minimize chat/i }))

      expect(screen.getByTestId('ces-survey')).toBeInTheDocument()
      expect(screen.getByRole('dialog', { name: /ai chat assistant/i })).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: 'Score 5' }))
      fireEvent.click(screen.getByRole('button', { name: /^submit$/i }))

      await waitFor(() => {
        const surveyCall = (fetch as unknown as Mock).mock.calls.find(([url]) =>
          (url as string).includes('/survey')
        )
        expect(surveyCall).toBeDefined()
      })

      const [surveyUrl, surveyOptions] = (fetch as unknown as Mock).mock.calls.find(([url]) =>
        (url as string).includes('/survey')
      ) as [string, RequestInit]
      expect(surveyUrl).toContain('/chat/sessions/42/survey')
      expect(JSON.parse(surveyOptions.body as string)).toEqual({ score: 5 })

      // The widget actually closes once the survey is resolved.
      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: /ai chat assistant/i })).not.toBeInTheDocument()
      })
    })

    it('skipping the CES survey closes the widget without posting a score', async () => {
      mockChatFetch()
      renderWidget()

      await openWidget()
      await sendMessage('How is my fleet doing?')
      await waitFor(() => {
        expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
      })

      fireEvent.click(screen.getByRole('button', { name: /minimize chat/i }))
      expect(screen.getByTestId('ces-survey')).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: /^skip$/i }))

      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: /ai chat assistant/i })).not.toBeInTheDocument()
      })

      expect((fetch as unknown as Mock).mock.calls.some(([url]) => (url as string).includes('/survey'))).toBe(
        false
      )
    })

    it('does not show the survey when closing the widget before any message has been sent', async () => {
      mockChatFetch()
      renderWidget()

      await openWidget()
      fireEvent.click(screen.getByRole('button', { name: /minimize chat/i }))

      expect(screen.queryByTestId('ces-survey')).not.toBeInTheDocument()
      expect(screen.queryByRole('dialog', { name: /ai chat assistant/i })).not.toBeInTheDocument()
    })
  })

  describe('route-planning + warnings', () => {
    it('renders the route map panel when the assistant response includes route_plan', async () => {
      ;(fetch as unknown as Mock).mockImplementation(async (url: string) => {
        if (url.includes('/devices')) {
          return { ok: true, status: 200, json: async () => customerADevices }
        }
        if (url.includes('/chat')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              session_id: 42,
              message_id: 555,
              answer: 'This route is 23.4km and takes about 38 minutes, with a risk zone near the midpoint.',
              confidence: 1.0,
              escalated: false,
              route_plan: {
                distance_km: 23.4,
                duration_min: 38.2,
                geometry: {
                  type: 'LineString',
                  coordinates: [
                    [151.2093, -33.8688],
                    [151.0011, -33.815],
                  ],
                },
                warnings: [],
                unavailable: false,
              },
            }),
          }
        }
        throw new Error(`Unexpected fetch to ${url}`)
      })
      renderWidget()

      await openWidget()
      await sendMessage('plan a trip from Sydney CBD to Parramatta')

      await waitFor(() => {
        expect(screen.getByTestId('chat-route-map-panel')).toBeInTheDocument()
      })
      expect(screen.getByTestId('mock-route-map')).toHaveAttribute('data-distance', '23.4')
    })

    it('does not render the route map panel for an ordinary chat response', async () => {
      mockChatFetch()
      renderWidget()

      await openWidget()
      await sendMessage('How is my fleet doing?')

      await waitFor(() => {
        expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('chat-route-map-panel')).not.toBeInTheDocument()
    })

    it('hides the route map panel once a later, ordinary response arrives (does not stay stuck open)', async () => {
      let chatCallCount = 0
      ;(fetch as unknown as Mock).mockImplementation(async (url: string) => {
        if (url.includes('/devices')) {
          return { ok: true, status: 200, json: async () => customerADevices }
        }
        if (url.includes('/chat')) {
          chatCallCount += 1
          if (chatCallCount === 1) {
            return {
              ok: true,
              status: 200,
              json: async () => ({
                session_id: 42,
                message_id: 555,
                answer: 'This route is 23.4km and takes about 38 minutes, with a risk zone near the midpoint.',
                confidence: 1.0,
                escalated: false,
                route_plan: {
                  distance_km: 23.4,
                  duration_min: 38.2,
                  geometry: {
                    type: 'LineString',
                    coordinates: [
                      [151.2093, -33.8688],
                      [151.0011, -33.815],
                    ],
                  },
                  warnings: [],
                  unavailable: false,
                },
              }),
            }
          }
          return {
            ok: true,
            status: 200,
            json: async () => ({
              session_id: 42,
              message_id: 556,
              answer: 'Your vehicle traveled 42.5 km on its last trip.',
              confidence: 0.95,
              escalated: false,
            }),
          }
        }
        throw new Error(`Unexpected fetch to ${url}`)
      })
      renderWidget()

      await openWidget()
      await sendMessage('plan a trip from Sydney CBD to Parramatta')
      await waitFor(() => {
        expect(screen.getByTestId('chat-route-map-panel')).toBeInTheDocument()
      })

      // Follow up with an ordinary question -- the response has no
      // `route_plan`, so the map panel must disappear along with it (not
      // stay stuck open just because SOME earlier message had a route_plan).
      await sendMessage('How is my fleet doing?')
      await waitFor(() => {
        expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('chat-route-map-panel')).not.toBeInTheDocument()

      const dialog = screen.getByRole('dialog', { name: /ai chat assistant/i })
      expect(dialog.className).toContain('w-80')
      expect(dialog.className).not.toContain('w-[44rem]')
    })
  })
})
