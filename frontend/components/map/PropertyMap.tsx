'use client'
import 'leaflet/dist/leaflet.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import { latLng, latLngBounds, type CircleMarker as LeafletCircleMarker } from 'leaflet'
import { Loader2, MapPin, X } from 'lucide-react'
import {
  Circle,
  CircleMarker,
  MapContainer,
  Polygon,
  Polyline,
  Popup,
  ZoomControl,
  useMap,
  useMapEvents,
} from 'react-leaflet'
import { BaseTiles } from '@/components/map/BaseTiles'
import { useResolvedTheme } from '@/lib/theme'
import { distanceMeters, distanceToLinesMeters, formatDistance, pointInPolygon } from '@/lib/geo'
import { operacionLabel, sortVentaFirst } from '@/lib/operacion'
import { EMPTY_FILTER, matchesFilter, type Filter } from '@/lib/propertyFilters'
import { AgencySelector } from '@/components/chat/AgencySelector'
import { ProgressBubble } from '@/components/chat/ProgressBubble'
import { PropertyPhoto } from '@/components/property/PropertyPhoto'
import { SourceSelector } from '@/components/chat/SourceSelector'
import { SaveSearchPanel } from '@/components/map/SaveSearchPanel'
import { SavedZonesPanel } from '@/components/map/SavedZonesPanel'
import { ApifyCostChip } from '@/components/cost/ApifyCostChip'
import { useZoneSearch } from '@/hooks/useZoneSearch'
import { useSavedZones, type SavedZone } from '@/hooks/useSavedZones'
import { useBarriosCerrados } from '@/hooks/useBarriosCerrados'
import { fetchPlaceGeometry } from '@/hooks/usePlaceSearch'
import { MapSearchBar, type SearchPick } from '@/components/map/MapSearchBar'
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
// La zona dibujada es tinta sobre el fondo: casi negra en claro, casi blanca
// en oscuro. Con un solo color uno de los dos temas la pierde.
const ZONE_STYLES = {
  light: { color: '#111', weight: 2, fillColor: '#111', fillOpacity: 0.06, dashArray: '6 4' },
  dark: { color: '#f5f5f5', weight: 2, fillColor: '#f5f5f5', fillOpacity: 0.08, dashArray: '6 4' },
}
const VERTEX_STYLES = {
  light: { color: '#111', fillColor: '#fff', fillOpacity: 1, weight: 2 },
  dark: { color: '#f5f5f5', fillColor: '#171717', fillOpacity: 1, weight: 2 },
}

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

/** Zooms the map to a delineation the user just picked from the saved list.
 *  `nonce` re-fires the fit when the SAME zone is picked twice (the polygon
 *  reference alone wouldn't change). */
function FitPolygonHandler({
  polygon,
  nonce,
}: {
  polygon: [number, number][]
  nonce: number
}) {
  const map = useMap()
  useEffect(() => {
    if (polygon.length < 3) return
    map.fitBounds(polygon, { padding: [40, 40] })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, nonce])
  return null
}

/** Fits the map to the active place (circle or street) whenever `nonce` bumps. */
function FitPlaceHandler({ place, radiusM, nonce }: { place: ActivePlace; radiusM: number; nonce: number }) {
  const map = useMap()
  useEffect(() => {
    const bounds = place.lines
      ? latLngBounds(place.lines.flat())
      : latLng(place.point).toBounds(radiusM * 2)
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 17 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, nonce])
  return null
}

/** One-shot "elegí un punto": the next map click becomes the search center. */
function PointPickHandler({ enabled, onPick }: { enabled: boolean; onPick: (p: [number, number]) => void }) {
  const map = useMapEvents({
    click(e) {
      if (enabled) onPick([e.latlng.lat, e.latlng.lng])
    },
  })
  useEffect(() => {
    if (enabled) map.getContainer().style.cursor = 'crosshair'
    return () => {
      map.getContainer().style.cursor = ''
    }
  }, [enabled, map])
  return null
}

type DrawState = 'idle' | 'drawing' | 'closed'

/** A search center: a point (address, my location, clicked spot, barrio
 *  without boundary) or a street. Properties match within `radiusM` of it. */
type ActivePlace = { label: string; point: [number, number]; lines?: [number, number][][] }

const POINT_RADII = [300, 500, 1000, 2000, 5000]
const STREET_RADII = [50, 100, 200, 400]
const DEFAULT_POINT_RADIUS = 1000
const DEFAULT_STREET_RADIUS = 100

