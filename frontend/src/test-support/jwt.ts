/**
 * Test-only helper for building a JWT-shaped string (header.payload.signature)
 * with a realistic base64url-encoded payload, so tests can exercise
 * `AuthProvider`'s client-side decode without needing the real backend's
 * signing key. The "signature" segment is a fixed placeholder -- nothing
 * in the frontend verifies it (see `AuthProvider.tsx` module docs for why
 * that's an intentional, documented tradeoff).
 */
function base64UrlEncode(value: Record<string, unknown>): string {
  const json = JSON.stringify(value)
  const base64 = btoa(json)
  return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export function makeFakeJwt(payload: Record<string, unknown>): string {
  const header = base64UrlEncode({ alg: 'HS256', typ: 'JWT' })
  const body = base64UrlEncode(payload)
  return `${header}.${body}.test-signature`
}
