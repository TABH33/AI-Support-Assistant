/**
 * API client for communicating with the backend.
 * Reads the API base URL from the VITE_API_BASE_URL environment variable.
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api'

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

  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  })

  if (!response.ok) {
    throw new Error(`API error: ${response.statusText}`)
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
