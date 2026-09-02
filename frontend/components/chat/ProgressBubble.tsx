import { Check, Loader2, X, Circle, Square } from 'lucide-react'
import type { ProgressMap, SourceProgress, SourceStatus } from '@/hooks/useSSEStream'

const LABEL: Record<string, string> = {
  zonaprop: 'ZonaProp',
  mercadolibre: 'MercadoLibre',
  googlemaps: 'Google Maps',
  inmobiliarias: 'Inmobiliarias',
  extraccion: 'Analizando páginas',
}

/** Fuentes que sí conocen su universo de antemano y por eso merecen una barra
 * con números absolutos ("132 de 260") en vez de una fila más de la lista. */
const COUNTED_SOURCES = ['inmobiliarias', 'extraccion'] as const

function StatusIcon({ status }: { status: SourceStatus }) {
  if (status === 'running') return <Loader2 className="size-3.5 animate-spin text-foreground" />
  if (status === 'done') return (
    <div className="flex size-3.5 items-center justify-center rounded-full bg-foreground">
      <Check className="size-2.5 text-background" strokeWidth={3} />
    </div>
  )
  if (status === 'error') return (
    <div className="flex size-3.5 items-center justify-center rounded-full bg-muted-foreground/20">
      <X className="size-2.5 text-foreground" strokeWidth={3} />
    </div>
  )
  return <Circle className="size-3.5 text-muted-foreground/40" />
}

function StatusBar({ progress }: { progress: ProgressMap }) {
  const entries = Object.values(progress)
  const done = entries.filter((s) => s.status === 'done').length
  const pct = entries.length ? (done / entries.length) * 100 : 0
  return (
    <div className="h-0.5 w-full overflow-hidden rounded-full bg-border">
      <div
        className="h-full rounded-full bg-foreground transition-all duration-500"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

/** Barra con cuenta absoluta: cuántas van y cuántas faltan. Es la única forma
 * de saber si una búsqueda de 260 inmobiliarias está avanzando o colgada. */
function CountedBar({ label, s }: { label: string; s: SourceProgress }) {
  const total = s.total ?? 0
  const done = Math.min(s.done ?? 0, total)
  const pct = total ? (done / total) * 100 : 0
  const faltan = total - done

  return (
    <div className="space-y-1.5 rounded-xl bg-muted/50 p-2.5">
      <div className="flex items-center gap-2">
        <StatusIcon status={s.status} />
        <span className="flex-1 text-xs font-medium text-foreground">{label}</span>
        <span className="text-xs font-medium tabular-nums text-foreground">
          {done} / {total}
        </span>
      </div>

      <div className="h-1.5 w-full overflow-hidden rounded-full bg-border">
        <div
          className="h-full rounded-full bg-foreground transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>

      <p className="text-[11px] text-muted-foreground">
        {s.status === 'done'
          ? s.message
          : faltan > 0
          ? `Faltan ${faltan} · ${Math.round(pct)}%`
          : s.message}
      </p>
    </div>
  )
}

export function ProgressBubble({
  progress, matchedCount, totalCount = 0, onStop,
}: {
  progress: ProgressMap; matchedCount: number; totalCount?: number
  /** Detiene la búsqueda conservando lo encontrado. Omitido = sin botón, que
   * es el caso de las burbujas históricas (una búsqueda ya terminada no se
   * detiene) y el del mapa, que no tiene el hook a mano. */
  onStop?: () => void
}) {
  const counted = COUNTED_SOURCES
    .map((key) => [key, progress[key]] as const)
    .filter(([, s]) => s && (s.total ?? 0) > 0)

  // Las fuentes con barra propia salen de la lista de abajo: repetirlas sería
  // mostrar el mismo dato dos veces con menos información.
  const rows = Object.entries(progress).filter(
    ([src, s]) => !(COUNTED_SOURCES as readonly string[]).includes(src) || !s.total
  )

  return (
    <div className="w-full max-w-xs space-y-2.5 rounded-2xl rounded-tl-sm border border-border bg-card p-3.5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-foreground">Buscando propiedades</span>
        {(matchedCount > 0 || totalCount > 0) && (
          <span className="text-xs text-muted-foreground">
            {totalCount > matchedCount
              ? `${matchedCount} de ${totalCount} coinciden`
              : `${matchedCount} coincidencias`}
          </span>
        )}
      </div>

      <StatusBar progress={progress} />

      {counted.map(([key, s]) => (
        <CountedBar key={key} label={LABEL[key] ?? key} s={s!} />
      ))}

      <ul className="space-y-2">
        {rows.map(([src, s]) => (
          <li key={src} className="flex items-center gap-2.5">
            <StatusIcon status={s.status} />
            <span className={`flex-1 text-xs font-medium transition-colors ${
              s.status === 'done' ? 'text-foreground' :
              s.status === 'running' ? 'text-foreground' :
              s.status === 'error' ? 'text-muted-foreground line-through' :
              'text-muted-foreground'
            }`}>
              {LABEL[src] ?? src}
            </span>
            <span className="text-xs tabular-nums text-muted-foreground">
              {s.status === 'done' && s.count != null
                ? `${s.count} props`
                : s.status === 'running'
                ? s.count && s.count > 0 ? `${s.count}...` : '...'
                : s.status === 'error' ? 'error'
                : ''}
            </span>
          </li>
        ))}
      </ul>

      {onStop && (
        <button
          type="button"
          onClick={onStop}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-border
                     px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors
                     hover:bg-muted hover:text-foreground"
        >
          <Square className="size-3" strokeWidth={2.5} />
          Detener y ver resultados
        </button>
      )}
    </div>
  )
}
