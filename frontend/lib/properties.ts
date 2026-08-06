const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

/**
 * Borra las propiedades elegidas con el seleccionador.
 *
 * A diferencia de `marcarEnviadas`, acá los errores SE PROPAGAN: un borrado que
 * falla en silencio deja al usuario creyendo que limpió su base cuando no lo
 * hizo. Devuelve los ids realmente eliminados.
 */
export async function borrarPropiedades(ids: string[]): Promise<string[]> {
  const clean = [...new Set(ids.filter(Boolean))]
  if (clean.length === 0) return []

  const res = await fetch(`${API}/api/v1/properties/bulk-delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids: clean }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json()
  if (data.error) throw new Error(data.error)
  return (data.ids ?? []) as string[]
}
