'use client'
import { Building2, Globe, MapPin } from 'lucide-react'
import { useSourceZonas } from '@/hooks/useManualSources'
import { PORTALES, type PortalId, type SourceSelection } from '@/lib/sources'
import { cn } from '@/lib/utils'

/**
 * Pre-search source picker: WHERE the scraping runs.
 *
 * Two independent tracks, either or both:
 * - Portales inmobiliarios → optional subset of the portal scrapers.
 * - Inmobiliarias → reveals the extra step, picking one manually-curated zona
 *   (or all of them). The zona→inmobiliaria classification is loaded by hand in
 *   the Fuentes tab; this only reads it.
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
  const { zonas, loading } = useSourceZonas()

  const togglePortal = (id: PortalId) => {
    const next = value.portales.includes(id)
      ? value.portales.filter((p) => p !== id)
      : [...value.portales, id]
    onChange({ ...value, portales: next })
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
          hint={value.zona_inmobiliarias ?? 'Todas las zonas'}
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

      {/* Sólo el registro curado, sin descubrir con Google Maps.
          El descubrimiento es lo que trae cientos de inmobiliarias que nadie
          eligió — y lo que se paga scrapeándolas y analizándolas. Antes esto
          se conseguía de rebote eligiendo una zona; ahora se pide derecho. */}
      {value.buscar_inmobiliarias && (
        <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-border bg-card p-3">
          <input
            type="checkbox"
            checked={value.solo_fuentes_cargadas}
            onChange={() =>
              onChange({ ...value, solo_fuentes_cargadas: !value.solo_fuentes_cargadas })
            }
            disabled={disabled}
            className="mt-0.5 size-4 shrink-0 accent-foreground"
          />
          <span className="space-y-0.5">
            <span className="block text-xs font-medium text-foreground">
              Solo mis inmobiliarias cargadas
            </span>
            <span className="block text-xs text-muted-foreground">
              {value.solo_fuentes_cargadas
                ? 'Se buscan únicamente las de la pestaña Fuentes. No se descubren nuevas con Google Maps.'
                : 'Además de las cargadas, se descubren inmobiliarias nuevas en la zona (más cobertura, más costo).'}
            </span>
          </span>
        </label>
      )}

      {/* Extra step: pick the zona whose curated inmobiliarias to consult. */}
      {value.buscar_inmobiliarias && !value.solo_fuentes_cargadas && (
        <div className="space-y-1.5 rounded-xl border border-border bg-card p-3">
          <label className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <MapPin className="size-3.5" />
            Zona de las inmobiliarias
          </label>
          <select
            value={value.zona_inmobiliarias ?? ''}
            onChange={(e) =>
              onChange({ ...value, zona_inmobiliarias: e.target.value || null })
            }
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20"
          >
            <option value="">Todas las zonas</option>
            {zonas.map((z) => (
              <option key={z.zona_norm} value={z.zona}>
                {z.zona} ({z.count})
              </option>
            ))}
          </select>
          <p className="text-xs text-muted-foreground">
            {value.zona_inmobiliarias
              ? `Solo las inmobiliarias cargadas en ${value.zona_inmobiliarias}. No se buscan otras zonas ni se descubren nuevas.`
              : 'Todas las inmobiliarias cargadas, más las que se descubran en la zona de la búsqueda.'}
          </p>
          {!loading && zonas.length === 0 && (
            <p className="text-xs text-muted-foreground">
              Todavía no clasificaste inmobiliarias por zona. Cargalas en la pestaña Fuentes.
            </p>
          )}
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
