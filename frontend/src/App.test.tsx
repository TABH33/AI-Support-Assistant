import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import App from './App'

describe('App', () => {
  it('redirects an unauthenticated visitor to the login page', async () => {
    localStorage.clear()
    render(<App />)

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /sign in/i })).toBeInTheDocument()
    })
  })

  it('renders the login form fields', async () => {
    localStorage.clear()
    render(<App />)

    await waitFor(() => {
      expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    })
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })
})
