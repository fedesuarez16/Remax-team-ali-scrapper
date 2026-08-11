'use client'

import { useCallback, useEffect, useState } from 'react'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const METRICS_URL = `${API}/api/v1/metrics`

/** Presets del filtro de rango. Filas, no calendario: nadie pelea con una grilla
 * de fechas para pedir "últimos 30 días". */
export const RANGE_PRESETS = [7, 30, 90, 365] as const

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

export type SpendDay = {
  dia: string
  llm_usd: number
  apify_usd: number
  total_usd: number
}

export type CostMetrics = {
  dias: number
  desde: string
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
  serie_diaria: SpendDay[]
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
async function fetchBundle(window: number): Promise<MetricsBundle> {
  const [costs, searches, properties, zones] = await Promise.all([
    getJson<CostMetrics>(`${METRICS_URL}/costs?days=${window}`),
    getJson<SearchMetrics>(`${METRICS_URL}/searches?days=${window}`),
    getJson<PropertyMetrics>(`${METRICS_URL}/properties?days=${window}`),
    getJson<ZoneMetrics>(`${METRICS_URL}/zones`),
  ])
  return { costs, searches, properties, zones }
}

/**
 * Carga los cuatro paneles del dashboard contra la misma ventana temporal.
 *
 * `days` scopea todo lo que está debajo del filtro, así los números siempre
 * concuerdan entre paneles. Zonas es la excepción deliberada: no se filtra por
 * fecha, porque "qué sabemos de esta zona" no es una pregunta de los últimos 30
 * días — recortarla dejaría sin medianas justo a las zonas que no se buscaron
 * este mes.
 *
 * `loading` arranca en true y solo la PRIMERA carga muestra esqueleto. Los
 * refetch mantienen el render anterior (ver `stale`), sin salto de layout.
 */
export function useMetrics() {
  const [days, setDays] = useState<number>(30)
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
  useEffect(() => {
    let cancelled = false
    void (async () => {
      const bundle = await fetchBundle(30)
      if (!cancelled) apply(bundle)
    })()
    return () => {
      cancelled = true
    }
  }, [apply])

  // Recargas disparadas por el usuario: mantienen el render anterior atenuado.
  const reload = useCallback(
    async (window: number) => {
      setStale(true)
      apply(await fetchBundle(window))
    },
    [apply],
  )

  const setRange = useCallback(
    (next: number) => {
      setDays(next)
      void reload(next)
    },
    [reload],
  )

  const refresh = useCallback(() => reload(days), [days, reload])

  // Los errores por panel del backend son informativos, no fatales: cada panel
  // renderiza en cero y explica por qué (típicamente, migración sin aplicar).
  const panelErrors = [
    data.costs?.error, data.searches?.error, data.properties?.error, data.zones?.error,
  ].filter((e): e is string => Boolean(e))

  return { days, setRange, data, loading, stale, unreachable, panelErrors, refresh }
}
