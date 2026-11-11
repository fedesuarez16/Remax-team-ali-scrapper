'use client'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef } from 'react'
import type { CircleMarker as LeafletCircleMarker } from 'leaflet'
import { CircleMarker, MapContainer, Popup, ZoomControl, useMap } from 'react-leaflet'
import { BaseTiles } from '@/components/map/BaseTiles'
import { PropertyPhoto } from '@/components/property/PropertyPhoto'
import type { Ubicada } from '@/lib/geo'
import { operacionLabel, sortVentaFirst } from '@/lib/operacion'

// Igual que SeleccionMap: `Ubicada`/`estaUbicada` se importan de `@/lib/geo`,
// nunca de acá — este módulo arrastra Leaflet y tiene que entrar sólo vía
// `dynamic({ ssr: false })`.

const VENTA_STYLE = { color: '#15803d', fillColor: '#16a34a', fillOpacity: 0.85, weight: 1 }
const ALQUILER_STYLE = { color: '#a16207', fillColor: '#eab308', fillOpacity: 0.95, weight: 1.5 }
// Fuera de criterio: se ve, pero no compite con lo que el usuario pidió.
const FUERA_STYLE = { color: '#525252', fillColor: '#a3a3a3', fillOpacity: 0.5, weight: 1 }
// La activa se reconoce por el borde grueso y el tamaño, no por un color nuevo:
// el color sigue diciendo venta/alquiler.
const ACTIVE_RING = { color: '#2563eb', weight: 3, fillOpacity: 1 }

function fmtPrice(p: Ubicada) {
  if (p.precio == null) return 'Consultar'
  const n = new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 }).format(p.precio)
  return `${p.moneda ?? 'USD'} ${n}${p.tipo_operacion !== 'venta' ? '/mes' : ''}`
}

const baseStyle = (p: Ubicada) =>
  p.matches_criteria === false
    ? FUERA_STYLE
    : p.tipo_operacion === 'alquiler'
      ? ALQUILER_STYLE
      : VENTA_STYLE

/** Encuadra todos los marcadores cuando cambia el CONJUNTO (no en cada render:
 *  seleccionar una propiedad no tiene que volver a alejar el mapa). La key es
 *  el string de ids porque el array del padre se recrea en cada render. */
function FitToResults({ propiedades }: { propiedades: Ubicada[] }) {
  const map = useMap()
  const fitKey = propiedades.map((p) => p.id).join(',')
  useEffect(() => {
    if (propiedades.length === 0) return
    if (propiedades.length === 1) {
      map.setView([propiedades[0].lat, propiedades[0].lng], 16)
      return
    }
    map.fitBounds(
      propiedades.map((p): [number, number] => [p.lat, p.lng]),
      { padding: [40, 40], maxZoom: 16 }
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, fitKey])
  return null
}

/** Lleva el mapa a la propiedad activa y abre su popup. Nunca aleja: si el
 *  usuario ya hizo zoom de calle, se respeta. */
function FocusActive({
  target,
  markers,
}: {
  target: Ubicada | null
  markers: React.RefObject<Map<string, LeafletCircleMarker>>
}) {
  const map = useMap()
  useEffect(() => {
    if (!target) return
    const marker = markers.current.get(target.id)
    marker?.bringToFront()
    const open = () => marker?.openPopup()
    map.once('moveend', open)
    // setView y no flyTo: si el marcador ya está centrado (click desde el
    // mapa), setView igual dispara `moveend`; flyTo con distancia cero no es
    // confiable.
    map.setView([target.lat, target.lng], Math.max(map.getZoom(), 15), { animate: true })
    return () => {
      map.off('moveend', open)
    }
    // Por id y no por objeto: si el padre reemplaza la propiedad (p. ej. al
    // marcarla como enviada) no hay que volver a mover el mapa.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, target?.id, markers])
  return null
}

/**
 * Mapa de los resultados de una búsqueda, sincronizado con el listado.
 * `activeId` es la única fuente de verdad: el padre la cambia al tocar una
 * tarjeta, y este componente la pide con `onSelect` al tocar un marcador.
 */
export default function ResultsMap({
  propiedades,
  activeId,
  onSelect,
}: {
  propiedades: Ubicada[]
  activeId: string | null
  onSelect: (id: string) => void
}) {
  const markers = useRef(new Map<string, LeafletCircleMarker>())
  const active = propiedades.find((p) => p.id === activeId) ?? null

  // SVG pinta en orden: venta arriba de alquiler, y la activa arriba de todo.
  const ordered = sortVentaFirst(propiedades).reverse()

  return (
    <MapContainer
      center={propiedades[0] ? [propiedades[0].lat, propiedades[0].lng] : [-34.6037, -58.3816]}
      zoom={13}
      className="h-full w-full"
      scrollWheelZoom
      zoomControl={false}
    >
      <BaseTiles />
      <ZoomControl position="bottomright" />
      <FitToResults propiedades={propiedades} />
      <FocusActive target={active} markers={markers} />
      {ordered.map((p) => {
        const isActive = p.id === activeId
        return (
          <CircleMarker
            key={p.id}
            ref={(m) => {
              if (m) markers.current.set(p.id, m)
              else markers.current.delete(p.id)
            }}
            center={[p.lat, p.lng]}
            radius={isActive ? 11 : 7}
            pathOptions={isActive ? { ...baseStyle(p), ...ACTIVE_RING } : baseStyle(p)}
            eventHandlers={{ click: () => onSelect(p.id) }}
          >
            <Popup>
              <div className="w-48">
                <PropertyPhoto
                  src={p.imagenes?.[0]}
                  alt={p.titulo ?? p.direccion}
                  imgClassName="mb-2 h-24 w-full rounded-md object-cover"
                  boxClassName="mb-2 h-24 w-full rounded-md"
                />
                {operacionLabel(p) && (
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {operacionLabel(p)}
                  </p>
                )}
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
        )
      })}
    </MapContainer>
  )
}
