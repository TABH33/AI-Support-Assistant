/**
 * Selection context: the shared "what is the user currently looking at"
 * state -- selected driver/trip/vehicle ids -- lifted out of Overview.tsx
 * (Task 18) and Drivers.tsx (Task 19) so ChatWidget (Task 21) can read
 * whatever the user was just looking at and pass it along as
 * `driver_id`/`trip_id`/`vehicle_id` on `POST /chat` (see `ChatRequest` in
 * `backend/app/api/chat.py`) -- the backend uses these to pull relevant
 * telematics context into the AI's answer via `retrieve_context` (Task 12).
 *
 * Also tracks `selectedCustomerId` -- the `customer_id` that *owns* the
 * selected driver/trip/vehicle (from `DriverOut.customer_id` /
 * `VehicleOut.customer_id`, or a trip's driver's `customer_id` -- Trip
 * itself has no `customer_id` column, see `backend/app/api/telematics.py`'s
 * module docstring). This exists specifically so `ChatWidget` can resolve
 * a `support_agent` caller's `customer_id` from the entity the agent
 * actually selected, rather than guessing it from an unrelated device
 * (see `ChatWidget.tsx`'s module docstring for the incident this fixes:
 * an earlier version derived `customer_id` from an arbitrary
 * most-recently-active device across ALL customers, which could silently
 * attribute a new chat session -- and any escalated support ticket -- to
 * the wrong customer).
 *
 * Deliberately minimal, mirroring `AuthProvider`'s
 * createContext/useContext/Provider shell (Task 17): four nullable ids
 * plus setters, no persistence, no validation against what actually exists
 * (Overview/Drivers already validate ids are real before calling these).
 */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

export interface SelectionState {
  selectedDriverId: number | null
  selectedTripId: number | null
  selectedVehicleId: number | null
  /** The `customer_id` that owns whatever is currently selected. See module docs above. */
  selectedCustomerId: number | null
}

export interface SelectionContextValue extends SelectionState {
  /**
   * Sets the selected driver directly (used by Drivers.tsx's driver list).
   * `customerId` is that driver's own `customer_id` (`DriverOut.customer_id`)
   * -- pass it whenever `driverId` is non-null so `selectedCustomerId` stays
   * accurate; omit/pass `undefined` to leave `selectedCustomerId` unchanged.
   */
  selectDriver: (driverId: number | null, customerId?: number | null) => void
  /**
   * Sets the selected trip (used by Overview.tsx's trip table). A trip
   * implies a driver, a vehicle, and (transitively, via the driver) a
   * customer, so this also updates those ids when provided -- pass
   * `undefined` (not `null`) for a field to leave it unchanged.
   */
  selectTrip: (trip: {
    tripId: number | null
    driverId?: number | null
    vehicleId?: number | null
    customerId?: number | null
  }) => void
}

const SelectionContext = createContext<SelectionContextValue | undefined>(undefined)

export function SelectionProvider({ children }: { children: ReactNode }) {
  const [selectedDriverId, setSelectedDriverId] = useState<number | null>(null)
  const [selectedTripId, setSelectedTripId] = useState<number | null>(null)
  const [selectedVehicleId, setSelectedVehicleId] = useState<number | null>(null)
  const [selectedCustomerId, setSelectedCustomerId] = useState<number | null>(null)

  const selectDriver = useCallback((driverId: number | null, customerId?: number | null) => {
    setSelectedDriverId(driverId)
    if (customerId !== undefined) {
      setSelectedCustomerId(customerId)
    }
  }, [])

  const selectTrip = useCallback(
    (trip: {
      tripId: number | null
      driverId?: number | null
      vehicleId?: number | null
      customerId?: number | null
    }) => {
      setSelectedTripId(trip.tripId)
      if (trip.driverId !== undefined) {
        setSelectedDriverId(trip.driverId)
      }
      if (trip.vehicleId !== undefined) {
        setSelectedVehicleId(trip.vehicleId)
      }
      if (trip.customerId !== undefined) {
        setSelectedCustomerId(trip.customerId)
      }
    },
    []
  )

  const value = useMemo<SelectionContextValue>(
    () => ({
      selectedDriverId,
      selectedTripId,
      selectedVehicleId,
      selectedCustomerId,
      selectDriver,
      selectTrip,
    }),
    [selectedDriverId, selectedTripId, selectedVehicleId, selectedCustomerId, selectDriver, selectTrip]
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
