/**
 * Alerts screen (Task 20): lists `SupportTicket`/`Notification` records for
 * the logged-in customer, fetched from the Task 20 backend endpoints
 * (`GET /tickets`, `GET /notifications` in `backend/app/api/chat.py`) via
 * `apiClient`, mirroring Overview's fetch/loading/error pattern.
 *
 * Status indicators for tickets use `TicketStatus` values straight from
 * `backend/app/models/enums.py` (open / in_progress / resolved / closed),
 * color-coded the same way Drivers.tsx (Task 19) color-codes driving-event
 * badges.
 */
import { useEffect, useState } from 'react'
import { apiGet } from '../lib/apiClient'
import type { Notification, SupportTicket, TicketStatus } from '../types/telematics'

interface AlertsData {
  tickets: SupportTicket[]
  notifications: Notification[]
}

/** Label + color-coded badge classes per ticket status. */
const TICKET_STATUS_CONFIG: Record<TicketStatus, { label: string; badgeClass: string }> = {
  open: {
    label: 'Open',
    badgeClass: 'bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300',
  },
  in_progress: {
    label: 'In progress',
    badgeClass: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-300',
  },
  resolved: {
    label: 'Resolved',
    badgeClass: 'bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300',
  },
  closed: {
    label: 'Closed',
    badgeClass: 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300',
  },
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

export default function Alerts() {
  const [data, setData] = useState<AlertsData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    async function load() {
      setIsLoading(true)
      setError(null)
      try {
        const [tickets, notifications] = await Promise.all([
          apiGet<SupportTicket[]>('/tickets'),
          apiGet<Notification[]>('/notifications'),
        ])
        if (!cancelled) {
          setData({ tickets, notifications })
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load alerts.')
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
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Alerts</h1>
        <p className="mt-2 text-gray-600 dark:text-gray-300">Loading alerts…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Alerts</h1>
        <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
          Failed to load alerts: {error}
        </p>
      </div>
    )
  }

  // isLoading is false and error is null, so data must be populated.
  const { tickets, notifications } = data as AlertsData

  const ticketSubjectById = new Map(
    tickets.map((ticket) => [ticket.support_ticket_id, ticket.subject ?? `Ticket #${ticket.support_ticket_id}`])
  )

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Alerts</h1>

      <h2 className="mt-6 text-lg font-semibold text-gray-900 dark:text-white">Support tickets</h2>
      {tickets.length === 0 ? (
        <p className="mt-2 text-gray-600 dark:text-gray-300">No support tickets.</p>
      ) : (
        <div className="mt-2 overflow-x-auto rounded-lg bg-white dark:bg-gray-800 shadow">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead>
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:text-gray-400">
                  Subject
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:text-gray-400">
                  Status
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:text-gray-400">
                  Priority
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:text-gray-400">
                  Created
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:text-gray-400">
                  Resolved
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {tickets.map((ticket) => {
                const config = TICKET_STATUS_CONFIG[ticket.ticket_status]
                return (
                  <tr key={ticket.support_ticket_id}>
                    <td className="px-4 py-2 text-sm text-gray-900 dark:text-white">
                      {ticket.subject ?? `Ticket #${ticket.support_ticket_id}`}
                    </td>
                    <td className="px-4 py-2 text-sm">
                      <span
                        data-testid={`ticket-status-${ticket.support_ticket_id}`}
                        className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-medium ${config.badgeClass}`}
                      >
                        {config.label}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-sm text-gray-600 dark:text-gray-300 capitalize">
                      {ticket.priority}
                    </td>
                    <td className="px-4 py-2 text-sm text-gray-600 dark:text-gray-300">
                      {formatDateTime(ticket.created_at)}
                    </td>
                    <td className="px-4 py-2 text-sm text-gray-600 dark:text-gray-300">
                      {formatDateTime(ticket.resolved_at)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <h2 className="mt-8 text-lg font-semibold text-gray-900 dark:text-white">Notifications</h2>
      {notifications.length === 0 ? (
        <p className="mt-2 text-gray-600 dark:text-gray-300">No notifications.</p>
      ) : (
        <ul className="mt-2 divide-y divide-gray-200 dark:divide-gray-700 overflow-hidden rounded-lg bg-white dark:bg-gray-800 shadow">
          {notifications.map((notification) => (
            <li
              key={notification.notification_id}
              data-testid={`notification-${notification.notification_id}`}
              className="px-4 py-3"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-gray-900 dark:text-white">
                  {ticketSubjectById.get(notification.support_ticket_id) ??
                    `Ticket #${notification.support_ticket_id}`}
                </span>
                <span className="text-xs uppercase text-gray-500 dark:text-gray-400">
                  {notification.notification_type}
                </span>
              </div>
              <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">{notification.message}</p>
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                {notification.sent_at ? `Sent ${formatDateTime(notification.sent_at)}` : 'Not yet sent'}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
