import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider, TOKEN_STORAGE_KEY } from '../context/AuthProvider'
import { Layout } from './Layout'
import { makeFakeJwt } from '../test-support/jwt'

vi.mock('./ChatWidget', () => ({
  ChatWidget: () => <div data-testid="mock-chat-widget" />,
}))

function loginAs(role: 'customer' | 'support_agent') {
  const token = makeFakeJwt({
    sub: '3',
    role,
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 3600,
  })
  localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

function renderLayout(initialPath = '/routes') {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <Layout />
      </MemoryRouter>
    </AuthProvider>
  )
}

describe('Layout', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('links to the Live Tracking page from the nav bar', () => {
    loginAs('customer')
    renderLayout()

    expect(screen.getByRole('link', { name: 'Live Tracking' })).toHaveAttribute('href', '/tracking')
  })

  it("hides Overview and Drivers from a customer's nav", () => {
    loginAs('customer')
    renderLayout()

    expect(screen.getByRole('link', { name: 'Routes' })).toHaveAttribute('href', '/routes')
    expect(screen.getByRole('link', { name: 'Alerts' })).toHaveAttribute('href', '/alerts')
    expect(screen.queryByRole('link', { name: 'Overview' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Drivers' })).not.toBeInTheDocument()
  })

  it("keeps Overview and Drivers for a support agent, but hides Routes (covered by Live Tracking)", () => {
    loginAs('support_agent')
    renderLayout('/overview')

    expect(screen.getByRole('link', { name: 'Overview' })).toHaveAttribute('href', '/overview')
    expect(screen.getByRole('link', { name: 'Drivers' })).toHaveAttribute('href', '/drivers')
    expect(screen.getByRole('link', { name: 'Alerts' })).toHaveAttribute('href', '/alerts')
    expect(screen.queryByRole('link', { name: 'Routes' })).not.toBeInTheDocument()
  })

  it('hides the role label and Log out action until the logo is clicked', () => {
    loginAs('customer')
    renderLayout()

    expect(screen.queryByText('customer')).not.toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'Log out' })).not.toBeInTheDocument()
  })

  it('opens a menu with the role and a Log out action when the logo is clicked', () => {
    loginAs('support_agent')
    renderLayout('/overview')

    fireEvent.click(screen.getByRole('button', { name: 'Ctrack' }))

    expect(screen.getByText('support agent')).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Log out' })).toBeInTheDocument()
  })

  it('logs the user out when the menu\'s Log out action is clicked', () => {
    loginAs('customer')
    renderLayout()

    fireEvent.click(screen.getByRole('button', { name: 'Ctrack' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Log out' }))

    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
  })
})
