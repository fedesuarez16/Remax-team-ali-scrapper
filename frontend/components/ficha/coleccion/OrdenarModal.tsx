'use client'
import { useState } from 'react'
import { ArrowDown, ArrowUp, ArrowUpToLine, ArrowUpDown, GripVertical, Loader2, X } from 'lucide-react'
import type { ColeccionItem } from '@/lib/colecciones'

/** Reordenar la selección: arrastrar, o flechas para quien está en el celular
 *  (el drag nativo de HTML no funciona con touch). Nada se guarda hasta
 *  "Guardar orden": cancelar deja todo como estaba. */
export function OrdenarModal({
  items,
  onClose,
  onSave,
}: {
  items: ColeccionItem[]
  onClose: () => void
  onSave: (ids: string[]) => Promise<boolean>
}) {
  const [orden, setOrden] = useState(items)
  const [arrastrando, setArrastrando] = useState<number | null>(null)
  const [guardando, setGuardando] = useState(false)
  const [error, setError] = useState(false)

  const mover = (desde: number, hasta: number) => {
    if (hasta < 0 || hasta >= orden.length || desde === hasta) return
    setOrden((prev) => {
      const next = [...prev]
      const [it] = next.splice(desde, 1)
      next.splice(hasta, 0, it)
      return next
    })
  }

  const guardar = async () => {
    setGuardando(true)
    setError(false)
    const ok = await onSave(orden.map((i) => i.property_id))
    setGuardando(false)
    if (ok) onClose()
    else setError(true)
  }

  const iconBtn = 'flex size-7 items-center justify-center rounded-md text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose} role="dialog" aria-modal="true" aria-label="Ordenar propiedades">
      <div className="w-full max-w-lg rounded-2xl bg-background p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-1 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold"><ArrowUpDown className="size-4" />Ordenar propiedades</h2>
          <button type="button" onClick={onClose} aria-label="Cerrar" className={iconBtn}><X className="size-4" /></button>
        </div>
        <p className="mb-4 text-xs text-muted-foreground">Arrastrá para cambiar el orden en que las ve tu cliente.</p>

        <ol className="max-h-[60vh] space-y-2 overflow-y-auto">
          {orden.map((it, i) => (
            <li
              key={it.property_id}
              draggable
              onDragStart={() => setArrastrando(i)}
              onDragOver={(e) => {
                e.preventDefault()
                if (arrastrando !== null && arrastrando !== i) {
                  mover(arrastrando, i)
                  setArrastrando(i)
                }
              }}
              onDragEnd={() => setArrastrando(null)}
              className={`flex items-center gap-3 rounded-xl border bg-card px-2 py-2 ${arrastrando === i ? 'border-foreground opacity-60' : 'border-border'}`}
            >
              <GripVertical className="size-4 shrink-0 cursor-grab text-muted-foreground" />
              {it.property.imagenes?.[0] ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={it.property.imagenes[0]} alt="" className="size-10 shrink-0 rounded-md object-cover" />
              ) : (
                <span className="size-10 shrink-0 rounded-md bg-muted" />
              )}
              <span className="w-7 shrink-0 text-xs font-semibold text-muted-foreground">#{i + 1}</span>
              <span className="min-w-0 flex-1 truncate text-sm">{it.property.titulo || it.property.direccion}</span>
              <button type="button" onClick={() => mover(i, 0)} disabled={i === 0} title="Al principio" className={iconBtn}><ArrowUpToLine className="size-3.5" /></button>
              <button type="button" onClick={() => mover(i, i - 1)} disabled={i === 0} title="Subir" className={iconBtn}><ArrowUp className="size-3.5" /></button>
              <button type="button" onClick={() => mover(i, i + 1)} disabled={i === orden.length - 1} title="Bajar" className={iconBtn}><ArrowDown className="size-3.5" /></button>
            </li>
          ))}
        </ol>

        {error && <p className="mt-3 text-xs text-destructive">No se pudo guardar el orden. Probá de nuevo.</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg border border-border px-3 py-1.5 text-sm transition hover:bg-muted">Cancelar</button>
          <button type="button" onClick={guardar} disabled={guardando} className="flex items-center gap-1.5 rounded-lg bg-foreground px-3 py-1.5 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:opacity-60">
            {guardando && <Loader2 className="size-3.5 animate-spin" />}
            Guardar orden
          </button>
        </div>
      </div>
    </div>
  )
}
