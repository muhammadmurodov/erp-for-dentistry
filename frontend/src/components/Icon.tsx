/**
 * The navigation icon set, inline so there is no icon-font request and both
 * themes get `currentColor`.
 */
const PATHS: Record<string, string> = {
  grid: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z',
  tooth:
    'M8 3c-2 0-3.2 1.6-3.2 3.8 0 1.5.5 2.4.7 3.6.2 1.4 0 3 .4 5 .3 1.6.8 2.6 1.6 2.6 1 0 1.1-1.4 1.4-3.1.2-1.2.5-2 1.1-2s.9.8 1.1 2c.3 1.7.4 3.1 1.4 3.1.8 0 1.3-1 1.6-2.6.4-2 .2-3.6.4-5 .2-1.2.7-2.1.7-3.6C15.2 4.6 14 3 12 3c-1 0-1.4.5-2 .5S9 3 8 3z',
  receipt: 'M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6',
  cash: 'M3 7h18v10H3zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  bank: 'M3 10 12 4l9 6M5 10v9h14v-9M9 19v-5h6v5',
  cart: 'M3 4h2l2.2 10h10.3l1.8-7H6.5M9 20a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM17 20a1 1 0 1 0 0-2 1 1 0 0 0 0 2z',
  box: 'M3 8l9-4 9 4v8l-9 4-9-4zM3 8l9 4 9-4M12 12v8',
  people:
    'M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2 20c0-3 2.7-5 6-5s6 2 6 5M17 11a3 3 0 1 0 0-6M22 20c0-2.4-1.6-4.2-4-4.8',
  wallet: 'M3 7h15a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3H3zM3 7V5h12M17 13h2',
  ledger: 'M5 3h12a2 2 0 0 1 2 2v16H7a2 2 0 0 1-2-2zM9 7h6M9 11h6M9 15h4',
  chart: 'M4 20V4M4 20h16M8 17v-5M12 17V8M16 17v-8',
  folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  gear: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2l1.2 2.4 2.6-.6.4 2.7 2.4 1.2-1.4 2.3 1.4 2.3-2.4 1.2-.4 2.7-2.6-.6L12 22l-1.2-2.4-2.6.6-.4-2.7L5.4 16l1.4-2.3L5.4 11.4l2.4-1.2.4-2.7 2.6.6z',
}

export function Icon({
  name,
  size = 18,
}: {
  name: string
  size?: number
}) {
  const path = PATHS[name] ?? PATHS.grid
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      <path d={path} />
    </svg>
  )
}
