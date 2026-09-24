import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../context/AuthProvider'
import { Layout } from './Layout'

vi.mock('./ChatWidget', () => ({
  ChatWidget: () => <div data-testid="mock-chat-widget" />,
}))

function renderLayout() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/overview']}>
        <Layout />
      </MemoryRouter>
    </AuthProvider>
  )
}

describe('Layout', () => {
  it('links to the Live Tracking page from the nav bar', () => {
    renderLayout()

    expect(screen.getByRole('link', { name: 'Live Tracking' })).toHaveAttribute('href', '/tracking')
  })

  it('keeps the pre-existing nav entries', () => {
    renderLayout()

    expect(screen.getByRole('link', { name: 'Overview' })).toHaveAttribute('href', '/overview')
    expect(screen.getByRole('link', { name: 'Routes' })).toHaveAttribute('href', '/routes')
    expect(screen.getByRole('link', { name: 'Drivers' })).toHaveAttribute('href', '/drivers')
    expect(screen.getByRole('link', { name: 'Alerts' })).toHaveAttribute('href', '/alerts')
  })
})
