export type ConOperacion = { tipo_operacion?: string | null }

const RANK: Record<string, number> = { venta: 0, alquiler: 1 }

export function operacionRank(p: ConOperacion): number {
  return RANK[p.tipo_operacion ?? ''] ?? 2
}

// Stable: venta first, then alquiler, then unknown — preserves incoming order
// within each group.
export function sortVentaFirst<T extends ConOperacion>(list: T[]): T[] {
  return [...list].sort((a, b) => operacionRank(a) - operacionRank(b))
}

export function operacionLabel(p: ConOperacion): string | null {
  if (p.tipo_operacion === 'venta') return 'Venta'
  if (p.tipo_operacion === 'alquiler') return 'Alquiler'
  return null
}
