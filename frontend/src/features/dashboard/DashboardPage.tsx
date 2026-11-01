import { Link } from 'react-router-dom'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { useToday } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  buttonClassName,
  Card,
  CardHeader,
  ErrorState,
  LoadingState,
  PageHeader,
} from '@/components/ui'
import {
  actionsFor,
  exceptionsFor,
  tilesFor,
  type TileSpec,
} from '@/lib/dashboard'
import type { TodayView } from '@/lib/types'

/**
 * What is happening in the clinic today.
 *
 * This used to show the organization's registration details and a list of
 * module statuses. Both were true and neither was any use at eight in the
 * morning: the owner of a three-person clinic wants to know who is coming in,
 * what is still owed, how much went through the till, and whether anything
 * needs attention. The registration details live in settings, where somebody
 * looks for them once a year, and the module statuses live in
 * Settings → System status, where a developer looks for them.
 *
 * Three rules hold this screen honest.
 *
 * **Nothing is invented.** Every figure comes from `GET /clinic/today`, which
 * derives it on the server from the records themselves — the debt through the
 * same service the ageing report uses, the drawer through the same service the
 * cash screen uses. There are no placeholder statistics, because a made-up
 * number on a finance screen is worse than an absent one.
 *
 * **A withheld figure is absent, not zero.** The API returns `null` for any
 * section the signed-in person may not read, and a card with no data simply
 * does not appear. Rendering `0 UZS` owed to a receptionist who may not read
 * the total would be a lie in the shape of a fact.
 *
 * **No decorative buttons.** A quick action appears only when the person may
 * perform it *and* the screen it opens exists. The clinical workflows have no
 * screens yet, so there is no "start treatment" button pretending otherwise;
 * what the dashboard does instead is tell the dentist how much unfinished
 * work is waiting, and say plainly where it has to be recorded for now.
 */
export function DashboardPage() {
  const { t, formatDate } = useI18n()
  const { user } = useAuth()
  const today = useToday()

  return (
    <>
      <PageHeader
        title={t.dashboard.title}
        description={`${t.dashboard.welcome}, ${user?.full_name ?? ''}`}
        actions={
          today.data ? (
            <span className="text-sm text-[var(--text-secondary)]">
              {formatDate(today.data.business_date)}
            </span>
          ) : undefined
        }
      />

      {today.isLoading ? (
        <LoadingState label={t.common.loading} />
      ) : today.isError ? (
        <ErrorState
          title={t.errors.loadFailedTitle}
          message={describeError(today.error, t)}
          requestId={errorRequestId(today.error)}
          requestIdLabel={t.common.requestId}
          onRetry={() => void today.refetch()}
          retryLabel={t.common.retry}
        />
      ) : today.data ? (
        <TodayBoard view={today.data} />
      ) : null}
    </>
  )
}

function TodayBoard({ view }: { view: TodayView }) {
  const { t } = useI18n()
  const { can } = useAuth()
  const exceptions = exceptionsFor(view, can)
  const actions = actionsFor(view, can)
  const tiles = tilesFor(view, can)

  return (
    <div className="space-y-5">
      {/* Exceptions first: the reason somebody opened this screen. */}
      {exceptions.length > 0 ? (
        <div className="space-y-2">
          {exceptions.map((item) => (
            <Alert key={item.key} tone={item.tone}>
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span>{message(t, item.messageKey, item.params)}</span>
                {item.action ? (
                  <Link
                    to={item.action.to}
                    className="font-medium underline underline-offset-2"
                  >
                    {label(t, item.action.labelKey)}
                  </Link>
                ) : null}
              </span>
            </Alert>
          ))}
        </div>
      ) : null}

      {tiles.length > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {tiles.map((tile) => (
            <StatTile key={tile.key} tile={tile} currency={view.currency} />
          ))}
        </div>
      ) : null}

      {actions.length > 0 ? (
        <Card>
          <CardHeader title={t.dashboard.quickActions} />
          <div className="flex flex-wrap gap-2 p-4 pt-0">
            {actions.map((action) => (
              <Link
                key={action.key}
                to={action.to}
                className={buttonClassName({ size: 'sm' })}
              >
                {label(t, action.labelKey)}
              </Link>
            ))}
          </div>
        </Card>
      ) : null}

      {tiles.length === 0 && actions.length === 0 ? (
        <Card>
          <div className="p-6 text-sm text-[var(--text-secondary)]">
            {t.dashboard.nothingVisible}
          </div>
        </Card>
      ) : null}
    </div>
  )
}

