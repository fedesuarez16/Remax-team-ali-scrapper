import { ImageIcon, MapPin } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
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
  zonaprop: 'text-blue-400 bg-blue-500/10 border-blue-500/20',
  mercadolibre: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20',
  googlemaps: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
}

function ConfidenceDot({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color = pct >= 80 ? 'bg-emerald-500' : pct >= 50 ? 'bg-yellow-500' : 'bg-red-500'
  return (
    <span className="flex items-center gap-1 text-xs text-zinc-500">
      <span className={`size-1.5 rounded-full ${color}`} />
      {pct}%
    </span>
  )
}

export function PropertyCard({ p }: { p: Property }) {
  const fuente = p.fuente ?? ''
  const conf = p.confianza_extraccion ?? 0

  return (
    <div className="group relative overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.03] transition-all duration-200 hover:border-white/[0.16] hover:bg-white/[0.06] hover:shadow-xl hover:shadow-black/20">

      {/* Image */}
      <div className="relative aspect-[4/3] overflow-hidden bg-zinc-900">
        {p.imagenes?.[0] ? (
          <img
            src={p.imagenes[0]}
            alt={p.titulo ?? p.direccion}
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
          />
        ) : (
          <div className="flex h-full items-center justify-center bg-gradient-to-br from-zinc-800 to-zinc-900">
            <ImageIcon className="size-8 text-zinc-700" />
          </div>
        )}
        {/* Source badge overlay */}
        <div className="absolute right-2 top-2">
          <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium backdrop-blur-sm ${FUENTE_COLOR[fuente] ?? 'text-zinc-400 bg-zinc-800/80 border-white/10'}`}>
            {FUENTE_LABEL[fuente] ?? fuente}
          </span>
        </div>
      </div>

      {/* Content */}
      <div className="p-3">
        {/* Price */}
        <p className="text-base font-bold text-zinc-100">{fmtPrice(p)}</p>

        {/* Address */}
        {p.direccion && (
          <div className="mt-1 flex items-start gap-1">
            <MapPin className="mt-0.5 size-3 shrink-0 text-zinc-600" />
            <p className="line-clamp-1 text-xs text-zinc-500">{p.direccion}</p>
          </div>
        )}

        {/* Specs row */}
        <div className="mt-2.5 flex items-center gap-2">
          {p.ambientes != null && (
            <span className="rounded-md bg-white/[0.05] px-2 py-0.5 text-xs font-medium text-zinc-300">
              {p.ambientes} amb
            </span>
          )}
          {p.m2_total != null && (
            <span className="rounded-md bg-white/[0.05] px-2 py-0.5 text-xs font-medium text-zinc-300">
              {p.m2_total} m²
            </span>
          )}
          {p.antiguedad != null && (
            <span className="rounded-md bg-white/[0.05] px-2 py-0.5 text-xs text-zinc-500">
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
              <span key={a} className="rounded-full border border-white/[0.06] bg-white/[0.03] px-2 py-0.5 text-xs text-zinc-600">
                {a}
              </span>
            ))}
            {p.amenities.length > 3 && (
              <span className="text-xs text-zinc-700">+{p.amenities.length - 3}</span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
