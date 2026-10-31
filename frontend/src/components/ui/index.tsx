/**
 * The shared component vocabulary.
 *
 * Small and accessible on purpose: every state a screen can be in — loading,
 * empty, failed, forbidden — has a component here, so no page has to invent
 * one and none of them can quietly skip it.
 */
import clsx from 'clsx'
import {
  forwardRef,
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'

// --- surfaces ---------------------------------------------------------------

export function Card({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={clsx(
        // `min-w-0` because a card is usually a grid or flex item, whose
        // default `min-width: auto` is wide enough to hold its content —
        // which means the `overflow-x-auto` on a wide table inside never
        // engages, and the card stretches the page instead of scrolling.
        'min-w-0 rounded-xl border p-5 shadow-sm',
        'border-[var(--border-subtle)] bg-[var(--surface-raised)]',
        className,
      )}
    >
      {children}
    </div>
  )
}

export function CardHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-base font-semibold text-[var(--text-primary)]">
          {title}
        </h2>
        {description ? (
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? (
        // Wrapping rather than `shrink-0`: a header carrying three controls —
        // a filter, a toggle and a button — is wider than a phone screen, and
        // `shrink-0` holds the group at its full width, which pushes the whole
        // page sideways instead of moving one control to the next line.
        <div className="flex flex-wrap items-center justify-end gap-2">
          {actions}
        </div>
      ) : null}
    </div>
  )
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-[var(--text-primary)]">
          {title}
        </h1>
        {description ? (
          <p className="mt-1 max-w-2xl text-sm text-[var(--text-secondary)]">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? (
        // Wrapping rather than `shrink-0`: a header carrying three controls —
        // a filter, a toggle and a button — is wider than a phone screen, and
        // `shrink-0` holds the group at its full width, which pushes the whole
        // page sideways instead of moving one control to the next line.
        <div className="flex flex-wrap items-center justify-end gap-2">
          {actions}
        </div>
      ) : null}
    </header>
  )
}

// --- button -----------------------------------------------------------------

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'bg-brand-600 text-white hover:bg-brand-700 disabled:bg-brand-300 border border-transparent',
  secondary:
    'bg-[var(--surface-raised)] text-[var(--text-primary)] border border-[var(--border-strong)] hover:bg-[var(--surface-sunken)]',
  ghost:
    'bg-transparent text-[var(--text-secondary)] border border-transparent hover:bg-[var(--surface-sunken)]',
  danger:
    'bg-[var(--danger)] text-white hover:brightness-95 border border-transparent',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  loading?: boolean
  size?: 'sm' | 'md'
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    { variant = 'secondary', loading, size = 'md', className, children, ...rest },
    ref,
  ) {
    return (
      <button
        ref={ref}
        // A loading button stays disabled so a slow save cannot be submitted
        // twice — the same double-submission the backend guards against.
        disabled={rest.disabled || loading}
        aria-busy={loading || undefined}
        className={clsx(
          'inline-flex items-center justify-center gap-2 rounded-lg font-medium',
          'transition-colors disabled:cursor-not-allowed disabled:opacity-60',
          size === 'sm' ? 'px-2.5 py-1.5 text-xs' : 'px-3.5 py-2 text-sm',
          BUTTON_VARIANTS[variant],
          className,
        )}
        {...rest}
      >
        {loading ? <Spinner size={14} /> : null}
        {children}
      </button>
    )
  },
)

// --- feedback ---------------------------------------------------------------

