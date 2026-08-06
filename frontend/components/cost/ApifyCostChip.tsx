import { costBreakdownTitle, formatUsd, type ApifyCostBreakdown } from '@/lib/apifyCost'

/**
 * What one search cost in Apify credits.
 *
 * `costUsd === 0` is a real, useful fact ("esta búsqueda salió gratis" —
 * caché o fuentes directas), so it renders. `null`/`undefined` means UNKNOWN
 * (job anterior a la columna) and renders nothing: showing "US$ 0" there would
 * be a lie.
 */
export function ApifyCostChip({
  costUsd,
  breakdown = null,
  className = '',
}: {
  costUsd: number | null | undefined
  breakdown?: ApifyCostBreakdown | null
  className?: string
}) {
  if (costUsd === null || costUsd === undefined) return null
  const free = costUsd === 0
  return (
    <span
      title={costBreakdownTitle(breakdown)}
      className={`shrink-0 rounded-md px-1.5 py-0.5 font-mono text-[11px] tabular-nums ${
        free ? 'bg-muted text-muted-foreground' : 'bg-amber-500/10 text-amber-700 dark:text-amber-400'
      } ${className}`}
    >
      {free ? 'US$ 0' : formatUsd(costUsd)}
    </span>
  )
}
