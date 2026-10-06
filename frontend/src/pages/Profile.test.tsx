import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import { AuthProvider, TOKEN_STORAGE_KEY } from '../context/AuthProvider'
import { makeFakeJwt } from '../test-support/jwt'
import ProfilePage from './Profile'
import type { Profile } from '../types/profile'

function renderPage() {
  return render(
    <AuthProvider>
      <ProfilePage />
    </AuthProvider>
  )
}

function loginAsSupportAgent() {
  const token = makeFakeJwt({
    sub: '7',
    role: 'support_agent',
    access_level: 'admin',
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 3600,
  })
  localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

function loginAsDriver() {
  const token = makeFakeJwt({
    sub: '100',
    role: 'customer',
    driver_id: 11,
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 3600,
  })
  localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

function mockMeFetch(profile: Profile) {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string) => {
    if (url.includes('/auth/me')) {
      return { ok: true, status: 200, json: async () => profile }
    }
    throw new Error(`Unexpected fetch to ${url}`)
  })
}

describe('ProfilePage', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("shows a support agent's profile with no vehicle row", async () => {
    loginAsSupportAgent()
    mockMeFetch({
      role: 'support_agent',
      full_name: 'Alice Agent',
      company: 'Ctrack',
      email: 'alice@example.test',
      phone_number: '+61000000001',
      vehicle: null,
    })

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Alice Agent')).toBeInTheDocument()
    })
    expect(screen.getByText('Ctrack')).toBeInTheDocument()
    expect(screen.getByText('alice@example.test')).toBeInTheDocument()
    expect(screen.getByText('+61000000001')).toBeInTheDocument()
    expect(screen.queryByText('Vehicle')).not.toBeInTheDocument()
  })

  it("shows a driver's profile including their company and assigned vehicle", async () => {
    loginAsDriver()
    mockMeFetch({
      role: 'customer',
      full_name: 'Bob Driver',
      company: 'Acme Logistics',
      email: 'bob@example.test',
      phone_number: '+61000000002',
      vehicle: { registration_number: 'ABC-123', make: 'Toyota', model: 'HiAce' },
    })

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Bob Driver')).toBeInTheDocument()
    })
    expect(screen.getByText('Acme Logistics')).toBeInTheDocument()
    expect(screen.getByText('Toyota HiAce · ABC-123')).toBeInTheDocument()
  })

  it('shows the Ctrack logo as the avatar fallback when no photo is stored', async () => {
    loginAsDriver()
    mockMeFetch({
      role: 'customer',
      full_name: 'Bob Driver',
      company: 'Acme Logistics',
      email: 'bob@example.test',
      phone_number: null,
      vehicle: null,
    })

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Bob Driver')).toBeInTheDocument()
    })
    expect(screen.queryByAltText('Your profile')).not.toBeInTheDocument()
  })

  it('stores an uploaded photo and displays it as the avatar', async () => {
    loginAsDriver()
    mockMeFetch({
      role: 'customer',
      full_name: 'Bob Driver',
      company: 'Acme Logistics',
      email: 'bob@example.test',
      phone_number: null,
      vehicle: null,
    })

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Bob Driver')).toBeInTheDocument()
    })

    const file = new File(['fake-image-bytes'], 'photo.png', { type: 'image/png' })
    const input = screen.getByLabelText(/upload profile photo/i) as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => {
      expect(screen.getByAltText('Your profile')).toBeInTheDocument()
    })
  })
})
