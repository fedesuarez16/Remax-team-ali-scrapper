'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ArrowLeft, Check, Copy, Eye, Loader2, Pencil, Send, Sparkles } from 'lucide-react'
import { agenteByEmail } from '@/lib/ficha'
import {
  actualizarColeccion, coleccionUrl, comentarItem, leerColeccion, marcarColeccionEnviada,
  quitarItem, reordenarColeccion, type Coleccion, type ColeccionDetalle,
} from '@/lib/colecciones'
import { ColeccionView } from '@/components/ficha/coleccion/ColeccionView'
import { fmtPrice } from '@/components/ficha/PropertyFicha'

/** Editor del agente: personaliza la selección antes de mandarla y después
 *  ve qué calificó el cliente. "Ver como cliente" es una vista previa local:
 *  ahí no se puede calificar, eso es sólo del cliente. */
export default function SeleccionEditorPage() {
  const params = useParams<{ id: string }>()
  const id = params?.id
  const [detalle, setDetalle] = useState<ColeccionDetalle | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState(false)
  const [copiado, setCopiado] = useState(false)
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

  if (error) {
    return <div className="p-8 text-sm text-destructive">{error}</div>
  }
  if (!detalle || !id) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const { coleccion, items } = detalle
  const url = coleccionUrl(id)
  const agente = agenteByEmail(coleccion.agente_email)
  const fallo = (msg: string) => {
    setAviso(msg)
    setTimeout(() => setAviso(null), 3500)
  }

  const setColeccion = (c: Coleccion) => setDetalle((d) => d && { ...d, coleccion: { ...d.coleccion, ...c } })

  const editar = async (patch: Parameters<typeof actualizarColeccion>[1]) => {
    const updated = await actualizarColeccion(id, patch)
    if (!updated) {
      fallo('No se pudo guardar el cambio.')
      return false
    }
    setColeccion(updated)
    return true
  }

  const ordenar = async (ids: string[]) => {
    const ok = await reordenarColeccion(id, ids)
    if (ok) {
      setDetalle((d) => d && {
        ...d,
        items: ids
          .map((pid, i) => {
            const it = d.items.find((x) => x.property_id === pid)
            return it && { ...it, orden: i }
          })
          .filter((x): x is NonNullable<typeof x> => !!x),
      })
    }
    return ok
  }

  const comentar = async (pid: string, texto: string) => {
    const ok = await comentarItem(id, pid, texto)
    if (ok) {
      setDetalle((d) => d && {
        ...d,
        items: d.items.map((it) => (it.property_id === pid ? { ...it, comentario: texto.trim() || null } : it)),
      })
    }
    return ok
  }

  const quitar = async (pid: string) => {
    if (!(await quitarItem(id, pid))) {
      fallo('No se pudo quitar la propiedad.')
      return
    }
    setDetalle((d) => d && { ...d, items: d.items.filter((it) => it.property_id !== pid) })
  }

  const copiar = async () => {
    try {
      await navigator.clipboard.writeText(url)
      setCopiado(true)
      setTimeout(() => setCopiado(false), 1500)
    } catch {
      /* clipboard no disponible */
    }
  }

  // `window.open` primero y sin await: si no, el navegador deja de contarlo
  // como gesto del usuario y bloquea el popup. La marca es informativa.
  const enviar = () => {
    const saludo = coleccion.cliente ? `Hola ${coleccion.cliente}!` : 'Hola!'
    const lista = items
      .slice(0, 10)
      .map((it, i) => `#${i + 1} ${it.property.titulo ?? it.property.direccion} — ${fmtPrice(it.property)}`)
      .join('\n')
    const texto = `${saludo} Te armé una selección de ${items.length} propiedad${items.length === 1 ? '' : 'es'}:\n\n${lista}${items.length > 10 ? '\n…' : ''}\n\nMiralas y marcá cuáles te interesan acá:\n${url}\n\n${agente.nombre} · ${agente.inmobiliaria}\n${agente.telefono}`
    window.open(`https://wa.me/?text=${encodeURIComponent(texto)}`, '_blank', 'noopener')
    void marcarColeccionEnviada(id).then((c) => c && setColeccion(c))
  }

  return (
    <div className="flex h-full flex-col bg-muted/30 text-foreground">
      <div className="flex-1 overflow-y-auto pb-6 sm:pt-4">
        <div className="mx-auto mb-3 flex max-w-5xl items-center justify-between px-4 sm:px-0">
          <Link href="/ficha-propio" className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition hover:text-foreground">
            <ArrowLeft className="size-3.5" />
            Fichas propias
          </Link>
          {coleccion.enviada_at && (
            <span className="flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
              <Check className="size-3" />
              Enviada el {new Date(coleccion.enviada_at).toLocaleDateString('es-AR')}
            </span>
          )}
        </div>
        {preview ? (
          <ColeccionView key="cliente" detalle={detalle} modo="cliente" />
        ) : (
          <ColeccionView
            key="agente"
            detalle={detalle}
            modo="agente"
            onEditar={editar}
            onOrdenar={ordenar}
            onComentar={comentar}
            onQuitar={quitar}
          />
        )}
      </div>

      <div className="sticky bottom-0 z-20 border-t border-border bg-foreground text-background">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-0">
          <div className="min-w-0">
            {preview ? (
              <>
                <p className="flex items-center gap-1.5 text-sm font-semibold"><Eye className="size-4" />Vista previa</p>
                <p className="text-xs opacity-70">Así es como lo ve tu cliente</p>
              </>
            ) : (
              <>
                <p className="flex items-center gap-1.5 text-sm font-semibold"><Sparkles className="size-4" />Personalizá antes de compartir</p>
                <p className="text-xs opacity-70">Editá títulos · Agregá comentarios · Activá el mapa · Ordená las propiedades</p>
              </>
            )}
            {aviso && <p className="mt-1 text-xs text-red-300">{aviso}</p>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setPreview((v) => !v)}
              className="flex items-center gap-1.5 rounded-lg border border-background/30 px-3 py-1.5 text-xs font-medium transition hover:bg-background/10"
            >
              {preview ? <Pencil className="size-3.5" /> : <Eye className="size-3.5" />}
              {preview ? 'Ver como agente' : 'Ver como cliente'}
            </button>
            <button
              type="button"
              onClick={copiar}
              className="flex items-center gap-1.5 rounded-lg border border-background/30 px-3 py-1.5 text-xs font-medium transition hover:bg-background/10"
            >
              {copiado ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
              {copiado ? 'Copiado' : 'Copiar link'}
            </button>
            <button
              type="button"
              onClick={enviar}
              disabled={items.length === 0}
              className="flex items-center gap-1.5 rounded-lg bg-background px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-background/90 disabled:opacity-50"
            >
              <Send className="size-3.5" />
              {coleccion.enviada_at ? 'Reenviar al cliente' : 'Enviar al cliente'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
