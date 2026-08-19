import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider, TOKEN_STORAGE_KEY } from '../context/AuthProvider'
import { ProtectedRoute } from './ProtectedRoute'
import { makeFakeJwt } from '../test-support/jwt'

function renderProtected(initialPath: string) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/login" element={<div>Login Page</div>} />
          <Route
            path="/protected"
            element={
              <ProtectedRoute>
                <div>Secret Content</div>
              </ProtectedRoute>
            }
          />
        </Routes>
      </MemoryRouter>
    </AuthProvider>
  )
}

describe('ProtectedRoute', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('redirects to /login when there is no authenticated session', async () => {
    renderProtected('/protected')

    await waitFor(() => {
      expect(screen.getByText('Login Page')).toBeInTheDocument()
    })
    expect(screen.queryByText('Secret Content')).not.toBeInTheDocument()
  })

  it('renders children when a valid session is present', async () => {
    const token = makeFakeJwt({
      sub: '3',
      role: 'customer',
      iat: Math.floor(Date.now() / 1000),
      exp: Math.floor(Date.now() / 1000) + 3600,
    })
    localStorage.setItem(TOKEN_STORAGE_KEY, token)

    renderProtected('/protected')

    await waitFor(() => {
      expect(screen.getByText('Secret Content')).toBeInTheDocument()
    })
    expect(screen.queryByText('Login Page')).not.toBeInTheDocument()
  })

  it('redirects when the stored session is expired', async () => {
    const expiredToken = makeFakeJwt({
      sub: '3',
      role: 'customer',
      iat: Math.floor(Date.now() / 1000) - 7200,
      exp: Math.floor(Date.now() / 1000) - 3600,
    })
    localStorage.setItem(TOKEN_STORAGE_KEY, expiredToken)

    renderProtected('/protected')

    await waitFor(() => {
      expect(screen.getByText('Login Page')).toBeInTheDocument()
    })
    expect(screen.queryByText('Secret Content')).not.toBeInTheDocument()
  })
})
