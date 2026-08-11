'use client'

import type { ReactNode } from 'react'
import { EM_DASH, pct } from '@/components/metrics/format'

/**
 * Piezas del dashboard. Las especificaciones fijas que se repiten acá:
 *
 * - Marcas finas: barras ≤24px, punta redondeada 4px y escuadra en la línea base.
 * - Grilla y ejes: hairline sólida, un paso off-surface, recesiva. Nunca punteada.
 * - Separación entre rellenos por HUECO de 2px del color de la superficie, nunca
 *   por un borde dibujado alrededor de la marca (eso agrega tinta que no es dato).
 * - El texto NUNCA lleva el color de la serie: los valores y etiquetas usan tokens
 *   de texto, y la identidad la aporta la marca de color al lado.
 * - Números grandes con figuras proporcionales; `tabular-nums` solo en columnas
 *   que tienen que alinearse verticalmente.
 */

// ── contenedores ─────────────────────────────────────────────────────────────

export function Panel({
  title, hint, children, actions,
}: {
  title: string
  hint?: string
  children: ReactNode
  actions?: ReactNode
}) {
  return (
    <section className="rounded-xl border border-border bg-card p-5">
      <header className="mb-4 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold tracking-tight text-foreground">{title}</h2>
          {hint ? <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{hint}</p> : null}
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </header>
      {children}
    </section>
  )
}

/** El número que encabeza la vista. Exactamente uno por pantalla, ≥48px, en la
 * misma sans que todo lo demás, con figuras proporcionales. */
export function HeroFigure({
  label, value, sub,
}: {
  label: string
  value: string
  sub?: string
}) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 text-5xl font-semibold leading-none tracking-tight text-foreground">{value}</p>
      {sub ? <p className="mt-2 text-xs text-muted-foreground">{sub}</p> : null}
    </div>
  )
}

/** Tile de estadística: etiqueta en sentence case, valor, y una pista opcional.
 * Sin sparkline acá — cuando hay serie temporal, va al gráfico, no repetida en
 * doce tiles. */
export function StatTile({
  label, value, hint, emphasis,
}: {
  label: string
  value: string
  hint?: string
  emphasis?: 'normal' | 'warn'
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p
        className={`mt-1.5 text-2xl font-semibold leading-none tracking-tight ${
          emphasis === 'warn' ? 'text-destructive' : 'text-foreground'
        }`}
      >
        {value}
      </p>
      {hint ? <p className="mt-1.5 text-xs leading-snug text-muted-foreground">{hint}</p> : null}
    </div>
  )
}

// ── leyenda ──────────────────────────────────────────────────────────────────

/** Presente siempre que haya 2+ series: la identidad nunca depende solo del
 * color. La marca refleja la forma que usa el gráfico (rect para áreas/barras). */
export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-4">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block size-2.5 rounded-sm"
            style={{ background: item.color }}
          />
          <span className="text-xs text-muted-foreground">{item.label}</span>
        </li>
      ))}
    </ul>
  )
}

// ── meter ────────────────────────────────────────────────────────────────────

/**
 * Una razón contra su límite. El track es un paso más claro de la MISMA rampa
 * que el relleno, así el estado se lee a lo largo de toda la barra.
 *
 * `invert` marca las razones donde más es PEOR (gasto desperdiciado, propiedades
 * sin verificar). Sin eso, un 100% de inventario sin verificar se vería como una
 * barra llena y saludable.
 */
export function Meter({
  label, ratio, detail, invert = false,
}: {
  label: string
  ratio: number | null
  detail?: string
  invert?: boolean
}) {
  const known = ratio !== null && !Number.isNaN(ratio)
  const clamped = known ? Math.max(0, Math.min(1, ratio)) : 0
  // Umbrales de severidad: un 50% de completitud de precio no es "medio bien",
  // es una ficha que no se puede publicar.
  const severity = !known ? 'unknown' : invert
    ? (clamped >= 0.5 ? 'bad' : clamped >= 0.2 ? 'warn' : 'ok')
    : (clamped >= 0.8 ? 'ok' : clamped >= 0.5 ? 'warn' : 'bad')

  const fill = severity === 'bad'
    ? 'var(--destructive)'
    : severity === 'warn'
      ? 'var(--serie-llm)'
      : 'var(--serie-apify)'

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="text-xs font-semibold tabular-nums text-foreground">
          {known ? pct(ratio, 0) : EM_DASH}
        </span>
      </div>
      <div
        className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full"
        style={{ background: 'var(--serie-track)' }}
        role="meter"
        aria-valuenow={known ? Math.round(clamped * 100) : undefined}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        {known ? (
          <div className="h-full rounded-full" style={{ width: `${clamped * 100}%`, background: fill }} />
        ) : null}
      </div>
      {detail ? <p className="mt-1 text-[11px] text-muted-foreground">{detail}</p> : null}
    </div>
  )
}

