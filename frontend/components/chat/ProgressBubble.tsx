import { Check, Loader2, X, Circle } from 'lucide-react'
import type { ProgressMap, SourceStatus } from '@/hooks/useSSEStream'

const LABEL: Record<string, string> = {
  zonaprop: 'ZonaProp', mercadolibre: 'MercadoLibre', googlemaps: 'Google Maps',
}

function Icon({ status }: { status: SourceStatus }) {
  if (status === 'running') return <Loader2 className="size-4 animate-spin text-primary" />
  if (status === 'done') return <Check className="size-4 text-emerald-500" />
  if (status === 'error') return <X className="size-4 text-destructive" />
  return <Circle className="size-4 text-muted-foreground" />
}

export function ProgressBubble({ progress }: { progress: ProgressMap }) {
  return (
    <div className="max-w-md space-y-2 rounded-lg border border-border bg-muted/40 p-3">
      <p className="text-sm font-medium">Buscando propiedades...</p>
      <ul className="space-y-1.5">
        {Object.entries(progress).map(([src, s]) => (
          <li key={src} className="flex items-center gap-2 text-sm">
            <Icon status={s.status} />
            <span className="font-medium">{LABEL[src] ?? src}</span>
            <span className="ml-auto text-xs text-muted-foreground">
              {s.message || (s.count ? `${s.count}` : '')}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
