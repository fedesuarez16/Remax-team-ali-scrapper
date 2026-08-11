/**
 * Formateo para el dashboard de métricas.
 *
 * La regla que atraviesa todo el archivo: **null no es cero**. El backend
 * devuelve null cuando una razón no tiene denominador (cero búsquedas, cero
 * propiedades útiles) y un número cuando midió de verdad. Un dashboard que
 * pinta ambos como "0%" convierte "todavía no hay datos" en "fracaso total",
 * que es una lectura opuesta. Por eso todo formateador de razón devuelve EM DASH
 * para null y nunca lo colapsa a 0.
 */

const EM_DASH = '—'

/** USD con la precisión que el monto merece. Los costos acá son fracciones de
 * centavo por llamada y dólares por búsqueda, así que una precisión fija miente
 * en un extremo o en el otro: $0.00 borra el gasto real de un extract, y
 * $1.878200 es ruido. */
export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EM_DASH
  const abs = Math.abs(value)
  if (abs === 0) return '$0'
  if (abs < 0.01) return `$${value.toFixed(5)}`
  if (abs < 1) return `$${value.toFixed(4)}`
  if (abs < 1000) return `$${value.toFixed(2)}`
  return `$${Math.round(value).toLocaleString('es-AR')}`
}

/** Enteros con separador de miles, o em dash. */
export function count(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EM_DASH
  return value.toLocaleString('es-AR')
}

/** Compacta para stat tiles: 1.284 / 12,9K / 2,5M. */
export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EM_DASH
  const abs = Math.abs(value)
  if (abs < 10_000) return value.toLocaleString('es-AR')
  if (abs < 1_000_000) return `${(value / 1000).toLocaleString('es-AR', { maximumFractionDigits: 1 })}K`
  return `${(value / 1_000_000).toLocaleString('es-AR', { maximumFractionDigits: 1 })}M`
}

/** Porcentaje. Un 0 real se muestra como "0%" — es un hallazgo, no una ausencia. */
export function pct(ratio: number | null | undefined, decimals = 1): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return EM_DASH
  return `${(ratio * 100).toFixed(decimals)}%`
}

/** Duración legible desde segundos. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return EM_DASH
  if (seconds < 60) return `${seconds.toFixed(0)}s`
  const mins = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  if (mins < 60) return rest === 0 ? `${mins}m` : `${mins}m ${rest}s`
  const hours = Math.floor(mins / 60)
  return `${hours}h ${mins % 60}m`
}

/** Fecha corta para ejes y tablas. */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return EM_DASH
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return EM_DASH
  return d.toLocaleDateString('es-AR', { day: '2-digit', month: 'short' })
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return EM_DASH
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return EM_DASH
  return d.toLocaleString('es-AR', { dateStyle: 'medium', timeStyle: 'short' })
}

/** Nombres legibles de los scopes del ledger de tokens. Se muestran tal cual si
 * aparece uno nuevo: un scope sin traducir es preferible a esconderlo. */
export const SCOPE_LABELS: Record<string, string> = {
  extract_website: 'Extracción web',
  extract_instagram: 'Extracción Instagram',
  search_parse: 'Parseo de búsqueda',
  match_parse: 'Parseo de criterios',
  ficha_propio: 'Ficha propia',
  ficha_enrich: 'Enriquecer ficha',
}

export function scopeLabel(scope: string): string {
  return SCOPE_LABELS[scope] ?? scope
}

export { EM_DASH }
