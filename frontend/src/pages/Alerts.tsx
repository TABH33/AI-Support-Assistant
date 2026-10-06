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
    badgeClass: 'bg-status-danger-surface text-status-danger-text dark:bg-status-danger-text/30 dark:text-status-danger-surface',
  },
  in_progress: {
    label: 'In progress',
    badgeClass: 'bg-status-warning-surface text-status-warning-text dark:bg-status-warning-text/30 dark:text-status-warning-surface',
  },
  resolved: {
    label: 'Resolved',
    badgeClass: 'bg-status-success-surface text-status-success-text dark:bg-status-success-text/30 dark:text-status-success-surface',
  },
  closed: {
    label: 'Closed',
    badgeClass: 'bg-surface-page text-muted dark:bg-white/10 dark:text-white/70',
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
        <h1 className="font-heading text-2xl font-bold text-white">Alerts</h1>
        <p className="mt-2 text-white/70">Loading alerts…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div>
        <h1 className="font-heading text-2xl font-bold text-white">Alerts</h1>
        <p role="alert" className="mt-2 text-sm text-accent-pink dark:text-accent-pink">
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
      <h1 className="font-heading text-2xl font-bold text-white">Alerts</h1>

      <h2 className="mt-6 font-heading text-lg font-semibold text-white">Support tickets</h2>
      {tickets.length === 0 ? (
        <p className="mt-2 text-white/70">No support tickets.</p>
      ) : (
        <div className="mt-2 overflow-x-auto rounded-lg bg-surface-card dark:bg-brand-darker-blue shadow-card">
          <table className="min-w-full divide-y divide-line dark:divide-white/10">
            <thead>
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-muted dark:text-white/60">
                  Subject
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-muted dark:text-white/60">
                  Status
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-muted dark:text-white/60">
                  Priority
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-muted dark:text-white/60">
                  Created
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase text-muted dark:text-white/60">
                  Resolved
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line dark:divide-white/10">
              {tickets.map((ticket) => {
                const config = TICKET_STATUS_CONFIG[ticket.ticket_status]
                return (
                  <tr key={ticket.support_ticket_id}>
                    <td className="px-4 py-2 text-sm text-brand-dark dark:text-white">
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
                    <td className="px-4 py-2 text-sm text-muted dark:text-white/70 capitalize">
                      {ticket.priority}
                    </td>
                    <td className="px-4 py-2 text-sm text-muted dark:text-white/70">
                      {formatDateTime(ticket.created_at)}
                    </td>
                    <td className="px-4 py-2 text-sm text-muted dark:text-white/70">
                      {formatDateTime(ticket.resolved_at)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <h2 className="mt-8 font-heading text-lg font-semibold text-white">Notifications</h2>
      {notifications.length === 0 ? (
        <p className="mt-2 text-white/70">No notifications.</p>
      ) : (
        <ul className="mt-2 divide-y divide-line dark:divide-white/10 overflow-hidden rounded-lg bg-surface-card dark:bg-brand-darker-blue shadow-card">
          {notifications.map((notification) => (
            <li
              key={notification.notification_id}
              data-testid={`notification-${notification.notification_id}`}
              className="px-4 py-3"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-brand-dark dark:text-white">
                  {ticketSubjectById.get(notification.support_ticket_id) ??
                    `Ticket #${notification.support_ticket_id}`}
                </span>
                <span className="text-xs uppercase text-muted dark:text-white/60">
                  {notification.notification_type}
                </span>
              </div>
              <p className="mt-1 text-sm text-muted dark:text-white/70">{notification.message}</p>
              <p className="mt-1 text-xs text-muted dark:text-white/60">
                {notification.sent_at ? `Sent ${formatDateTime(notification.sent_at)}` : 'Not yet sent'}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
