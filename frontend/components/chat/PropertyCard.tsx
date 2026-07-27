import { ImageIcon, MapPin } from 'lucide-react'
import type { Property } from '@/hooks/useSSEStream'
import { operacionLabel } from '@/lib/operacion'

function fmtPrice(p: Property) {
  if (p.precio == null) return 'Consultar'
  const n = new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 }).format(p.precio)
  return `${p.moneda ?? 'USD'} ${n}${p.tipo_operacion !== 'venta' ? '/mes' : ''}`
}

const FUENTE_LABEL: Record<string, string> = {
  zonaprop: 'ZonaProp',
  mercadolibre: 'MercadoLibre',
  argenprop: 'Argenprop',
  remax: 'RE/MAX',
  googlemaps: 'Google Maps',
}

function MatchBar({ score }: { score: number }) {
  const label = score >= 80 ? 'Coincidencia alta' : score >= 50 ? 'Coincidencia media' : 'Coincidencia baja'
  return (
    <div className="border-b border-border px-3 py-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs font-medium text-foreground">{label}</span>
        <span className="text-xs font-semibold text-foreground">{score}%</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-foreground transition-all duration-300"
          style={{ width: `${Math.max(score, 4)}%` }}
        />
      </div>
    </div>
  )
}

function ConfidenceDot({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const opacity = pct >= 80 ? 'bg-foreground' : pct >= 50 ? 'bg-foreground/60' : 'bg-foreground/30'
  return (
    <span className="flex items-center gap-1 text-xs text-muted-foreground">
      <span className={`size-1.5 rounded-full ${opacity}`} />
      {pct}%
    </span>
  )
}

export function PropertyCard({ p }: { p: Property }) {
  const fuente = p.fuente ?? ''
  const conf = p.confianza_extraccion ?? 0

  return (
    <div className="group relative overflow-hidden rounded-2xl border border-border bg-card transition-all duration-200 hover:border-foreground/20 hover:bg-muted/30 hover:shadow-lg hover:shadow-black/5" onClick={() => p.url_origen && window.open(p.url_origen, '_blank', 'noopener')} style={p.url_origen ? {cursor: 'pointer'} : undefined}>

      {/* Match bar — only present when ranked against a search query */}
      {p.match_score != null && <MatchBar score={p.match_score} />}

      {/* Scraped in the search but outside the user's criteria (price/rooms) */}
      {p.matches_criteria === false && (
        <div className="border-b border-border bg-muted/40 px-3 py-1.5">
          <span className="text-xs text-muted-foreground">Fuera de tus criterios</span>
        </div>
      )}

      {/* Image */}
      <div className="relative aspect-[4/3] overflow-hidden bg-muted">
        {p.imagenes?.[0] ? (
          <img
            src={p.imagenes[0]}
            alt={p.titulo ?? p.direccion}
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
          />
        ) : (
          <div className="flex h-full items-center justify-center bg-muted">
            <ImageIcon className="size-8 text-muted-foreground/40" />
          </div>
        )}
        {/* Source badge overlay */}
        <div className="absolute right-2 top-2">
          <span className="inline-flex items-center rounded-full border border-border bg-background/90 px-2 py-0.5 text-xs font-medium text-foreground backdrop-blur-sm">
            {FUENTE_LABEL[fuente] ?? fuente}
          </span>
        </div>
        {/* Operation badge overlay — venta solid, alquiler outlined */}
        {operacionLabel(p) && (
          <div className="absolute bottom-2 left-2">
            <span
              className={
                p.tipo_operacion === 'venta'
                  ? 'inline-flex items-center rounded-full bg-foreground px-2 py-0.5 text-xs font-semibold text-background'
                  : 'inline-flex items-center rounded-full border border-border bg-background/90 px-2 py-0.5 text-xs font-medium text-foreground backdrop-blur-sm'
              }
            >
              {operacionLabel(p)}
            </span>
          </div>
        )}
      </div>

      {/* Content */}
      <div className="p-3">
        {/* Price */}
        <p className="text-base font-semibold tracking-tight text-foreground">{fmtPrice(p)}</p>

        {/* Address */}
        {p.direccion && (
          <div className="mt-1 flex items-start gap-1">
            <MapPin className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
            <p className="line-clamp-1 text-xs text-muted-foreground">{p.direccion}</p>
          </div>
        )}

        {/* Specs row */}
        <div className="mt-2.5 flex items-center gap-2">
          {p.tipo_propiedad != null && (
            <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-medium text-foreground">
              {p.tipo_propiedad.charAt(0).toUpperCase() + p.tipo_propiedad.slice(1)}
            </span>
          )}
          {p.ambientes != null && (
            <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-medium text-foreground">
              {p.ambientes} amb
            </span>
          )}
          {p.banos != null && (
            <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-medium text-foreground">
              {p.banos} baño/s
            </span>
          )}
          {p.cocheras != null && (
            <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-medium text-foreground">
              {p.cocheras} cochera/s
            </span>
          )}
          {p.m2_total != null && (
            <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-medium text-foreground">
              {p.m2_total} m²
            </span>
          )}
          {p.antiguedad != null && (
            <span className="rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground">
              {p.antiguedad === 0 ? 'A estrenar' : `${p.antiguedad} años`}
            </span>
          )}
          <div className="ml-auto">
            <ConfidenceDot value={conf} />
          </div>
        </div>

        {/* Secondary info row */}
        {(p.piso != null || p.expensas != null) && (
          <div className="mt-1.5 flex items-center gap-2">
            {p.piso != null && (
              <span className="text-xs text-muted-foreground">
                Piso {p.piso}
              </span>
            )}
            {p.expensas != null && (
              <span className="text-xs text-muted-foreground">
                Expensas {new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', maximumFractionDigits: 0 }).format(p.expensas)}
              </span>
            )}
          </div>
        )}

        {/* Description */}
        {p.descripcion && (
          <p className="mt-2 line-clamp-2 text-sm text-gray-500">{p.descripcion}</p>
        )}

        {/* Amenities */}
        {p.amenities && p.amenities.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {p.amenities.slice(0, 3).map((a) => (
              <span key={a} className="rounded-full border border-border bg-muted/50 px-2 py-0.5 text-xs text-muted-foreground">
                {a}
              </span>
            ))}
            {p.amenities.length > 3 && (
              <span className="text-xs text-muted-foreground/60">+{p.amenities.length - 3}</span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
