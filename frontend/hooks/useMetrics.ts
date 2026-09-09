'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const METRICS_URL = `${API}/api/v1/metrics`

/** Cómo se agrupa la serie temporal. `null` en el estado significa "que la
 * resuelva el backend según el largo del rango" — la respuesta devuelve cuál
 * eligió, y ESE es el botón que se marca activo. */
export type Granularity = 'dia' | 'semana' | 'mes'

export const GRANULARITIES: { key: Granularity; label: string }[] = [
  { key: 'dia', label: 'Día' },
  { key: 'semana', label: 'Semana' },
  { key: 'mes', label: 'Mes' },
]

/** El rango que scopea todo el dashboard. Fechas concretas y no un `days`: el
 * rango arbitrario es la primitiva, y los presets son solo atajos que la
 * calculan. Un solo modelo de datos abajo de los dos controles. */
export type MetricsRange = {
  desde: string // YYYY-MM-DD
  hasta: string
  granularidad: Granularity | null
  /** Qué preset está apretado, o null si el rango se escribió a mano. */
  preset: string | null
}

/** Fecha ISO en el huso LOCAL. `toISOString()` normaliza a UTC y le resta un día
 * a cualquiera al oeste de Greenwich después de las 21hs — el usuario elige en su
 * calendario, no en el de Londres. */
