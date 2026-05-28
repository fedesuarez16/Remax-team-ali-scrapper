import { ImageIcon } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import type { Property } from '@/hooks/useSSEStream'

function fmtPrice(p: Property) {
  if (p.precio == null) return 'Consultar'
  const n = new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 }).format(p.precio)
  return `${p.moneda} ${n}${p.tipo_operacion !== 'venta' ? '/mes' : ''}`
}

const FUENTE_LABEL: Record<string, string> = {
  zonaprop: 'ZonaProp', mercadolibre: 'MercadoLibre', googlemaps: 'Google Maps',
}

export function PropertyCard({ p }: { p: Property }) {
  const conf = Math.round((p.confianza_extraccion ?? 0) * 100)
  return (
    <Card className="overflow-hidden">
      <div className="flex aspect-video items-center justify-center bg-muted">
        {p.imagenes?.[0]
          ? <img src={p.imagenes[0]} alt={p.titulo ?? p.direccion} className="h-full w-full object-cover" />
          : <ImageIcon className="size-8 text-muted-foreground" />}
      </div>
      <CardContent className="space-y-1.5 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-base font-semibold">{fmtPrice(p)}</span>
          <Badge variant="secondary">{FUENTE_LABEL[p.fuente] ?? p.fuente}</Badge>
        </div>
        <p className="line-clamp-1 text-sm text-muted-foreground">{p.direccion}</p>
        <div className="flex flex-wrap gap-2 pt-1 text-xs text-muted-foreground">
          {p.ambientes != null && <span>{p.ambientes} amb</span>}
          {p.m2_total != null && <span>{p.m2_total} m²</span>}
          <Badge variant={conf >= 80 ? 'default' : 'outline'} className="ml-auto">
            confianza {conf}%
          </Badge>
        </div>
      </CardContent>
    </Card>
  )
}
