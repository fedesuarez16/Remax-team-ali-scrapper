'use client'
import 'leaflet/dist/leaflet.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { CircleMarker as LeafletCircleMarker } from 'leaflet'
import { Loader2 } from 'lucide-react'
import {
  CircleMarker,
  MapContainer,
  Polygon,
  Polyline,
  Popup,
  TileLayer,
  useMap,
  useMapEvents,
} from 'react-leaflet'
import { pointInPolygon } from '@/lib/geo'
import { operacionLabel, sortVentaFirst } from '@/lib/operacion'
import { EMPTY_FILTER, matchesFilter, type Filter } from '@/lib/propertyFilters'
import { AgencySelector } from '@/components/chat/AgencySelector'
import { ProgressBubble } from '@/components/chat/ProgressBubble'
import { SourceSelector } from '@/components/chat/SourceSelector'
import { SaveSearchPanel } from '@/components/map/SaveSearchPanel'
import { ApifyCostChip } from '@/components/cost/ApifyCostChip'
import { useZoneSearch } from '@/hooks/useZoneSearch'
import {
  DEFAULT_SELECTION,
  describeSelection,
  isSelectionEmpty,
  type SourceSelection,
} from '@/lib/sources'

export type MapProperty = {
  id: string
  titulo: string | null
  precio: number | null
  moneda: string
  direccion: string
  tipo_operacion: string
  tipo_propiedad: string
  imagenes: string[]
  lat: number
  lng: number
  // Attribute-filter columns — served by GET /properties/map so the map can
  // filter markers client-side (see matchesFilter). Optional: legacy payloads
  // and the job overlay may omit them, in which case range filters exclude them.
  fuente?: string | null
  ambientes?: number | null
  banos?: number | null
  cocheras?: number | null
  m2_total?: number | null
  /** Marca de envío al cliente (ver filtro "Envío"). null = todavía no enviada. */
  enviada_at?: string | null
  // Server-classified (GET /{job_id}/properties) inside/outside tag for
  // zone-search job results — the single source of truth, no client
  // re-derivation. Undefined/null for properties outside the job-results flow.
  in_polygon?: boolean | null
}

export type CarteraProperty = {
  id: number
  direccion: string | null
  zona: string | null
  valor: string | null
  tipo_de_propiedad: string | null
  dormitorios: string | null
  link: string | null
  lat: number
  lng: number
}

const BUENOS_AIRES_CENTER: [number, number] = [-34.6037, -58.3816]
const INITIAL_ZOOM = 12

// Verde: scrapeadas (venta) · Amarillo: alquiler · Rojo: cartera (propias).
const MARKER_STYLE = { color: '#15803d', fillColor: '#16a34a', fillOpacity: 0.85, weight: 1 }
const ALQUILER_STYLE = { color: '#a16207', fillColor: '#eab308', fillOpacity: 0.95, weight: 1.5 }
const CARTERA_STYLE = { color: '#991b1b', fillColor: '#dc2626', fillOpacity: 0.9, weight: 1 }
const DIMMED_STYLE = { color: '#15803d', fillColor: '#16a34a', fillOpacity: 0.12, weight: 1, opacity: 0.25 }
const CARTERA_DIMMED_STYLE = { color: '#991b1b', fillColor: '#dc2626', fillOpacity: 0.12, weight: 1, opacity: 0.25 }
const ZONE_STYLE = { color: '#111', weight: 2, fillColor: '#111', fillOpacity: 0.06, dashArray: '6 4' }
const VERTEX_STYLE = { color: '#111', fillColor: '#fff', fillOpacity: 1, weight: 2 }

function fmtPrice(p: MapProperty) {
  if (p.precio == null) return 'Consultar'
  const n = new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 }).format(p.precio)
  return `${p.moneda ?? 'USD'} ${n}${p.tipo_operacion !== 'venta' ? '/mes' : ''}`
}

const markerStyleFor = (p: MapProperty) =>
  p.tipo_operacion === 'alquiler' ? ALQUILER_STYLE : MARKER_STYLE

// SVG stacking: later markers paint on top — venta last so it sits above.
const ventaOnTop = (list: MapProperty[]) => sortVentaFirst(list).reverse()

function DrawClickHandler({
  enabled,
  onAddVertex,
}: {
  enabled: boolean
  onAddVertex: (v: [number, number]) => void
}) {
  const map = useMapEvents({
    click(e) {
      if (enabled) onAddVertex([e.latlng.lat, e.latlng.lng])
    },
  })
  useEffect(() => {
    map.getContainer().style.cursor = enabled ? 'crosshair' : ''
  }, [enabled, map])
  return null
}

