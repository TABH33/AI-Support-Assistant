import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import { AuthProvider, useAuth, TOKEN_STORAGE_KEY } from './AuthProvider'
import { makeFakeJwt } from '../test-support/jwt'

/** Minimal consumer that exercises the AuthProvider contract via the UI. */
function TestConsumer() {
  const { login, logout, user, isAuthenticated } = useAuth()
  const handleLogin = () => {
    login('test@example.com', 'hunter2').catch((err: unknown) => {
      const message = err instanceof Error ? err.message : 'unknown error'
      const el = document.getElementById('error')
      if (el) el.textContent = message
    })
  }
  return (
    <div>
      <div data-testid="status">
        {isAuthenticated ? `authenticated:${user?.role}:${user?.id}` : 'anonymous'}
      </div>
      <button onClick={handleLogin}>Login</button>
      <button onClick={logout}>Logout</button>
      <div id="error" data-testid="error" />
    </div>
  )
}

function futureExp(): number {
  return Math.floor(Date.now() / 1000) + 3600
}

describe('AuthProvider', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('logs in successfully against the real /auth/login response shape and stores the user', async () => {
    const token = makeFakeJwt({
      sub: '42',
      role: 'customer',
      iat: Math.floor(Date.now() / 1000),
      exp: futureExp(),
    })
    ;(fetch as unknown as Mock).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        access_token: token,
        token_type: 'bearer',
        role: 'customer',
        access_level: null,
      }),
    })

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    )

    expect(screen.getByTestId('status')).toHaveTextContent('anonymous')

    fireEvent.click(screen.getByText('Login'))

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('authenticated:customer:42')
    })

    // Hit the real endpoint shape with the real request body shape.
    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, options] = (fetch as unknown as Mock).mock.calls[0]
    expect(url).toContain('/auth/login')
    expect(options.method).toBe('POST')
    expect(JSON.parse(options.body)).toEqual({ email: 'test@example.com', password: 'hunter2' })

    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBe(token)
  })

  it('surfaces a login failure instead of swallowing it', async () => {
    ;(fetch as unknown as Mock).mockResolvedValueOnce({
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      json: async () => ({ detail: 'Incorrect email or password' }),
    })

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    )

    fireEvent.click(screen.getByText('Login'))

    await waitFor(() => {
      expect(screen.getByTestId('error')).toHaveTextContent('Incorrect email or password')
    })

    // Failure must not leave the user in an authenticated state, and must
    // not write a token to storage.
    expect(screen.getByTestId('status')).toHaveTextContent('anonymous')
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
  })

  it('logout clears the stored token and user', async () => {
    const token = makeFakeJwt({
      sub: '7',
      role: 'support_agent',
      access_level: 'admin',
      iat: Math.floor(Date.now() / 1000),
      exp: futureExp(),
    })
    ;(fetch as unknown as Mock).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        access_token: token,
        token_type: 'bearer',
        role: 'support_agent',
        access_level: 'admin',
      }),
    })

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    )

    fireEvent.click(screen.getByText('Login'))
    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
    })

    fireEvent.click(screen.getByText('Logout'))

    expect(screen.getByTestId('status')).toHaveTextContent('anonymous')
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
  })

  it('rehydrates the session from a valid token already in localStorage', async () => {
    const token = makeFakeJwt({
      sub: '9',
      role: 'customer',
      iat: Math.floor(Date.now() / 1000),
      exp: futureExp(),
    })
    localStorage.setItem(TOKEN_STORAGE_KEY, token)

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    )

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('authenticated:customer:9')
    })
  })

  it('discards an expired token found in localStorage', async () => {
    const expiredToken = makeFakeJwt({
      sub: '9',
      role: 'customer',
      iat: Math.floor(Date.now() / 1000) - 7200,
      exp: Math.floor(Date.now() / 1000) - 3600,
    })
    localStorage.setItem(TOKEN_STORAGE_KEY, expiredToken)

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    )

    await waitFor(() => {
      expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
    })
    expect(screen.getByTestId('status')).toHaveTextContent('anonymous')
  })
})
