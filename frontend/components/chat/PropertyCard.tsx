import { ImageIcon, MapPin } from 'lucide-react'
import type { Property } from '@/hooks/useSSEStream'

function fmtPrice(p: Property) {
  if (p.precio == null) return 'Consultar'
  const n = new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 }).format(p.precio)
  return `${p.moneda ?? 'USD'} ${n}${p.tipo_operacion !== 'venta' ? '/mes' : ''}`
}

const FUENTE_LABEL: Record<string, string> = {
  zonaprop: 'ZonaProp',
  mercadolibre: 'MercadoLibre',
  googlemaps: 'Google Maps',
}

const FUENTE_COLOR: Record<string, string> = {
  zonaprop: 'text-blue-600 bg-blue-500/10 border-blue-500/20',
  mercadolibre: 'text-yellow-600 bg-yellow-500/10 border-yellow-500/20',
  googlemaps: 'text-emerald-600 bg-emerald-500/10 border-emerald-500/20',
}

function ConfidenceDot({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color = pct >= 80 ? 'bg-emerald-500' : pct >= 50 ? 'bg-yellow-500' : 'bg-red-500'
  return (
    <span className="flex items-center gap-1 text-xs text-muted-foreground">
      <span className={`size-1.5 rounded-full ${color}`} />
      {pct}%
    </span>
  )
}

export function PropertyCard({ p }: { p: Property }) {
  const fuente = p.fuente ?? ''
  const conf = p.confianza_extraccion ?? 0

  return (
    <div className="group relative overflow-hidden rounded-2xl border border-border bg-card transition-all duration-200 hover:border-border hover:bg-muted/50 hover:shadow-xl hover:shadow-black/5">

      {/* Image */}
      <div className="relative aspect-[4/3] overflow-hidden bg-muted">
        {p.imagenes?.[0] ? (
          <img
            src={p.imagenes[0]}
            alt={p.titulo ?? p.direccion}
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
          />
        ) : (
          <div className="flex h-full items-center justify-center bg-gradient-to-br from-muted to-muted/50">
            <ImageIcon className="size-8 text-muted-foreground/40" />
          </div>
        )}
        {/* Source badge overlay */}
        <div className="absolute right-2 top-2">
          <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium backdrop-blur-sm ${FUENTE_COLOR[fuente] ?? 'text-muted-foreground bg-card/80 border-border'}`}>
            {FUENTE_LABEL[fuente] ?? fuente}
          </span>
        </div>
      </div>

      {/* Content */}
      <div className="p-3">
        {/* Price */}
        <p className="text-base font-bold text-foreground">{fmtPrice(p)}</p>

        {/* Address */}
        {p.direccion && (
          <div className="mt-1 flex items-start gap-1">
            <MapPin className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
            <p className="line-clamp-1 text-xs text-muted-foreground">{p.direccion}</p>
          </div>
        )}

        {/* Specs row */}
        <div className="mt-2.5 flex items-center gap-2">
          {p.ambientes != null && (
            <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-medium text-foreground">
              {p.ambientes} amb
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
