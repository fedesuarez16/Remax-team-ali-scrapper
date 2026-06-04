import { Check, Loader2, X, Circle } from 'lucide-react'
import type { ProgressMap, SourceStatus } from '@/hooks/useSSEStream'

const LABEL: Record<string, string> = {
  zonaprop: 'ZonaProp',
  mercadolibre: 'MercadoLibre',
  googlemaps: 'Google Maps',
}

function StatusIcon({ status }: { status: SourceStatus }) {
  if (status === 'running') return <Loader2 className="size-3.5 animate-spin text-violet-400" />
  if (status === 'done') return (
    <div className="flex size-3.5 items-center justify-center rounded-full bg-emerald-500/20">
      <Check className="size-2.5 text-emerald-400" strokeWidth={3} />
    </div>
  )
  if (status === 'error') return (
    <div className="flex size-3.5 items-center justify-center rounded-full bg-red-500/20">
      <X className="size-2.5 text-red-400" strokeWidth={3} />
    </div>
  )
  return <Circle className="size-3.5 text-zinc-700" />
}

function StatusBar({ progress }: { progress: ProgressMap }) {
  const entries = Object.values(progress)
  const done = entries.filter((s) => s.status === 'done').length
  const pct = entries.length ? (done / entries.length) * 100 : 0
  return (
    <div className="h-0.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
      <div
        className="h-full rounded-full bg-gradient-to-r from-violet-500 to-indigo-500 transition-all duration-500"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

export function ProgressBubble({ progress }: { progress: ProgressMap }) {
  const totalFound = Object.values(progress).reduce((acc, s) => acc + (s.count ?? 0), 0)

  return (
    <div className="w-full max-w-xs space-y-2.5 rounded-2xl rounded-tl-sm border border-white/[0.08] bg-white/[0.04] p-3.5 backdrop-blur-sm">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-zinc-300">Buscando propiedades</span>
        {totalFound > 0 && (
          <span className="text-xs text-zinc-500">{totalFound} encontradas</span>
        )}
      </div>

      <StatusBar progress={progress} />

      <ul className="space-y-2">
        {Object.entries(progress).map(([src, s]) => (
          <li key={src} className="flex items-center gap-2.5">
            <StatusIcon status={s.status} />
            <span className={`flex-1 text-xs font-medium transition-colors ${
              s.status === 'done' ? 'text-zinc-300' :
              s.status === 'running' ? 'text-violet-300' :
              s.status === 'error' ? 'text-red-400' :
              'text-zinc-600'
            }`}>
              {LABEL[src] ?? src}
            </span>
            <span className="text-xs tabular-nums text-zinc-600">
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
    </div>
  )
}
