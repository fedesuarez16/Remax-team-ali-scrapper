'use client'
import { Building2, Globe } from 'lucide-react'
import {
  INMOBILIARIAS,
  PORTALES,
  type InmobiliariaId,
  type PortalId,
  type SourceSelection,
} from '@/lib/sources'
import { cn } from '@/lib/utils'

/**
 * Pre-search source picker: WHERE the scraping runs.
 *
 * Two independent tracks, either or both:
 * - Portales inmobiliarios → optional subset of the portal scrapers.
 * - Inmobiliarias → optional subset of the ten reviewed integrations.
 */
export function SourceSelector({
  value,
  onChange,
  disabled = false,
}: {
  value: SourceSelection
  onChange: (next: SourceSelection) => void
  disabled?: boolean
}) {
  const togglePortal = (id: PortalId) => {
    const next = value.portales.includes(id)
      ? value.portales.filter((p) => p !== id)
      : [...value.portales, id]
    onChange({ ...value, portales: next })
  }

  const toggleInmobiliaria = (id: InmobiliariaId) => {
    const current = value.inmobiliarias.length === 0
      ? INMOBILIARIAS.map((source) => source.id)
      : value.inmobiliarias
    const next = current.includes(id)
      ? current.filter((source) => source !== id)
      : [...current, id]
    if (next.length === 0) {
      onChange({ ...value, buscar_inmobiliarias: false, inmobiliarias: [] })
      return
    }
    onChange({
      ...value,
      inmobiliarias: next.length === INMOBILIARIAS.length ? [] : next,
    })
  }

  return (
    <div className={cn('space-y-3', disabled && 'pointer-events-none opacity-60')}>
      <div className="grid grid-cols-2 gap-2">
        <TrackToggle
          icon={<Globe className="size-4" />}
          label="Portales inmobiliarios"
          hint={value.portales.length === 0 ? 'Todos' : `${value.portales.length} seleccionados`}
          active={value.buscar_portales}
          onClick={() => onChange({ ...value, buscar_portales: !value.buscar_portales })}
        />
        <TrackToggle
          icon={<Building2 className="size-4" />}
          label="Inmobiliarias"
          hint={
            value.inmobiliarias.length === 0
              ? `${INMOBILIARIAS.length} configuradas`
              : `${value.inmobiliarias.length} seleccionadas`
          }
          active={value.buscar_inmobiliarias}
          onClick={() =>
            onChange({ ...value, buscar_inmobiliarias: !value.buscar_inmobiliarias })
          }
        />
      </div>

      {value.buscar_portales && (
        <div className="flex flex-wrap gap-1.5">
          {PORTALES.map((p) => {
            // Empty subset means "todos": show every chip as on, so the state
            // the user sees matches what the backend will actually scrape.
            const on = value.portales.length === 0 || value.portales.includes(p.id)
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => togglePortal(p.id)}
                className={cn(
                  'rounded-full border px-3 py-1 text-xs font-medium transition',
                  on
                    ? 'border-foreground bg-foreground text-background'
                    : 'border-border bg-card text-muted-foreground hover:text-foreground'
                )}
              >
                {p.label}
              </button>
            )
          })}
          {value.portales.length > 0 && (
            <button
              type="button"
              onClick={() => onChange({ ...value, portales: [] })}
              className="rounded-full px-2 py-1 text-xs text-muted-foreground underline-offset-2 hover:underline"
            >
              Todos
            </button>
          )}
        </div>
      )}

      {value.buscar_inmobiliarias && (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {INMOBILIARIAS.map((source) => {
              const on = value.inmobiliarias.length === 0
                || value.inmobiliarias.includes(source.id)
              return (
                <button
                  key={source.id}
                  type="button"
                  onClick={() => toggleInmobiliaria(source.id)}
                  className={cn(
                    'rounded-full border px-3 py-1 text-xs font-medium transition',
                    on
                      ? 'border-foreground bg-foreground text-background'
                      : 'border-border bg-card text-muted-foreground hover:text-foreground'
                  )}
                >
                  {source.label}
                </button>
              )
            })}
            {value.inmobiliarias.length > 0 && (
              <button
                type="button"
                onClick={() => onChange({ ...value, inmobiliarias: [] })}
                className="rounded-full px-2 py-1 text-xs text-muted-foreground underline-offset-2 hover:underline"
              >
                Todas
              </button>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            Se usan sus rutas y ubicaciones configuradas. No se agregan inmobiliarias de Google Maps.
          </p>
        </div>
      )}
    </div>
  )
}

function TrackToggle({
  icon,
  label,
  hint,
  active,
  onClick,
}: {
  icon: React.ReactNode
  label: string
  hint: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'flex items-start gap-2 rounded-xl border px-3 py-2.5 text-left transition',
        active
          ? 'border-foreground/30 bg-muted ring-1 ring-foreground/10'
          : 'border-border bg-card opacity-60 hover:opacity-100'
      )}
    >
      <span className={cn('mt-0.5', active ? 'text-foreground' : 'text-muted-foreground')}>
        {icon}
      </span>
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium text-foreground">{label}</span>
        <span className="block truncate text-xs text-muted-foreground">
          {active ? hint : 'Desactivado'}
        </span>
      </span>
    </button>
  )
}
