/**
 * The HTTP client.
 *
 * Responsibilities kept deliberately narrow:
 *  - attach the access token,
 *  - refresh it once when it has expired, and retry the original request,
 *  - turn the backend's uniform error envelope into a typed `ApiError`.
 *
 * It does not decide what a user may do. Permissions arrive from the backend
 * and are only used to choose what to render; every request is authorised
 * again server-side.
 */

const BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  'http://localhost:8000'

const API_PREFIX = '/api/v1'

/** Machine-readable codes the backend returns in `code`. */
export type ApiErrorCode =
  | 'validation_failed'
  | 'not_found'
  | 'conflict'
  | 'authentication_failed'
  | 'permission_denied'
  | 'rate_limited'
  | 'module_not_available'
  | 'internal_error'
  | 'network_error'
  | 'unbalanced_entry'
  | 'period_closed'
  | 'entry_immutable'
  | 'duplicate_posting_event'

export interface ApiErrorPayload {
  code: string
  message: string
  details?: Record<string, unknown>
}

export class ApiError extends Error {
  readonly status: number
  readonly code: ApiErrorCode | string
  readonly details: Record<string, unknown>
  readonly requestId: string | null

  constructor(
    status: number,
    payload: ApiErrorPayload,
    requestId: string | null,
  ) {
    super(payload.message)
    this.name = 'ApiError'
    this.status = status
    this.code = payload.code
    this.details = payload.details ?? {}
    this.requestId = requestId
  }

  /** Field-level messages from a 422, keyed by field name. */
  fieldErrors(): Record<string, string> {
    const errors = this.details.errors
    if (!Array.isArray(errors)) return {}
    const mapped: Record<string, string> = {}
    for (const entry of errors) {
      if (typeof entry !== 'object' || entry === null) continue
      const record = entry as { loc?: unknown[]; msg?: string }
      const field = record.loc?.filter((p) => p !== 'body').join('.')
      if (field && record.msg) mapped[field] = record.msg
    }
    return mapped
  }
}

// --- token storage ----------------------------------------------------------
//
// Held in memory, with the refresh token mirrored to sessionStorage so that a
// page reload does not sign the user out. sessionStorage rather than
// localStorage: the session should not outlive the browser tab, and it is
// never shared with another origin.

const REFRESH_STORAGE_KEY = 'erp.refresh'

let accessToken: string | null = null
let refreshToken: string | null = null

function loadRefreshToken(): string | null {
  try {
    return window.sessionStorage.getItem(REFRESH_STORAGE_KEY)
  } catch {
    return null
  }
}

refreshToken = loadRefreshToken()

export function setTokens(access: string | null, refresh: string | null): void {
  accessToken = access
  refreshToken = refresh
  try {
    if (refresh) window.sessionStorage.setItem(REFRESH_STORAGE_KEY, refresh)
    else window.sessionStorage.removeItem(REFRESH_STORAGE_KEY)
  } catch {
    /* an unpersisted session only means a reload asks for sign-in again */
  }
}

export function getAccessToken(): string | null {
  return accessToken
}

export function getRefreshToken(): string | null {
  return refreshToken
}

export function clearTokens(): void {
  setTokens(null, null)
}

// --- session expiry notification -------------------------------------------

type SessionExpiredHandler = () => void
let onSessionExpired: SessionExpiredHandler = () => {}

export function setSessionExpiredHandler(handler: SessionExpiredHandler): void {
  onSessionExpired = handler
}

// --- refresh coordination ---------------------------------------------------
//
// Several queries can fail with 401 at the same moment. Without this, each
// would start its own refresh and every one but the first would present an
// already-rotated token — which the backend treats as a replayed token and
// punishes by revoking the whole chain. So one refresh is shared.

let inFlightRefresh: Promise<boolean> | null = null

async function refreshAccessToken(): Promise<boolean> {
  if (inFlightRefresh) return inFlightRefresh

  inFlightRefresh = (async () => {
    const current = refreshToken
    if (!current) return false
    try {
      const response = await fetch(`${BASE_URL}${API_PREFIX}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: current }),
      })
      if (!response.ok) {
        clearTokens()
        return false
      }
      const body = (await response.json()) as {
        access_token: string
        refresh_token: string
      }
      setTokens(body.access_token, body.refresh_token)
      return true
    } catch {
      return false
    } finally {
      // Cleared in a microtask so concurrent callers all observe this result.
      queueMicrotask(() => {
        inFlightRefresh = null
      })
    }
  })()

  return inFlightRefresh
}

// --- request ----------------------------------------------------------------

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  body?: unknown
  query?: Record<string, string | number | boolean | undefined | null>
  /** Set for sign-in and refresh, which must not trigger a refresh loop. */
  skipAuth?: boolean
  signal?: AbortSignal
}

function buildUrl(
  path: string,
  query?: RequestOptions['query'],
): string {
  const url = new URL(`${BASE_URL}${API_PREFIX}${path}`)
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, String(value))
      }
    }
  }
  return url.toString()
}

async function parseError(response: Response): Promise<ApiError> {
  const requestId = response.headers.get('X-Request-ID')
  let payload: ApiErrorPayload = {
    code: 'internal_error',
    message: `Request failed with status ${response.status}`,
  }
  try {
    const parsed = (await response.json()) as unknown
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      'message' in parsed &&
      'code' in parsed
    ) {
      payload = parsed as ApiErrorPayload
    }
  } catch {
    /* a non-JSON body keeps the generic payload above */
  }
  return new ApiError(response.status, payload, requestId)
}

async function performRequest(
  path: string,
  options: RequestOptions,
  isRetry: boolean,
): Promise<Response> {
  const headers: Record<string, string> = {}
  // A multipart body must carry the boundary the browser generates, so the
  // header is left for `fetch` to write. Setting it here would send a
  // boundary that does not match the body and the upload would be rejected.
  const multipart = options.body instanceof FormData
  if (options.body !== undefined && !multipart) {
    headers['Content-Type'] = 'application/json'
  }
  if (!options.skipAuth && accessToken) {
    headers.Authorization = `Bearer ${accessToken}`
  }

  let response: Response
  try {
    response = await fetch(buildUrl(path, options.query), {
      method: options.method ?? 'GET',
      headers,
      body:
        options.body === undefined
          ? undefined
          : multipart
            ? (options.body as FormData)
            : JSON.stringify(options.body),
      signal: options.signal,
    })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError(
      0,
      { code: 'network_error', message: 'Could not reach the server' },
      null,
    )
  }

  // One refresh-and-retry. A second 401 means the session is genuinely over.
  if (
    response.status === 401 &&
    !options.skipAuth &&
    !isRetry &&
    refreshToken
  ) {
    const refreshed = await refreshAccessToken()
    if (refreshed) return performRequest(path, options, true)
    clearTokens()
    onSessionExpired()
  }

  return response
}

export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const response = await performRequest(path, options, false)
  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const api = {
  get: <T>(path: string, query?: RequestOptions['query'], signal?: AbortSignal) =>
    request<T>(path, { method: 'GET', query, signal }),
  post: <T>(path: string, body?: unknown, options?: Partial<RequestOptions>) =>
    request<T>(path, { method: 'POST', body, ...options }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  /** Posts one file as `multipart/form-data` under the field name `file`. */
  upload: <T>(path: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<T>(path, { method: 'POST', body: form })
  },
}
