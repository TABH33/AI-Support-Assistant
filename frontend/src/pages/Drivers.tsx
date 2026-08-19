/**
 * Driver performance panels: lists drivers (`GET /drivers`, mirroring
 * Overview's fetch pattern) and, for the selected driver, a detail panel
 * showing driving-event counts by type.
 *
 * Task 7's API has no single "events for driver X" endpoint -- only
 * `GET /trips?driver_id=X` and `GET /trips/{id}/events` (per-trip). So the
 * per-driver event breakdown is built by chaining: fetch that driver's
 * trips, then fetch each trip's events in parallel, then tally counts by
 * `event_type` client-side. `event_type` values come from
 * `backend/app/models/enums.py`'s `DrivingEventType` (speeding /
 * harsh_braking / idling / route_deviation) -- not guessed.
 */
import { useEffect, useState } from 'react'
import { apiGet } from '../lib/apiClient'
import type { Driver, DrivingEvent, DrivingEventType, Trip } from '../types/telematics'

/** Display order for event-type badges; also doubles as the set of known types. */
const EVENT_TYPE_ORDER: DrivingEventType[] = [
  'speeding',
  'harsh_braking',
  'idling',
  'route_deviation',
]

/** Label + color-coded badge classes per driving-event type (ASS3 §2.2). */
const EVENT_TYPE_CONFIG: Record<DrivingEventType, { label: string; badgeClass: string }> = {
  speeding: {
    label: 'Speeding',
    badgeClass: 'bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300',
  },
  harsh_braking: {
    label: 'Harsh braking',
    badgeClass: 'bg-orange-100 text-orange-800 dark:bg-orange-900/50 dark:text-orange-300',
  },
  idling: {
    label: 'Idling',
    badgeClass: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-300',
  },
  route_deviation: {
    label: 'Route deviation',
    badgeClass: 'bg-purple-100 text-purple-800 dark:bg-purple-900/50 dark:text-purple-300',
  },
}

type EventCounts = Record<DrivingEventType, number>

function emptyEventCounts(): EventCounts {
  return { speeding: 0, harsh_braking: 0, idling: 0, route_deviation: 0 }
}

/** Fetches every trip for `driverId` then every event for each of those trips, tallying counts by type. */
async function loadDriverEventCounts(
  driverId: number
): Promise<{ counts: EventCounts; tripCount: number }> {
  const trips = await apiGet<Trip[]>(`/trips?driver_id=${driverId}`)
  const eventLists = await Promise.all(
    trips.map((trip) => apiGet<DrivingEvent[]>(`/trips/${trip.trip_id}/events`))
  )
  const counts = emptyEventCounts()
  for (const events of eventLists) {
    for (const event of events) {
      counts[event.event_type] += 1
    }
  }
  return { counts, tripCount: trips.length }
}

