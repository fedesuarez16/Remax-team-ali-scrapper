'use client'
import { useEffect, useState } from 'react'
import { Globe, Loader2, MapPin, Star } from 'lucide-react'

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
  const [isLoading, setIsLoading] = useState(false)

  // Reset loading state when parent re-enables (stream resumes → disabled goes false)
  useEffect(() => {
    if (!disabled) setIsLoading(false)
  }, [disabled])

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  const handleConfirm = () => {
    setIsLoading(true)
    onConfirm([...selected])
  }

  const withInstagram = agencies.filter((a) => a.instagram_handle)
  const withoutInstagram = agencies.filter((a) => !a.instagram_handle)
  const selectedCount = selected.size

  return (
    <div className="w-full max-w-md space-y-3 rounded-2xl rounded-tl-sm border border-border bg-card p-4">
      <p className="text-sm text-foreground">{message}</p>

      {withInstagram.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">Con Instagram detectado</p>
          {withInstagram.map((a) => (
            <AgencyRow key={a.id} agency={a} checked={selected.has(a.id)}
              onChange={() => toggle(a.id)} disabled={disabled} />
          ))}
        </div>
      )}

      {withoutInstagram.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">Se buscará en su sitio web</p>
          {withoutInstagram.map((a) => (
            <AgencyRow key={a.id} agency={a} checked={selected.has(a.id)}
              onChange={() => toggle(a.id)} disabled={disabled} />
          ))}
        </div>
      )}

      <div className="flex items-center justify-between pt-1">
        <p className="text-xs text-muted-foreground">{selectedCount} seleccionadas</p>
        <button
          onClick={handleConfirm}
          disabled={disabled || selectedCount === 0 || isLoading}
          className="flex items-center gap-2 rounded-xl bg-foreground px-4 py-2 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {isLoading ? (
            <>
              <Loader2 className="size-4 animate-spin" />
              Procesando...
            </>
          ) : (
            'Continuar →'
          )}
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
      ${checked ? 'border-foreground/40 bg-muted' : 'border-border hover:border-foreground/20 hover:bg-muted/50'}
      ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
    >
      <input type="checkbox" checked={checked} onChange={onChange}
        disabled={disabled} className="mt-0.5 accent-foreground" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-foreground truncate">{a.nombre}</span>
          {a.calificacion && (
            <span className="flex shrink-0 items-center gap-0.5 text-xs text-muted-foreground">
              <Star className="size-3 fill-current" />
              {a.calificacion.toFixed(1)}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
          {a.instagram_handle && (
            <span className="flex items-center gap-1 text-xs text-foreground">
              @{a.instagram_handle}
            </span>
          )}
          {a.sitio_web && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground truncate max-w-[150px]">
              <Globe className="size-3 shrink-0" />{a.sitio_web.replace(/^https?:\/\//, '')}
            </span>
          )}
          {a.direccion && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground truncate max-w-[200px]">
              <MapPin className="size-3 shrink-0" />{a.direccion}
            </span>
          )}
        </div>
      </div>
    </label>
  )
}
