import { LOCALES, LOCALE_LABELS, useI18n, type Locale } from '@/i18n'
import { Select } from './ui'

export function LocaleSwitcher() {
  const { locale, setLocale, t } = useI18n()
  return (
    <>
      <label htmlFor="locale-switcher" className="sr-only">
        {t.settings.languageLabel}
      </label>
      <Select
        id="locale-switcher"
        value={locale}
        onChange={(event) => setLocale(event.target.value as Locale)}
        className="w-auto py-1.5 text-xs"
      >
        {LOCALES.map((code) => (
          <option key={code} value={code}>
            {LOCALE_LABELS[code]}
          </option>
        ))}
      </Select>
    </>
  )
}
