'use client'
import { useState, type ReactNode } from 'react'
import { Loader2, Trash2 } from 'lucide-react'
import { borrarPropiedades } from '@/lib/properties'

type Props = {
  /** Cuántas tarjetas hay marcadas (incluye las que no tienen id persistido). */
  count: number
  /** Ids reales de la selección — sólo estas se pueden borrar. */
  ids: string[]
  onClear: () => void
  /** Se llama con los ids efectivamente eliminados para que la página los saque de su lista. */
  onDeleted: (ids: string[]) => void
  /** Acciones extra a la derecha (por ejemplo "Preparar y enviar"). */
  children?: ReactNode
}

/**
 * Barra de acciones de la selección. Única fuente de verdad del borrado:
 * /properties y /search la comparten, así el flujo (confirmar → borrar →
 * reportar error) no se bifurca entre pantallas.
 */
export function SelectionBar({ count, ids, onClear, onDeleted, children }: Props) {
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const borrar = async () => {
    if (deleting || ids.length === 0) return
    setDeleting(true)
    setError(null)
    try {
      const removed = await borrarPropiedades(ids)
      onDeleted(removed)
      setConfirming(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error desconocido')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border bg-card px-6 py-3">
      <div className="flex flex-col">
        <span className="text-sm text-foreground">
          {count} {count === 1 ? 'propiedad seleccionada' : 'propiedades seleccionadas'}
        </span>
        {error && <span className="text-xs text-destructive">No se pudo eliminar: {error}</span>}
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={() => {
            setConfirming(false)
            setError(null)
            onClear()
          }}
          className="rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-muted"
        >
          Limpiar
        </button>

        {confirming ? (
          <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-2 py-1">
            <span className="text-xs text-foreground">
              ¿Eliminar {ids.length} {ids.length === 1 ? 'propiedad' : 'propiedades'}?
            </span>
            <button
              onClick={() => setConfirming(false)}
              disabled={deleting}
              className="rounded-md px-2 py-1 text-xs font-medium text-muted-foreground transition hover:text-foreground disabled:opacity-50"
            >
              Cancelar
            </button>
            <button
              onClick={borrar}
              disabled={deleting}
              className="flex items-center gap-1.5 rounded-md bg-destructive px-3 py-1 text-xs font-medium text-white transition hover:bg-destructive/85 disabled:opacity-60"
            >
              {deleting ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
              {deleting ? 'Eliminando...' : 'Sí, eliminar'}
            </button>
          </div>
        ) : (
          <button
            onClick={() => setConfirming(true)}
            disabled={ids.length === 0}
            title={ids.length === 0 ? 'Estas propiedades todavía no están guardadas' : undefined}
            className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-background px-3 py-1.5 text-xs font-medium text-destructive transition hover:bg-destructive/10 disabled:opacity-40"
          >
            <Trash2 className="size-3.5" />
            Eliminar
          </button>
        )}

        {children}
      </div>
    </div>
  )
}
