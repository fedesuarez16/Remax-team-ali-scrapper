'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Loader2, Send } from 'lucide-react'
import { SelectionBar } from '@/components/properties/SelectionBar'
import { AgenteSelector } from '@/components/ficha/AgenteSelector'
import type { Property } from '@/hooks/useSSEStream'
import { AGENTES, agenteByEmail, asignarAgente, enrichFicha, guardarSeleccion, marcarEnviadas } from '@/lib/ficha'

type Props = {
  /** Propiedades marcadas, completas — son las que se convierten en fichas. */
  seleccionadas: Property[]
  /** Ids persistidos de la selección: sólo estas se pueden borrar. */
  ids: string[]
  onClear: () => void
  onDeleted: (ids: string[]) => void
  /** Se llama tras un envío exitoso para que la pantalla refleje el nuevo
   *  estado (enviadas + perfil) sin tener que recargar la búsqueda. */
  onEnviadas?: (enviadas: string[], agenteEmail: string) => void
}

/**
 * Barra de selección + envío de fichas. Es la ÚNICA implementación del flujo
 * "preparar y enviar": /properties y /search la comparten, así el paso de
 * elegir perfil, el sellado del agente y la marca de enviada no se bifurcan
 * entre pantallas.
 *
 * El envío tiene dos pasos a propósito: primero se elige a nombre de qué agente
 * salen las fichas, recién después se generan. Una vez que el link salió, el
 * cliente ya vio ese contacto — no hay vuelta atrás.
 */
export function EnviarFichasBar({ seleccionadas, ids, onClear, onDeleted, onEnviadas }: Props) {
  const router = useRouter()
  const [eligiendoAgente, setEligiendoAgente] = useState(false)
  const [agenteEmail, setAgenteEmail] = useState<string>(AGENTES[0].email)
  const [preparing, setPreparing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const cerrarPaso = () => {
    setEligiendoAgente(false)
    setError(null)
  }

  const enviar = async () => {
    if (seleccionadas.length === 0 || preparing) return
    setPreparing(true)
    setError(null)
    try {
      // Parse each description with the LLM (amenities + destacados) before building the ficha.
      const enriched = await Promise.all(seleccionadas.map(enrichFicha))

      // Sellar el perfil elegido ANTES de mandar nada: la ficha pública lee el
      // agente de la base, así que si esto falla la ficha saldría con el
      // contacto de otro. En ese caso se corta acá y no se marca como enviada.
      const { props: conAgente, fallidas } = await asignarAgente(enriched, agenteEmail)
      if (fallidas.length > 0) {
        setError(
          `No se pudo asignar el perfil en ${fallidas.length} ${fallidas.length === 1 ? 'propiedad' : 'propiedades'}. No se envió nada.`,
        )
        return
      }

      // Dejar sellado el envío: al volver a esta búsqueda las enviadas se
      // distinguen de las pendientes. Si falla, el envío sigue igual.
      const marcadas = await marcarEnviadas(seleccionadas.map((p) => p.id ?? ''))
      onEnviadas?.(marcadas, agenteEmail)

      guardarSeleccion(conAgente)
      router.push('/ficha')
    } finally {
      setPreparing(false)
    }
  }

  return (
    <div>
      {/* Paso previo: elegir el perfil. Sólo aparece al pedir el envío, para no
          cargar la barra mientras el usuario todavía está eligiendo tarjetas. */}
      {eligiendoAgente && (
        <div className="space-y-2 border-t border-border bg-card px-6 py-3">
          <AgenteSelector
            selected={agenteEmail}
            onSelect={setAgenteEmail}
            disabled={preparing}
            legend="¿Con qué perfil enviamos estas fichas?"
          />
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
      )}

      <SelectionBar
        count={seleccionadas.length}
        ids={ids}
        onClear={() => {
          cerrarPaso()
          onClear()
        }}
        onDeleted={onDeleted}
      >
        {eligiendoAgente && (
          <button
            onClick={cerrarPaso}
            disabled={preparing}
            className="rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-muted disabled:opacity-50"
          >
            Cancelar
          </button>
        )}
        <button
          onClick={() => (eligiendoAgente ? enviar() : setEligiendoAgente(true))}
          disabled={preparing}
          className="flex items-center gap-2 rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:opacity-60"
        >
          {preparing ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
          {preparing
            ? 'Preparando fichas...'
            : eligiendoAgente
              ? `Enviar como ${agenteByEmail(agenteEmail).nombre}`
              : 'Preparar y enviar'}
        </button>
      </SelectionBar>
    </div>
  )
}
