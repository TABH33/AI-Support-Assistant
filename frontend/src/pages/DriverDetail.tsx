/**
 * Per-driver page (`/drivers/:driverId`): the driver's own record plus a
 * driving-event breakdown, reached by selecting a driver from the Drivers
 * list. Mirrors `selectedDriver`'s data into `SelectionContext` on mount
 * (Task 21's pattern) so `ChatWidget`'s floating panel can include the
 * currently-viewed driver as context on `/chat`, regardless of how this
 * page was reached (list click, browser back/forward, a direct URL).
 */
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiGet } from '../lib/apiClient'
import { useSelection } from '../context/SelectionContext'
import {
  EVENT_TYPE_ORDER,
  EVENT_TYPE_CONFIG,
  loadDriverEventCounts,
  type EventCounts,
} from '../lib/driverEvents'
import type { Driver } from '../types/telematics'

export default function DriverDetail() {
  const { driverId } = useParams<{ driverId: string }>()
  const { selectDriver } = useSelection()

  const [driver, setDriver] = useState<Driver | null>(null)
  const [driverError, setDriverError] = useState<string | null>(null)
  const [isLoadingDriver, setIsLoadingDriver] = useState(true)

  const [eventCounts, setEventCounts] = useState<EventCounts | null>(null)
  const [tripCount, setTripCount] = useState(0)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [isLoadingDetail, setIsLoadingDetail] = useState(true)

  useEffect(() => {
    if (!driverId) {
      return
    }
    let cancelled = false

    async function load() {
      setIsLoadingDriver(true)
      setDriverError(null)
      try {
        const data = await apiGet<Driver>(`/drivers/${driverId}`)
        if (!cancelled) {
          setDriver(data)
          selectDriver(data.driver_id, data.customer_id)
        }
      } catch (err) {
        if (!cancelled) {
          setDriverError(err instanceof Error ? err.message : 'Failed to load driver.')
        }
      } finally {
        if (!cancelled) {
          setIsLoadingDriver(false)
        }
      }
    }

    void load()

    return () => {
      cancelled = true
    }
  }, [driverId, selectDriver])

  useEffect(() => {
    if (!driverId) {
      return
    }
    let cancelled = false

    async function loadDetail() {
      setIsLoadingDetail(true)
      setDetailError(null)
      try {
        const { counts, tripCount: fetchedTripCount } = await loadDriverEventCounts(
          Number(driverId)
        )
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
  }, [driverId])

  return (
    <div>
      <Link to="/drivers" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
        ← Back to drivers
      </Link>

      {isLoadingDriver ? (
        <p className="mt-4 text-gray-600 dark:text-gray-300">Loading driver…</p>
      ) : driverError ? (
        <p role="alert" className="mt-4 text-sm text-red-600 dark:text-red-400">
          Failed to load driver: {driverError}
        </p>
      ) : (
        driver && (
          <div className="mt-4 rounded-lg bg-white dark:bg-gray-800 shadow p-4">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">{driver.full_name}</h1>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              License {driver.license_number}
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
          </div>
        )
      )}
    </div>
  )
}