// ── lista de barras ──────────────────────────────────────────────────────────

/**
 * Barras horizontales para comparar magnitud entre categorías nominales
 * (fuentes, scopes). UNA sola serie, así que un solo color para todas las
 * barras: pintar cada una más oscura según su tamaño sería codificar el largo
 * dos veces y quemar el único canal libre en información que la barra ya muestra.
 *
 * Horizontal y no vertical porque los nombres de categoría son largos
 * ("Extracción Instagram") y en columnas habría que rotarlos.
 */
export function BarList({
  rows, emptyLabel = 'Sin datos en el rango',
}: {
  rows: { key: string; label: string; value: number; display: string; detail?: string }[]
  emptyLabel?: string
}) {
  if (rows.length === 0) {
    return <p className="py-6 text-center text-xs text-muted-foreground">{emptyLabel}</p>
  }
  const max = Math.max(...rows.map((r) => r.value), 0)

  return (
    <ul className="space-y-3">
      {rows.map((row) => {
        // Un valor real pero diminuto igual tiene que verse: piso de 2% para que
        // una fuente que gastó algo nunca se confunda con una que no gastó nada.
        const width = max > 0 && row.value > 0 ? Math.max(2, (row.value / max) * 100) : 0
        return (
          <li key={row.key}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="truncate text-xs text-foreground">{row.label}</span>
              <span className="shrink-0 text-xs font-semibold tabular-nums text-foreground">
                {row.display}
              </span>
            </div>
            <div className="mt-1.5 h-2">
              {width > 0 ? (
                <div
                  className="h-2 rounded-r-[4px]"
                  style={{ width: `${width}%`, background: 'var(--serie-apify)' }}
                />
              ) : (
                <div className="h-2 w-full" style={{ background: 'var(--serie-track)', height: 1 }} />
              )}
            </div>
            {row.detail ? (
              <p className="mt-1 text-[11px] text-muted-foreground">{row.detail}</p>
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}

// ── tabla ────────────────────────────────────────────────────────────────────

export type Column<T> = {
  key: string
  header: string
  align?: 'left' | 'right'
  render: (row: T) => ReactNode
}

/**
 * Tabla. Es la forma correcta cuando hay más de ~7 clases con significado, y
 * además es el gemelo accesible obligatorio de cada gráfico: ningún valor queda
 * accesible solo por hover.
 *
 * `tabular-nums` en las celdas numéricas — acá SÍ, porque son columnas que se
 * alinean verticalmente.
 */
export function DataTable<T>({
  columns, rows, rowKey, emptyLabel = 'Sin datos',
}: {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  emptyLabel?: string
}) {
  if (rows.length === 0) {
    return <p className="py-6 text-center text-xs text-muted-foreground">{emptyLabel}</p>
  }
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full min-w-[520px] border-collapse text-xs">
        <thead>
          <tr className="border-b border-border">
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                className={`px-2 pb-2 font-medium text-muted-foreground ${
                  col.align === 'right' ? 'text-right' : 'text-left'
                }`}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className="border-b border-border/60 last:border-0">
              {columns.map((col) => (
                <td
                  key={col.key}
                  className={`px-2 py-2 align-top ${
                    col.align === 'right'
                      ? 'text-right tabular-nums text-foreground'
                      : 'text-left text-foreground'
                  }`}
                >
                  {col.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Aviso de panel degradado. El backend reporta el motivo (típicamente una vista
 * que no existe porque falta correr la migración) y mostrarlo es más útil que
 * mostrar ceros sin explicación. */
export function PanelNote({ children }: { children: ReactNode }) {
  return (
    <p className="mt-3 rounded-lg border border-border bg-muted/40 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
      {children}
    </p>
  )
}
