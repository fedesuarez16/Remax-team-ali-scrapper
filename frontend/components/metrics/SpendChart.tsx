'use client'

import { useCallback, useEffect, useLayoutEffect, useState } from 'react'
import type { SpendDay } from '@/hooks/useMetrics'
import { Column, DataTable, Legend } from '@/components/metrics/Primitives'
import { shortDate, usd } from '@/components/metrics/format'

/**
 * Gasto diario, Apify + LLM, en columnas apiladas.
 *
 * Apiladas y no dos líneas porque la pregunta es parte-respecto-del-total: cuánto
 * se gastó ese día y en qué proporción. Y explícitamente NO es un gráfico de dos
 * ejes: las dos series están en la misma unidad (USD), así que comparten una sola
 * escala. Dos escalas y inventaríamos una correlación que no está en los datos.
 *
 * El SVG se dibuja al ancho real medido del contenedor en vez de escalar por
 * viewBox: escalar deforma el texto de los ejes y provoca justo las colisiones de
 * etiquetas que hay que evitar.
 */

const SERIES = [
  { key: 'apify_usd' as const, label: 'Apify', color: 'var(--serie-apify)' },
  { key: 'llm_usd' as const, label: 'LLM (Anthropic)', color: 'var(--serie-llm)' },
]

const PLOT_H = 180 // alto de la ESCALA (no del contenedor)
const AXIS_BAND = 22 // banda reservada para las etiquetas del eje X
// Aire arriba de la escala: sin esto la etiqueta del tick más alto y la etiqueta
// directa del pico se recortan contra el borde del SVG.
const TOP_PAD = 12
const BASE = TOP_PAD + PLOT_H // coordenada Y de la línea base
const Y_AXIS_W = 52
const MAX_BAR_W = 24
const STACK_GAP = 2 // hueco de superficie entre segmentos apilados
const TICKS = 4

/** Redondea el techo del eje a 1/2/5 × 10ⁿ, así los ticks caen en números limpios. */
function niceMax(value: number): number {
  if (value <= 0) return 1
  const exp = Math.floor(Math.log10(value))
  const pow = 10 ** exp
  const frac = value / pow
  const step = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10
  return step * pow
}

/**
 * Ticks del eje con UNA sola precisión para todo el eje.
 *
 * El formateador general de USD elige decimales según la magnitud, que es lo
 * correcto para un monto suelto y lo incorrecto para un eje: da la escalera
 * "$1.00 / $0.7500 / $0.5000", donde cada tick se lee con distinta precisión.
 * Acá la precisión la fija el techo del eje y se aplica a todos por igual.
 */
function axisTick(value: number, max: number): string {
  const decimals = max >= 100 ? 0 : max >= 10 ? 1 : max >= 1 ? 2 : max >= 0.1 ? 3 : 4
  if (value === 0) return '$0'
  return `$${value.toFixed(decimals)}`
}

/**
 * Completa la ventana con los días que no tienen ninguna fila.
 *
 * Sin esto el eje X se posiciona por índice de fila y no por fecha: once días con
 * datos repartidos en treinta se dibujan equiespaciados y se leen como
 * consecutivos, comprimiendo semanas enteras sin avisar. Un eje temporal tiene
 * que ser proporcional al tiempo, así que los días sin gasto van explícitos en
 * cero — que además es el dato correcto: ese día no se gastó nada.
 */
function fillWindow(rows: SpendDay[], fromISO: string, windowDays: number): SpendDay[] {
  const byDay = new Map(rows.map((r) => [r.dia, r]))
  const start = new Date(`${fromISO}T00:00:00Z`)
  if (Number.isNaN(start.getTime()) || windowDays <= 0) return rows

  // Si llegaran filas fuera de la ventana (reloj corrido, datos viejos), se
  // respeta el rango pedido y no se inventa un eje más largo.
  const out: SpendDay[] = []
  for (let i = 0; i < windowDays; i += 1) {
    const day = new Date(start)
    day.setUTCDate(start.getUTCDate() + i)
    const iso = day.toISOString().slice(0, 10)
    out.push(byDay.get(iso) ?? { dia: iso, llm_usd: 0, apify_usd: 0, total_usd: 0 })
  }
  return out
}

/** Rect con las esquinas de ARRIBA redondeadas y la base en escuadra: la marca
 * crece desde una única línea base y solo la punta de dato se redondea. */
function topRoundedPath(x: number, y: number, w: number, h: number, r: number): string {
  if (h <= 0) return ''
  const radius = Math.min(r, w / 2, h)
  return [
    `M ${x} ${y + h}`,
    `L ${x} ${y + radius}`,
    `Q ${x} ${y} ${x + radius} ${y}`,
    `L ${x + w - radius} ${y}`,
    `Q ${x + w} ${y} ${x + w} ${y + radius}`,
    `L ${x + w} ${y + h}`,
    'Z',
  ].join(' ')
}

