/**
 * TypeScript mirrors of `ChatRequest`/`ChatResponse` in
 * `backend/app/api/chat.py` (Task 15's `POST /chat`). Field
 * names/nullability/required-ness were copied directly from that file's
 * Pydantic models, not guessed -- keep these in sync if the backend schema
 * changes.
 *
 * `ChatRequest`:
 *   - `session_id`: reuse an existing session if given; a new one is
 *     created if omitted/null (in which case `device_id` is required, and
 *     `customer_id` is required for a `support_agent` caller -- see
 *     `_create_new_session` in `chat.py`).
 *   - `query`: required.
 *   - `driver_id`/`trip_id`/`vehicle_id`/`device_id`/`customer_id`: all
 *     optional (`int | None = None` on the backend).
 */
import type { RoutePlanResult } from './routePlan'

export interface ChatRequest {
  session_id?: number | null
  query: string
  driver_id?: number | null
  trip_id?: number | null
  vehicle_id?: number | null
  device_id?: number | null
  customer_id?: number | null
}

export interface ChatResponse {
  session_id: number
  /** `ChatMessage.chat_message_id` of the assistant's turn (Task 22) --
   * needed to submit thumbs up/down feedback via
   * `PATCH /chat/messages/{message_id}/feedback`, and to accept an
   * escalation offer via `POST /chat/messages/{message_id}/escalate`. */
  message_id: number
  answer: string
  confidence: number
  /** "A ticket now exists for this exchange." Always `false` from
   * `POST /chat` since escalation became opt-in. */
  escalated: boolean
  /** `true` on a low-confidence answer: the widget should ASK whether to
   * escalate to a human. Nothing has been escalated yet. */
  escalation_offered: boolean
  /** Structured route data -- only present when this turn answered a
   * route-plan chat intent successfully (route-planning + warnings
   * feature). See backend/app/api/chat.py's ChatResponse. */
  route_plan?: RoutePlanResult | null
}

/**
 * TypeScript mirrors of Task 22's feedback/survey schemas
 * (`ChatMessageFeedbackRequest`/`Response`, `CesSurveyRequest`/`Response`
 * in `backend/app/api/chat.py`), copied directly from that file's Pydantic
 * models.
 */
export interface ChatMessageFeedbackRequest {
  feedback: boolean
}

export interface ChatMessageFeedbackResponse {
  chat_message_id: number
  feedback: boolean
  escalated: boolean
  support_ticket_id: number | null
}

/** `POST /chat/messages/{message_id}/escalate` response
 * (`ChatMessageEscalateResponse` in `backend/app/api/chat.py`). The ticket
 * always exists on a 200; `email_sent: false` only means the notification
 * email could not be delivered. */
export interface ChatMessageEscalateResponse {
  support_ticket_id: number
  email_sent: boolean
}

/** `score`: Customer Effort Score, 1 (very easy) - 7 (very difficult) --
 * the backend rejects anything outside that range with a 422. */
export interface CesSurveyRequest {
  score: number
}

export interface CesSurveyResponse {
  chat_session_id: number
  ces_score: number
}