export default function PropertyMap({
  properties,
  cartera = [],
  focusId,
  filter = EMPTY_FILTER,
  onFilterChange,
}: {
  properties: MapProperty[]
  cartera?: CarteraProperty[]
  focusId?: string
  filter?: Filter
  onFilterChange?: (next: Filter) => void
}) {
  const [drawState, setDrawState] = useState<DrawState>('idle')
  const [vertices, setVertices] = useState<[number, number][]>([])
  // Where to scrape this zone. Chosen in the confirm modal, defaults to
  // "search everything" so behaviour matches the pre-selector map.
  const [selection, setSelection] = useState<SourceSelection>(DEFAULT_SELECTION)
  const zone = useZoneSearch()
  // Named delineations: geometry only, independent of any search. Picking one
  // draws it; `activeZoneId` marks that what's on the map came from the list
  // (so we don't offer to save it again).
  const savedZones = useSavedZones()
  const [activeZoneId, setActiveZoneId] = useState<string | null>(null)
  const [fitNonce, setFitNonce] = useState(0)
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

  const barrios = useBarriosCerrados()
  const [place, setPlace] = useState<ActivePlace | null>(null)
  const [radiusM, setRadiusM] = useState(DEFAULT_POINT_RADIUS)
  const [placeNonce, setPlaceNonce] = useState(0)
  const [pickingPoint, setPickingPoint] = useState(false)
  const [placeLoading, setPlaceLoading] = useState(false)
  const [placeNotice, setPlaceNotice] = useState<string | null>(null)

  // Place and drawn zone are alternative ways to say "where": the place is
  // only applied when no polygon is on the map.
  const placeActive = place !== null && !zoneActive
  const areaActive = zoneActive || placeActive

  const distanceTo = useMemo(() => {
    if (!place) return null
    return (lat: number, lng: number) =>
      place.lines ? distanceToLinesMeters([lat, lng], place.lines) : distanceMeters([lat, lng], place.point)
  }, [place])

  const insideProps = useMemo(() => {
    if (zoneActive) return visibleProperties.filter((p) => pointInPolygon(p.lat, p.lng, vertices))
    if (placeActive && distanceTo) {
      return visibleProperties
        .map((p) => ({ p, d: distanceTo(p.lat, p.lng) }))
        .filter(({ d }) => d <= radiusM)
        .sort((a, b) => a.d - b.d)
        .map(({ p }) => p)
    }
    return []
  }, [zoneActive, placeActive, distanceTo, radiusM, visibleProperties, vertices])
  const insideCartera = useMemo(() => {
    if (zoneActive) return cartera.filter((c) => pointInPolygon(c.lat, c.lng, vertices))
    if (placeActive && distanceTo) return cartera.filter((c) => distanceTo(c.lat, c.lng) <= radiusM)
    return []
  }, [zoneActive, placeActive, distanceTo, radiusM, cartera, vertices])
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

  const clearPlace = () => {
    setPlace(null)
    setPickingPoint(false)
    setPlaceNotice(null)
  }

  const showPlace = (next: ActivePlace, radius: number) => {
    setPlace(next)
    setRadiusM(radius)
    setPlaceNonce((n) => n + 1)
  }

  const showPolygon = (polygon: [number, number][]) => {
    zone.cancel()
    clearPlace()
    setVertices(polygon)
    setDrawState('closed')
    setActiveZoneId(null)
    setFitNonce((n) => n + 1)
  }

  // A polygon or place pick replaces whatever "where" was active (drawn zone
  // or previous place); attribute picks (zona, inmobiliaria, palabra) merge
  // into the filter and compose with it.
  const handleSearchPick = async (pick: SearchPick) => {
    setPlaceNotice(null)
    setPickingPoint(false)
    switch (pick.type) {
      case 'zona':
      case 'fuente':
        onFilterChange?.({ ...filter, [pick.type]: pick.value })
        return
      case 'palabra':
        onFilterChange?.({ ...filter, q: pick.value })
        return
      case 'polygon':
        if (pick.savedZone) {
          clearPlace()
          selectSavedZone(pick.savedZone)
        } else {
          showPolygon(pick.polygon)
        }
        return
      case 'pick-on-map':
        setPickingPoint(true)
        return
      case 'my-location':
        if (!navigator.geolocation) {
          setPlaceNotice('El navegador no permite obtener tu ubicación')
          return
        }
        setPlaceLoading(true)
        navigator.geolocation.getCurrentPosition(
          (pos) => {
            setPlaceLoading(false)
            leaveZone()
            showPlace({ label: 'Mi ubicación', point: [pos.coords.latitude, pos.coords.longitude] }, DEFAULT_POINT_RADIUS)
          },
          () => {
            setPlaceLoading(false)
            setPlaceNotice('No se pudo obtener tu ubicación (¿permiso denegado?)')
          },
          { enableHighAccuracy: true, timeout: 10_000 }
        )
        return
      case 'place': {
        const p = pick.place
        const point: [number, number] = [p.lat, p.lng]
        setPlaceLoading(true)
        const geom = await fetchPlaceGeometry(p)
        setPlaceLoading(false)
        if (p.kind === 'area' && geom.polygon && geom.polygon.length >= 3) {
          showPolygon(geom.polygon)
          return
        }
        leaveZone()
        if (p.kind === 'street' && geom.lines?.length) {
          showPlace({ label: p.label, point, lines: geom.lines }, DEFAULT_STREET_RADIUS)
          return
        }
        if (p.kind === 'area' || p.kind === 'street') {
          setPlaceNotice(`No encontramos el contorno de ${p.name}: buscamos por radio`)
        }
        showPlace({ label: p.label, point }, p.kind === 'area' ? 2000 : DEFAULT_POINT_RADIUS)
        return
      }
    }
  }

  // Drops a drawn/selected polygon so a place takes over as the "where".
  const leaveZone = () => {
    if (drawState === 'idle') return
    setVertices([])
    setActiveZoneId(null)
    setDrawState('idle')
    zone.cancel()
  }

  const startDrawing = () => {
    clearPlace()
    setVertices([])
    setActiveZoneId(null)
    setDrawState('drawing')
  }
  const closeZone = () => setDrawState('closed')
  const clearZone = () => {
    setVertices([])
    setActiveZoneId(null)
    setDrawState('idle')
    zone.cancel()
  }

  // Re-draw a stored delineation. Any previous job overlay is dropped: the
  // saved zone carries no results, it's a fresh starting point.
  const selectSavedZone = (saved: SavedZone) => {
    zone.cancel()
    setVertices(saved.polygon)
    setDrawState('closed')
    setActiveZoneId(saved.id)
    setFitNonce((n) => n + 1)
  }

  const saveCurrentZone = (name: string) => {
    void savedZones.saveZone(name, vertices).then((saved) => {
      if (saved) setActiveZoneId(saved.id)
    })
  }

  const deleteSavedZone = (id: string) => {
    void savedZones.deleteZone(id)
    if (id === activeZoneId) setActiveZoneId(null)
  }

  const theme = useResolvedTheme()
  const ZONE_STYLE = ZONE_STYLES[theme]
  const VERTEX_STYLE = VERTEX_STYLES[theme]

  return (
    <div className="relative h-full w-full">
    <MapContainer
      center={BUENOS_AIRES_CENTER}
      zoom={INITIAL_ZOOM}
      className="h-full w-full"
      scrollWheelZoom
      zoomControl={false}
    >
      <BaseTiles />
      <ZoomControl position="bottomright" />
      <PointPickHandler
        enabled={pickingPoint}
        onPick={(point) => {
          setPickingPoint(false)
          leaveZone()
          showPlace({ label: 'Punto elegido', point }, DEFAULT_POINT_RADIUS)
        }}
      />
      {placeActive && place && (
        <>
          {place.lines ? (
            place.lines.map((line, i) => (
              <Polyline key={`street-${i}`} positions={line} pathOptions={{ ...ZONE_STYLE, dashArray: undefined, weight: 4 }} />
            ))
          ) : (
            <Circle center={place.point} radius={radiusM} pathOptions={ZONE_STYLE} />
          )}
          {!place.lines && <CircleMarker center={place.point} radius={5} pathOptions={VERTEX_STYLE} />}
          <FitPlaceHandler place={place} radiusM={radiusM} nonce={placeNonce} />
        </>
      )}
      <DrawClickHandler
        enabled={drawState === 'drawing'}
        onAddVertex={(v) => setVertices((prev) => [...prev, v])}
      />
      {drawState === 'drawing' && vertices.length >= 2 && (
        <Polyline positions={vertices} pathOptions={ZONE_STYLE} />
      )}
      {zoneActive && <Polygon positions={vertices} pathOptions={ZONE_STYLE} />}
      {fitNonce > 0 && <FitPolygonHandler polygon={vertices} nonce={fitNonce} />}
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
          pathOptions={areaActive && !insideIds.has(p.id) ? DIMMED_STYLE : markerStyleFor(p)}
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
      ))}
      {cartera.map((c) => (
        <CircleMarker
          key={`cartera-${c.id}`}
          center={[c.lat, c.lng]}
          radius={6}
          pathOptions={
            areaActive && !insideCarteraIds.has(c.id) ? CARTERA_DIMMED_STYLE : CARTERA_STYLE
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
        ))}
    </MapContainer>

    <div className="absolute left-4 top-4 z-[1001] flex w-[22rem] max-w-[calc(100%-2rem)] flex-col gap-2 md:max-w-[calc(100%-22rem)]">
      <MapSearchBar savedZones={savedZones.zones} barrios={barrios.barrios} onPick={(pick) => void handleSearchPick(pick)} />
      {placeLoading && (
        <p className="flex items-center gap-2 rounded-lg border border-border bg-background/95 px-3 py-1.5 text-xs text-muted-foreground shadow-sm">
          <Loader2 className="size-3.5 animate-spin" /> Buscando...
        </p>
      )}
      {pickingPoint && (
        <p className="flex items-center justify-between gap-2 rounded-lg border border-border bg-background/95 px-3 py-1.5 text-xs text-muted-foreground shadow-sm">
          Hacé clic en el mapa para elegir el punto
          <button onClick={() => setPickingPoint(false)} className="font-medium text-foreground underline">
            Cancelar
          </button>
        </p>
      )}
      {placeNotice && (
        <p className="rounded-lg border border-border bg-background/95 px-3 py-1.5 text-xs text-muted-foreground shadow-sm">
          {placeNotice}
        </p>
      )}
      {placeActive && place && (
        <div className="flex items-center gap-2 rounded-lg border border-border bg-background/95 px-3 py-1.5 text-xs shadow-sm">
          <MapPin className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate text-foreground" title={place.label}>
            {place.label}
          </span>
          <label className="flex shrink-0 items-center gap-1 text-muted-foreground">
            {place.lines ? 'a' : 'radio'}
            <select
              value={radiusM}
              onChange={(e) => setRadiusM(Number(e.target.value))}
              className="rounded border border-border bg-card px-1 py-0.5 text-xs text-foreground"
            >
              {(place.lines ? STREET_RADII : POINT_RADII).map((r) => (
                <option key={r} value={r}>
                  {formatDistance(r)}
                </option>
              ))}
            </select>
          </label>
          <button
            aria-label="Quitar ubicación"
            onClick={clearPlace}
            className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="size-3.5" />
          </button>
        </div>
      )}
    </div>

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
      <SavedZonesPanel
        zones={savedZones.zones}
        loading={savedZones.loading}
        error={savedZones.error}
        activeZoneId={activeZoneId}
        canSave={zoneActive && activeZoneId === null}
        onSelect={selectSavedZone}
        onSave={saveCurrentZone}
        onRename={(id, name) => void savedZones.renameZone(id, name)}
        onDelete={deleteSavedZone}
      />
      {zone.error && (
        <p className="rounded-lg border border-border bg-background/90 px-3 py-1.5 text-xs text-muted-foreground shadow-sm">
          {zone.error}
        </p>
      )}
      {areaActive && (
        <div className="flex max-h-[60vh] w-72 flex-col overflow-hidden rounded-lg border border-border bg-background/95 shadow-sm">
          <p className="border-b border-border px-3 py-2 text-xs font-semibold text-foreground">
            {insideProps.length + insideCartera.length} propiedades{' '}
            {placeActive ? `a menos de ${formatDistance(radiusM)}` : 'en la zona'}
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
                <p className="flex items-baseline justify-between gap-2 text-xs font-semibold text-foreground">
                  {fmtPrice(p)}
                  {placeActive && distanceTo && (
                    <span className="shrink-0 font-normal text-muted-foreground">
                      {formatDistance(distanceTo(p.lat, p.lng))}
                    </span>
                  )}
                </p>
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
                {placeActive ? 'No hay propiedades en ese radio.' : 'No hay propiedades dentro de la zona marcada.'}
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
            manualSources={zone.manualSources}
            message={zone.agenciesMessage ?? 'Seleccioná las agencias a incluir'}
            onConfirm={(ids, manualIds) => zone.confirmAgencies(ids, manualIds)}
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
