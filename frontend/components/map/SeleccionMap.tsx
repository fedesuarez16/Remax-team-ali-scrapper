'use client'
import 'leaflet/dist/leaflet.css'
import { useEffect, useMemo } from 'react'
import { CircleMarker, MapContainer, Popup, useMap } from 'react-leaflet'
import { BaseTiles } from '@/components/map/BaseTiles'
import type { Property } from '@/hooks/useSSEStream'
import type { Ubicada } from '@/lib/geo'
import { operacionLabel } from '@/lib/operacion'

// `estaUbicada` / `Ubicada` viven en `@/lib/geo` y NO se re-exportan desde
// acá: este módulo arrastra Leaflet, que toca `window` al evaluarse, y un
// import de valor contra este archivo anula el `dynamic({ ssr: false })` del
// modal — así reventaba el prerender de `/properties`. Ver la nota en geo.ts.

const VENTA_STYLE = { color: '#15803d', fillColor: '#16a34a', fillOpacity: 0.9, weight: 1.5 }
const ALQUILER_STYLE = { color: '#a16207', fillColor: '#eab308', fillOpacity: 0.95, weight: 1.5 }

function fmtPrice(p: Property) {
  if (p.precio == null) return 'Consultar'
  const n = new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 }).format(p.precio)
  return `${p.moneda ?? 'USD'} ${n}${p.tipo_operacion !== 'venta' ? '/mes' : ''}`
}

/** Encuadra todos los marcadores. Con una sola propiedad, `fitBounds` sobre
 *  un punto haría zoom infinito: ahí centramos a un zoom de calle. */
function FitToSelection({ puntos }: { puntos: [number, number][] }) {
  const map = useMap()
  useEffect(() => {
    if (puntos.length === 0) return
    if (puntos.length === 1) {
      map.setView(puntos[0], 16)
      return
    }
    map.fitBounds(puntos, { padding: [40, 40], maxZoom: 16 })
  }, [map, puntos])
  return null
}

/**
 * Mapa liviano de la selección actual. Sólo marcadores y encuadre: nada de
 * delinear zonas ni cartera — eso es del /map completo. Vive aparte para que
 * el modal cargue rápido y no arrastre los hooks de búsqueda por zona.
 */
export default function SeleccionMap({ propiedades }: { propiedades: Ubicada[] }) {
  // Memo: el encuadre se dispara cuando cambian los puntos, no en cada render
  // (abrir un popup no tiene que volver a mover el mapa).
  const puntos = useMemo(() => propiedades.map((p): [number, number] => [p.lat, p.lng]), [propiedades])

  return (
    <MapContainer
      center={puntos[0] ?? [-34.6037, -58.3816]}
      zoom={13}
      className="h-full w-full"
      scrollWheelZoom
    >
      <BaseTiles />
      <FitToSelection puntos={puntos} />
      {propiedades.map((p, i) => (
        <CircleMarker
          key={p.id}
          center={[p.lat, p.lng]}
          radius={8}
          pathOptions={p.tipo_operacion === 'alquiler' ? ALQUILER_STYLE : VENTA_STYLE}
        >
          <Popup>
            <div className="w-48">
              {p.imagenes?.[0] && (
                <img
                  src={p.imagenes[0]}
                  alt={p.titulo ?? p.direccion}
                  className="mb-2 h-24 w-full rounded-md object-cover"
                />
              )}
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                #{i + 1}
                {operacionLabel(p) ? ` · ${operacionLabel(p)}` : ''}
              </p>
              <p className="text-sm font-semibold text-foreground">{fmtPrice(p)}</p>
              {p.titulo && <p className="line-clamp-1 text-xs text-muted-foreground">{p.titulo}</p>}
              <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{p.direccion}</p>
              <a
                href={`/p/${p.id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-block text-xs font-medium text-foreground underline"
              >
                Ver ficha
              </a>
            </div>
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  )
}
