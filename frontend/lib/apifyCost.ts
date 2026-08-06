/**
 * Apify spend, as the operator reads it.
 *
 * The backend books every actor run's `usageTotalUsd` into a per-search ledger
 * and reports it in two places: live on the SSE `done` event (map + /chat) and
 * persisted on the job row (historial). Same numbers, same wording — so the
 * formatting lives here instead of being re-invented at each call site.
 */

/** `{source: {usd, runs}}`. A source absent from the object never hit an actor. */
export type ApifyCostBreakdown = Record<string, { usd: number; runs: number }>

/** Four decimals: a single actor run often bills less than a cent. */
export const formatUsd = (n: number) => `US$ ${n.toFixed(4)}`

/** Hover detail: which portal burned the credits, and over how many actor runs. */
export const costBreakdownTitle = (breakdown: ApifyCostBreakdown | null | undefined) => {
  const rows = Object.entries(breakdown ?? {})
  if (rows.length === 0) {
    return 'Sin runs de Apify: servida desde caché o desde fuentes directas (MercadoLibre, RE/MAX)'
  }
  return rows
    .map(([source, { usd, runs }]) => `${source}: ${formatUsd(usd)} (${runs} run${runs === 1 ? '' : 's'})`)
    .join('\n')
}
