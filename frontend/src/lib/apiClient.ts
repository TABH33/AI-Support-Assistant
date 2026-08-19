/**
 * API client for communicating with the backend.
 * Reads the API base URL from the VITE_API_BASE_URL environment variable.
 */

// The backend (see `backend/app/main.py`) mounts its routers at the root
// path (e.g. `POST /auth/login`, not `POST /api/auth/login`) -- there is no
// `/api` prefix anywhere in the FastAPI app. The default here matches that.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

/**
 * In-memory bearer token attached to every request once set. Kept as
 * module-level state (rather than threading a token through every
 * `apiGet`/`apiPost` call site) so call sites don't need to know about
 * auth at all -- `AuthProvider` is the sole caller of `setAuthToken`.
 */
let authToken: string | null = null

/**
 * Sets (or clears, with `null`) the bearer token attached to subsequent
 * requests as an `Authorization: Bearer <token>` header. Called by
 * `AuthProvider` on login/logout/rehydration.
 */
export function setAuthToken(token: string | null): void {
  authToken = token
}

/**
 * Fetches data from the API.
 * @param endpoint - The API endpoint (relative to the base URL)
 * @param options - Fetch options
 * @returns The parsed JSON response
 */
export async function fetchAPI<T>(
  endpoint: string,
  options?: RequestInit
): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options?.headers as Record<string, string> | undefined),
  }
  if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`
  }

  const response = await fetch(url, {
    ...options,
    headers,
  })

  if (!response.ok) {
    // FastAPI's HTTPException responses carry the real error message in a
    // JSON `detail` field (e.g. "Incorrect email or password") -- prefer
    // that over the generic HTTP status text so failures are actionable
    // instead of just "API error: Unauthorized".
    let message = `API error: ${response.statusText || response.status}`
    try {
      const body = await response.json()
      if (body && typeof body.detail === 'string') {
        message = body.detail
      }
    } catch {
      // Response body wasn't JSON (or was empty) -- fall back to the
      // generic status-based message above.
    }
    throw new Error(message)
  }

  return response.json()
}

/**
 * Performs a GET request to the API.
 * @param endpoint - The API endpoint
 * @returns The parsed JSON response
 */
export async function apiGet<T>(endpoint: string): Promise<T> {
  return fetchAPI<T>(endpoint, { method: 'GET' })
}

/**
 * Performs a POST request to the API.
 * @param endpoint - The API endpoint
 * @param data - The request body data
 * @returns The parsed JSON response
 */
export async function apiPost<T>(
  endpoint: string,
  data: unknown
): Promise<T> {
  return fetchAPI<T>(endpoint, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

/**
 * Performs a PUT request to the API.
 * @param endpoint - The API endpoint
 * @param data - The request body data
 * @returns The parsed JSON response
 */
export async function apiPut<T>(
  endpoint: string,
  data: unknown
): Promise<T> {
  return fetchAPI<T>(endpoint, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

/**
 * Performs a DELETE request to the API.
 * @param endpoint - The API endpoint
 * @returns The parsed JSON response
 */
export async function apiDelete<T>(endpoint: string): Promise<T> {
  return fetchAPI<T>(endpoint, { method: 'DELETE' })
}