export function Spinner({ size = 18 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      role="presentation"
      className="animate-spin"
    >
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeWidth="3"
        opacity="0.25"
      />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function LoadingState({ label }: { label: string }) {
  return (
    <div
      className="flex items-center justify-center gap-3 py-12 text-sm text-[var(--text-secondary)]"
      role="status"
      aria-live="polite"
    >
      <Spinner />
      <span>{label}</span>
    </div>
  )
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string
  body?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-[var(--border-strong)] px-6 py-12 text-center">
      <p className="text-sm font-medium text-[var(--text-primary)]">{title}</p>
      {body ? (
        <p className="max-w-md text-sm text-[var(--text-secondary)]">{body}</p>
      ) : null}
      {action}
    </div>
  )
}

export function ErrorState({
  title,
  message,
  requestId,
  requestIdLabel,
  onRetry,
  retryLabel,
}: {
  title: string
  message: string
  requestId?: string | null
  requestIdLabel?: string
  onRetry?: () => void
  retryLabel?: string
}) {
  return (
    <div
      role="alert"
      className="rounded-xl border px-5 py-6"
      style={{
        borderColor: 'var(--danger)',
        background: 'var(--danger-surface)',
      }}
    >
      <p className="text-sm font-semibold text-[var(--text-primary)]">{title}</p>
      <p className="mt-1 text-sm text-[var(--text-secondary)]">{message}</p>
      {requestId ? (
        <p className="mt-2 font-mono text-xs text-[var(--text-muted)]">
          {requestIdLabel}: {requestId}
        </p>
      ) : null}
      {onRetry ? (
        <Button className="mt-4" onClick={onRetry} size="sm">
          {retryLabel}
        </Button>
      ) : null}
    </div>
  )
}

export function Alert({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'success' | 'warning' | 'danger'
  children: ReactNode
}) {
  const surface = {
    info: 'var(--info-surface)',
    success: 'var(--success-surface)',
    warning: 'var(--warning-surface)',
    danger: 'var(--danger-surface)',
  }[tone]
  const border = {
    info: 'var(--color-brand-500)',
    success: 'var(--success)',
    warning: 'var(--warning)',
    danger: 'var(--danger)',
  }[tone]
  return (
    <div
      role={tone === 'danger' ? 'alert' : 'status'}
      className="rounded-lg border-l-4 px-4 py-3 text-sm text-[var(--text-primary)]"
      style={{ background: surface, borderLeftColor: border }}
    >
      {children}
    </div>
  )
}

// --- badge ------------------------------------------------------------------

type BadgeTone = 'neutral' | 'success' | 'warning' | 'danger' | 'brand'

export function Badge({
  tone = 'neutral',
  children,
}: {
  tone?: BadgeTone
  children: ReactNode
}) {
  const styles: Record<BadgeTone, string> = {
    neutral:
      'bg-[var(--surface-sunken)] text-[var(--text-secondary)] border-[var(--border-subtle)]',
    success: 'border-transparent text-white',
    warning: 'border-transparent text-black/80',
    danger: 'border-transparent text-white',
    brand: 'bg-brand-100 text-brand-800 border-brand-200',
  }
  const background = {
    neutral: undefined,
    success: 'var(--success)',
    warning: 'var(--warning)',
    danger: 'var(--danger)',
    brand: undefined,
  }[tone]
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap',
        styles[tone],
      )}
      style={background ? { background } : undefined}
    >
      {children}
    </span>
  )
}

// --- form fields ------------------------------------------------------------

export function Field({
  label,
  hint,
  error,
  required,
  children,
  htmlFor,
}: {
  label: string
  hint?: string
  error?: string
  required?: boolean
  children: ReactNode
  htmlFor?: string
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={htmlFor}
        className="text-sm font-medium text-[var(--text-primary)]"
      >
        {label}
        {required ? (
          <span aria-hidden="true" className="ml-0.5 text-[var(--danger)]">
            *
          </span>
        ) : null}
      </label>
      {children}
      {error ? (
        <p className="text-xs" style={{ color: 'var(--danger)' }} role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-[var(--text-muted)]">{hint}</p>
      ) : null}
    </div>
  )
}

// No width here on purpose. `Field` is a flex column, so a control inside one
// stretches by itself; a standalone control (a filter box, a switcher) sets
// its own width. Putting `w-full` here made every such override lose, because
// Tailwind resolves conflicting utilities by CSS source order rather than by
// the order they appear in the class string.
const CONTROL_CLASS =
  'rounded-lg border px-3 py-2 text-sm bg-[var(--surface-raised)] ' +
  'border-[var(--border-strong)] text-[var(--text-primary)] ' +
  'placeholder:text-[var(--text-muted)] disabled:opacity-60'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return <input ref={ref} className={clsx(CONTROL_CLASS, className)} {...rest} />
  },
)

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...rest }, ref) {
  return (
    <textarea
      ref={ref}
      rows={3}
      className={clsx(CONTROL_CLASS, 'resize-y', className)}
      {...rest}
    />
  )
})

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement>
>(function Select({ className, children, ...rest }, ref) {
  // `min-w-0` by default: a select's intrinsic width is its longest option,
  // which in a grid or flex row pushes the whole layout wider than its
  // container. Callers that want it sized to content pass their own width.
  return (
    <select ref={ref} className={clsx(CONTROL_CLASS, 'min-w-0', className)} {...rest}>
      {children}
    </select>
  )
})

