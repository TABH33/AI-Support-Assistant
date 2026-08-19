/**
 * Floating chat widget (Task 21): a persistent panel rendered once in
 * `Layout.tsx` (so it survives route changes -- it is not a routed page)
 * that lets the logged-in user talk to the AI assistant built in
 * Tasks 12-15, via `POST /chat` (`backend/app/api/chat.py`).
 *
 * Request/response contract (`ChatRequest`/`ChatResponse` in
 * `frontend/src/types/chat.ts`, copied from `chat.py`, not guessed):
 *   - `query` is the only always-required field.
 *   - `session_id` is omitted/null on the first message of a conversation;
 *     the backend creates a new `ChatSession` and returns its id in the
 *     response, which we then reuse for every subsequent message in this
 *     widget instance.
 *   - `driver_id`/`trip_id`/`vehicle_id` are read from `SelectionContext`
 *     (Tasks 18/19's "currently selected" state) and sent with every
 *     message, not just the first -- the backend threads them into
 *     `retrieve_context` on every turn, regardless of session reuse.
 *   - `device_id` is REQUIRED the first time a session is created
 *     (`ChatSession.device_id` is NOT NULL). See `resolveNewSessionFields`
 *     below for how we source it, since no screen built so far has device
 *     selection UI.
 *
 * Transparency requirement (ASS2, already established in Task 13's prompt
 * engineering): the widget must show a disclosure banner reading exactly
 * "You are talking to an AI assistant" the first time it's opened.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiGet, apiPost } from '../lib/apiClient'
import { useAuth } from '../context/AuthProvider'
import { useSelection } from '../context/SelectionContext'
import type { ChatRequest, ChatResponse } from '../types/chat'
import type { Device } from '../types/telematics'

interface ChatWidgetMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** Only ever `true` on an assistant message -- see `ChatResponse.escalated`. */
  escalated?: boolean
}

/** sessionStorage key used to remember "the disclosure banner has already been shown this browser session" (POC-level persistence, per the task brief). */
const DISCLOSURE_SEEN_KEY = 'telematics_chat_disclosure_seen'

function hasSeenDisclosure(): boolean {
  try {
    return sessionStorage.getItem(DISCLOSURE_SEEN_KEY) === '1'
  } catch {
    // sessionStorage unavailable (e.g. some test environments) -- treat as
    // "not seen yet" every time, which is a safe default (shows the banner
    // more often, never fewer).
    return false
  }
}

function markDisclosureSeen(): void {
  try {
    sessionStorage.setItem(DISCLOSURE_SEEN_KEY, '1')
  } catch {
    // Best-effort only -- see hasSeenDisclosure above.
  }
}

/**
 * Resolves the `device_id` (and, for a `support_agent` caller, the
 * `customer_id`) needed to start a brand-new `/chat` session.
 *
 * Design gap this solves (per the task brief): `POST /chat` requires a
 * `device_id` when creating a new session, but no frontend screen built so
 * far (Overview/Drivers/Alerts) has device-selection UI -- devices aren't
 * listed anywhere yet. Building a full device picker is out of scope for
 * this task, so instead: fetch the caller's own devices via `GET /devices`
 * (Task 7, already tenant-scoped -- a `customer` caller only ever sees
 * their own devices) and auto-select the most recently active one (highest
 * `last_seen`, nulls sorted last), falling back to the first device in the
 * (device_id-ordered) list if none has ever reported in. "Most recently
 * active" is a better default than "first by id" for a support use case:
 * the device the user is most likely asking about is the one that's
 * actually been transmitting.
 *
 * For a `support_agent` caller, `GET /devices` returns devices across every
 * customer (Task 7's scoping only restricts `customer`-role callers), so
 * picking "the most recently active device" also picks an arbitrary
 * customer. We handle this by also returning that device's own
 * `customer_id` and sending it back as `ChatRequest.customer_id` -- which
 * `_create_new_session` in `chat.py` requires (and honors) for a
 * `support_agent` caller. This keeps the two ids self-consistent even
 * though which customer gets picked is arbitrary; a real support-agent UX
 * would let the agent pick a customer/device explicitly, but that's a
 * separate, larger feature outside this task's scope.
 */
async function resolveNewSessionFields(): Promise<{ deviceId: number; customerId: number }> {
  const devices = await apiGet<Device[]>('/devices')
  if (devices.length === 0) {
    throw new Error('No devices found for this account -- cannot start a chat session.')
  }

  let chosen = devices[0]
  for (const device of devices) {
    if (device.last_seen === null) continue
    if (chosen.last_seen === null || device.last_seen > chosen.last_seen) {
      chosen = device
    }
  }

  return { deviceId: chosen.device_id, customerId: chosen.customer_id }
}

