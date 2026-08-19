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
 */
import { useEffect, useState } from 'react'
import { apiGet } from '../lib/apiClient'
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
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Overview</h1>
        <p className="mt-2 text-gray-600 dark:text-gray-300">Loading fleet data…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Overview</h1>
        <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
          Failed to load fleet data: {error}
        </p>
      </div>
    )
  }

  // isLoading is false and error is null, so data must be populated.
  const { drivers, vehicles, trips } = data as OverviewData

  const driverNameById = new Map(drivers.map((driver) => [driver.driver_id, driver.full_name]))
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
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Overview</h1>

      <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div className="rounded-lg bg-white dark:bg-gray-800 shadow p-4">
          <dt className="text-sm text-gray-500 dark:text-gray-400">Drivers</dt>
          <dd className="text-2xl font-semibold text-gray-900 dark:text-white">
            {drivers.length}
          </dd>
        </div>
        <div className="rounded-lg bg-white dark:bg-gray-800 shadow p-4">
          <dt className="text-sm text-gray-500 dark:text-gray-400">Vehicles</dt>
          <dd className="text-2xl font-semibold text-gray-900 dark:text-white">
            {vehicles.length}
          </dd>
        </div>
        <div className="rounded-lg bg-white dark:bg-gray-800 shadow p-4">
          <dt className="text-sm text-gray-500 dark:text-gray-400">Trips</dt>
          <dd className="text-2xl font-semibold text-gray-900 dark:text-white">
            {trips.length}
            <span className="ml-2 text-sm font-normal text-gray-500 dark:text-gray-400">
              ({activeTrips} active)
            </span>
          </dd>
        </div>
        <div className="rounded-lg bg-white dark:bg-gray-800 shadow p-4">
          <dt className="text-sm text-gray-500 dark:text-gray-400">Total distance</dt>
          <dd className="text-2xl font-semibold text-gray-900 dark:text-white">
            {totalDistanceKm.toFixed(1)} km
          </dd>
        </div>
      </dl>

      <h2 className="mt-8 text-lg font-semibold text-gray-900 dark:text-white">Routes</h2>
      {trips.length === 0 ? (
        <p className="mt-2 text-gray-600 dark:text-gray-300">No trips recorded yet.</p>
      ) : (
        <div className="mt-2 overflow-x-auto rounded-lg bg-white dark:bg-gray-800 shadow">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead>
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:text-gray-400">
                  Driver
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:text-gray-400">
                  Vehicle
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:text-gray-400">
                  Start
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:text-gray-400">
                  End
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:text-gray-400">
                  Distance
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {trips.map((trip) => (
                <tr key={trip.trip_id}>
                  <td className="px-4 py-2 text-sm text-gray-900 dark:text-white">
                    {driverNameById.get(trip.driver_id) ?? `Driver #${trip.driver_id}`}
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-900 dark:text-white">
                    {vehicleLabelById.get(trip.vehicle_id) ?? `Vehicle #${trip.vehicle_id}`}
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-600 dark:text-gray-300">
                    {formatDateTime(trip.start_time)}
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-600 dark:text-gray-300">
                    {formatDateTime(trip.end_time)}
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-600 dark:text-gray-300">
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
