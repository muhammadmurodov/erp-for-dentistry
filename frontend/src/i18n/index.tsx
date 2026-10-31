import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { uz, type Dictionary } from './uz'
import { ru } from './ru'
import { en } from './en'

export const LOCALES = ['uz', 'ru', 'en'] as const
export type Locale = (typeof LOCALES)[number]

export const LOCALE_LABELS: Record<Locale, string> = {
  uz: 'O‘zbekcha',
  ru: 'Русский',
  en: 'English',
}

const DICTIONARIES: Record<Locale, Dictionary> = { uz, ru, en }

const STORAGE_KEY = 'erp.locale'

/**
 * Digit-group and currency separators, written as escapes rather than literal
 * characters so they are visible in review.
 *
 * A narrow no-break space is the thousands separator used in Uzbek and
 * Russian financial text; a no-break space keeps the amount and its currency
 * code on one line.
 */
const GROUP_SEPARATOR = '\u202f'
const CURRENCY_SEPARATOR = '\u00a0'
const MINUS_SIGN = '\u2212'

function readStoredLocale(): Locale {
  // localStorage can throw in a private window or with site data blocked.
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored && (LOCALES as readonly string[]).includes(stored)) {
      return stored as Locale
    }
  } catch {
    /* fall through to the default */
  }
  return 'uz'
}

interface I18nValue {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: Dictionary
  /** Formats a date for display in the active locale. */
  formatDate: (value: string | Date | null | undefined) => string
  formatDateTime: (value: string | Date | null | undefined) => string
  /**
   * Formats a monetary amount.
   *
   * The amount arrives from the API as a decimal **string** and is formatted
   * as text. It is never converted to a JavaScript number, because `number`
   * is a binary float and would quietly corrupt large som values.
   */
  formatMoney: (value: string | null | undefined, currency?: string) => string
}

const I18nContext = createContext<I18nValue | null>(null)

const DATE_LOCALES: Record<Locale, string> = {
  uz: 'uz-UZ',
  ru: 'ru-RU',
  en: 'en-GB',
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readStoredLocale)

  useEffect(() => {
    document.documentElement.lang = locale
  }, [locale])

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next)
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      /* a non-persisted preference is acceptable */
    }
  }, [])

  const value = useMemo<I18nValue>(() => {
    const intlLocale = DATE_LOCALES[locale]
    return {
      locale,
      setLocale,
      t: DICTIONARIES[locale],
      formatDate: (input) => {
        if (!input) return '—'
        const date = typeof input === 'string' ? new Date(input) : input
        if (Number.isNaN(date.getTime())) return '—'
        return new Intl.DateTimeFormat(intlLocale, {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
        }).format(date)
      },
      formatDateTime: (input) => {
        if (!input) return '—'
        const date = typeof input === 'string' ? new Date(input) : input
        if (Number.isNaN(date.getTime())) return '—'
        return new Intl.DateTimeFormat(intlLocale, {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
        }).format(date)
      },
      formatMoney: (input, currency = 'UZS') => {
        if (input === null || input === undefined || input === '') return '—'
        // Group the integer digits by hand, on the string, so no float is
        // ever involved.
        const negative = input.startsWith('-')
        const unsigned = negative ? input.slice(1) : input
        const [whole = '0', fraction] = unsigned.split('.')
        const grouped = whole.replace(
          /\B(?=(\d{3})+(?!\d))/g,
          GROUP_SEPARATOR,
        )
        const trimmed = fraction?.replace(/0+$/, '')
        const body = trimmed ? `${grouped},${trimmed}` : grouped
        return `${negative ? MINUS_SIGN : ''}${body}${CURRENCY_SEPARATOR}${currency}`
      },
    }
  }, [locale, setLocale])

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext)
  if (!value) throw new Error('useI18n must be used inside <I18nProvider>')
  return value
}

/** Shorthand for the common case of only needing the dictionary. */
export function useT(): Dictionary {
  return useI18n().t
}
