import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthProvider'
import ctrackLogo from '../assets/ctrack-logo.png'

interface LocationState {
  from?: { pathname?: string }
}

export default function Login() {
  const { login, isAuthenticated, user } = useAuth()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Overview is support_agent-only (see RequireRole in App.tsx); a customer's
  // default landing page is Routes.
  const defaultRedirect = user?.role === 'support_agent' ? '/overview' : '/routes'
  const redirectTo = (location.state as LocationState | null)?.from?.pathname ?? defaultRedirect

  // Already logged in (e.g. navigated back to /login manually) -- bounce
  // straight to where they were headed instead of showing the form again.
  if (isAuthenticated) {
    return <Navigate to={redirectTo} replace />
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setIsSubmitting(true)
    try {
      await login(email, password)
      // No explicit navigate() call needed: isAuthenticated flips to true
      // on the next render, which triggers the <Navigate> above.
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed. Please try again.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <main className="flex items-center justify-center min-h-screen bg-brand-dark">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm bg-brand-darker-blue rounded-lg shadow-card p-8 space-y-4"
      >
        <div className="flex flex-col items-center gap-3 mb-2">
          <img src={ctrackLogo} alt="Ctrack" className="h-10 w-auto" />
        </div>

        {error && (
          <p role="alert" className="text-sm text-accent-pink bg-white/5 rounded px-3 py-2">
            {error}
          </p>
        )}

        <div>
          <label htmlFor="email" className="block text-sm font-medium text-white/70">
            Email
          </label>
          <input
            id="email"
            name="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="mt-1 w-full rounded border border-white/20 bg-white/10 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-brand-teal"
          />
        </div>

        <div>
          <label htmlFor="password" className="block text-sm font-medium text-white/70">
            Password
          </label>
          <input
            id="password"
            name="password"
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 w-full rounded border border-white/20 bg-white/10 px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-brand-teal"
          />
        </div>

        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full px-4 py-2 bg-brand-teal hover:bg-brand-darker-teal disabled:opacity-50 text-white font-semibold rounded-lg transition duration-200"
        >
          {isSubmitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}
