'use client'
import 'leaflet/dist/leaflet.css'
import L from 'leaflet'
import { useEffect, useMemo } from 'react'
import { MapContainer, Marker, useMap } from 'react-leaflet'
import { BaseTiles } from '@/components/map/BaseTiles'
import type { Ubicada } from '@/lib/geo'

// Se importa SÓLO con `dynamic({ ssr: false })`: Leaflet toca `window` al
// evaluarse (ver la nota en lib/geo.ts).

/** "U$S130k" / "$450k": corto a propósito, entra en un pin. */
function precioCorto(p: Ubicada): string {
  if (p.precio == null) return 'Consultar'
  const sim = p.moneda === 'ARS' ? '$' : 'U$S'
  if (p.precio >= 1_000_000) return `${sim}${(p.precio / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  if (p.precio >= 1_000) return `${sim}${Math.round(p.precio / 1_000)}k`
  return `${sim}${p.precio}`
}

function pin(texto: string, activo: boolean): L.DivIcon {
  const cls = activo
    ? 'background:#111;color:#fff;border-color:#111'
    : 'background:#fff;color:#111;border-color:rgba(0,0,0,.18)'
  return L.divIcon({
    className: '',
    iconSize: undefined,
    html: `<span style="${cls};display:inline-block;transform:translate(-50%,-50%);white-space:nowrap;border:1px solid;border-radius:999px;padding:2px 8px;font:600 11px/1.4 ui-sans-serif,system-ui;box-shadow:0 1px 3px rgba(0,0,0,.2)">${texto}</span>`,
  })
}

function Encuadre({ puntos }: { puntos: [number, number][] }) {
  const map = useMap()
  useEffect(() => {
    if (puntos.length === 0) return
    if (puntos.length === 1) map.setView(puntos[0], 15)
    else map.fitBounds(puntos, { padding: [40, 40], maxZoom: 15 })
  }, [map, puntos])
  return null
}

/** Mapa de la selección: un pin con el precio por propiedad ubicada. */
export default function ColeccionMap({
  propiedades,
  activa,
  onSelect,
}: {
  propiedades: Ubicada[]
  activa?: string | null
  onSelect?: (id: string) => void
}) {
  const puntos = useMemo(() => propiedades.map((p): [number, number] => [p.lat, p.lng]), [propiedades])

  return (
    <MapContainer center={puntos[0] ?? [-34.9214, -57.9544]} zoom={13} className="h-full w-full" scrollWheelZoom={false}>
      <BaseTiles />
      <Encuadre puntos={puntos} />
      {propiedades.map((p) => (
        <Marker
          key={p.id}
          position={[p.lat, p.lng]}
          icon={pin(precioCorto(p), p.id === activa)}
          eventHandlers={onSelect ? { click: () => onSelect(p.id) } : undefined}
        />
      ))}
    </MapContainer>
  )
}
