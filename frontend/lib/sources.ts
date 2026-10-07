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

export type InmobiliariaId =
  | 'mauroperri'
  | 'urquiza'
  | 'kwsuma'
  | 'dacalbr'
  | 'keymex'
  | 'albertodacal'
  | 'remaxroble'
  | 'axion'
  | 'sabella'
  | 'yacoub'
  | 'piazza'
  | 'feysulaj'
  | 'arraras'
  | 'prado'
  | 'reyesaversa'
  | 'doorotero'
  | 'oterorossilp'
  | 'peterspuhl'
  | 'pabloamado'
  | 'jeronimoponce'
  | 'manuelponce'

/** Inmobiliarias con integración revisada y filtro por ubicación.
 * Vacío en SourceSelection significa todas las configuradas, igual que en portales. */
export const INMOBILIARIAS: { id: InmobiliariaId; label: string }[] = [
  { id: 'mauroperri', label: 'Mauro Perri' },
  { id: 'urquiza', label: 'Urquiza Propiedades' },
  { id: 'kwsuma', label: 'KW Suma' },
  { id: 'dacalbr', label: 'Dacal Bienes Raíces' },
  { id: 'keymex', label: 'Keymex La Plata' },
  { id: 'albertodacal', label: 'Alberto Dacal' },
  { id: 'remaxroble', label: 'RE/MAX Roble' },
  { id: 'axion', label: 'Axion Group' },
  { id: 'sabella', label: 'Sabella Propiedades' },
  { id: 'yacoub', label: 'Yacoub' },
  { id: 'piazza', label: 'Piazza Propiedades' },
  { id: 'feysulaj', label: 'Feysulaj Propiedades' },
  { id: 'arraras', label: 'Arrarás Propiedades' },
  { id: 'prado', label: 'Prado Propiedades' },
  { id: 'reyesaversa', label: 'Reyes Aversa Propiedades' },
  { id: 'doorotero', label: 'Door Otero Rossi CB' },
  { id: 'oterorossilp', label: 'Otero Rossi LP' },
  { id: 'peterspuhl', label: 'Peter Puhl Company' },
  { id: 'pabloamado', label: 'Pablo Amado Propiedades' },
  { id: 'jeronimoponce', label: 'Jerónimo Ponce Propiedades' },
  { id: 'manuelponce', label: 'Manuel Ponce Propiedades' },
]

export type SourceSelection = {
  buscar_portales: boolean
  /** Empty = todos los portales. A subset restricts the fan-out. */
  portales: PortalId[]
  buscar_inmobiliarias: boolean
  /** Empty = todas las inmobiliarias configuradas. */
  inmobiliarias: InmobiliariaId[]
  /** Campos legados conservados para poder abrir búsquedas anteriores. */
  zona_inmobiliarias: string | null
  solo_fuentes_cargadas: boolean
}

/** Search everything — what every caller did before the selector existed. */
export const DEFAULT_SELECTION: SourceSelection = {
  buscar_portales: true,
  portales: [],
  buscar_inmobiliarias: true,
  inmobiliarias: [],
  zona_inmobiliarias: null,
  solo_fuentes_cargadas: true,
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
      s.inmobiliarias.length === 0
        ? `las ${INMOBILIARIAS.length} inmobiliarias configuradas`
        : s.inmobiliarias
            .map((id) => INMOBILIARIAS.find((source) => source.id === id)?.label ?? id)
            .join(', ')
    )
  }
  return parts.length > 0 ? parts.join(' + ') : 'ninguna fuente seleccionada'
}
