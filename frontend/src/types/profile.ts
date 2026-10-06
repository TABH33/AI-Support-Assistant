/**
 * Mirrors `MeResponse`/`MeVehicle` in `backend/app/api/auth.py`
 * (`GET /auth/me`). Field names/nullability copied directly from that
 * file, not guessed.
 */

export interface ProfileVehicle {
  registration_number: string
  make: string
  model: string
}

export interface Profile {
  role: string
  full_name: string
  company: string
  email: string | null
  phone_number: string | null
  vehicle: ProfileVehicle | null
}
