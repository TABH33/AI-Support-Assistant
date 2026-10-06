/**
 * Fleet overview screen: fetches drivers/vehicles/trips from the Task 7
 * telematics endpoints (`GET /drivers`, `GET /vehicles`, `GET /trips`) via
 * `apiClient` (which attaches the caller's JWT automatically, per
 * `AuthProvider`/Task 17) and renders:
 *   - a summary panel (fleet-wide counts + aggregate distance/active trips)
 *   - a route list: a plain table of trips (start/end time, distance,
 *     driver + vehicle names).
 *
 * Deliberate scope simplification (per the Task 18 brief): no map SDK.
 * A list/table view of trips is sufficient for this POC.
 *
 * Task 21 extension: clicking a trip row selects it in `SelectionContext`
 * (also setting that trip's driver/vehicle), so `ChatWidget`'s floating
 * panel can include the currently-selected trip as context on `/chat`.
 */
import { useEffect, useState } from 'react'
import { apiGet } from '../lib/apiClient'
import { useSelection } from '../context/SelectionContext'
import type { Driver, Trip, Vehicle } from '../types/telematics'

interface OverviewData {
  drivers: Driver[]
  vehicles: Vehicle[]
  trips: Trip[]
}

/** Formats an ISO timestamp for display; returns an em dash for null/invalid input. */
function formatDateTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatDistance(km: number | null): string {
  return km === null ? '—' : `${km.toFixed(1)} km`
}

export default function Overview() {
  const [data, setData] = useState<OverviewData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const { selectedTripId, selectTrip } = useSelection()

  useEffect(() => {
    let cancelled = false

    async function load() {
      setIsLoading(true)
      setError(null)
      try {
        const [drivers, vehicles, trips] = await Promise.all([
          apiGet<Driver[]>('/drivers'),
          apiGet<Vehicle[]>('/vehicles'),
          apiGet<Trip[]>('/trips'),
        ])
        if (!cancelled) {
          setData({ drivers, vehicles, trips })
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load fleet data.')
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    }

    void load()

    return () => {
      cancelled = true
    }
  }, [])

  if (isLoading) {
    return (
      <div>
        <h1 className="font-heading text-2xl font-bold text-white">Overview</h1>
        <p className="mt-2 text-white/70">Loading fleet data…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div>
        <h1 className="font-heading text-2xl font-bold text-white">Overview</h1>
        <p role="alert" className="mt-2 text-sm text-accent-pink dark:text-accent-pink">
          Failed to load fleet data: {error}
        </p>
      </div>
    )
  }

  // isLoading is false and error is null, so data must be populated.
  const { drivers, vehicles, trips } = data as OverviewData

  const driverNameById = new Map(drivers.map((driver) => [driver.driver_id, driver.full_name]))
  // Trip has no `customer_id` of its own (see telematics.py's module
  // docstring) -- resolve it transitively via the trip's driver, whose
  // `customer_id` is authoritative (`DriverOut.customer_id`). Used when a
  // trip row is selected, so `SelectionContext.selectedCustomerId` reflects
  // the real owning customer rather than being left stale/wrong.
  const driverCustomerById = new Map(drivers.map((driver) => [driver.driver_id, driver.customer_id]))
  const vehicleLabelById = new Map(
    vehicles.map((vehicle) => [
      vehicle.vehicle_id,
      `${vehicle.make} ${vehicle.model} (${vehicle.registration_number})`,
    ])
  )

  const completedTrips = trips.filter((trip) => trip.end_time !== null)
  const activeTrips = trips.length - completedTrips.length
  const totalDistanceKm = trips.reduce((sum, trip) => sum + (trip.distance_km ?? 0), 0)

  return (
    <div>
      <h1 className="font-heading text-2xl font-bold text-white">Overview</h1>

      <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div className="rounded-lg bg-surface-card dark:bg-brand-darker-blue shadow-card p-4">
          <dt className="text-sm text-muted dark:text-white/60">Drivers</dt>
          <dd className="text-2xl font-semibold text-brand-dark dark:text-white">
            {drivers.length}
          </dd>
        </div>
        <div className="rounded-lg bg-surface-card dark:bg-brand-darker-blue shadow-card p-4">
          <dt className="text-sm text-muted dark:text-white/60">Vehicles</dt>
          <dd className="text-2xl font-semibold text-brand-dark dark:text-white">
            {vehicles.length}
          </dd>
        </div>
        <div className="rounded-lg bg-surface-card dark:bg-brand-darker-blue shadow-card p-4">
          <dt className="text-sm text-muted dark:text-white/60">Trips</dt>
          <dd className="text-2xl font-semibold text-brand-dark dark:text-white">
            {trips.length}
            <span className="ml-2 text-sm font-normal text-muted dark:text-white/60">
              ({activeTrips} active)
            </span>
          </dd>
        </div>
        <div className="rounded-lg bg-surface-card dark:bg-brand-darker-blue shadow-card p-4">
          <dt className="text-sm text-muted dark:text-white/60">Total distance</dt>
          <dd className="text-2xl font-semibold text-brand-dark dark:text-white">
            {totalDistanceKm.toFixed(1)} km
          </dd>
        </div>
      </dl>

      <h2 className="mt-8 font-heading text-lg font-semibold text-white">Routes</h2>
      {trips.length === 0 ? (
        <p className="mt-2 text-white/70">No trips recorded yet.</p>
      ) : (
        <div className="mt-2 overflow-x-auto rounded-lg bg-surface-card dark:bg-brand-darker-blue shadow-card">
          <table className="min-w-full divide-y divide-line dark:divide-white/10">
            <thead>
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-muted dark:text-white/60">
                  Driver
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-muted dark:text-white/60">
                  Vehicle
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-muted dark:text-white/60">
                  Start
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-muted dark:text-white/60">
                  End
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-muted dark:text-white/60">
                  Distance
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line dark:divide-white/10">
              {trips.map((trip) => (
                <tr
                  key={trip.trip_id}
                  onClick={() =>
                    selectTrip({
                      tripId: trip.trip_id,
                      driverId: trip.driver_id,
                      vehicleId: trip.vehicle_id,
                      customerId: driverCustomerById.get(trip.driver_id) ?? null,
                    })
                  }
                  aria-selected={trip.trip_id === selectedTripId}
                  className={`cursor-pointer ${
                    trip.trip_id === selectedTripId
                      ? 'bg-brand-teal/10 dark:bg-brand-teal/20'
                      : 'hover:bg-surface-page dark:hover:bg-white/10'
                  }`}
                >
                  <td className="px-4 py-2 text-sm text-brand-dark dark:text-white">
                    {driverNameById.get(trip.driver_id) ?? `Driver #${trip.driver_id}`}
                  </td>
                  <td className="px-4 py-2 text-sm text-brand-dark dark:text-white">
                    {vehicleLabelById.get(trip.vehicle_id) ?? `Vehicle #${trip.vehicle_id}`}
                  </td>
                  <td className="px-4 py-2 text-sm text-muted dark:text-white/70">
                    {formatDateTime(trip.start_time)}
                  </td>
                  <td className="px-4 py-2 text-sm text-muted dark:text-white/70">
                    {formatDateTime(trip.end_time)}
                  </td>
                  <td className="px-4 py-2 text-sm text-muted dark:text-white/70">
                    {formatDistance(trip.distance_km)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
