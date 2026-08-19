import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import App from './App'

describe('App', () => {
  it('renders the placeholder landing page', () => {
    render(<App />)

    const heading = screen.getByRole('heading', {
      name: /Telematics AI Assistant/i,
    })
    expect(heading).toBeInTheDocument()
  })

  it('renders the subtitle text', () => {
    render(<App />)

    const subtitle = screen.getByText(/AI-Driven Support for Fleet Management/i)
    expect(subtitle).toBeInTheDocument()
  })

  it('renders the welcome message', () => {
    render(<App />)

    const message = screen.getByText(/Welcome to the intelligent telematics platform/i)
    expect(message).toBeInTheDocument()
  })

  it('renders the Get Started button', () => {
    render(<App />)

    const button = screen.getByRole('button', { name: /Get Started/i })
    expect(button).toBeInTheDocument()
  })
})
