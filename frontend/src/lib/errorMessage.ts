import { ApiError } from './api'
import type { Dictionary } from '@/i18n/uz'

/**
 * Turns an unknown thrown value into a message the user can act on.
 *
 * The backend's `code` is mapped to a localised string. When it sends a
 * specific message for a business-rule failure — "the primary owner must keep
 * the owner role" — that message is shown as-is, because it says something
 * the generic text cannot.
 */
export function describeError(error: unknown, t: Dictionary): string {
  if (!(error instanceof ApiError)) {
    return t.common.unknownError
  }
  switch (error.code) {
    case 'network_error':
      return t.common.networkError
    case 'authentication_failed':
      return t.auth.invalidCredentials
    case 'permission_denied':
      return t.errors.permissionDenied
    case 'not_found':
      return t.errors.notFound
    case 'rate_limited':
      return t.errors.rateLimited
    case 'module_not_available':
      return t.errors.moduleNotAvailable
    case 'conflict':
      // A conflict message names the specific rule that was broken.
      return error.message || t.errors.conflict
    case 'validation_failed':
      return error.message || t.errors.validationFailed
    default:
      return error.message || t.common.unknownError
  }
}

export function errorRequestId(error: unknown): string | null {
  return error instanceof ApiError ? error.requestId : null
}