function isoDate(d: Date): string {
  const mes = String(d.getMonth() + 1).padStart(2, '0')
  const dia = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mes}-${dia}`
}

/** Ventana de n días terminando hoy, ambos extremos incluidos. */
function lastNDays(n: number): { desde: string; hasta: string } {
  const hasta = new Date()
  const desde = new Date()
  desde.setDate(desde.getDate() - (n - 1))
  return { desde: isoDate(desde), hasta: isoDate(hasta) }
}

function monthToDate(): { desde: string; hasta: string } {
  const hoy = new Date()
  return { desde: isoDate(new Date(hoy.getFullYear(), hoy.getMonth(), 1)), hasta: isoDate(hoy) }
}

function weekToDate(): { desde: string; hasta: string } {
  const hoy = new Date()
  const lunes = new Date(hoy)
  // getDay() cuenta desde domingo; la semana ISO arranca el lunes, igual que los
  // buckets del backend. Si acá cortáramos en domingo, "esta semana" y la barra
  // de la serie dirían cosas distintas.
  lunes.setDate(hoy.getDate() - ((hoy.getDay() + 6) % 7))
  return { desde: isoDate(lunes), hasta: isoDate(hoy) }
}

/** Presets del filtro. Filas, no calendario: nadie pelea con una grilla de fechas
 * para pedir "últimos 30 días". El rango a mano queda al lado, para el resto. */
export const RANGE_PRESETS: { key: string; label: string; resolve: () => { desde: string; hasta: string } }[] = [
  { key: 'semana', label: 'Esta semana', resolve: weekToDate },
  { key: 'mes', label: 'Este mes', resolve: monthToDate },
  { key: '30d', label: '30 días', resolve: () => lastNDays(30) },
  { key: '90d', label: '90 días', resolve: () => lastNDays(90) },
  { key: '12m', label: '12 meses', resolve: () => lastNDays(365) },
]

const DEFAULT_RANGE: MetricsRange = {
  ...lastNDays(30),
  granularidad: null,
  preset: '30d',
}

/** Toda razón del backend puede venir en null: significa "no hay denominador",
 * NO "medimos y salió cero". La UI tiene que distinguirlos o miente. */
type Ratio = number | null

export type ScopeSpend = {
  scope: string
  cost_usd: number
  llamadas: number
  input_tokens: number
  output_tokens: number
}

export type SourceSpend = {
  fuente: string
  cost_usd: number
  runs: number
  jobs: number
  costo_por_run: Ratio
}

/** Un bucket de la serie: día, semana o mes según `granularidad`. */
export type SpendBucket = {
  /** Comienzo REAL del período, aunque la ventana lo corte: así la etiqueta dice
   * "enero" y no "20 de enero". */
  periodo: string
  /** Extremos efectivamente cubiertos por la ventana. */
  desde: string
  fin: string
  /** El período está cortado por la ventana (típicamente el mes en curso).
   * Compararlo contra uno completo es la mentira clásica de estos paneles. */
  parcial: boolean
  llm_usd: number
  apify_usd: number
  total_usd: number
  llm_llamadas: number
  llm_input_tokens: number
  llm_output_tokens: number
}

export type CostMetrics = {
  dias: number
  desde: string
  hasta: string
  /** La que el backend RESOLVIÓ, que no siempre es la que se pidió. */
  granularidad: Granularity
  total_usd: number
  proyeccion_mensual_usd: Ratio
  llm: {
    cost_usd: number
    cost_usd_busquedas: number
    cost_usd_fichas: number
    llamadas: number
    input_tokens: number
    output_tokens: number
    cache_read_tokens: number
    cache_creation_tokens: number
    cache_hit_ratio: Ratio
    costo_por_llamada: Ratio
    por_scope: ScopeSpend[]
    por_model: { model: string; cost_usd: number; llamadas: number }[]
  }
  apify: {
    cost_usd: number
    cost_usd_desperdiciado: number
    desperdicio_ratio: Ratio
    jobs: number
    jobs_ok: number
    jobs_error: number
    /** Búsquedas cuyo costo nunca se registró (anteriores a la columna). Mientras
     * sea > 0, `cost_usd` es un PISO y no el total. */
    jobs_costo_desconocido: number
    costo_incompleto: boolean
    props: number
    costo_por_prop: Ratio
    por_fuente: SourceSpend[]
  }
  serie: SpendBucket[]
  error?: string
}

export type ExpensiveSearch = {
  job_id: string
  query_raw: string | null
  zona: string | null
  estado: string
  creado_at: string
  fuentes: string[]
  total_cost_usd: number
  /** null = nunca se registró; 0 = se registró como gratis (servido del cache de
   * inmobiliarias, o solo fuentes que no pasan por Apify). Son hallazgos opuestos. */
  apify_cost_usd: number | null
  llm_cost_usd: number
  props_total: number
  props_match: number
  costo_por_prop_util: Ratio
}

export type SearchMetrics = {
  dias: number
  desde: string
  hasta: string
  jobs: number
  por_estado: Record<string, number>
  error_ratio: Ratio
  cost_usd: number
  apify_cost_usd: number
  llm_cost_usd: number
  llm_llamadas: number
  costo_por_busqueda: Ratio
  props_total: number
  props_match: number
  precision_ratio: Ratio
  costo_por_prop_util: Ratio
  duracion_p50_seg: Ratio
  duracion_p95_seg: Ratio
  mas_caras: ExpensiveSearch[]
  error?: string
}

export type PropertyMetrics = {
  dias: number
  desde: string
  hasta: string
  total: number
  enviadas: number
  enviadas_ratio: Ratio
  fichas_propias: number
  confianza_promedio: Ratio
  completitud: Record<string, Ratio>
  frescura: {
    nunca_verificadas: number
    verificacion_vencida: number
    nunca_verificadas_ratio: Ratio
    primera_alta: string | null
    ultima_alta: string | null
  }
  altas_en_ventana: number
  por_fuente: { fuente: string; props: number }[]
  por_operacion: { tipo_operacion: string; props: number }[]
  serie_altas: { dia: string; props: number }[]
  error?: string
}

export type ZoneRow = {
  zona: string
  busquedas: number
  busquedas_error: number
  ultima_busqueda: string | null
  props: number
  props_match: number
  props_enviadas: number
  props_geocodificadas: number
  cobertura_geo_ratio: Ratio
  precision_ratio: Ratio
  precio_mediano_usd: Ratio
  precio_m2_mediano_usd: Ratio
  apify_cost_usd: number
  llm_cost_usd: number
  total_cost_usd: number
  costo_por_prop_util: Ratio
}

export type ZoneMetrics = {
  total_zonas: number
  zonas: ZoneRow[]
  moneda_medianas: string
  error?: string
}

export type MetricsBundle = {
  costs: CostMetrics | null
  searches: SearchMetrics | null
  properties: PropertyMetrics | null
  zones: ZoneMetrics | null
}

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url, { cache: 'no-store' })
    if (!res.ok) return null
    return (await res.json()) as T
  } catch {
    return null
  }
}

/** Trae los cuatro paneles. Pura: no toca estado de React, así el efecto puede
 * esperarla y recién después setear — sin setState sincrónico en el cuerpo del
 * efecto, y sin escribir estado sobre un componente ya desmontado. */
async function fetchBundle(range: MetricsRange): Promise<MetricsBundle> {
  const ventana = `desde=${range.desde}&hasta=${range.hasta}`
  // La granularidad solo la entiende /costs: es cómo se dibuja la serie, no qué
  // filas entran. Los otros paneles comparten la ventana y nada más.
  const conGran = range.granularidad ? `${ventana}&granularidad=${range.granularidad}` : ventana

  const [costs, searches, properties, zones] = await Promise.all([
    getJson<CostMetrics>(`${METRICS_URL}/costs?${conGran}`),
    getJson<SearchMetrics>(`${METRICS_URL}/searches?${ventana}`),
    getJson<PropertyMetrics>(`${METRICS_URL}/properties?${ventana}`),
    getJson<ZoneMetrics>(`${METRICS_URL}/zones`),
  ])
  return { costs, searches, properties, zones }
}

/**
 * Carga los cuatro paneles del dashboard contra la misma ventana temporal.
 *
 * El rango (`desde`/`hasta`) scopea todo lo que está debajo del filtro, así los
 * números siempre concuerdan entre paneles. Zonas es la excepción deliberada: no
 * se filtra por fecha, porque "qué sabemos de esta zona" no es una pregunta de
 * los últimos 30 días — recortarla dejaría sin medianas justo a las zonas que no
 * se buscaron este mes.
 *
 * La granularidad es independiente del rango: se puede pedir un año agrupado por
 * mes o dos semanas agrupadas por día. Cuando no se elige, la resuelve el backend
 * y la devuelve en la respuesta.
 *
 * `loading` arranca en true y solo la PRIMERA carga muestra esqueleto. Los
 * refetch mantienen el render anterior (ver `stale`), sin salto de layout.
 */
export function useMetrics() {
  const [range, setRangeState] = useState<MetricsRange>(DEFAULT_RANGE)
  const [data, setData] = useState<MetricsBundle>({
    costs: null, searches: null, properties: null, zones: null,
  })
  const [loading, setLoading] = useState(true)
  const [stale, setStale] = useState(false)
  const [unreachable, setUnreachable] = useState(false)

  const apply = useCallback((bundle: MetricsBundle) => {
    const { costs, searches, properties, zones } = bundle
    setData(bundle)
    // Los cuatro en null significa que no hubo respuesta de nadie: es un backend
    // caído, no cuatro paneles vacíos.
    setUnreachable(!costs && !searches && !properties && !zones)
    setLoading(false)
    setStale(false)
  }, [])

  // Carga inicial. `loading` ya arranca en true, así que el efecto no setea nada
  // sincrónicamente: espera y recién entonces escribe. `cancelled` evita escribir
  // sobre un componente desmontado si la respuesta llega tarde.
  // Cada recarga se numera y solo la ÚLTIMA puede escribir. Con dos inputs de
  // fecha, cambiar `desde` y enseguida `hasta` dispara dos consultas: sin esto, si
  // la primera (rango más ancho, más lento) vuelve después, pinta el dashboard con
  // un rango que el filtro ya no muestra.
  const pedido = useRef(0)

  useEffect(() => {
    let cancelled = false
    // Entra en la misma numeración que los refetch: si el usuario cambia el rango
    // antes de que la carga inicial llegue, la inicial ya no puede pisarlo.
    const mio = (pedido.current += 1)
    void (async () => {
      const bundle = await fetchBundle(DEFAULT_RANGE)
      if (!cancelled && pedido.current === mio) apply(bundle)
    })()
    return () => {
      cancelled = true
    }
  }, [apply])

  // Recargas disparadas por el usuario: mantienen el render anterior atenuado.
  const reload = useCallback(
    async (next: MetricsRange) => {
      const mio = (pedido.current += 1)
      setStale(true)
      const bundle = await fetchBundle(next)
      if (pedido.current === mio) apply(bundle)
    },
    [apply],
  )

  // Un solo camino de escritura: cualquier control arma el rango completo y lo
  // manda. Sin esto habría tres setters seteando pedazos y disparando fetches
  // sobre un estado a medio actualizar.
  const applyRange = useCallback(
    (next: MetricsRange) => {
      setRangeState(next)
      void reload(next)
    },
    [reload],
  )

  const setPreset = useCallback(
    (key: string) => {
      const preset = RANGE_PRESETS.find((p) => p.key === key)
      if (!preset) return
      applyRange({ ...range, ...preset.resolve(), preset: key })
    },
    [applyRange, range],
  )

  /** Rango escrito a mano. Ignora los estados intermedios del input nativo (una
   * fecha vacía o a medio tipear) en vez de disparar una consulta por tecla. */
  const setDates = useCallback(
    (desde: string, hasta: string) => {
      if (!desde || !hasta) return
      applyRange({ ...range, desde, hasta, preset: null })
    },
    [applyRange, range],
  )

  /** Volver a apretar la granularidad activa la suelta: se vuelve a "que decida
   * el backend según el rango", que es lo correcto al saltar de un mes a un año. */
  const setGranularidad = useCallback(
    (gran: Granularity) => {
      applyRange({ ...range, granularidad: range.granularidad === gran ? null : gran })
    },
    [applyRange, range],
  )

  const refresh = useCallback(() => reload(range), [range, reload])

  // Los errores por panel del backend son informativos, no fatales: cada panel
  // renderiza en cero y explica por qué (típicamente, migración sin aplicar).
  const panelErrors = [
    data.costs?.error, data.searches?.error, data.properties?.error, data.zones?.error,
  ].filter((e): e is string => Boolean(e))

  return {
    range, setPreset, setDates, setGranularidad,
    data, loading, stale, unreachable, panelErrors, refresh,
  }
}
