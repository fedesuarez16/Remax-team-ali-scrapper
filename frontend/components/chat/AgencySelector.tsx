'use client'
import { useState } from 'react'
import { Globe, MapPin, Star } from 'lucide-react'
import { Badge } from '@/components/ui/badge'

export type Agency = {
  id: string
  nombre: string
  direccion?: string | null
  telefono?: string | null
  sitio_web?: string | null
  instagram_handle?: string | null
  calificacion?: number | null
  zona: string
}

type Props = {
  agencies: Agency[]
  message: string
  onConfirm: (selectedIds: string[]) => void
  disabled?: boolean
}

export function AgencySelector({ agencies, message, onConfirm, disabled }: Props) {
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(agencies.map((a) => a.id))
  )

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  const withInstagram = agencies.filter((a) => a.instagram_handle)
  const withoutInstagram = agencies.filter((a) => !a.instagram_handle)
  const selectedCount = selected.size

  return (
    <div className="w-full max-w-md space-y-3 rounded-2xl rounded-tl-sm border border-white/[0.08] bg-white/[0.04] p-4">
      <p className="text-sm text-zinc-300">{message}</p>

      {withInstagram.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-zinc-500">Con Instagram detectado</p>
          {withInstagram.map((a) => (
            <AgencyRow key={a.id} agency={a} checked={selected.has(a.id)}
              onChange={() => toggle(a.id)} disabled={disabled} />
          ))}
        </div>
      )}

      {withoutInstagram.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-zinc-500">Sin Instagram — se buscará en su sitio web</p>
          {withoutInstagram.map((a) => (
            <AgencyRow key={a.id} agency={a} checked={selected.has(a.id)}
              onChange={() => toggle(a.id)} disabled={disabled} />
          ))}
        </div>
      )}

      <div className="flex items-center justify-between pt-1">
        <p className="text-xs text-zinc-600">{selectedCount} seleccionadas</p>
        <button
          onClick={() => onConfirm([...selected])}
          disabled={disabled || selectedCount === 0}
          className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Continuar →
        </button>
      </div>
    </div>
  )
}

function AgencyRow({ agency: a, checked, onChange, disabled }: {
  agency: Agency; checked: boolean; onChange: () => void; disabled?: boolean
}) {
  return (
    <label className={`flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 transition
      ${checked ? 'border-violet-500/30 bg-violet-500/10' : 'border-white/[0.06] hover:border-white/[0.12]'}
      ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
    >
      <input type="checkbox" checked={checked} onChange={onChange}
        disabled={disabled} className="mt-0.5 accent-violet-500" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-zinc-200 truncate">{a.nombre}</span>
          {a.calificacion && (
            <span className="flex shrink-0 items-center gap-0.5 text-xs text-amber-400">
              <Star className="size-3 fill-amber-400" />
              {a.calificacion.toFixed(1)}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
          {a.instagram_handle && (
            <span className="flex items-center gap-1 text-xs text-violet-400">
              @{a.instagram_handle}
            </span>
          )}
          {a.sitio_web && (
            <span className="flex items-center gap-1 text-xs text-zinc-500 truncate max-w-[150px]">
              <Globe className="size-3 shrink-0" />{a.sitio_web.replace(/^https?:\/\//, '')}
            </span>
          )}
          {a.direccion && (
            <span className="flex items-center gap-1 text-xs text-zinc-600 truncate max-w-[200px]">
              <MapPin className="size-3 shrink-0" />{a.direccion}
            </span>
          )}
        </div>
      </div>
    </label>
  )
}
