/**
 * TypeScript mirrors of the Pydantic response models in
 * `backend/app/api/telematics.py` (`DriverOut`, `VehicleOut`, `TripOut`).
 * Field names/nullability were copied directly from that file, not
 * guessed -- keep these in sync if the backend schemas change.
 */

export interface Driver {
  driver_id: number
  customer_id: number
  full_name: string
  license_number: string
  email: string | null
  phone_number: string | null
  created_at: string
}

export interface Vehicle {
  vehicle_id: number
  customer_id: number
  registration_number: string
  make: string
  model: string
  year: number
  created_at: string
}

export interface Trip {
  trip_id: number
  driver_id: number
  vehicle_id: number
  start_time: string
  end_time: string | null
  start_location: string | null
  end_location: string | null
  distance_km: number | null
  created_at: string
}
