import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider, TOKEN_STORAGE_KEY } from '../context/AuthProvider'
import { RequireRole } from './RequireRole'
import { makeFakeJwt } from '../test-support/jwt'

function renderGuarded(initialPath: string) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/routes" element={<div>Fallback Page</div>} />
          <Route
            path="/overview"
            element={
              <RequireRole role="support_agent" redirectTo="/routes">
                <div>Support Agent Content</div>
              </RequireRole>
            }
          />
        </Routes>
      </MemoryRouter>
    </AuthProvider>
  )
}

function loginAs(role: 'customer' | 'support_agent') {
  const token = makeFakeJwt({
    sub: '3',
    role,
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 3600,
  })
  localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

describe('RequireRole', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('renders the children when the user has the required role', async () => {
    loginAs('support_agent')

    renderGuarded('/overview')

    await waitFor(() => {
      expect(screen.getByText('Support Agent Content')).toBeInTheDocument()
    })
  })

  it('redirects to the fallback route when the user lacks the required role', async () => {
    loginAs('customer')

    renderGuarded('/overview')

    await waitFor(() => {
      expect(screen.getByText('Fallback Page')).toBeInTheDocument()
    })
    expect(screen.queryByText('Support Agent Content')).not.toBeInTheDocument()
  })
})