function FocusHandler({
  target,
  markerRef,
}: {
  target: [number, number]
  markerRef: React.RefObject<LeafletCircleMarker | null>
}) {
  const map = useMap()
  useEffect(() => {
    map.flyTo(target, 16)
    map.once('moveend', () => markerRef.current?.openPopup())
  }, [map, target, markerRef])
  return null
}

type DrawState = 'idle' | 'drawing' | 'closed'

export default function PropertyMap({
  properties,
  cartera = [],
  focusId,
  filter = EMPTY_FILTER,
}: {
  properties: MapProperty[]
  cartera?: CarteraProperty[]
  focusId?: string
  filter?: Filter
}) {
  const [drawState, setDrawState] = useState<DrawState>('idle')
  const [vertices, setVertices] = useState<[number, number][]>([])
  // Where to scrape this zone. Chosen in the confirm modal, defaults to
  // "search everything" so behaviour matches the pre-selector map.
  const [selection, setSelection] = useState<SourceSelection>(DEFAULT_SELECTION)
  const zone = useZoneSearch()
  const focusMarkerRef = useRef<LeafletCircleMarker | null>(null)
  const focused = useMemo(
    () => (focusId ? properties.find((p) => p.id === focusId) ?? null : null),
    [focusId, properties]
  )

  // Attribute filters narrow which markers render; they compose with the drawn
  // zone (filter first, then the polygon dims what's left outside it). Cartera
  // has a different shape (no numeric precio/ambientes) so it stays unfiltered.
  const visibleProperties = useMemo(
    () => properties.filter((p) => matchesFilter(p, filter)),
    [properties, filter]
  )
  const visibleJobProperties = useMemo(
    () => zone.jobProperties.filter((p) => matchesFilter(p, filter)),
    [zone.jobProperties, filter]
  )

  const zoneActive = drawState === 'closed' && vertices.length >= 3
  const jobOverlayActive = zone.phase !== 'idle'

  const insideProps = useMemo(
    () => (zoneActive ? visibleProperties.filter((p) => pointInPolygon(p.lat, p.lng, vertices)) : []),
    [zoneActive, visibleProperties, vertices]
  )
  const insideCartera = useMemo(
    () => (zoneActive ? cartera.filter((c) => pointInPolygon(c.lat, c.lng, vertices)) : []),
    [zoneActive, cartera, vertices]
  )
  const insideIds = useMemo(() => new Set(insideProps.map((p) => p.id)), [insideProps])
  const insideCarteraIds = useMemo(() => new Set(insideCartera.map((c) => c.id)), [insideCartera])

  // Job results are classified server-side (`GET /{job_id}/properties`
  // `in_polygon` tag) — no client-side `pointInPolygon` re-derivation here.
  const jobInsideIds = useMemo(() => {
    if (zone.phase !== 'done') return new Set<string>()
    return new Set(
      visibleJobProperties.filter((p) => p.in_polygon !== false).map((p) => p.id)
    )
  }, [zone.phase, visibleJobProperties])

  const startDrawing = () => {
    setVertices([])
    setDrawState('drawing')
  }
  const closeZone = () => setDrawState('closed')
  const clearZone = () => {
    setVertices([])
    setDrawState('idle')
    zone.cancel()
  }

  return (
    <div className="relative h-full w-full">
    <MapContainer
      center={BUENOS_AIRES_CENTER}
      zoom={INITIAL_ZOOM}
      className="h-full w-full"
      scrollWheelZoom
    >
      <TileLayer
        url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
        attribution="&copy; OpenStreetMap &copy; CARTO"
      />
      <DrawClickHandler
        enabled={drawState === 'drawing'}
        onAddVertex={(v) => setVertices((prev) => [...prev, v])}
      />
      {drawState === 'drawing' && vertices.length >= 2 && (
        <Polyline positions={vertices} pathOptions={ZONE_STYLE} />
      )}
      {zoneActive && <Polygon positions={vertices} pathOptions={ZONE_STYLE} />}
      {drawState === 'drawing' &&
        vertices.map((v, i) => (
          <CircleMarker key={`vertex-${i}`} center={v} radius={5} pathOptions={VERTEX_STYLE} />
        ))}
      {focused && (
        <FocusHandler target={[focused.lat, focused.lng]} markerRef={focusMarkerRef} />
      )}
      {ventaOnTop(visibleProperties).map((p) => (
        <CircleMarker
          key={p.id}
          ref={p.id === focusId ? focusMarkerRef : undefined}
          center={[p.lat, p.lng]}
          radius={p.id === focusId ? 9 : 6}
          pathOptions={zoneActive && !insideIds.has(p.id) ? DIMMED_STYLE : markerStyleFor(p)}
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
      ))}
      {cartera.map((c) => (
        <CircleMarker
          key={`cartera-${c.id}`}
          center={[c.lat, c.lng]}
          radius={6}
          pathOptions={
            zoneActive && !insideCarteraIds.has(c.id) ? CARTERA_DIMMED_STYLE : CARTERA_STYLE
          }
        >
          <Popup>
            <div className="w-48">
              <p className="text-sm font-semibold text-foreground">{c.valor || 'Consultar'}</p>
              <p className="text-xs text-muted-foreground">
                {[c.tipo_de_propiedad, c.dormitorios && `${c.dormitorios} dorm.`]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
              <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                {[c.direccion, c.zona].filter(Boolean).join(', ')}
              </p>
              {c.link && (
                <a
                  href={c.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2 inline-block text-xs font-medium text-foreground underline"
                >
                  Ver aviso
                </a>
              )}
            </div>
          </Popup>
        </CircleMarker>
      ))}
      {zone.phase === 'done' &&
        ventaOnTop(visibleJobProperties).map((p) => (
          <CircleMarker
            key={`job-${p.id}`}
            center={[p.lat, p.lng]}
            radius={6}
            pathOptions={!jobInsideIds.has(p.id) ? DIMMED_STYLE : markerStyleFor(p)}
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
        ))}
    </MapContainer>

    <div className="absolute right-4 top-4 z-[1000] flex flex-col items-end gap-2">
      <div className="flex gap-2">
        {drawState === 'idle' && (
          <button
            onClick={startDrawing}
            className="rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-muted"
          >
            Delinear zona
          </button>
        )}
        {drawState === 'drawing' && (
          <>
            <button
              onClick={closeZone}
              disabled={vertices.length < 3}
              className="rounded-lg bg-foreground px-3 py-1.5 text-xs font-medium text-background shadow-sm disabled:opacity-40"
            >
              Cerrar zona
            </button>
            <button
              onClick={clearZone}
              className="rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-muted"
            >
              Cancelar
            </button>
          </>
        )}
        {drawState === 'closed' && (
          <>
            <button
              onClick={() => zone.resolveZone(vertices)}
              disabled={zone.phase === 'resolving' || jobOverlayActive}
              className="flex items-center gap-1.5 rounded-lg bg-foreground px-3 py-1.5 text-xs font-medium text-background shadow-sm disabled:opacity-60"
            >
              {zone.phase === 'resolving' && <Loader2 className="size-3.5 animate-spin" />}
              {zone.phase === 'resolving' ? 'Detectando barrios...' : 'Buscar en esta zona'}
            </button>
            <button
              onClick={clearZone}
              className="rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-muted"
            >
              Borrar zona
            </button>
          </>
        )}
      </div>
      {drawState === 'drawing' && (
        <p className="rounded-lg border border-border bg-background/90 px-3 py-1.5 text-xs text-muted-foreground shadow-sm">
          Hacé clic en el mapa para marcar los vértices ({vertices.length}
          {vertices.length < 3 ? ' · mínimo 3' : ''})
        </p>
      )}
      {zone.error && (
        <p className="rounded-lg border border-border bg-background/90 px-3 py-1.5 text-xs text-muted-foreground shadow-sm">
          {zone.error}
        </p>
      )}
      {zoneActive && (
        <div className="flex max-h-[60vh] w-72 flex-col overflow-hidden rounded-lg border border-border bg-background/95 shadow-sm">
          <p className="border-b border-border px-3 py-2 text-xs font-semibold text-foreground">
            {insideProps.length + insideCartera.length} propiedades en la zona
          </p>
          <div className="overflow-y-auto">
            {insideProps.map((p) => (
              <a
                key={p.id}
                href={`/p/${p.id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="block border-b border-border px-3 py-2 hover:bg-muted"
              >
                <p className="text-xs font-semibold text-foreground">{fmtPrice(p)}</p>
                <p className="line-clamp-1 text-xs text-muted-foreground">
                  {p.titulo ?? p.direccion}
                </p>
              </a>
            ))}
            {insideCartera.map((c) => (
              <a
                key={`cartera-${c.id}`}
                href={c.link ?? undefined}
                target={c.link ? '_blank' : undefined}
                rel={c.link ? 'noopener noreferrer' : undefined}
                className="block border-b border-border px-3 py-2 hover:bg-muted"
              >
                <p className="text-xs font-semibold text-[#dc2626]">{c.valor || 'Consultar'}</p>
                <p className="line-clamp-1 text-xs text-muted-foreground">
                  {[c.direccion, c.zona].filter(Boolean).join(', ') || c.tipo_de_propiedad}
                </p>
              </a>
            ))}
            {insideProps.length + insideCartera.length === 0 && (
              <p className="px-3 py-3 text-xs text-muted-foreground">
                No hay propiedades dentro de la zona marcada.
              </p>
            )}
          </div>
        </div>
      )}
    </div>

    <div className="absolute bottom-4 left-4 z-[1000] flex flex-col gap-1 rounded-lg border border-border bg-background/90 px-3 py-2 text-xs text-foreground shadow-sm">
      <span className="flex items-center gap-2">
        <span className="inline-block size-2.5 rounded-full bg-[#16a34a]" /> Scrapeadas
      </span>
      <span className="flex items-center gap-2">
        <span className="inline-block size-2.5 rounded-full bg-[#dc2626]" /> Propias
      </span>
      <span className="flex items-center gap-2">
        <span className="inline-block size-2.5 rounded-full bg-[#eab308]" /> Alquiler
      </span>
    </div>

    {zone.phase === 'confirm' && (
      <div className="absolute inset-0 z-[1100] flex items-center justify-center bg-black/30 p-4">
        <div className="max-h-[85vh] w-96 max-w-full space-y-3 overflow-y-auto rounded-2xl border border-border bg-background p-4 shadow-lg">
          <p className="text-sm font-semibold text-foreground">Zonas detectadas</p>
          <ul className="max-h-28 space-y-1 overflow-y-auto text-xs text-muted-foreground">
            {zone.zonas.map((z) => (
              <li key={z}>{z}</li>
            ))}
          </ul>

          <div className="space-y-1.5 border-t border-border pt-3">
            <p className="text-xs font-medium text-foreground">¿Dónde buscamos?</p>
            <SourceSelector value={selection} onChange={setSelection} />
            <p className="text-[11px] text-muted-foreground">
              {isSelectionEmpty(selection)
                ? 'Elegí al menos una fuente para buscar'
                : `Buscando en: ${describeSelection(selection)}`}
            </p>
          </div>

          <p className="text-xs text-muted-foreground">
            hasta ~{zone.estimatedActors} actores
          </p>
          <div className="flex justify-end gap-2 pt-1">
            <button
              onClick={zone.cancel}
              className="rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-muted"
            >
              Cancelar
            </button>
            <button
              onClick={() => zone.confirmRun(vertices, selection)}
              disabled={isSelectionEmpty(selection)}
              className="rounded-lg bg-foreground px-3 py-1.5 text-xs font-medium text-background shadow-sm disabled:opacity-40"
            >
              Buscar (~{zone.estimatedActors} actores)
            </button>
          </div>
        </div>
      </div>
    )}

    {(zone.phase === 'running' || zone.phase === 'agencies_review' || zone.phase === 'done') && (
      <div className="absolute bottom-4 right-4 z-[1000] w-80 max-w-[90vw]">
        {zone.phase === 'running' && !zone.stalled && (
          <ProgressBubble progress={zone.progress} matchedCount={zone.matchedCount} />
        )}
        {zone.phase === 'running' && zone.stalled && (
          <div className="space-y-2 rounded-2xl border border-border bg-background p-3.5 shadow-sm">
            <p className="text-xs font-medium text-foreground">
              Se perdió la conexión con el trabajo en curso.
            </p>
            <button
              onClick={() => void zone.checkJobStatus()}
              className="rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-muted"
            >
              Revisar estado
            </button>
          </div>
        )}
        {zone.phase === 'agencies_review' && zone.agencies && (
          <AgencySelector
            agencies={zone.agencies}
            message={zone.agenciesMessage ?? 'Seleccioná las agencias a incluir'}
            onConfirm={(ids) => zone.confirmAgencies(ids)}
            disabled={zone.isStreaming}
          />
        )}
        {zone.phase === 'done' && (
          <div className="space-y-2 rounded-2xl border border-border bg-background/95 p-3.5 shadow-sm">
            <div className="flex items-center gap-2">
              <p className="text-xs font-medium text-foreground">
                {zone.insideCount} propiedades ubicadas
              </p>
              <ApifyCostChip
                costUsd={zone.apifyCostUsd}
                breakdown={zone.apifyCostBreakdown}
                className="ml-auto"
              />
            </div>
            {zone.ungeocodedCount > 0 && (
              <p className="text-xs text-muted-foreground">
                sin ubicación aún ({zone.ungeocodedCount}){zone.draining ? ' · ubicando...' : ''}
              </p>
            )}
            {zone.jobId && (
              <a
                href={`/properties?job_id=${zone.jobId}`}
                className="inline-block text-xs font-medium text-foreground underline"
              >
                Ver grilla completa
              </a>
            )}
            {zone.savedEntryId && (
              <SaveSearchPanel entryId={zone.savedEntryId} defaultName={zone.runQuery} />
            )}
          </div>
        )}
      </div>
    )}
    </div>
  )
}