export default function Drivers() {
  const [drivers, setDrivers] = useState<Driver[] | null>(null)
  const [driversError, setDriversError] = useState<string | null>(null)
  const [isLoadingDrivers, setIsLoadingDrivers] = useState(true)

  const [selectedDriverId, setSelectedDriverId] = useState<number | null>(null)
  const [eventCounts, setEventCounts] = useState<EventCounts | null>(null)
  const [tripCount, setTripCount] = useState(0)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [isLoadingDetail, setIsLoadingDetail] = useState(false)

  // Load the driver list once on mount.
  useEffect(() => {
    let cancelled = false

    async function load() {
      setIsLoadingDrivers(true)
      setDriversError(null)
      try {
        const data = await apiGet<Driver[]>('/drivers')
        if (!cancelled) {
          setDrivers(data)
          if (data.length > 0) {
            setSelectedDriverId(data[0].driver_id)
          }
        }
      } catch (err) {
        if (!cancelled) {
          setDriversError(err instanceof Error ? err.message : 'Failed to load drivers.')
        }
      } finally {
        if (!cancelled) {
          setIsLoadingDrivers(false)
        }
      }
    }

    void load()

    return () => {
      cancelled = true
    }
  }, [])

  // Load the trips->events chain whenever the selected driver changes.
  useEffect(() => {
    if (selectedDriverId === null) {
      return
    }

    let cancelled = false

    async function loadDetail() {
      setIsLoadingDetail(true)
      setDetailError(null)
      try {
        // selectedDriverId is narrowed to `number` by the guard above, but
        // TS can't see that inside this nested closure -- capture it here.
        const driverId = selectedDriverId as number
        const { counts, tripCount: fetchedTripCount } = await loadDriverEventCounts(driverId)
        if (!cancelled) {
          setEventCounts(counts)
          setTripCount(fetchedTripCount)
        }
      } catch (err) {
        if (!cancelled) {
          setDetailError(err instanceof Error ? err.message : 'Failed to load driving events.')
        }
      } finally {
        if (!cancelled) {
          setIsLoadingDetail(false)
        }
      }
    }

    void loadDetail()

    return () => {
      cancelled = true
    }
  }, [selectedDriverId])

  if (isLoadingDrivers) {
    return (
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Drivers</h1>
        <p className="mt-2 text-gray-600 dark:text-gray-300">Loading drivers…</p>
      </div>
    )
  }

  if (driversError) {
    return (
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Drivers</h1>
        <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
          Failed to load drivers: {driversError}
        </p>
      </div>
    )
  }

  // isLoadingDrivers is false and driversError is null, so drivers must be populated.
  const driverList = drivers as Driver[]
  const selectedDriver = driverList.find((driver) => driver.driver_id === selectedDriverId) ?? null

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Drivers</h1>

      {driverList.length === 0 ? (
        <p className="mt-2 text-gray-600 dark:text-gray-300">No drivers recorded yet.</p>
      ) : (
        <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="lg:col-span-1 overflow-hidden rounded-lg bg-white dark:bg-gray-800 shadow">
            <ul className="divide-y divide-gray-200 dark:divide-gray-700">
              {driverList.map((driver) => (
                <li key={driver.driver_id}>
                  <button
                    type="button"
                    aria-pressed={driver.driver_id === selectedDriverId}
                    onClick={() => setSelectedDriverId(driver.driver_id)}
                    className={`w-full px-4 py-3 text-left text-sm ${
                      driver.driver_id === selectedDriverId
                        ? 'bg-blue-50 dark:bg-blue-900/30 font-medium text-blue-900 dark:text-blue-200'
                        : 'text-gray-900 dark:text-white hover:bg-gray-50 dark:hover:bg-gray-700'
                    }`}
                  >
                    {driver.full_name}
                    <span className="block text-xs text-gray-500 dark:text-gray-400">
                      {driver.license_number}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className="lg:col-span-2 rounded-lg bg-white dark:bg-gray-800 shadow p-4">
            {selectedDriver === null ? (
              <p className="text-gray-600 dark:text-gray-300">Select a driver to see details.</p>
            ) : (
              <>
                <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                  {selectedDriver.full_name}
                </h2>
                <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                  License {selectedDriver.license_number}
                </p>

                {isLoadingDetail ? (
                  <p className="mt-4 text-gray-600 dark:text-gray-300">Loading driving events…</p>
                ) : detailError ? (
                  <p role="alert" className="mt-4 text-sm text-red-600 dark:text-red-400">
                    Failed to load driving events: {detailError}
                  </p>
                ) : (
                  eventCounts && (
                    <>
                      <p className="mt-4 text-sm text-gray-500 dark:text-gray-400">
                        Based on {tripCount} trip{tripCount === 1 ? '' : 's'}
                      </p>
                      <dl className="mt-2 flex flex-wrap gap-2">
                        {EVENT_TYPE_ORDER.map((eventType) => {
                          const config = EVENT_TYPE_CONFIG[eventType]
                          return (
                            <div
                              key={eventType}
                              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium ${config.badgeClass}`}
                              data-testid={`event-badge-${eventType}`}
                            >
                              <dt>{config.label}</dt>
                              <dd className="font-semibold">{eventCounts[eventType]}</dd>
                            </div>
                          )
                        })}
                      </dl>
                    </>
                  )
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
