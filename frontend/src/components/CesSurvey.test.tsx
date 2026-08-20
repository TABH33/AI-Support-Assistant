import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import { CesSurvey } from './CesSurvey'

describe('CesSurvey', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('posts the selected score to POST /chat/sessions/{sessionId}/survey and calls onSubmit', async () => {
    ;(fetch as unknown as Mock).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ chat_session_id: 42, ces_score: 4 }),
    })

    const onSubmit = vi.fn()
    const onSkip = vi.fn()
    render(<CesSurvey sessionId={42} onSubmit={onSubmit} onSkip={onSkip} />)

    fireEvent.click(screen.getByRole('button', { name: 'Score 4' }))
    fireEvent.click(screen.getByRole('button', { name: /^submit$/i }))

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1)
    })

    const [url, options] = (fetch as unknown as Mock).mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/chat/sessions/42/survey')
    expect(options.method).toBe('POST')
    expect(JSON.parse(options.body as string)).toEqual({ score: 4 })
    expect(onSkip).not.toHaveBeenCalled()
  })

  it('disables the submit button until a score is chosen', () => {
    render(<CesSurvey sessionId={42} onSubmit={vi.fn()} onSkip={vi.fn()} />)
    expect(screen.getByRole('button', { name: /^submit$/i })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Score 2' }))
    expect(screen.getByRole('button', { name: /^submit$/i })).not.toBeDisabled()
  })

  it('marks the selected score button as pressed', () => {
    render(<CesSurvey sessionId={42} onSubmit={vi.fn()} onSkip={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Score 7' }))

    expect(screen.getByRole('button', { name: 'Score 7' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Score 1' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('calls onSkip without making a network request when skipped', () => {
    const onSubmit = vi.fn()
    const onSkip = vi.fn()
    render(<CesSurvey sessionId={42} onSubmit={onSubmit} onSkip={onSkip} />)

    fireEvent.click(screen.getByRole('button', { name: /^skip$/i }))

    expect(onSkip).toHaveBeenCalledTimes(1)
    expect(onSubmit).not.toHaveBeenCalled()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('shows an error and does not call onSubmit when the request fails', async () => {
    ;(fetch as unknown as Mock).mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: async () => ({}),
    })

    const onSubmit = vi.fn()
    render(<CesSurvey sessionId={42} onSubmit={onSubmit} onSkip={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Score 1' }))
    fireEvent.click(screen.getByRole('button', { name: /^submit$/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
    expect(onSubmit).not.toHaveBeenCalled()
    // Recoverable: the submit button is usable again after a failure.
    expect(screen.getByRole('button', { name: /^submit$/i })).not.toBeDisabled()
  })
})
