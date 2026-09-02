/** Where a search is allowed to scrape, picked BEFORE it runs.
 *
 * Mirrors `SourceSelection` in backend/app/api/v1/scraping.py — the backend
 * validates every portal id against its own `PORTAL_SOURCES` tuple, so this
 * catalog must stay in sync with `backend/app/services/apify.py`.
 */

export type PortalId =
  | 'zonaprop'
  | 'argenprop'
  | 'mercadolibre'
  | 'remax'
  | 'inmobusqueda'
  | 'mudafy'
  | 'century21'

export const PORTALES: { id: PortalId; label: string }[] = [
  { id: 'zonaprop', label: 'Zonaprop' },
  { id: 'argenprop', label: 'Argenprop' },
  { id: 'mercadolibre', label: 'Mercado Libre' },
  { id: 'remax', label: 'RE/MAX' },
  { id: 'inmobusqueda', label: 'InmoBusqueda' },
  { id: 'mudafy', label: 'Mudafy' },
  { id: 'century21', label: 'CENTURY 21' },
]

export type SourceSelection = {
  buscar_portales: boolean
  /** Empty = todos los portales. A subset restricts the fan-out. */
  portales: PortalId[]
  buscar_inmobiliarias: boolean
  /** null = todas las zonas. Otherwise only that zona's curated inmobiliarias. */
  zona_inmobiliarias: string | null
  /** Buscar SÓLO en las inmobiliarias cargadas a mano en /sources, sin salir a
   * descubrir con Google Maps. El descubrimiento trae cientos que nadie
   * eligió; el registro curado lo cargó alguien que las conoce. */
  solo_fuentes_cargadas: boolean
}

/** Search everything — what every caller did before the selector existed. */
export const DEFAULT_SELECTION: SourceSelection = {
  buscar_portales: true,
  portales: [],
  buscar_inmobiliarias: true,
  zona_inmobiliarias: null,
  solo_fuentes_cargadas: false,
}

/** The backend rejects a selection with no track enabled (400), so the UI
 * blocks submit on the same condition instead of round-tripping an error. */
export function isSelectionEmpty(s: SourceSelection): boolean {
  return !s.buscar_portales && !s.buscar_inmobiliarias
}

export function describeSelection(s: SourceSelection): string {
  const parts: string[] = []
  if (s.buscar_portales) {
    parts.push(
      s.portales.length === 0
        ? 'todos los portales'
        : s.portales
            .map((id) => PORTALES.find((p) => p.id === id)?.label ?? id)
            .join(', ')
    )
  }
  if (s.buscar_inmobiliarias) {
    parts.push(
      s.zona_inmobiliarias
        ? `inmobiliarias de ${s.zona_inmobiliarias}`
        : 'todas las inmobiliarias'
    )
  }
  return parts.length > 0 ? parts.join(' + ') : 'ninguna fuente seleccionada'
}