/**
 * Ancho medido del contenedor.
 *
 * El nodo se guarda en ESTADO y se expone un callback ref, no un `useRef`: el
 * contenedor del gráfico se desmonta al pasar a la vista de tabla y vuelve a
 * montarse al volver. Con un `useRef` y deps vacías, el efecto no se re-ejecuta
 * en ese remonte, el observer sigue atado al nodo viejo ya desprendido, y ese
 * nodo reporta ancho 0 — que quedaba guardado y dejaba el gráfico invisible al
 * regresar. Con el nodo en estado, el efecto se re-ejecuta en cada cambio de nodo.
 */
function useContainerWidth() {
  const [node, setNode] = useState<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    if (!node) return
    // Sin medición sincrónica: ResizeObserver ya emite el tamaño actual en cuanto
    // se hace `observe`, así que leer `clientWidth` acá sería medir dos veces — y
    // encima es un setState sincrónico en el cuerpo del efecto.
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        // Un 0 se ignora: lo emiten los nodos ocultos o en proceso de desmontarse,
        // y no es una medida del ancho real.
        if (entry.contentRect.width > 0) setWidth(entry.contentRect.width)
      }
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [node])

  return { ref: setNode, width }
}

export default function SpendChart({
  days: rows, from, windowDays,
}: {
  days: SpendDay[]
  from?: string
  windowDays?: number
}) {
  const { ref, width } = useContainerWidth()
  const [active, setActive] = useState<number | null>(null)
  const [showTable, setShowTable] = useState(false)

  // El eje se arma sobre la ventana completa; la tabla muestra lo mismo, así el
  // gráfico y su gemelo accesible nunca discrepan.
  const days = from && windowDays ? fillWindow(rows, from, windowDays) : rows

  const plotW = Math.max(0, width - Y_AXIS_W)
  const max = niceMax(Math.max(...days.map((d) => d.total_usd), 0))
  const band = days.length > 0 && plotW > 0 ? plotW / days.length : 0
  const barW = Math.min(MAX_BAR_W, band * 0.6)

  const yFor = useCallback((value: number) => BASE - (value / max) * PLOT_H, [max])

  const handleMove = useCallback(
    (event: React.PointerEvent<SVGRectElement>) => {
      if (band <= 0) return
      const box = event.currentTarget.getBoundingClientRect()
      const idx = Math.floor((event.clientX - box.left) / band)
      setActive(idx >= 0 && idx < days.length ? idx : null)
    },
    [band, days.length],
  )

  // Escape cierra el tooltip: el crosshair es estado y tiene que poder soltarse
  // sin mover el puntero.
  useEffect(() => {
    if (active === null) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setActive(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active])

  // El día más caro es la única etiqueta directa: un número sobre cada columna
  // es caos y no se lee.
  const peakIdx = days.reduce(
    (best, day, i) => (day.total_usd > (days[best]?.total_usd ?? -1) ? i : best),
    0,
  )

  const columns: Column<SpendDay>[] = [
    { key: 'dia', header: 'Día', render: (r) => shortDate(r.dia) },
    { key: 'apify', header: 'Apify', align: 'right', render: (r) => usd(r.apify_usd) },
    { key: 'llm', header: 'LLM', align: 'right', render: (r) => usd(r.llm_usd) },
    { key: 'total', header: 'Total', align: 'right', render: (r) => usd(r.total_usd) },
  ]

  const activeDay = active !== null ? days[active] : null

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <Legend items={SERIES.map((s) => ({ label: s.label, color: s.color }))} />
        <button
          type="button"
          onClick={() => setShowTable((v) => !v)}
          className="rounded-lg border border-border px-2.5 py-1 text-xs text-muted-foreground transition hover:bg-muted hover:text-foreground"
          aria-pressed={showTable}
        >
          {showTable ? 'Ver gráfico' : 'Ver tabla'}
        </button>
      </div>

      {showTable ? (
        <DataTable
          columns={columns}
          rows={days}
          rowKey={(r) => r.dia}
          emptyLabel="Sin gasto registrado en el rango"
        />
      ) : (
        <div ref={ref} className="relative w-full">
          {days.length === 0 ? (
            <p className="py-12 text-center text-xs text-muted-foreground">
              Sin gasto registrado en el rango
            </p>
          ) : (
            <>
              {/* Alto = plot + banda del eje, para que las etiquetas del eje X
                  entren en el contenedor y no aparezca un scroll anidado. */}
              <svg
                width={width || 1}
                height={BASE + AXIS_BAND}
                role="img"
                aria-label="Gasto diario en Apify y LLM"
              >
                {/* Grilla: hairline sólida, un paso off-surface, recesiva. */}
                {Array.from({ length: TICKS + 1 }, (_, i) => {
                  const value = (max / TICKS) * i
                  const y = yFor(value)
                  return (
                    <g key={i}>
                      <line
                        x1={Y_AXIS_W} y1={y} x2={width} y2={y}
                        stroke="var(--serie-grid)" strokeWidth={1} shapeRendering="crispEdges"
                      />
                      <text
                        x={Y_AXIS_W - 8} y={y + 3.5} textAnchor="end"
                        className="fill-muted-foreground text-[10px] tabular-nums"
                      >
                        {axisTick(value, max)}
                      </text>
                    </g>
                  )
                })}

                {days.map((day, i) => {
                  const cx = Y_AXIS_W + band * i + band / 2
                  const x = cx - barW / 2
                  const apifyH = (day.apify_usd / max) * PLOT_H
                  const llmH = (day.llm_usd / max) * PLOT_H
                  // El hueco solo existe si ambos segmentos están presentes.
                  const gap = apifyH > 0 && llmH > 0 ? STACK_GAP : 0
                  const apifyTop = BASE - apifyH
                  const llmTop = apifyTop - gap - llmH
                  const topIsLlm = llmH > 0

                  return (
                    <g key={day.dia}>
                      {/* Segmento inferior: escuadra si algo se apila encima. */}
                      {apifyH > 0 ? (
                        topIsLlm ? (
                          <rect
                            x={x} y={apifyTop} width={barW} height={apifyH}
                            fill="var(--serie-apify)"
                          />
                        ) : (
                          <path
                            d={topRoundedPath(x, apifyTop, barW, apifyH, 4)}
                            fill="var(--serie-apify)"
                          />
                        )
                      ) : null}
                      {llmH > 0 ? (
                        <path
                          d={topRoundedPath(x, Math.max(0, llmTop), barW, llmH, 4)}
                          fill="var(--serie-llm)"
                        />
                      ) : null}
                    </g>
                  )
                })}

                {/* Crosshair: el lector apunta a una fecha, nunca a una línea de 2px. */}
                {active !== null && days[active] ? (
                  <line
                    x1={Y_AXIS_W + band * active + band / 2}
                    y1={0}
                    x2={Y_AXIS_W + band * active + band / 2}
                    y2={BASE}
                    stroke="var(--serie-llm)"
                    strokeWidth={1}
                    shapeRendering="crispEdges"
                  />
                ) : null}

                {/* Etiqueta directa del pico, fuera de la columna. */}
                {days[peakIdx] && days[peakIdx].total_usd > 0 ? (
                  <text
                    x={Y_AXIS_W + band * peakIdx + band / 2}
                    y={Math.max(TOP_PAD - 3, yFor(days[peakIdx].total_usd) - 6)}
                    textAnchor="middle"
                    className="fill-foreground text-[10px] font-semibold"
                  >
                    {usd(days[peakIdx].total_usd)}
                  </text>
                ) : null}

                {/* Eje X: primero, último y el pico. Con pocos días, todos. */}
                {days.map((day, i) => {
                  const show = days.length <= 8 || i === 0 || i === days.length - 1 || i === peakIdx
                  if (!show) return null
                  const anchor = i === 0 ? 'start' : i === days.length - 1 ? 'end' : 'middle'
                  const cx = Y_AXIS_W + band * i + band / 2
                  return (
                    <text
                      key={day.dia}
                      x={anchor === 'start' ? Y_AXIS_W : anchor === 'end' ? width : cx}
                      y={BASE + 14}
                      textAnchor={anchor}
                      className="fill-muted-foreground text-[10px]"
                    >
                      {shortDate(day.dia)}
                    </text>
                  )
                })}

                {/* Capa de hit: el objetivo es la banda entera, no la columna
                    pintada, así el puntero solo tiene que estar CERCA. */}
                <rect
                  x={Y_AXIS_W} y={0} width={Math.max(0, width - Y_AXIS_W)} height={BASE}
                  fill="transparent"
                  onPointerMove={handleMove}
                  onPointerLeave={() => setActive(null)}
                />
              </svg>

              {activeDay ? (
                <div
                  role="status"
                  className="pointer-events-none absolute top-0 z-10 min-w-[150px] rounded-lg border border-border bg-card p-2.5 shadow-md"
                  style={{
                    left: Math.min(
                      Math.max(Y_AXIS_W, Y_AXIS_W + band * (active ?? 0) + band / 2 - 75),
                      Math.max(Y_AXIS_W, width - 160),
                    ),
                  }}
                >
                  <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">
                    {shortDate(activeDay.dia)}
                  </p>
                  {/* El valor manda y el nombre de la serie acompaña: acá el
                      lector ya tiene la serie y lo que quiere es el número. */}
                  {SERIES.map((s) => (
                    <div key={s.key} className="flex items-center gap-2">
                      <span
                        aria-hidden
                        className="inline-block h-0.5 w-3 rounded-full"
                        style={{ background: s.color }}
                      />
                      <span className="text-xs font-semibold tabular-nums text-foreground">
                        {usd(activeDay[s.key])}
                      </span>
                      <span className="text-[11px] text-muted-foreground">{s.label}</span>
                    </div>
                  ))}
                  <div className="mt-1.5 border-t border-border pt-1.5">
                    <span className="text-xs font-semibold tabular-nums text-foreground">
                      {usd(activeDay.total_usd)}
                    </span>
                    <span className="ml-1.5 text-[11px] text-muted-foreground">total</span>
                  </div>
                </div>
              ) : null}
            </>
          )}
        </div>
      )}
    </div>
  )
}
