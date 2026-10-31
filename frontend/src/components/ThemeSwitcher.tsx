import { useEffect, useState } from 'react'
import { useI18n } from '@/i18n'
import { Select } from './ui'

type Theme = 'system' | 'light' | 'dark'
const STORAGE_KEY = 'erp.theme'

function readStoredTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') {
      return stored
    }
  } catch {
    /* fall through */
  }
  return 'system'
}

export function ThemeSwitcher() {
  const { t } = useI18n()
  const [theme, setTheme] = useState<Theme>(readStoredTheme)

  useEffect(() => {
    const root = document.documentElement
    // `system` removes the attribute so the CSS media query takes over.
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      window.localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      /* an unpersisted preference is acceptable */
    }
  }, [theme])

  return (
    <>
      <label htmlFor="theme-switcher" className="sr-only">
        {t.settings.themeLabel}
      </label>
      <Select
        id="theme-switcher"
        value={theme}
        onChange={(event) => setTheme(event.target.value as Theme)}
        className="w-auto py-1.5 text-xs"
      >
        <option value="system">{t.settings.themeSystem}</option>
        <option value="light">{t.settings.themeLight}</option>
        <option value="dark">{t.settings.themeDark}</option>
      </Select>
    </>
  )
}
