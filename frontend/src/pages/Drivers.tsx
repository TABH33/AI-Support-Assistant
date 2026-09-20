/**
 * Driver list: fetches `GET /drivers` and links each row to its own
 * dedicated page (`/drivers/:driverId`, see DriverDetail.tsx) rather than
 * showing an inline detail panel -- selecting a driver navigates to that
 * driver's page, so it's a real bookmarkable/shareable URL.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiGet } from '../lib/apiClient'
import type { Driver } from '../types/telematics'

export default function Drivers() {
  const [drivers, setDrivers] = useState<Driver[] | null>(null)
  const [driversError, setDriversError] = useState<string | null>(null)
  const [isLoadingDrivers, setIsLoadingDrivers] = useState(true)

  useEffect(() => {
    let cancelled = false

    async function load() {
      setIsLoadingDrivers(true)
      setDriversError(null)
      try {
        const data = await apiGet<Driver[]>('/drivers')
        if (!cancelled) {
          setDrivers(data)
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

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Drivers</h1>

      {driverList.length === 0 ? (
        <p className="mt-2 text-gray-600 dark:text-gray-300">No drivers recorded yet.</p>
      ) : (
        <div className="mt-4 overflow-hidden rounded-lg bg-white dark:bg-gray-800 shadow">
          <ul className="divide-y divide-gray-200 dark:divide-gray-700">
            {driverList.map((driver) => (
              <li key={driver.driver_id}>
                <Link
                  to={`/drivers/${driver.driver_id}`}
                  className="block w-full px-4 py-3 text-left text-sm text-gray-900 hover:bg-gray-50 dark:text-white dark:hover:bg-gray-700"
                >
                  {driver.full_name}
                  <span className="block text-xs text-gray-500 dark:text-gray-400">
                    {driver.license_number}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
