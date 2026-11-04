'use client'
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { Building2, Loader2 } from 'lucide-react'
import { calificarItem, leerColeccion, type Calificacion, type ColeccionDetalle } from '@/lib/colecciones'
import { ColeccionView } from '@/components/ficha/coleccion/ColeccionView'
import { useFichaTextos } from '@/hooks/useFichaTextos'

/** Ficha de selección pública: la que recibe el cliente. Sin contraseña
 *  (ver `PUBLIC_PREFIXES`); lo único que escribe es la calificación. */
export default function SeleccionPublicaPage() {
  const params = useParams<{ id: string }>()
  const id = params?.id
  const { textos } = useFichaTextos()
  const [detalle, setDetalle] = useState<ColeccionDetalle | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [calificando, setCalificando] = useState<string | null>(null)
  const [aviso, setAviso] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    const ctrl = new AbortController()
    leerColeccion(id, ctrl.signal)
      .then(setDetalle)
      .catch((e) => {
        if (e instanceof DOMException && e.name === 'AbortError') return
        setError(e instanceof Error ? e.message : 'Error desconocido')
      })
    return () => ctrl.abort()
  }, [id])

  const setCalificacion = (pid: string, c: Calificacion | null) =>
    setDetalle((d) => d && {
      ...d,
      items: d.items.map((it) => (it.property_id === pid ? { ...it, calificacion: c } : it)),
    })

  // Optimista: el cliente ve el cambio al toque. Si el server no lo guarda,
  // se vuelve atrás y se avisa — que crea que nos llegó su "me interesa" sin
  // que haya llegado es justo lo que no puede pasar.
  const calificar = async (pid: string, c: Calificacion | null) => {
    if (!id || !detalle) return
    const anterior = detalle.items.find((i) => i.property_id === pid)?.calificacion ?? null
    setCalificacion(pid, c)
    setCalificando(pid)
    setAviso(null)
    const ok = await calificarItem(id, pid, c)
    setCalificando(null)
    if (!ok) {
      setCalificacion(pid, anterior)
      setAviso('No pudimos guardar tu respuesta. Revisá tu conexión y probá de nuevo.')
    }
  }

  if (error) {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-2 bg-background px-6 text-center">
        <Building2 className="size-10 text-muted-foreground/40" />
        <p className="text-sm font-medium text-foreground">No se pudo cargar la selección</p>
        <p className="text-xs text-muted-foreground">{error}</p>
      </div>
    )
  }

  if (!detalle) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="min-h-dvh bg-muted/30 pb-10 text-foreground sm:pt-6">
      <ColeccionView
        detalle={detalle}
        modo="cliente"
        calificando={calificando}
        onCalificar={calificar}
        pie={
          <footer className="mx-4 space-y-3 border-t border-border pt-6 text-xs text-muted-foreground sm:mx-0">
            <p className="whitespace-pre-line leading-relaxed">{textos.texto_seleccion}</p>
            <div>
              <p className="font-medium text-foreground">{textos.firma}</p>
              <p className="mt-0.5">{textos.colegiatura}</p>
            </div>
            {/* Descargo normativo — obligatorio en toda ficha publicada. */}
            <p className="whitespace-pre-line leading-relaxed">{textos.disclaimer_legal}</p>
            <p className="whitespace-pre-line">{textos.pie_publicacion}</p>
          </footer>
        }
      />
      {aviso && (
        <div role="alert" className="fixed inset-x-4 bottom-4 z-40 mx-auto max-w-md rounded-xl bg-destructive px-4 py-3 text-sm text-white shadow-lg">
          {aviso}
        </div>
      )}
    </div>
  )
}