/**
 * The dashboard's own phrasebook.
 *
 * `lib/dashboard.ts` decides *what* to show and returns keys; this maps a key
 * to the translated string and fills in its placeholders. Keeping the lookup
 * in one place is what lets the rules be tested without a dictionary.
 */
type Phrases = Record<string, string>

function label(t: ReturnType<typeof useI18n>['t'], key: string): string {
  return (t.dashboard as unknown as Phrases)[key] ?? key
}

function message(
  t: ReturnType<typeof useI18n>['t'],
  key: string,
  params?: Record<string, string>,
): string {
  let text = label(t, key)
  for (const [name, value] of Object.entries(params ?? {})) {
    text = text.replace(`{${name}}`, value)
  }
  return text
}

function StatTile({ tile, currency }: { tile: TileSpec; currency: string }) {
  const { t, formatMoney, formatDate } = useI18n()

  // A money tile carries a decimal string; a count carries a number; a tile
  // with neither — the closed till — shows a dash rather than a nought,
  // because nothing is open, not zero things.
  const value =
    tile.amount !== undefined
      ? formatMoney(tile.amount, currency)
      : tile.count === null || tile.count === undefined
        ? '—'
        : String(tile.count)

  const hint = tile.hint
    ? message(
        t,
        tile.hint.key,
        formatParams(tile.hint.params, { formatMoney, formatDate, currency }),
      )
    : undefined

  const body = (
    <>
      <p className="text-sm text-[var(--text-secondary)]">
        {label(t, tile.labelKey ?? TILE_LABELS[tile.key])}
      </p>
      <p
        className={`mt-1 text-2xl font-semibold tabular-nums ${
          tile.tone === 'warning' ? 'text-[var(--warning)]' : ''
        }`}
      >
        {value}
      </p>
      {hint ? (
        <p className="mt-1 text-xs text-[var(--text-secondary)]">{hint}</p>
      ) : null}
    </>
  )

  const shell =
    'block rounded-xl border border-[var(--border-subtle)] bg-[var(--surface)] p-4'
  return tile.to ? (
    <Link
      to={tile.to}
      className={`${shell} transition-colors hover:border-[var(--border)] hover:bg-[var(--surface-sunken)]`}
    >
      {body}
    </Link>
  ) : (
    <div className={shell}>{body}</div>
  )
}

/**
 * Amounts arrive raw so they can be rendered in the reader's locale; counts
 * and register codes pass through untouched.
 */
function formatParams(
  params: Record<string, string> | undefined,
  fmt: {
    formatMoney: (value: string, currency?: string) => string
    formatDate: (value: string) => string
    currency: string
  },
): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [name, raw] of Object.entries(params ?? {})) {
    out[name] =
      name === 'cash' || name === 'card'
        ? fmt.formatMoney(raw, fmt.currency)
        : name === 'date'
          ? fmt.formatDate(raw)
          : raw
  }
  return out
}

/** Which heading each tile carries. The closed till gets its own wording. */
const TILE_LABELS: Record<TileSpec['key'], string> = {
  takings: 'takingsToday',
  debt: 'debtOutstanding',
  advances: 'advancesAvailable',
  drawer: 'drawerOpen',
  appointments: 'appointmentsToday',
  patients: 'patientsRegistered',
  treatments: 'treatmentsInProgress',
  billable: 'treatmentsBillable',
}
