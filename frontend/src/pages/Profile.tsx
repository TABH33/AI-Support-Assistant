/**
 * Profile page: the signed-in principal's own details, reached via the nav
 * bar's avatar dropdown (see Layout.tsx). Shows fewer rows for a
 * support_agent than for a customer/driver login -- see `GET /auth/me`
 * (backend/app/api/auth.py) for which fields each role gets.
 */
import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { apiGet } from '../lib/apiClient'
import { useAuth } from '../context/AuthProvider'
import { getProfilePhoto, setProfilePhoto } from '../lib/avatar'
import ctrackLogo from '../assets/ctrack-logo.png'
import type { Profile } from '../types/profile'

function ProfileRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between px-4 py-3">
      <dt className="text-sm text-muted dark:text-white/60">{label}</dt>
      <dd className="text-sm font-medium text-brand-dark dark:text-white">{value}</dd>
    </div>
  )
}

export default function ProfilePage() {
  const { user } = useAuth()
  const [profile, setProfile] = useState<Profile | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [photo, setPhoto] = useState<string | null>(() => (user ? getProfilePhoto(user) : null))
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let cancelled = false

    async function loadProfile() {
      try {
        const data = await apiGet<Profile>('/auth/me')
        if (!cancelled) setProfile(data)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load profile.')
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    }

    void loadProfile()
    return () => {
      cancelled = true
    }
  }, [])

  function handlePhotoChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file || !user) return
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        setProfilePhoto(user, reader.result)
        setPhoto(reader.result)
      }
    }
    reader.readAsDataURL(file)
  }

  return (
    <div>
      <h1 className="font-heading text-2xl font-bold text-white">Profile</h1>

      <div className="mt-4 flex items-center gap-4">
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="flex h-20 w-20 items-center justify-center overflow-hidden rounded-full bg-brand-darker-blue shadow-card"
        >
          {photo ? (
            <img src={photo} alt="Your profile" className="h-full w-full object-cover" />
          ) : (
            <img src={ctrackLogo} alt="" className="h-10 w-auto" />
          )}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          onChange={handlePhotoChange}
          className="hidden"
          aria-label="Upload profile photo"
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="text-sm font-medium text-brand-teal hover:text-brand-darker-teal"
        >
          Change photo
        </button>
      </div>

      {isLoading ? (
        <p className="mt-6 text-white/70">Loading profile…</p>
      ) : error ? (
        <p role="alert" className="mt-6 text-sm text-accent-pink">
          {error}
        </p>
      ) : profile ? (
        <dl className="mt-6 max-w-md divide-y divide-line overflow-hidden rounded-lg bg-surface-card shadow-card dark:divide-white/10 dark:bg-brand-darker-blue">
          <ProfileRow label="Name" value={profile.full_name} />
          <ProfileRow label="Company" value={profile.company} />
          {profile.vehicle && (
            <ProfileRow
              label="Vehicle"
              value={`${profile.vehicle.make} ${profile.vehicle.model} · ${profile.vehicle.registration_number}`}
            />
          )}
          <ProfileRow label="Email" value={profile.email ?? '—'} />
          <ProfileRow label="Phone" value={profile.phone_number ?? '—'} />
        </dl>
      ) : null}
    </div>
  )
}
