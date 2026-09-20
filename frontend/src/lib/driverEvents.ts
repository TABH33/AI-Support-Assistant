/**
 * Shared driver-event-count loading logic, used by DriverDetail (the
 * per-driver page). Extracted from Drivers.tsx so the tally logic isn't
 * duplicated.
 *
 * The API has no single "events for driver X" endpoint -- only
 * `GET /trips?driver_id=X` and `GET /trips/{id}/events` (per-trip). So the
 * per-driver event breakdown is built by chaining: fetch that driver's
 * trips, then fetch each trip's events in parallel, then tally counts by
 * `event_type` client-side. `event_type` values come from
 * `backend/app/models/enums.py`'s `DrivingEventType` (speeding /
 * harsh_braking / idling / route_deviation) -- not guessed.
 */
import { apiGet } from './apiClient'
import type { DrivingEvent, DrivingEventType, Trip } from '../types/telematics'

/** Display order for event-type badges; also doubles as the set of known types. */
export const EVENT_TYPE_ORDER: DrivingEventType[] = [
  'speeding',
  'harsh_braking',
  'idling',
  'route_deviation',
]

/** Label + color-coded badge classes per driving-event type (ASS3 §2.2). */
export const EVENT_TYPE_CONFIG: Record<DrivingEventType, { label: string; badgeClass: string }> = {
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

export type EventCounts = Record<DrivingEventType, number>

export function emptyEventCounts(): EventCounts {
  return { speeding: 0, harsh_braking: 0, idling: 0, route_deviation: 0 }
}

/** Fetches every trip for `driverId` then every event for each of those trips, tallying counts by type. */
export async function loadDriverEventCounts(
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
