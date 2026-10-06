/**
 * Post-resolution Customer Effort Score (CES) micro-survey (Task 22),
 * shown inline inside `ChatWidget.tsx` in place of the message thread when
 * the widget is closed after the user has sent at least one message (see
 * `ChatWidget.tsx`'s module docstring for the trigger-design reasoning).
 *
 * Posts to `POST /chat/sessions/{sessionId}/survey`
 * (`backend/app/api/chat.py`, Task 22), which validates `score` is between
 * 1 and 7 (standard CES-7 scale: "very easy" to "very difficult") and
 * stores it on `ChatSession.ces_score`.
 *
 * Deliberately self-contained (owns its own submit call, like
 * `ChatWidget.handleSend` owns `POST /chat`) rather than having the parent
 * widget make the API call -- keeps the parent's job limited to deciding
 * *when* to show this and what to do once it's dismissed (`onSubmit`/
 * `onSkip`), not how the survey itself is submitted.
 */
import { useCallback, useState } from 'react'
import { apiPost } from '../lib/apiClient'
import type { CesSurveyResponse } from '../types/chat'

const SCORES = [1, 2, 3, 4, 5, 6, 7]

interface CesSurveyProps {
  /** The `ChatSession.chat_session_id` this survey result is stored against. */
  sessionId: number
  /** Called after a successful submit. */
  onSubmit: () => void
  /** Called when the user dismisses the survey without submitting a score. */
  onSkip: () => void
}

export function CesSurvey({ sessionId, onSubmit, onSkip }: CesSurveyProps) {
  const [score, setScore] = useState<number | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = useCallback(async () => {
    if (score === null || isSubmitting) return
    setIsSubmitting(true)
    setError(null)
    try {
      await apiPost<CesSurveyResponse>(`/chat/sessions/${sessionId}/survey`, { score })
      onSubmit()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit feedback.')
      setIsSubmitting(false)
    }
  }, [score, isSubmitting, sessionId, onSubmit])

  return (
    <div
      role="group"
      aria-label="Rate this conversation"
      data-testid="ces-survey"
      className="flex flex-1 flex-col justify-between p-4"
    >
      <div>
        <p className="mb-3 text-sm font-medium text-brand-dark dark:text-white">
          How much effort did it take to resolve your issue today?
        </p>
        <div role="group" aria-label="Effort score, 1 to 7" className="flex justify-between gap-1">
          {SCORES.map((value) => (
            <button
              key={value}
              type="button"
              aria-label={`Score ${value}`}
              aria-pressed={score === value}
              onClick={() => setScore(value)}
              disabled={isSubmitting}
              className={`h-8 w-8 rounded border text-xs font-medium transition-colors disabled:opacity-50 ${
                score === value
                  ? 'border-brand-teal bg-brand-teal text-white'
                  : 'border-line text-muted hover:bg-surface-page dark:border-white/20 dark:text-white/80 dark:hover:bg-white/10'
              }`}
            >
              {value}
            </button>
          ))}
        </div>
        <div className="mt-1 flex justify-between text-[10px] text-muted dark:text-white/60">
          <span>Very easy</span>
          <span>Very difficult</span>
        </div>
      </div>

      {error && (
        <p role="alert" className="mt-3 text-xs text-status-danger-text dark:text-status-danger-text">
          {error}
        </p>
      )}

      <div className="mt-4 flex justify-end gap-2">
        <button
          type="button"
          onClick={onSkip}
          disabled={isSubmitting}
          className="rounded px-3 py-1.5 text-xs text-muted hover:text-brand-dark disabled:opacity-50 dark:text-white/60 dark:hover:text-white"
        >
          Skip
        </button>
        <button
          type="button"
          onClick={() => void handleSubmit()}
          disabled={score === null || isSubmitting}
          className="rounded bg-brand-teal px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-darker-teal disabled:opacity-50"
        >
          Submit
        </button>
      </div>
    </div>
  )
}

export default CesSurvey
