/**
 * Profile photo storage: a data URL in `localStorage`, keyed per logged-in
 * account (not just per browser) -- see the brainstorming for the
 * driver-login feature. Deliberately client-side only (no backend upload
 * endpoint exists, and this is a cosmetic feature): the photo won't follow
 * an account to another browser/device, and clearing site data removes it.
 */
import type { AuthUser } from '../context/AuthProvider'

const STORAGE_PREFIX = 'telematics_profile_photo:'

/** Fired on `window` whenever the stored photo changes, so the nav bar's
 * avatar button (which doesn't own the Profile page's state) can update
 * without a page reload. */
const PHOTO_CHANGED_EVENT = 'telematics-profile-photo-changed'

/** `driverId` disambiguates two driver accounts that share the same
 * fleet customer_id (and therefore the same `user.id`) -- see
 * AuthProvider's JwtPayload/AuthUser docs for where that claim comes from. */
function storageKey(user: AuthUser): string {
  const identity = user.driverId != null ? `driver-${user.driverId}` : `id-${user.id}`
  return `${STORAGE_PREFIX}${user.role}:${identity}`
}

/** Returns the stored photo (a data URL) for `user`, or null if none is
 * set or storage is unavailable (private browsing, quota, etc.). */
export function getProfilePhoto(user: AuthUser): string | null {
  try {
    return localStorage.getItem(storageKey(user))
  } catch {
    return null
  }
}

/** Stores `dataUrl` as `user`'s photo and notifies any listeners. Swallows
 * storage errors -- a photo that fails to persist just doesn't show up on
 * the next load, which is an acceptable degradation for a cosmetic feature. */
export function setProfilePhoto(user: AuthUser, dataUrl: string): void {
  try {
    localStorage.setItem(storageKey(user), dataUrl)
  } catch {
    // Ignored -- see module docs.
  }
  window.dispatchEvent(new Event(PHOTO_CHANGED_EVENT))
}

/** Subscribes `handler` to photo changes; returns an unsubscribe function. */
export function onProfilePhotoChange(handler: () => void): () => void {
  window.addEventListener(PHOTO_CHANGED_EVENT, handler)
  return () => window.removeEventListener(PHOTO_CHANGED_EVENT, handler)
}
