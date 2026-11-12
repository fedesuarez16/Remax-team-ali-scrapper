'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Check, ImageIcon, Layers, ThumbsDown, ThumbsUp, Trash2 } from 'lucide-react'
import { borrarColeccion, listarColecciones, type ColeccionResumen } from '@/lib/colecciones'

/** Selecciones ya creadas, con lo que el cliente fue calificando. */
export function SeleccionesLista() {
  const [items, setItems] = useState<ColeccionResumen[] | null>(null)
  const [confirmando, setConfirmando] = useState<string | null>(null)

  useEffect(() => {
    listarColecciones().then(setItems)
  }, [])

  if (!items || items.length === 0) return null

  const borrar = async (id: string) => {
    if (await borrarColeccion(id)) setItems((prev) => prev?.filter((c) => c.id !== id) ?? null)
    setConfirmando(null)
  }

  return (
    <section>
      <p className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        <Layers className="size-3.5" />
        Fichas de selección · {items.length}
      </p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {items.map((c) => (
          <div key={c.id} className="group relative flex items-center gap-3 rounded-2xl border border-border bg-card p-2.5 shadow-sm transition hover:bg-muted/40">
            {c.portada ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={c.portada} alt="" className="size-14 shrink-0 rounded-xl object-cover" />
            ) : (
              <span className="flex size-14 shrink-0 items-center justify-center rounded-xl bg-muted"><ImageIcon className="size-5 text-muted-foreground/50" /></span>
            )}
            <Link href={`/ficha-propio/selecciones/${c.id}`} className="min-w-0 flex-1 after:absolute after:inset-0">
              <p className="truncate text-sm font-medium">{c.titulo}</p>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-2.5 text-xs text-muted-foreground">
                <span>{c.total} prop.</span>
                <span className="flex items-center gap-0.5 text-emerald-600 dark:text-emerald-400"><ThumbsUp className="size-3" />{c.interesa}</span>
                <span className="flex items-center gap-0.5 text-red-600 dark:text-red-400"><ThumbsDown className="size-3" />{c.no_interesa}</span>
                {c.enviada_at && <span className="flex items-center gap-0.5"><Check className="size-3" />Enviada</span>}
              </p>
            </Link>
            {confirmando === c.id ? (
              <div className="relative z-10 flex items-center gap-1">
                <button type="button" onClick={() => setConfirmando(null)} className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:text-foreground">No</button>
                <button type="button" onClick={() => void borrar(c.id)} className="rounded-md bg-destructive px-2 py-1 text-xs font-medium text-white">Borrar</button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmando(c.id)}
                title="Quitar de la lista (el link del cliente sigue funcionando)"
                className="relative z-10 rounded-md p-1.5 text-muted-foreground opacity-0 transition hover:bg-muted hover:text-destructive group-hover:opacity-100 focus-visible:opacity-100"
              >
                <Trash2 className="size-3.5" />
              </button>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}
