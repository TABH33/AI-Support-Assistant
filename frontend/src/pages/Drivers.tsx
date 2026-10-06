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
        <h1 className="font-heading text-2xl font-bold text-white">Drivers</h1>
        <p className="mt-2 text-white/70">Loading drivers…</p>
      </div>
    )
  }

  if (driversError) {
    return (
      <div>
        <h1 className="font-heading text-2xl font-bold text-white">Drivers</h1>
        <p role="alert" className="mt-2 text-sm text-accent-pink dark:text-accent-pink">
          Failed to load drivers: {driversError}
        </p>
      </div>
    )
  }

  // isLoadingDrivers is false and driversError is null, so drivers must be populated.
  const driverList = drivers as Driver[]

  return (
    <div>
      <h1 className="font-heading text-2xl font-bold text-white">Drivers</h1>

      {driverList.length === 0 ? (
        <p className="mt-2 text-white/70">No drivers recorded yet.</p>
      ) : (
        <div className="mt-4 overflow-hidden rounded-lg bg-surface-card dark:bg-brand-darker-blue shadow-card">
          <ul className="divide-y divide-line dark:divide-white/10">
            {driverList.map((driver) => (
              <li key={driver.driver_id}>
                <Link
                  to={`/drivers/${driver.driver_id}`}
                  className="block w-full px-4 py-3 text-left text-sm text-brand-dark hover:bg-surface-page dark:text-white dark:hover:bg-white/10"
                >
                  {driver.full_name}
                  <span className="block text-xs text-muted dark:text-white/60">
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
