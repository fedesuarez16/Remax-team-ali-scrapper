'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Layers, Loader2, X } from 'lucide-react'
import { AGENTES } from '@/lib/ficha'
import { crearColeccion } from '@/lib/colecciones'
import { AgenteSelector } from '@/components/ficha/AgenteSelector'

/** Botón de la barra de selección: junta las fichas marcadas en UNA ficha de
 *  selección para un cliente y abre el editor para personalizarla. */
export function CrearSeleccion({ ids, agenteInicial }: { ids: string[]; agenteInicial?: string | null }) {
  const router = useRouter()
  const [abierto, setAbierto] = useState(false)
  const [cliente, setCliente] = useState('')
  const [agenteEmail, setAgenteEmail] = useState<string>(
    AGENTES.find((a) => a.email === agenteInicial)?.email ?? AGENTES[0].email,
  )
  const [creando, setCreando] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const crear = async () => {
    if (creando || ids.length === 0) return
    setCreando(true)
    setError(null)
    const col = await crearColeccion({ property_ids: ids, cliente, agente_email: agenteEmail })
    if (!col) {
      setCreando(false)
      setError('No se pudo crear la selección. Probá de nuevo.')
      return
    }
    router.push(`/ficha-propio/selecciones/${col.id}`)
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setAbierto(true)}
        disabled={ids.length === 0}
        className="flex items-center gap-2 rounded-lg bg-foreground px-3 py-1.5 text-xs font-medium text-background transition hover:bg-foreground/85 disabled:opacity-40"
      >
        <Layers className="size-3.5" />
        Crear ficha con {ids.length} {ids.length === 1 ? 'propiedad' : 'propiedades'}
      </button>

      {abierto && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={() => !creando && setAbierto(false)} role="dialog" aria-modal="true" aria-label="Crear ficha de selección">
          <form
            onClick={(e) => e.stopPropagation()}
            onSubmit={(e) => {
              e.preventDefault()
              void crear()
            }}
            className="w-full max-w-2xl space-y-5 rounded-2xl bg-background p-5 shadow-xl sm:p-6"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold tracking-tight">Ficha de selección</h2>
                <p className="mt-0.5 text-sm text-muted-foreground">
                  {ids.length} {ids.length === 1 ? 'propiedad' : 'propiedades'} en un solo link. Tu cliente las califica con «Me interesa» / «No me interesa».
                </p>
              </div>
              <button type="button" onClick={() => setAbierto(false)} disabled={creando} aria-label="Cerrar" className="rounded-md p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground">
                <X className="size-4" />
              </button>
            </div>

            <div>
              <label htmlFor="seleccion-cliente" className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Cliente
              </label>
              <input
                id="seleccion-cliente"
                autoFocus
                value={cliente}
                onChange={(e) => setCliente(e.target.value)}
                placeholder="Ej: Romina García"
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground/20"
              />
              <p className="mt-1 text-[11px] text-muted-foreground">
                El título queda «Propiedades para {cliente.trim() || '…'}». Lo podés cambiar después.
              </p>
            </div>

            <AgenteSelector selected={agenteEmail} onSelect={setAgenteEmail} disabled={creando} />

            {error && <p className="text-xs text-destructive">{error}</p>}

            <button
              type="submit"
              disabled={creando}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-foreground px-4 py-3 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:opacity-50"
            >
              {creando ? <Loader2 className="size-4 animate-spin" /> : <Layers className="size-4" />}
              {creando ? 'Creando…' : 'Crear y personalizar'}
            </button>
          </form>
        </div>
      )}
    </>
  )
}