export function Checkbox({
  label,
  description,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & {
  label: ReactNode
  description?: ReactNode
}) {
  const id = useId()
  return (
    <label
      htmlFor={id}
      className="flex cursor-pointer items-start gap-2.5 rounded-lg px-2 py-1.5 hover:bg-[var(--surface-sunken)]"
    >
      <input
        id={id}
        type="checkbox"
        className="mt-0.5 size-4 shrink-0 accent-[var(--color-brand-600)]"
        {...rest}
      />
      <span className="min-w-0">
        <span className="block text-sm text-[var(--text-primary)]">{label}</span>
        {description ? (
          <span className="block text-xs text-[var(--text-secondary)]">
            {description}
          </span>
        ) : null}
      </span>
    </label>
  )
}

// --- table ------------------------------------------------------------------

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--border-subtle)]">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  )
}

export function Th({
  children,
  numeric,
}: {
  children: ReactNode
  numeric?: boolean
}) {
  return (
    <th
      scope="col"
      className={clsx(
        'border-b border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-3 py-2.5',
        'text-xs font-semibold tracking-wide text-[var(--text-secondary)] uppercase',
        numeric ? 'text-right' : 'text-left',
      )}
    >
      {children}
    </th>
  )
}

export function Td({
  children,
  numeric,
  className,
  colSpan,
}: {
  children?: ReactNode
  numeric?: boolean
  className?: string
  colSpan?: number
}) {
  return (
    <td
      colSpan={colSpan}
      className={clsx(
        'border-b border-[var(--border-subtle)] px-3 py-2.5 align-top text-[var(--text-primary)]',
        numeric ? 'tabular text-right' : 'text-left',
        className,
      )}
    >
      {children}
    </td>
  )
}

// --- dialog -----------------------------------------------------------------

/**
 * A modal built on the native `<dialog>` element, so focus trapping, the
 * backdrop and Escape-to-close come from the platform rather than from
 * hand-rolled key handlers.
 */
const DIALOG_WIDTHS = {
  sm: '28rem',
  md: '32rem',
  lg: '52rem',
} as const

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  closeLabel,
  size = 'md',
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  footer?: ReactNode
  closeLabel: string
  /** `lg` for forms with a multi-column grid, such as journal entry lines. */
  size?: keyof typeof DIALOG_WIDTHS
}) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const element = ref.current
    if (!element) return
    if (open && !element.open) element.showModal()
    if (!open && element.open) element.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        // Clicking the backdrop (the dialog element itself) dismisses.
        if (event.target === ref.current) onClose()
      }}
      style={{ width: `min(${DIALOG_WIDTHS[size]}, calc(100vw - 2rem))` }}
      className={clsx(
        'm-auto max-h-[calc(100vh-4rem)] rounded-xl border p-0 shadow-xl',
        'border-[var(--border-subtle)] bg-[var(--surface-raised)]',
        'backdrop:bg-black/40',
      )}
    >
      <div className="flex items-start justify-between gap-4 border-b border-[var(--border-subtle)] px-5 py-4">
        <h2 className="text-base font-semibold text-[var(--text-primary)]">
          {title}
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label={closeLabel}
          className="rounded-md px-2 text-[var(--text-secondary)] hover:bg-[var(--surface-sunken)]"
        >
          ✕
        </button>
      </div>
      <div className="max-h-[70vh] overflow-x-auto overflow-y-auto px-5 py-4">
        {children}
      </div>
      {footer ? (
        <div className="flex justify-end gap-2 border-t border-[var(--border-subtle)] px-5 py-4">
          {footer}
        </div>
      ) : null}
    </dialog>
  )
}

/**
 * Confirmation for an action with consequences.
 *
 * Used for anything the backend treats as sensitive — revoking access,
 * changing authority — so that such a step is never one stray click away.
 */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  body,
  confirmLabel,
  cancelLabel,
  closeLabel,
  destructive,
  loading,
}: {
  open: boolean
  onClose: () => void
  onConfirm: () => void
  title: string
  body: string
  confirmLabel: string
  cancelLabel: string
  closeLabel: string
  destructive?: boolean
  loading?: boolean
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      closeLabel={closeLabel}
      footer={
        <>
          <Button onClick={onClose} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? 'danger' : 'primary'}
            onClick={onConfirm}
            loading={loading}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="text-sm text-[var(--text-secondary)]">{body}</p>
    </Dialog>
  )
}

// --- misc -------------------------------------------------------------------

export function DescriptionList({
  items,
}: {
  items: { label: string; value: ReactNode }[]
}) {
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-[minmax(8rem,14rem)_1fr]">
      {items.map((item) => (
        <div key={item.label} className="contents">
          <dt className="text-sm text-[var(--text-secondary)]">{item.label}</dt>
          <dd className="text-sm font-medium text-[var(--text-primary)]">
            {item.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}
