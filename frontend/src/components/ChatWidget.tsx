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
 *
 * **Opt-in escalation** (docs/superpowers/specs/2026-09-30-demo-routes-and-
 * escalation-design.md): a low-confidence answer arrives with
 * `escalation_offered: true` instead of an auto-created ticket. That message
 * shows "Would you like to escalate this to a human? [Yes] [No]" in the same
 * spot as the feedback buttons. **No** is purely local (nothing is sent).
 * **Yes** calls `POST /chat/messages/{message_id}/escalate` (disabled while
 * in flight, so a double-click sends one request) and then shows "A support
 * agent has been notified (ref #N)" -- with "(email notification could not
 * be sent)" appended when `email_sent` is false, since the ticket exists
 * either way -- and marks the message `escalated`, reusing the same amber
 * label a thumbs-down escalation already gets. A failed request restores
 * the prompt and surfaces the error.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiGet, apiPatch, apiPost } from '../lib/apiClient'
import { useAuth } from '../context/AuthProvider'
import { useSelection } from '../context/SelectionContext'
import { CesSurvey } from './CesSurvey'
import { RouteMap } from './RouteMap'
import type {
  ChatMessageEscalateResponse,
  ChatMessageFeedbackResponse,
  ChatRequest,
  ChatResponse,
} from '../types/chat'
import type { Device } from '../types/telematics'
import type { RoutePlanResult } from '../types/routePlan'

/** Lifecycle of an escalation offer on one assistant message:
 * `offered` (prompt shown) -> `sending` (Yes clicked, request in flight) ->
 * `confirmed`; or `offered` -> `dismissed` (No). A failed request goes
 * `sending` -> back to `offered`. */
type EscalationStatus = 'offered' | 'sending' | 'dismissed' | 'confirmed'

