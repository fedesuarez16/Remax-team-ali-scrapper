'use client'
import { useEffect, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp, ImageIcon, ImagePlus, Loader2, Star, Trash2, X } from 'lucide-react'
import type { Property } from '@/hooks/useSSEStream'
import { updateProperty, type FichaTextos } from '@/lib/ficha'
import { useFichaTextos } from '@/hooks/useFichaTextos'

/** Campos de texto compartidos, en el orden en que aparecen en la ficha. */
const TEXTOS_CAMPOS: { key: keyof FichaTextos; label: string; hint?: string; rows: number }[] = [
  { key: 'texto_seleccion', label: 'Presentación', hint: 'Párrafo sobre la tarjeta de contacto.', rows: 3 },
  { key: 'firma', label: 'Firma del pie', rows: 1 },
  { key: 'colegiatura', label: 'Matrícula / colegiatura', rows: 1 },
  {
    key: 'disclaimer_legal',
    label: 'Descargo legal',
    hint: 'Texto normativo: es obligatorio en toda ficha publicada. Cambialo sólo si sabés lo que estás haciendo.',
    rows: 4,
  },
  { key: 'pie_publicacion', label: 'Cierre del pie', rows: 2 },
]

/**
 * Editor manual de la ficha propia. Existe sobre todo para curar las imágenes
 * que el scraper de Google Maps trae mal (logos de inmobiliarias, banners),
 * pero también permite corregir título, precio y descripción.
 *
 * Edita dos cosas de alcance MUY distinto, y por eso van en secciones separadas
 * y rotuladas: los datos de ESTA propiedad, y los textos del pie, que son del
 * equipo y se reescriben en todas las fichas, incluidas las ya compartidas.
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

  // Textos compartidos. Llegan async, así que el borrador se siembra recién
  // cuando la carga termina — sembrarlo antes guardaría los defaults encima de
  // lo que el equipo ya tenía escrito.
  const { textos, loading: textosLoading, guardar: guardarTextos } = useFichaTextos()
  const [textosDraft, setTextosDraft] = useState<FichaTextos>(textos)
  const [textosOpen, setTextosOpen] = useState(false)
  useEffect(() => {
    if (!textosLoading) setTextosDraft(textos)
  }, [textos, textosLoading])

  const textosCambiados = TEXTOS_CAMPOS
    .map((c) => c.key)
    .filter((k) => textosDraft[k].trim() !== textos[k])

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
    // Un texto compartido vacío borraría ese renglón del pie en TODAS las
    // fichas — el backend lo rechaza, pero avisamos acá para no gastar el viaje.
    const vacio = textosCambiados.find((k) => !textosDraft[k].trim())
    if (vacio) {
      setError(`"${TEXTOS_CAMPOS.find((c) => c.key === vacio)!.label}" no puede quedar vacío.`)
      setSaving(false)
      return
    }

    // Los textos primero: si fallan, se aborta antes de tocar la propiedad, así
    // no queda un guardado a medias con el usuario creyendo que salió todo.
    if (textosCambiados.length > 0) {
      const patch = Object.fromEntries(textosCambiados.map((k) => [k, textosDraft[k].trim()]))
      if (!(await guardarTextos(patch))) {
        setError('No se pudieron guardar los textos del pie. Probá de nuevo.')
        setSaving(false)
        return
      }
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

        {/* Textos del pie — alcance GLOBAL. Va colapsado y rotulado aparte
            porque no es lo mismo corregir el título de una ficha que reescribir
            el pie de todas las que ya se mandaron. */}
        <section className="rounded-xl border border-border">
          <button
            type="button"
            onClick={() => setTextosOpen((v) => !v)}
            className="flex w-full items-center gap-2 px-3 py-2.5 text-left transition hover:bg-muted/50"
          >
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Textos del pie y presentación
              </p>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                Aplican a <strong className="font-semibold">todas</strong> las fichas, también a las
                ya compartidas.
              </p>
            </div>
            {textosCambiados.length > 0 && (
              <span className="shrink-0 rounded-full bg-foreground px-2 py-0.5 text-[10px] font-medium text-background">
                {textosCambiados.length} sin guardar
              </span>
            )}
            {textosOpen ? (
              <ChevronUp className="size-4 shrink-0 text-muted-foreground" />
            ) : (
              <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
            )}
          </button>

          {textosOpen && (
            <div className="space-y-3 border-t border-border p-3">
              {textosLoading ? (
                <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
                  <Loader2 className="size-3.5 animate-spin" />
                  Cargando textos…
                </div>
              ) : (
                TEXTOS_CAMPOS.map(({ key, label, hint, rows }) => (
                  <label key={key} className="block">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      {label}
                    </span>
                    {hint && (
                      <span className="mb-1 flex items-start gap-1 text-[11px] leading-snug text-muted-foreground">
                        {key === 'disclaimer_legal' && (
                          <AlertTriangle className="mt-0.5 size-3 shrink-0" />
                        )}
                        {hint}
                      </span>
                    )}
                    {rows === 1 ? (
                      <input
                        value={textosDraft[key]}
                        onChange={(e) => setTextosDraft((prev) => ({ ...prev, [key]: e.target.value }))}
                        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-foreground"
                      />
                    ) : (
                      <textarea
                        value={textosDraft[key]}
                        onChange={(e) => setTextosDraft((prev) => ({ ...prev, [key]: e.target.value }))}
                        rows={rows}
                        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm leading-relaxed text-foreground focus:outline-none focus:ring-1 focus:ring-foreground"
                      />
                    )}
                  </label>
                ))
              )}
            </div>
          )}
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
