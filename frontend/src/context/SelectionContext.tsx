/**
 * Selection context: the shared "what is the user currently looking at"
 * state -- selected driver/trip/vehicle ids -- lifted out of Overview.tsx
 * (Task 18) and Drivers.tsx (Task 19) so ChatWidget (Task 21) can read
 * whatever the user was just looking at and pass it along as
 * `driver_id`/`trip_id`/`vehicle_id` on `POST /chat` (see `ChatRequest` in
 * `backend/app/api/chat.py`) -- the backend uses these to pull relevant
 * telematics context into the AI's answer via `retrieve_context` (Task 12).
 *
 * Deliberately minimal, mirroring `AuthProvider`'s
 * createContext/useContext/Provider shell (Task 17): three nullable ids
 * plus setters, no persistence, no validation against what actually exists
 * (Overview/Drivers already validate ids are real before calling these).
 */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

export interface SelectionState {
  selectedDriverId: number | null
  selectedTripId: number | null
  selectedVehicleId: number | null
}

export interface SelectionContextValue extends SelectionState {
  /** Sets the selected driver directly (used by Drivers.tsx's driver list). */
  selectDriver: (driverId: number | null) => void
  /**
   * Sets the selected trip (used by Overview.tsx's trip table). A trip
   * implies a driver and a vehicle, so this also updates those two ids
   * when provided -- pass `undefined` (not `null`) for a field to leave it
   * unchanged.
   */
  selectTrip: (trip: {
    tripId: number | null
    driverId?: number | null
    vehicleId?: number | null
  }) => void
}

const SelectionContext = createContext<SelectionContextValue | undefined>(undefined)

export function SelectionProvider({ children }: { children: ReactNode }) {
  const [selectedDriverId, setSelectedDriverId] = useState<number | null>(null)
  const [selectedTripId, setSelectedTripId] = useState<number | null>(null)
  const [selectedVehicleId, setSelectedVehicleId] = useState<number | null>(null)

  const selectDriver = useCallback((driverId: number | null) => {
    setSelectedDriverId(driverId)
  }, [])

  const selectTrip = useCallback(
    (trip: { tripId: number | null; driverId?: number | null; vehicleId?: number | null }) => {
      setSelectedTripId(trip.tripId)
      if (trip.driverId !== undefined) {
        setSelectedDriverId(trip.driverId)
      }
      if (trip.vehicleId !== undefined) {
        setSelectedVehicleId(trip.vehicleId)
      }
    },
    []
  )

  const value = useMemo<SelectionContextValue>(
    () => ({
      selectedDriverId,
      selectedTripId,
      selectedVehicleId,
      selectDriver,
      selectTrip,
    }),
    [selectedDriverId, selectedTripId, selectedVehicleId, selectDriver, selectTrip]
  )

  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>
}

export function useSelection(): SelectionContextValue {
  const ctx = useContext(SelectionContext)
  if (!ctx) {
    throw new Error('useSelection must be used within a SelectionProvider')
  }
  return ctx
}