interface ChatWidgetMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** Only ever `true` on an assistant message -- see `ChatResponse.escalated`. */
  escalated?: boolean
  /** `ChatMessage.chat_message_id` (Task 22) -- only set on assistant
   * messages, since only those can receive feedback. Used to target
   * `PATCH /chat/messages/{chatMessageId}/feedback` and
   * `POST /chat/messages/{chatMessageId}/escalate`. */
  chatMessageId?: number
  /** Thumbs up (`true`) / down (`false`) / not yet rated (`undefined`).
   * Only meaningful on assistant messages. */
  feedback?: boolean
  /** Structured route data (route-planning + warnings feature) -- only set
   * on an assistant message that answered a route-plan chat intent
   * successfully. Rendered as an inline map beside the transcript. */
  routePlan?: RoutePlanResult
  /** Set only on an assistant message whose response had
   * `escalation_offered: true`. See `EscalationStatus`. */
  escalationStatus?: EscalationStatus
  /** From `ChatMessageEscalateResponse`, once `escalationStatus` is `confirmed`. */
  escalationTicketId?: number
  escalationEmailSent?: boolean
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

  const handleSurveyDone = useCallback(() => {
    setSurveyResolved(true)
    setShowSurvey(false)
    setShowDisclosure(false)
    setIsOpen(false)
  }, [])

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

    // Fixed defect: the CES survey (below) used to be shown in place of
    // closing, but the X button kept calling this same handler -- once the
    // survey was already showing, a second click just called
    // `setShowSurvey(true)` again (already true) and returned, so the
    // widget could NEVER be closed via the X once a message had been sent,
    // only via the survey's own Skip/Submit buttons. A click while the
    // survey is already up now means "let me out" -- skip it and close.
    if (showSurvey) {
      handleSurveyDone()
      return
    }

    // Closing: if a session exists (i.e. at least one message round-trip
    // has completed -- see module docstring) and the survey hasn't already
    // been resolved, show the CES micro-survey in place of actually
    // closing THIS click -- a second click (handled above) closes for real.
    if (sessionId !== null && !surveyResolved) {
      setShowSurvey(true)
      return
    }

    setShowDisclosure(false)
    setIsOpen(false)
  }, [isOpen, showSurvey, sessionId, surveyResolved, handleSurveyDone])

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
      // (`ChatMessageFeedbackResponse.escalated`), same as the opt-in
      // escalation-confirmation path (a customer's explicit "Yes" via
      // POST /chat/messages/{id}/escalate) -- but that response was
      // previously awaited and discarded, so nothing told the user a
      // ticket had been created on their behalf. Setting `escalated` here
      // reuses the EXACT same rendering this message already has for the
      // opt-in escalation-confirmation case (the amber highlight +
      // `chat-escalation-label` banner below), so the two escalation
      // routes give consistent, not just similar, feedback for the same
      // underlying outcome. `response.escalated` is always `false` for a
      // thumbs-up (per the endpoint's own contract), so this is safe to
      // apply unconditionally on success.
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

  const patchMessage = useCallback((messageId: string, patch: Partial<ChatWidgetMessage>) => {
    setMessages((prev) => prev.map((message) => (message.id === messageId ? { ...message, ...patch } : message)))
  }, [])

  const handleEscalationChoice = useCallback(
    async (messageId: string, chatMessageId: number, accept: boolean) => {
      if (!accept) {
        // "No" is purely local -- nothing is sent to the backend.
        patchMessage(messageId, { escalationStatus: 'dismissed' })
        return
      }

      patchMessage(messageId, { escalationStatus: 'sending' })
      try {
        const response = await apiPost<ChatMessageEscalateResponse>(`/chat/messages/${chatMessageId}/escalate`, {})
        patchMessage(messageId, {
          escalationStatus: 'confirmed',
          escalationTicketId: response.support_ticket_id,
          escalationEmailSent: response.email_sent,
          // A ticket now exists -- reuse the same "Escalated" rendering the
          // thumbs-down path already gives (see handleFeedback above).
          escalated: true,
        })
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to escalate to a human.')
        // Nothing was escalated -- put the offer back so the user can retry.
        patchMessage(messageId, { escalationStatus: 'offered' })
      }
    },
    [patchMessage]
  )

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
          routePlan: response.route_plan ?? undefined,
          escalationStatus: response.escalation_offered ? 'offered' : undefined,
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

  // Tied to the CURRENT last message only -- not "the most recent message
  // that ever had a route_plan" -- so a route-plan turn followed by an
  // ordinary follow-up question collapses the map panel/widened dialog
  // back down, instead of leaving it stuck open for the rest of the
  // session (see fix report in task-16-report.md for the bug this
  // replaced).
  const lastMessage = messages[messages.length - 1]
  const activeRoutePlan = lastMessage?.routePlan

  return (
    <div className="fixed bottom-4 right-4 z-[9999] flex flex-col items-end">
      {isOpen && (
        <div
          role="dialog"
          aria-label="AI chat assistant"
          className={`mb-3 flex h-[32rem] overflow-hidden rounded-lg border border-line bg-surface-card shadow-card dark:border-white/10 dark:bg-brand-darker-blue ${
            activeRoutePlan ? 'w-[44rem]' : 'w-80'
          }`}
        >
          <div className="flex h-full w-80 flex-shrink-0 flex-col">
          <div className="flex items-center justify-between bg-brand-dark px-4 py-3 text-white">
            <span className="font-heading font-semibold">AI Assistant</span>
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
                  className="border-b border-brand-teal/30 bg-brand-teal/10 px-4 py-2 text-xs text-brand-dark dark:border-brand-teal/20 dark:bg-brand-teal/10 dark:text-white"
                >
                  You are talking to an AI assistant.
                </div>
              )}

              <div className="flex-1 space-y-2 overflow-y-auto px-3 py-3">
                {messages.length === 0 && (
                  <div className="max-w-[85%]">
                    <div
                      data-testid="chat-greeting"
                      className="rounded-lg bg-surface-page px-3 py-2 text-sm text-brand-dark dark:bg-white/10 dark:text-white"
                    >
                      <p>Hi, I'm your AI Assistant — how can I help you today?</p>
                    </div>
                  </div>
                )}
                {messages.map((message) => (
                  <div key={message.id} className={message.role === 'user' ? 'ml-auto max-w-[85%]' : 'max-w-[85%]'}>
                    <div
                      data-testid={`chat-message-${message.role}`}
                      className={`rounded-lg px-3 py-2 text-sm ${
                        message.role === 'user'
                          ? 'ml-auto bg-brand-teal text-white'
                          : message.escalated
                            ? 'border border-status-warning-text/30 bg-status-warning-surface text-status-warning-text dark:border-status-warning-text/40 dark:bg-status-warning-surface/20 dark:text-status-warning-surface'
                            : 'bg-surface-page text-brand-dark dark:bg-white/10 dark:text-white'
                      }`}
                    >
                      {message.role === 'assistant' && message.escalated && (
                        <p
                          data-testid="chat-escalation-label"
                          className="mb-1 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-status-warning-text dark:text-status-warning-surface"
                        >
                          ⚠ Escalated to human support
                        </p>
                      )}
                      <p>{message.content}</p>
                    </div>
                    {message.role === 'assistant' &&
                      message.chatMessageId !== undefined &&
                      (message.escalationStatus === 'offered' || message.escalationStatus === 'sending') && (
                        <div
                          data-testid="chat-escalation-offer"
                          className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted dark:text-white/70"
                        >
                          <span>Would you like to escalate this to a human?</span>
                          <button
                            type="button"
                            disabled={message.escalationStatus === 'sending'}
                            onClick={() =>
                              void handleEscalationChoice(message.id, message.chatMessageId as number, true)
                            }
                            className="rounded bg-brand-teal px-2 py-0.5 font-medium text-white hover:bg-brand-darker-teal disabled:opacity-50"
                          >
                            Yes
                          </button>
                          <button
                            type="button"
                            disabled={message.escalationStatus === 'sending'}
                            onClick={() =>
                              void handleEscalationChoice(message.id, message.chatMessageId as number, false)
                            }
                            className="rounded border border-line px-2 py-0.5 font-medium text-muted hover:bg-surface-page disabled:opacity-50 dark:border-white/20 dark:text-white/80 dark:hover:bg-white/10"
                          >
                            No
                          </button>
                        </div>
                      )}
                    {message.role === 'assistant' && message.escalationStatus === 'confirmed' && (
                      <p
                        data-testid="chat-escalation-confirmation"
                        className="mt-1 text-xs text-muted dark:text-white/70"
                      >
                        A support agent has been notified (ref #{message.escalationTicketId})
                        {message.escalationEmailSent ? '' : ' (email notification could not be sent)'}
                      </p>
                    )}
                    {message.role === 'assistant' && message.chatMessageId !== undefined && (
                      <div
                        data-testid="chat-feedback-controls"
                        className="mt-1 flex items-center gap-1.5 text-muted/70 dark:text-white/40"
                      >
                        <button
                          type="button"
                          aria-label="Thumbs up"
                          aria-pressed={message.feedback === true}
                          onClick={() => void handleFeedback(message.id, message.chatMessageId as number, true)}
                          className={`rounded px-1 text-sm hover:text-status-success-text dark:hover:text-status-success-text ${
                            message.feedback === true ? 'text-status-success-text dark:text-status-success-text' : ''
                          }`}
                        >
                          👍
                        </button>
                        <button
                          type="button"
                          aria-label="Thumbs down"
                          aria-pressed={message.feedback === false}
                          onClick={() => void handleFeedback(message.id, message.chatMessageId as number, false)}
                          className={`rounded px-1 text-sm hover:text-status-danger-text dark:hover:text-status-danger-text ${
                            message.feedback === false ? 'text-status-danger-text dark:text-status-danger-text' : ''
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
                <p role="alert" className="px-3 pb-1 text-xs text-status-danger-text dark:text-status-danger-text">
                  {error}
                </p>
              )}

              {needsCustomerSelection ? (
                <div
                  role="status"
                  data-testid="chat-needs-selection"
                  className="border-t border-status-warning-text/30 bg-status-warning-surface px-4 py-3 text-xs text-status-warning-text dark:border-status-warning-text/40 dark:bg-status-warning-surface/20 dark:text-status-warning-surface"
                >
                  Select a driver or vehicle from Overview or Drivers first, so the assistant knows which
                  customer this chat is about.
                </div>
              ) : (
                <form
                  className="flex items-center gap-2 border-t border-line p-3 dark:border-white/10"
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
                    className="flex-1 rounded border border-line bg-white px-2 py-1.5 text-sm text-brand-dark focus:border-brand-teal focus:outline-none dark:border-white/20 dark:bg-white/5 dark:text-white"
                  />
                  <button
                    type="submit"
                    disabled={isSending || inputValue.trim() === ''}
                    className="rounded bg-brand-teal px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-darker-teal disabled:opacity-50"
                  >
                    Send
                  </button>
                </form>
              )}
            </>
          )}
          </div>

          {activeRoutePlan && (
            <div
              data-testid="chat-route-map-panel"
              className="flex-1 border-l border-line p-2 dark:border-white/10"
            >
              <RouteMap routePlan={activeRoutePlan} />
            </div>
          )}
        </div>
      )}

      <button
        type="button"
        onClick={handleToggle}
        aria-label={isOpen ? 'Close chat' : 'Open chat'}
        className="flex h-14 w-14 items-center justify-center rounded-full bg-brand-teal text-2xl text-white shadow-lg hover:bg-brand-darker-teal"
      >
        {isOpen ? '✕' : '💬'}
      </button>
    </div>
  )
}

export default ChatWidget
