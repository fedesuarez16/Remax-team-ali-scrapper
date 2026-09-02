import Link from 'next/link'
import { ArrowRight, Check, Square } from 'lucide-react'
import { ApifyCostChip } from '@/components/cost/ApifyCostChip'
import type { ApifyCostBreakdown } from '@/lib/apifyCost'

export function SearchDoneCard({
  jobId,
  matchedCount,
  totalCount = null,
  insideCount = null,
  apifyCostUsd = null,
  apifyCostBreakdown = null,
  cancelled = false,
}: {
  jobId: string
  matchedCount: number
  // Everything the search scraped (matched + rest). `null`/omitted (map
  // path) falls back to matched-only wording.
  totalCount?: number | null
  // Server-classified inside-polygon count (`GET /{job_id}/properties`
  // `counts.inside`) — the single source of truth when the job has a
  // polygon. `null`/omitted (the /chat path, no polygon) falls back to the
  // SSE-accumulated `matchedCount`, unchanged from today.
  insideCount?: number | null
  // Lo que gastó esta búsqueda en Apify, del evento `done`. `null` = el
  // backend no lo informó y no se muestra nada.
  apifyCostUsd?: number | null
  apifyCostBreakdown?: ApifyCostBreakdown | null
  // El usuario la detuvo. Los resultados son los que había hasta ese momento,
  // así que la tarjeta no puede decir "completada" — sería mentirle sobre si
  // vio todo lo que había.
  cancelled?: boolean
}) {
  const count = insideCount ?? matchedCount
  const total = insideCount != null ? count : Math.max(totalCount ?? count, count)
  const label = total === 1 ? 'propiedad' : 'propiedades'

  return (
    <div className="w-full max-w-sm space-y-3 rounded-2xl rounded-tl-sm border border-border bg-card p-4">
      <div className="flex items-center gap-2">
        <div className="flex size-5 shrink-0 items-center justify-center rounded-full bg-foreground">
          {cancelled
            ? <Square className="size-2.5 text-background" strokeWidth={3} />
            : <Check className="size-3 text-background" strokeWidth={3} />}
        </div>
        <span className="text-sm font-medium text-foreground">
          {cancelled ? 'Búsqueda detenida' : 'Búsqueda completada'}
        </span>
        <ApifyCostChip
          costUsd={apifyCostUsd}
          breakdown={apifyCostBreakdown}
          className="ml-auto"
        />
      </div>

      <p className="text-sm text-muted-foreground">
        {total > 0
          ? total > count
            ? `Encontré ${total} ${label} (${count} coinciden con tus criterios)`
            : `Encontré ${total} ${label} que coinciden con tu búsqueda`
          : 'No encontré propiedades para tu búsqueda'}
      </p>

      {total > 0 && (
        <Link
          href={`/properties?job_id=${jobId}`}
          className="inline-flex items-center gap-2 rounded-xl bg-foreground px-4 py-2 text-sm text-background transition hover:bg-foreground/85"
        >
          Ver {total} {label}
          <ArrowRight className="size-3.5" />
        </Link>
      )}
    </div>
  )
}
