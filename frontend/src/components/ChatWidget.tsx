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
 *     (`ChatSession.device_id` is NOT NULL). See `resolveDeviceId` below for
 *     how we source it, since no screen built so far has device selection
 *     UI.
 *   - `customer_id` is only honored (and only needed) for a `support_agent`
 *     caller starting a new session -- see `handleSend`'s support_agent
 *     branch below for where it comes from.
 *
 * Transparency requirement (ASS2, already established in Task 13's prompt
 * engineering): the widget must show a disclosure banner reading exactly
 * "You are talking to an AI assistant" the first time it's opened.
 *
 * **Fixed defect (post-review)**: an earlier version of this component
 * resolved a `support_agent`'s `customer_id` from whichever device across
 * ALL customers happened to have the most recent `last_seen` -- completely
 * decoupled from whichever customer's driver/trip/vehicle the agent had
 * actually selected in Overview/Drivers. That silently created the new
 * `ChatSession` (and any later-escalated `SupportTicket`) against the
 * wrong customer, and silently dropped the agent's real selection from
 * `retrieve_context` (the scoped queries there return `None`/`[]` on a
 * customer_id mismatch rather than raising, so nothing surfaced the bug).
 * Fixed by deriving `customer_id` from `SelectionContext.selectedCustomerId`
 * -- the `customer_id` captured at the moment the agent actually selected a
 * driver/trip/vehicle (see `SelectionContext.tsx`) -- and refusing to start
 * a new session at all (see `needsCustomerSelection` below) until that
 * selection exists, instead of ever guessing.
 *
 * **Task 22 additions**: thumbs up/down feedback on each assistant message
 * (`PATCH /chat/messages/{message_id}/feedback`), and a post-resolution CES
 * (Customer Effort Score) micro-survey (`CesSurvey.tsx`, `POST
 * /chat/sessions/{id}/survey`).
 *
 * **CES survey trigger -- design latitude call (per the task brief)**: the
 * plan says "shown when a session ends," but no explicit "end session"
 * backend action is wired into this widget (`ChatSession.session_status`/
 * `end_time` are set by `end_chat_session` in `app/repositories/chat.py`,
 * but nothing here calls it -- adding that call is out of scope per the
 * brief's "don't over-engineer this" note). Of the brief's two suggested
 * options, this picks (a), the simpler one: the survey is triggered when
 * the user closes/dismisses the widget (`handleToggle`'s close path) after
 * having sent at least one message this session (`sessionId !== null` is
 * used as that signal -- it's only ever set once a full `POST /chat`
 * round-trip has completed). `surveyResolved` (a plain boolean, not reset
 * on reopen) ensures it's shown at most once per widget instance/session,
 * whether the user submits a score or skips.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiGet, apiPatch, apiPost } from '../lib/apiClient'
import { useAuth } from '../context/AuthProvider'
import { useSelection } from '../context/SelectionContext'
import { CesSurvey } from './CesSurvey'
import type { ChatMessageFeedbackResponse, ChatRequest, ChatResponse } from '../types/chat'
import type { Device } from '../types/telematics'

interface ChatWidgetMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** Only ever `true` on an assistant message -- see `ChatResponse.escalated`. */
  escalated?: boolean
  /** `ChatMessage.chat_message_id` (Task 22) -- only set on assistant
   * messages, since only those can receive feedback. Used to target
   * `PATCH /chat/messages/{chatMessageId}/feedback`. */
  chatMessageId?: number
  /** Thumbs up (`true`) / down (`false`) / not yet rated (`undefined`).
   * Only meaningful on assistant messages. */
  feedback?: boolean
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
 * Resolves the `device_id` needed to start a brand-new `/chat` session, by
 * fetching devices and auto-selecting the most recently active one (highest
 * `last_seen`, nulls sorted last), falling back to the first device in the
 * (device_id-ordered) list if none has ever reported in. "Most recently
 * active" is a better default than "first by id": the device the user is
 * most likely asking about is the one that's actually been transmitting.
 *
 * Design gap this solves (per the task brief): `POST /chat` requires a
 * `device_id` when creating a new session, but no frontend screen built so
 * far (Overview/Drivers/Alerts) has device-selection UI -- devices aren't
 * listed anywhere yet. Building a full device picker is out of scope for
 * this task, so this auto-selection stands in for one.
 *
 * `customerIdFilter`: which customer's devices to search.
 *   - For a `customer` caller, omit it -- `GET /devices` (Task 7) is
 *     already scoped to the caller's own `customer_id` via their JWT, so no
 *     filter is needed (or honored -- Task 7 only applies `?customer_id=`
 *     for a `support_agent` caller).
 *   - For a `support_agent` caller, this MUST be the `customer_id` the
 *     agent actually selected (`SelectionContext.selectedCustomerId`) --
 *     never omitted -- otherwise `GET /devices` returns devices across
 *     EVERY customer and "most recently active" would pick an arbitrary
 *     one, silently attributing the new session to the wrong customer (see
 *     this component's module docstring for the defect this replaced).
 */
async function resolveDeviceId(customerIdFilter?: number): Promise<number> {
  const endpoint = customerIdFilter === undefined ? '/devices' : `/devices?customer_id=${customerIdFilter}`
  const devices = await apiGet<Device[]>(endpoint)
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

  return chosen.device_id
}

export function ChatWidget() {
  const { user } = useAuth()
  const { selectedDriverId, selectedTripId, selectedVehicleId, selectedCustomerId } = useSelection()

  const [isOpen, setIsOpen] = useState(false)
  const [showDisclosure, setShowDisclosure] = useState(false)
  const [messages, setMessages] = useState<ChatWidgetMessage[]>([])
  const [inputValue, setInputValue] = useState('')
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [isSending, setIsSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showSurvey, setShowSurvey] = useState(false)
  // Set once the CES survey has been submitted or skipped -- prevents it
  // from being shown again for the lifetime of this widget instance (see
  // module docstring's "CES survey trigger" note).
  const [surveyResolved, setSurveyResolved] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)

  // A `support_agent` caller has no fleet of their own -- starting a NEW
  // session requires knowing which customer this chat is about. Rather than
  // ever guessing that (see the defect described in this file's module
  // docstring), require the agent to have actually selected a
  // driver/trip/vehicle somewhere (Overview/Drivers) first. Irrelevant once
  // a session already exists: `customer_id` is fixed on the session from
  // then on, so later selection changes don't need to (and shouldn't)
  // re-block sending.
  const needsCustomerSelection =
    user?.role === 'support_agent' && sessionId === null && selectedCustomerId === null

  useEffect(() => {
    // `scrollIntoView` isn't implemented in jsdom (the test environment) --
    // guard so tests don't crash on a no-op affordance.
    messagesEndRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messages])

  const handleToggle = useCallback(() => {
    if (!isOpen) {
      setIsOpen(true)
      // Only render the banner while open, and only the very first time
      // this browser session opens the widget -- closing (or reopening
      // later) must not bring it back, so it's reset to false here too.
      const shouldShow = !hasSeenDisclosure()
      setShowDisclosure(shouldShow)
      if (shouldShow) {
        markDisclosureSeen()
      }
      return
    }

    // Closing: if a session exists (i.e. at least one message round-trip
    // has completed -- see module docstring) and the survey hasn't already
    // been resolved, show the CES micro-survey in place of actually
    // closing. The survey's own onSubmit/onSkip callbacks (see the render
    // below) perform the real close once the user is done with it.
    if (sessionId !== null && !surveyResolved) {
      setShowSurvey(true)
      return
    }

    setShowDisclosure(false)
    setIsOpen(false)
  }, [isOpen, sessionId, surveyResolved])

  const handleSurveyDone = useCallback(() => {
    setSurveyResolved(true)
    setShowSurvey(false)
    setShowDisclosure(false)
    setIsOpen(false)
  }, [])

  const handleFeedback = useCallback(async (messageId: string, chatMessageId: number, value: boolean) => {
    // Capture the pre-click value so a failed PATCH can be rolled back to it
    // -- otherwise a failure would leave the optimistic update in place,
    // showing thumbs-up/down as "applied" even though the backend never
    // recorded it (and, for thumbs-down, never created the escalation
    // ticket).
    let previousFeedback: boolean | undefined
    setMessages((prev) =>
      prev.map((message) => {
        if (message.id !== messageId) return message
        previousFeedback = message.feedback
        return { ...message, feedback: value }
      })
    )
    try {
      const response = await apiPatch<ChatMessageFeedbackResponse>(`/chat/messages/${chatMessageId}/feedback`, {
        feedback: value,
      })
      // Final-review Fix 7: a thumbs-down can create a support ticket
      // (`ChatMessageFeedbackResponse.escalated`), same as the low-confidence
      // auto-escalation path -- but that response was previously awaited and
      // discarded, so nothing told the user a ticket had been created on
      // their behalf. Setting `escalated` here reuses the EXACT same
      // rendering this message already has for the auto-escalation case
      // (the amber highlight + `chat-escalation-label` banner below), so the
      // two escalation routes give consistent, not just similar, feedback
      // for the same underlying outcome. `response.escalated` is always
      // `false` for a thumbs-up (per the endpoint's own contract), so this
      // is safe to apply unconditionally on success.
      if (response.escalated) {
        setMessages((prev) =>
          prev.map((message) => (message.id === messageId ? { ...message, escalated: true } : message))
        )
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit feedback.')
      // Roll back the optimistic update -- the backend never actually
      // recorded this feedback, so the UI must not keep showing it as set.
      setMessages((prev) =>
        prev.map((message) => (message.id === messageId ? { ...message, feedback: previousFeedback } : message))
      )
    }
  }, [])

  const handleSend = useCallback(async () => {
    const query = inputValue.trim()
    if (!query || isSending) return

    // Defense in depth: the form is hidden/disabled whenever
    // `needsCustomerSelection` is true (see the render below), but guard
    // here too in case this is ever called some other way -- never fall
    // through to guessing a customer.
    if (needsCustomerSelection) {
      setError('Select a driver or vehicle first so the assistant knows which customer this chat is about.')
      return
    }

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
        if (user?.role === 'support_agent') {
          // `needsCustomerSelection` guarantees `selectedCustomerId` is
          // non-null here -- resolve devices scoped to THAT customer (Task
          // 7's `?customer_id=` filter), never the unfiltered global list.
          const customerId = selectedCustomerId as number
          const deviceId = await resolveDeviceId(customerId)
          payload.device_id = deviceId
          payload.customer_id = customerId
        } else {
          // A `customer` caller's own `GET /devices` call is already
          // scoped to their JWT-derived customer_id -- no filter needed.
          payload.device_id = await resolveDeviceId()
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
          chatMessageId: response.message_id,
        },
      ])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send message.')
    } finally {
      setIsSending(false)
    }
  }, [
    inputValue,
    isSending,
    needsCustomerSelection,
    sessionId,
    selectedDriverId,
    selectedTripId,
    selectedVehicleId,
    selectedCustomerId,
    user,
  ])

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

          {showSurvey && sessionId !== null ? (
            <CesSurvey sessionId={sessionId} onSubmit={handleSurveyDone} onSkip={handleSurveyDone} />
          ) : (
            <>
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
                  <div key={message.id} className={message.role === 'user' ? 'ml-auto max-w-[85%]' : 'max-w-[85%]'}>
                    <div
                      data-testid={`chat-message-${message.role}`}
                      className={`rounded-lg px-3 py-2 text-sm ${
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
                    {message.role === 'assistant' && message.chatMessageId !== undefined && (
                      <div
                        data-testid="chat-feedback-controls"
                        className="mt-1 flex items-center gap-1.5 text-gray-400 dark:text-gray-500"
                      >
                        <button
                          type="button"
                          aria-label="Thumbs up"
                          aria-pressed={message.feedback === true}
                          onClick={() => void handleFeedback(message.id, message.chatMessageId as number, true)}
                          className={`rounded px-1 text-sm hover:text-green-600 dark:hover:text-green-400 ${
                            message.feedback === true ? 'text-green-600 dark:text-green-400' : ''
                          }`}
                        >
                          👍
                        </button>
                        <button
                          type="button"
                          aria-label="Thumbs down"
                          aria-pressed={message.feedback === false}
                          onClick={() => void handleFeedback(message.id, message.chatMessageId as number, false)}
                          className={`rounded px-1 text-sm hover:text-red-600 dark:hover:text-red-400 ${
                            message.feedback === false ? 'text-red-600 dark:text-red-400' : ''
                          }`}
                        >
                          👎
                        </button>
                      </div>
                    )}
                  </div>
                ))}
                <div ref={messagesEndRef} />
              </div>

              {error && (
                <p role="alert" className="px-3 pb-1 text-xs text-red-600 dark:text-red-400">
                  {error}
                </p>
              )}

              {needsCustomerSelection ? (
                <div
                  role="status"
                  data-testid="chat-needs-selection"
                  className="border-t border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-200"
                >
                  Select a driver or vehicle from Overview or Drivers first, so the assistant knows which
                  customer this chat is about.
                </div>
              ) : (
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
              )}
            </>
          )}
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