export function ChatWidget() {
  const { user } = useAuth()
  const { selectedDriverId, selectedTripId, selectedVehicleId } = useSelection()

  const [isOpen, setIsOpen] = useState(false)
  const [showDisclosure, setShowDisclosure] = useState(false)
  const [messages, setMessages] = useState<ChatWidgetMessage[]>([])
  const [inputValue, setInputValue] = useState('')
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [isSending, setIsSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    // `scrollIntoView` isn't implemented in jsdom (the test environment) --
    // guard so tests don't crash on a no-op affordance.
    messagesEndRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messages])

  const handleToggle = useCallback(() => {
    setIsOpen((prev) => {
      const next = !prev
      if (next) {
        // Only render the banner while open, and only the very first time
        // this browser session opens the widget -- closing (or reopening
        // later) must not bring it back, so it's reset to false here too.
        const shouldShow = !hasSeenDisclosure()
        setShowDisclosure(shouldShow)
        if (shouldShow) {
          markDisclosureSeen()
        }
      } else {
        setShowDisclosure(false)
      }
      return next
    })
  }, [])

  const handleSend = useCallback(async () => {
    const query = inputValue.trim()
    if (!query || isSending) return

    setError(null)
    setInputValue('')
    setMessages((prev) => [...prev, { id: `user-${Date.now()}`, role: 'user', content: query }])
    setIsSending(true)

    try {
      const payload: ChatRequest = {
        query,
        session_id: sessionId,
        driver_id: selectedDriverId,
        trip_id: selectedTripId,
        vehicle_id: selectedVehicleId,
      }

      if (sessionId === null) {
        const { deviceId, customerId } = await resolveNewSessionFields()
        payload.device_id = deviceId
        if (user?.role === 'support_agent') {
          payload.customer_id = customerId
        }
      }

      const response = await apiPost<ChatResponse>('/chat', payload)
      setSessionId(response.session_id)
      setMessages((prev) => [
        ...prev,
        {
          id: `assistant-${response.session_id}-${Date.now()}`,
          role: 'assistant',
          content: response.answer,
          escalated: response.escalated,
        },
      ])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send message.')
    } finally {
      setIsSending(false)
    }
  }, [inputValue, isSending, sessionId, selectedDriverId, selectedTripId, selectedVehicleId, user])

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col items-end">
      {isOpen && (
        <div
          role="dialog"
          aria-label="AI chat assistant"
          className="mb-3 flex h-[32rem] w-80 flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-xl dark:border-gray-700 dark:bg-gray-800"
        >
          <div className="flex items-center justify-between bg-indigo-600 px-4 py-3 text-white">
            <span className="font-semibold">AI Assistant</span>
            <button
              type="button"
              aria-label="Minimize chat"
              onClick={handleToggle}
              className="text-white/80 hover:text-white"
            >
              ✕
            </button>
          </div>

          {showDisclosure && (
            <div
              role="status"
              data-testid="chat-disclosure-banner"
              className="border-b border-blue-200 bg-blue-50 px-4 py-2 text-xs text-blue-900 dark:border-blue-800 dark:bg-blue-900/40 dark:text-blue-200"
            >
              You are talking to an AI assistant.
            </div>
          )}

          <div className="flex-1 space-y-2 overflow-y-auto px-3 py-3">
            {messages.length === 0 && (
              <p className="text-sm text-gray-500 dark:text-gray-400">
                Ask a question about your fleet, drivers, or trips.
              </p>
            )}
            {messages.map((message) => (
              <div
                key={message.id}
                data-testid={`chat-message-${message.role}`}
                className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                  message.role === 'user'
                    ? 'ml-auto bg-indigo-600 text-white'
                    : message.escalated
                      ? 'border border-amber-400 bg-amber-50 text-amber-900 dark:border-amber-600 dark:bg-amber-900/30 dark:text-amber-200'
                      : 'bg-gray-100 text-gray-900 dark:bg-gray-700 dark:text-white'
                }`}
              >
                {message.role === 'assistant' && message.escalated && (
                  <p
                    data-testid="chat-escalation-label"
                    className="mb-1 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300"
                  >
                    ⚠ Escalated to human support
                  </p>
                )}
                <p>{message.content}</p>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          {error && (
            <p role="alert" className="px-3 pb-1 text-xs text-red-600 dark:text-red-400">
              {error}
            </p>
          )}

          <form
            className="flex items-center gap-2 border-t border-gray-200 p-3 dark:border-gray-700"
            onSubmit={(event) => {
              event.preventDefault()
              void handleSend()
            }}
          >
            <input
              type="text"
              value={inputValue}
              onChange={(event) => setInputValue(event.target.value)}
              placeholder="Type a message…"
              aria-label="Chat message"
              disabled={isSending}
              className="flex-1 rounded border border-gray-300 px-2 py-1.5 text-sm text-gray-900 focus:border-indigo-500 focus:outline-none dark:border-gray-600 dark:bg-gray-900 dark:text-white"
            />
            <button
              type="submit"
              disabled={isSending || inputValue.trim() === ''}
              className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              Send
            </button>
          </form>
        </div>
      )}

      <button
        type="button"
        onClick={handleToggle}
        aria-label={isOpen ? 'Close chat' : 'Open chat'}
        className="flex h-14 w-14 items-center justify-center rounded-full bg-indigo-600 text-2xl text-white shadow-lg hover:bg-indigo-700"
      >
        {isOpen ? '✕' : '💬'}
      </button>
    </div>
  )
}

export default ChatWidget
