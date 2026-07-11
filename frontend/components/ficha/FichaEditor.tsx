'use client'
import { useState } from 'react'
import { ImageIcon, ImagePlus, Loader2, Star, Trash2, X } from 'lucide-react'
import type { Property } from '@/hooks/useSSEStream'
import { updateProperty } from '@/lib/ficha'

/**
 * Editor manual de la ficha propia. Existe sobre todo para curar las imágenes
 * que el scraper de Google Maps trae mal (logos de inmobiliarias, banners),
 * pero también permite corregir título, precio y descripción.
 */
export function FichaEditor({ p, onSaved, onCancel }: {
  p: Property
  onSaved: (updated: Property) => void
  onCancel: () => void
}) {
  const [imagenes, setImagenes] = useState<string[]>(p.imagenes ?? [])
  const [titulo, setTitulo] = useState(p.titulo ?? '')
  const [precio, setPrecio] = useState(p.precio != null ? String(p.precio) : '')
  const [moneda, setMoneda] = useState<'USD' | 'ARS'>(p.moneda ?? 'USD')
  const [descripcion, setDescripcion] = useState(p.descripcion ?? '')
  const [nuevaUrl, setNuevaUrl] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const quitar = (i: number) => setImagenes((prev) => prev.filter((_, j) => j !== i))
  const hacerPortada = (i: number) =>
    setImagenes((prev) => [prev[i], ...prev.filter((_, j) => j !== i)])

  const agregar = () => {
    const url = nuevaUrl.trim()
    if (!url) return
    try {
      const parsed = new URL(url)
      if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error()
    } catch {
      setError('La URL de imagen no es válida.')
      return
    }
    setError(null)
    setImagenes((prev) => (prev.includes(url) ? prev : [...prev, url]))
    setNuevaUrl('')
  }

  const guardar = async () => {
    if (!p.id) return
    setSaving(true)
    setError(null)
    const precioNum = precio.trim() === '' ? null : Number(precio)
    if (precioNum != null && Number.isNaN(precioNum)) {
      setError('El precio tiene que ser un número.')
      setSaving(false)
      return
    }
    const updated = await updateProperty(p.id, {
      imagenes,
      titulo: titulo.trim() || null,
      precio: precioNum,
      moneda,
      descripcion: descripcion.trim() || null,
    })
    setSaving(false)
    if (!updated) {
      setError('No se pudo guardar. Probá de nuevo.')
      return
    }
    onSaved(updated)
  }

  return (
    <article className="overflow-hidden rounded-2xl border-2 border-foreground bg-card print:hidden">
      <header className="flex items-center justify-between border-b border-border bg-muted/40 px-5 py-3">
        <p className="text-sm font-semibold text-foreground">Editar ficha</p>
        <button
          onClick={onCancel}
          className="flex items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground transition hover:bg-muted"
        >
          <X className="size-3.5" />
          Cancelar
        </button>
      </header>

      <div className="space-y-5 p-5">
        {/* Imágenes */}
        <section>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Imágenes
          </p>
          <p className="mb-2 text-xs text-muted-foreground">
            La primera es la portada. Eliminá las que no correspondan (logos, banners) con el tacho.
          </p>
          {imagenes.length === 0 ? (
            <div className="flex h-24 items-center justify-center rounded-xl border border-dashed border-border bg-muted/30">
              <ImageIcon className="size-6 text-muted-foreground/40" />
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
              {imagenes.map((src, i) => (
                <div key={src} className="group relative aspect-[4/3] overflow-hidden rounded-lg border border-border bg-muted">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={src} alt={`Imagen ${i + 1}`} className="h-full w-full object-cover" />
                  {i === 0 && (
                    <span className="absolute left-1 top-1 rounded bg-foreground px-1.5 py-0.5 text-[10px] font-medium text-background">
                      Portada
                    </span>
                  )}
                  <div className="absolute inset-x-0 bottom-0 flex justify-end gap-1 bg-gradient-to-t from-black/60 to-transparent p-1 opacity-0 transition group-hover:opacity-100">
                    {i !== 0 && (
                      <button
                        onClick={() => hacerPortada(i)}
                        title="Usar como portada"
                        className="rounded bg-white/90 p-1 text-black transition hover:bg-white"
                      >
                        <Star className="size-3.5" />
                      </button>
                    )}
                    <button
                      onClick={() => quitar(i)}
                      title="Eliminar imagen"
                      className="rounded bg-white/90 p-1 text-black transition hover:bg-white"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="mt-2 flex gap-2">
            <input
              value={nuevaUrl}
              onChange={(e) => setNuevaUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && agregar()}
              placeholder="https://… pegar URL de imagen"
              className="flex-1 rounded-lg border border-border bg-background px-3 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground"
            />
            <button
              onClick={agregar}
              className="flex items-center gap-1 rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-muted"
            >
              <ImagePlus className="size-3.5" />
              Agregar
            </button>
          </div>
        </section>

        {/* Datos */}
        <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="sm:col-span-2">
            <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-muted-foreground">Título</span>
            <input
              value={titulo}
              onChange={(e) => setTitulo(e.target.value)}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-foreground"
            />
          </label>
          <label>
            <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-muted-foreground">Precio</span>
            <input
              value={precio}
              onChange={(e) => setPrecio(e.target.value)}
              inputMode="numeric"
              placeholder="Consultar"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-foreground"
            />
          </label>
          <label>
            <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-muted-foreground">Moneda</span>
            <select
              value={moneda}
              onChange={(e) => setMoneda(e.target.value as 'USD' | 'ARS')}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-foreground"
            >
              <option value="USD">USD</option>
              <option value="ARS">ARS</option>
            </select>
          </label>
          <label className="sm:col-span-2">
            <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-muted-foreground">Descripción</span>
            <textarea
              value={descripcion}
              onChange={(e) => setDescripcion(e.target.value)}
              rows={4}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm leading-relaxed text-foreground focus:outline-none focus:ring-1 focus:ring-foreground"
            />
          </label>
        </section>

        {error && <p className="text-xs font-medium text-foreground">{error}</p>}

        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <button
            onClick={onCancel}
            disabled={saving}
            className="rounded-lg border border-border bg-background px-4 py-2 text-sm font-medium text-foreground transition hover:bg-muted disabled:opacity-50"
          >
            Cancelar
          </button>
          <button
            onClick={guardar}
            disabled={saving}
            className="flex items-center gap-2 rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:opacity-50"
          >
            {saving && <Loader2 className="size-4 animate-spin" />}
            Guardar cambios
          </button>
        </div>
      </div>
    </article>
  )
}
