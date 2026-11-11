import type { Property } from '@/hooks/useSSEStream'

/**
 * polygonCentroid — area-weighted centroid (shoelace formula) of a closed
 * polygon defined as [lat, lng] vertices. Treats lng as x, lat as y (planar
 * approximation, fine at city scale). Falls back to the arithmetic mean of
 * the vertices when the signed area is ~0 (degenerate/collinear points), to
 * avoid a divide-by-zero.
 *
 * Pure, framework-free, no side effects — kept unit-testable if a JS test
 * runner is added later.
 */
export function polygonCentroid(vertices: [number, number][]): [number, number] {
  const n = vertices.length
  if (n === 0) return [0, 0]
  if (n === 1) return vertices[0]

  let area = 0
  let cxAcc = 0 // accumulator for lng (x)
  let cyAcc = 0 // accumulator for lat (y)

  for (let i = 0; i < n; i++) {
    const [latI, lngI] = vertices[i]
    const [latJ, lngJ] = vertices[(i + 1) % n]
    const cross = lngI * latJ - lngJ * latI
    area += cross
    cxAcc += (lngI + lngJ) * cross
    cyAcc += (latI + latJ) * cross
  }
  area /= 2

  const EPS = 1e-12
  if (Math.abs(area) <= EPS) {
    const meanLat = vertices.reduce((sum, [lat]) => sum + lat, 0) / n
    const meanLng = vertices.reduce((sum, [, lng]) => sum + lng, 0) / n
    return [meanLat, meanLng]
  }

  const lng = cxAcc / (6 * area)
  const lat = cyAcc / (6 * area)
  return [lat, lng]
}

/**
 * pointInPolygon — ray-casting test, lng as x, lat as y. Moved here (from
 * `components/map/PropertyMap.tsx`) so both the map component and
 * `useZoneSearch` share ONE implementation.
 */
export function pointInPolygon(lat: number, lng: number, poly: [number, number][]): boolean {
  let inside = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [latI, lngI] = poly[i]
    const [latJ, lngJ] = poly[j]
    if (latI > lat !== latJ > lat && lng < ((lngJ - lngI) * (lat - latI)) / (latJ - latI) + lngI) {
      inside = !inside
    }
  }
  return inside
}

/**
 * samplePolygon — reduces a closed polygon to <= `cap` representative points
 * (centroid FIRST, so a truncated/aborted reverse-zonas call still resolves
 * the central barrio as a floor) before sending it to
 * `POST /geo/reverse-zonas`. Pure, framework-free.
 *
 * Vertex budget = cap - 1. If the polygon already fits, all vertices are
 * included; otherwise vertices are evenly downsampled (every `step`-th one)
 * so multi-barrio polygons still get spread coverage.
 */
export function samplePolygon(vertices: [number, number][], cap = 8): [number, number][] {
  const centroid = polygonCentroid(vertices)
  const budget = cap - 1
  if (vertices.length <= budget) {
    return [centroid, ...vertices]
  }
  const step = Math.ceil(vertices.length / budget)
  const sampled: [number, number][] = []
  for (let i = 0; i < vertices.length && sampled.length < budget; i += step) {
    sampled.push(vertices[i])
  }
  return [centroid, ...sampled]
}

/**
 * estaUbicada — ¿esta propiedad tiene con qué ir al mapa?
 *
 * Vive ACÁ y no en `components/map/SeleccionMap.tsx`, donde nació, por una
 * razón concreta de build: `SeleccionMapModal` carga ese componente con
 * `dynamic(..., { ssr: false })` porque Leaflet toca `window` al evaluar el
 * módulo — pero además importaba `estaUbicada` del MISMO archivo, con un
 * import estático. Ese import anula al `dynamic`: el módulo entra igual al
 * grafo de SSR, se lleva `react-leaflet` → `leaflet` puesto, y el prerender
 * de `/properties` (que llega hasta acá vía `EnviarFichasBar`) explota con
 * `ReferenceError: window is not defined`.
 *
 * El predicado no tiene nada de Leaflet: es dominio puro sobre `Property`.
 * Separarlo es lo que deja que el `dynamic` haga su trabajo. Mismo criterio
 * que `pointInPolygon` arriba.
 *
 * NO re-exportar desde un módulo que importe Leaflet: eso reabre el mismo
 * agujero por la puerta de atrás.
 */
export type Ubicada = Property & { id: string; lat: number; lng: number }

export const estaUbicada = (p: Property): p is Ubicada =>
  !!p.id && typeof p.lat === 'number' && typeof p.lng === 'number'

type LatLng = [number, number]

// Equirectangular projection around the point — exact enough at city scale
// and much cheaper than haversine when run over every marker on each render.
const M_PER_DEG_LAT = 110_540
const mPerDegLng = (lat: number) => 111_320 * Math.cos((lat * Math.PI) / 180)

/** Distance in meters between two [lat, lng] points. */
export function distanceMeters(a: LatLng, b: LatLng): number {
  const kx = mPerDegLng((a[0] + b[0]) / 2)
  return Math.hypot((a[0] - b[0]) * M_PER_DEG_LAT, (a[1] - b[1]) * kx)
}

function segmentMeters(p: LatLng, a: LatLng, b: LatLng): number {
  const kx = mPerDegLng(p[0])
  const ax = (a[1] - p[1]) * kx
  const ay = (a[0] - p[0]) * M_PER_DEG_LAT
  const dx = (b[1] - p[1]) * kx - ax
  const dy = (b[0] - p[0]) * M_PER_DEG_LAT - ay
  const len2 = dx * dx + dy * dy
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2))
  return Math.hypot(ax + t * dx, ay + t * dy)
}

/** Shortest distance in meters from a point to any segment of any polyline. */
export function distanceToLinesMeters(p: LatLng, lines: LatLng[][]): number {
  let best = Infinity
  for (const line of lines) {
    for (let i = 0; i < line.length - 1; i++) {
      best = Math.min(best, segmentMeters(p, line[i], line[i + 1]))
    }
  }
  return best
}

/** "350 m" / "1,2 km". */
export function formatDistance(m: number): string {
  if (m < 1000) return `${Math.round(m / 10) * 10} m`
  return `${(m / 1000).toLocaleString('es-AR', { maximumFractionDigits: 1 })} km`
}
