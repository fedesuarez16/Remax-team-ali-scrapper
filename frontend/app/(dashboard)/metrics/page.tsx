'use client'

import { BarChart3, RefreshCw } from 'lucide-react'
import { RANGE_PRESETS, useMetrics, type ExpensiveSearch, type ZoneRow } from '@/hooks/useMetrics'
import SpendChart from '@/components/metrics/SpendChart'
import {
  BarList, Column, DataTable, HeroFigure, Legend, Meter, Panel, PanelNote, StatTile,
} from '@/components/metrics/Primitives'
import {
  compact, count, dateTime, duration, pct, scopeLabel, shortDate, usd,
} from '@/components/metrics/format'

const RANGE_LABELS: Record<number, string> = {
  7: '7 días', 30: '30 días', 90: '90 días', 365: '1 año',
}

export default function MetricsPage() {
  const { days, setRange, data, loading, stale, unreachable, panelErrors, refresh } = useMetrics()
  const { costs, searches, properties, zones } = data

  const searchColumns: Column<ExpensiveSearch>[] = [
    {
      key: 'query',
      header: 'Búsqueda',
      render: (r) => (
        <div className="max-w-[260px]">
          <p className="truncate text-foreground">{r.query_raw ?? '(sin query)'}</p>
          <p className="text-[11px] text-muted-foreground">
            {shortDate(r.creado_at)} · {r.estado}
          </p>
        </div>
      ),
    },
    { key: 'props', header: 'Útiles / total', align: 'right', render: (r) => `${count(r.props_match)} / ${count(r.props_total)}` },
    { key: 'apify', header: 'Apify', align: 'right', render: (r) => usd(r.apify_cost_usd) },
    { key: 'llm', header: 'LLM', align: 'right', render: (r) => usd(r.llm_cost_usd) },
    { key: 'total', header: 'Total', align: 'right', render: (r) => usd(r.total_cost_usd) },
    {
      key: 'unit',
      header: 'Por útil',
      align: 'right',
      render: (r) => (
        <span className={r.costo_por_prop_util === null ? 'text-destructive' : undefined}>
          {r.costo_por_prop_util === null ? 'nada útil' : usd(r.costo_por_prop_util)}
        </span>
      ),
    },
  ]

  const zoneColumns: Column<ZoneRow>[] = [
    { key: 'zona', header: 'Zona', render: (r) => <span className="text-foreground">{r.zona}</span> },
    { key: 'busquedas', header: 'Búsq.', align: 'right', render: (r) => count(r.busquedas) },
    { key: 'props', header: 'Útiles / total', align: 'right', render: (r) => `${count(r.props_match)} / ${count(r.props)}` },
    { key: 'mediana', header: 'Mediana USD', align: 'right', render: (r) => usd(r.precio_mediano_usd) },
    { key: 'm2', header: 'USD/m²', align: 'right', render: (r) => usd(r.precio_m2_mediano_usd) },
    { key: 'geo', header: 'Geo', align: 'right', render: (r) => pct(r.cobertura_geo_ratio, 0) },
    { key: 'costo', header: 'Gasto', align: 'right', render: (r) => usd(r.total_cost_usd) },
    { key: 'unit', header: 'Por útil', align: 'right', render: (r) => usd(r.costo_por_prop_util) },
  ]

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-background text-foreground">
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto w-full max-w-6xl">
          <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            <BarChart3 className="size-5" />
            Métricas
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Búsquedas, inventario, zonas y lo que cuesta operarlo.
          </p>

          {/* Una sola fila de filtros, arriba de todo lo que scopea: cada panel
              se recalcula contra la misma ventana, así los números concuerdan. */}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {RANGE_PRESETS.map((preset) => (
              <button
                key={preset}
                type="button"
                onClick={() => setRange(preset)}
                aria-pressed={days === preset}
                className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition ${
                  days === preset
                    ? 'border-foreground bg-foreground text-background'
                    : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground'
                }`}
              >
                {RANGE_LABELS[preset] ?? `${preset} días`}
              </button>
            ))}
            <button
              type="button"
              onClick={() => void refresh()}
              className="ml-1 flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs text-muted-foreground transition hover:bg-muted hover:text-foreground"
            >
              <RefreshCw className={`size-3 ${stale ? 'animate-spin' : ''}`} />
              Actualizar
            </button>
          </div>
        </div>
      </header>

      {/* En refetch el render anterior se mantiene atenuado: sin esqueleto, sin
          salto de layout. El esqueleto solo aparece en la primera carga. */}
      <div
        className={`mx-auto w-full max-w-6xl px-6 py-6 transition-opacity ${
          stale ? 'opacity-60' : 'opacity-100'
        }`}
      >
        {loading ? (
          <p className="py-16 text-center text-sm text-muted-foreground">Cargando métricas…</p>
        ) : unreachable ? (
          <div className="rounded-xl border border-border bg-card p-6 text-center">
            <p className="text-sm font-medium">No se pudo contactar al backend</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Verificá que la API esté corriendo en {process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'}.
            </p>
          </div>
        ) : (
          <div className="space-y-6">
            {/* ── Costos ───────────────────────────────────────────────── */}
            <Panel
              title="Gasto del sistema"
              hint="Apify y Anthropic son las dos únicas fuentes de gasto: el geocoding corre sobre Nominatim, que es gratis."
            >
              <div className="grid gap-6 lg:grid-cols-[minmax(0,240px)_minmax(0,1fr)]">
                <div className="space-y-5">
                  <HeroFigure
                    // La ventana sale de la RESPUESTA, no del estado local: durante
                    // un refetch el botón ya está apretado pero los datos abajo
                    // siguen siendo los anteriores, y un label que se adelanta
                    // atribuiría el total viejo al rango nuevo.
                    label={`Total · ${
                      costs ? RANGE_LABELS[costs.dias] ?? `${costs.dias} días` : '—'
                    }`}
                    value={
                      // El prefijo "≥" no es adorno: mientras haya búsquedas con
                      // costo sin registrar, este número es un piso.
                      costs?.apify.costo_incompleto
                        ? `≥ ${usd(costs.total_usd)}`
                        : usd(costs?.total_usd ?? null)
                    }
                    sub={
                      costs?.proyeccion_mensual_usd != null
                        ? `Proyección mensual al ritmo actual: ${usd(costs.proyeccion_mensual_usd)}`
                        : undefined
                    }
                  />
                  {costs?.apify.costo_incompleto ? (
                    <PanelNote>
                      Es un <strong>piso</strong>:{' '}
                      {count(costs.apify.jobs_costo_desconocido)} de{' '}
                      {count(costs.apify.jobs)} búsquedas del rango son anteriores a la
                      columna <code>apify_cost_usd</code> y nunca registraron su costo.
                      Cuentan como $0 en el total.
                    </PanelNote>
                  ) : null}
                  <div className="space-y-3 border-t border-border pt-4">
                    <Legend
                      items={[
                        { label: `Apify · ${usd(costs?.apify.cost_usd ?? null)}`, color: 'var(--serie-apify)' },
                        { label: `LLM · ${usd(costs?.llm.cost_usd ?? null)}`, color: 'var(--serie-llm)' },
                      ]}
                    />
                  </div>
                </div>
                <SpendChart
                  days={costs?.serie_diaria ?? []}
                  from={costs?.desde}
                  windowDays={costs?.dias}
                />
              </div>

              <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile
                  label="Gasto desperdiciado"
                  value={usd(costs?.apify.cost_usd_desperdiciado ?? null)}
                  hint="Búsquedas que fallaron o no devolvieron ninguna propiedad, pero facturaron actor time."
                  emphasis={(costs?.apify.cost_usd_desperdiciado ?? 0) > 0 ? 'warn' : 'normal'}
                />
                <StatTile
                  label="Costo por propiedad útil"
                  value={usd(searches?.costo_por_prop_util ?? null)}
                  hint="Gasto total sobre propiedades que matchearon los criterios. Las búsquedas fallidas suman al costo y no al denominador."
                />
                <StatTile
                  label="Cache hit del LLM"
                  value={pct(costs?.llm.cache_hit_ratio ?? null, 0)}
                  hint={
                    costs?.llm.cache_hit_ratio === 0
                      ? 'Cero: ninguna llamada usa cache_control. Los prompts de extracción son largos e idénticos entre corridas.'
                      : 'Los cache reads se facturan a un décimo de la tarifa de input.'
                  }
                  emphasis={costs?.llm.cache_hit_ratio === 0 ? 'warn' : 'normal'}
                />
                <StatTile
                  label="Llamadas al LLM"
                  value={compact(costs?.llm.llamadas ?? null)}
                  hint={
                    costs?.llm.costo_por_llamada != null
                      ? `${usd(costs.llm.costo_por_llamada)} promedio por llamada.`
                      : 'Todavía sin llamadas registradas en el rango.'
                  }
                />
              </div>

              <div className="mt-6 grid gap-6 md:grid-cols-2">
                <div>
                  <h3 className="mb-3 text-xs font-semibold text-foreground">Apify por fuente</h3>
                  <BarList
                    rows={(costs?.apify.por_fuente ?? []).map((s) => ({
                      key: s.fuente,
                      label: s.fuente,
                      value: s.cost_usd,
                      display: usd(s.cost_usd),
                      detail: `${count(s.runs)} runs · ${count(s.jobs)} búsquedas · ${usd(s.costo_por_run)} por run`,
                    }))}
                    emptyLabel="Sin desglose por fuente todavía"
                  />
                </div>
                <div>
                  <h3 className="mb-3 text-xs font-semibold text-foreground">LLM por tarea</h3>
                  <BarList
                    rows={(costs?.llm.por_scope ?? []).map((s) => ({
                      key: s.scope,
                      label: scopeLabel(s.scope),
                      value: s.cost_usd,
                      display: usd(s.cost_usd),
                      detail: `${count(s.llamadas)} llamadas · ${compact(s.input_tokens)} tokens de input`,
                    }))}
                    emptyLabel="Sin gasto de tokens en el rango"
                  />
                </div>
              </div>
            </Panel>

            {/* ── Búsquedas ────────────────────────────────────────────── */}
            <Panel
              title="Búsquedas"
              hint="Precisión y duración salen del conjunto completo (suma sobre suma), no del promedio de las razones por búsqueda: promediarlas dejaría que una búsqueda de 2 propiedades pese igual que una de 200."
            >
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile
                  label="Búsquedas"
                  value={count(searches?.jobs ?? null)}
                  hint={Object.entries(searches?.por_estado ?? {})
                    .map(([estado, n]) => `${n} ${estado}`)
                    .join(' · ') || undefined}
                />
                <StatTile
                  label="Precisión"
                  value={pct(searches?.precision_ratio ?? null)}
                  hint={`${count(searches?.props_match ?? null)} de ${count(searches?.props_total ?? null)} propiedades matchearon los criterios.`}
                />
                <StatTile
                  label="Duración mediana"
                  value={duration(searches?.duracion_p50_seg ?? null)}
                  hint={`p95: ${duration(searches?.duracion_p95_seg ?? null)}. Las búsquedas sin terminar no cuentan.`}
                />
                <StatTile
                  label="Costo por búsqueda"
                  value={usd(searches?.costo_por_busqueda ?? null)}
                  hint={`Tasa de error: ${pct(searches?.error_ratio ?? null, 1)}`}
                  emphasis={(searches?.error_ratio ?? 0) > 0.1 ? 'warn' : 'normal'}
                />
              </div>

              <h3 className="mb-3 mt-6 text-xs font-semibold text-foreground">Búsquedas más caras</h3>
              <DataTable
                columns={searchColumns}
                rows={searches?.mas_caras ?? []}
                rowKey={(r) => r.job_id}
                emptyLabel="Sin búsquedas en el rango"
              />
            </Panel>

            {/* ── Propiedades ──────────────────────────────────────────── */}
            <Panel
              title="Inventario"
              hint="La completitud no es vanidad: la generación de fichas se degrada campo por campo, y una propiedad sin m² no entra en ninguna mediana de precio por metro."
            >
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile
                  label="Propiedades"
                  value={count(properties?.total ?? null)}
                  hint={`${count(properties?.altas_en_ventana ?? null)} altas en el rango.`}
                />
                <StatTile
                  label="Enviadas a clientes"
                  value={count(properties?.enviadas ?? null)}
                  hint={`${pct(properties?.enviadas_ratio ?? null, 2)} del inventario. Es el único número que conecta el sistema con el negocio.`}
                />
                <StatTile
                  label="Confianza de extracción"
                  value={pct(properties?.confianza_promedio ?? null, 0)}
                  hint="Promedio de `confianza_extraccion` sobre todo el inventario."
                />
                <StatTile
                  label="Sin verificar nunca"
                  value={pct(properties?.frescura.nunca_verificadas_ratio ?? null, 0)}
                  hint={`${count(properties?.frescura.nunca_verificadas ?? null)} propiedades que el bot de limpieza nunca revisó.`}
                  emphasis={
                    (properties?.frescura.nunca_verificadas_ratio ?? 0) > 0.5 ? 'warn' : 'normal'
                  }
                />
              </div>

              <div className="mt-6 grid gap-6 md:grid-cols-2">
                <div>
                  <h3 className="mb-3 text-xs font-semibold text-foreground">Completitud de datos</h3>
                  <div className="space-y-3">
                    <Meter label="Con precio" ratio={properties?.completitud.precio ?? null} />
                    <Meter label="Con m² total" ratio={properties?.completitud.m2 ?? null} />
                    <Meter label="Geocodificadas" ratio={properties?.completitud.geocodificadas ?? null} />
                    <Meter label="Con imágenes" ratio={properties?.completitud.imagenes ?? null} />
                    <Meter label="Con descripción" ratio={properties?.completitud.descripcion ?? null} />
                    <Meter
                      label="Dirección normalizada"
                      ratio={properties?.completitud.direccion_norm ?? null}
                    />
                  </div>
                </div>
                <div>
                  <h3 className="mb-3 text-xs font-semibold text-foreground">Altas por fuente</h3>
                  <BarList
                    rows={(properties?.por_fuente ?? []).map((f) => ({
                      key: f.fuente,
                      label: f.fuente,
                      value: f.props,
                      display: count(f.props),
                    }))}
                    emptyLabel="Sin altas en el rango"
                  />
                  {properties?.frescura.ultima_alta ? (
                    <PanelNote>
                      Última alta: {dateTime(properties.frescura.ultima_alta)}
                    </PanelNote>
                  ) : null}
                </div>
              </div>
            </Panel>

            {/* ── Zonas ────────────────────────────────────────────────── */}
            <Panel
              title="Zonas"
              hint="Tabla y no gráfico: son muchas clases con significado propio. Las medianas son solo en USD — mezclar monedas en una mediana no da un precio, da ruido. No se filtra por fecha a propósito: recortar a 30 días dejaría sin medianas justo a las zonas que no se buscaron este mes."
            >
              <DataTable
                columns={zoneColumns}
                rows={zones?.zonas ?? []}
                rowKey={(r) => r.zona}
                emptyLabel="Sin zonas registradas"
              />
              {zones && zones.zonas.length > 0 && zones.zonas.every((z) => z.zona === '(sin zona)') ? (
                <PanelNote>
                  Todo el inventario cae en <strong>(sin zona)</strong>. La atribución por zona
                  sale de <code>scraping_jobs.zona</code>, y el pipeline nunca persiste esa
                  columna: el insert del job solo escribe <code>id</code>, <code>query_raw</code>{' '}
                  y <code>estado</code>. Hasta que se resuelva, este panel no puede desagregar.
                </PanelNote>
              ) : null}
            </Panel>

            {panelErrors.length > 0 ? (
              <PanelNote>
                Algún panel se degradó: {panelErrors.join(' · ')}. Suele significar que falta
                correr una migración.
              </PanelNote>
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}
