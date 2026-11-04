'use client'
import { useEffect, useRef } from 'react'
import dynamic from 'next/dynamic'
import { MapPin, Sparkles, X } from 'lucide-react'
import type { Agente } from '@/lib/ficha'
import type { Calificacion, ColeccionItem } from '@/lib/colecciones'
import { estaUbicada } from '@/lib/geo'
import { FichaGallery } from '@/components/ficha/FichaGallery'
import { AgenteCard } from '@/components/ficha/AgenteCard'
import { fmtPrice } from '@/components/ficha/PropertyFicha'
import { BotonesCalificar, EstadoCliente, capitalizar } from './PropiedadCard'

const ColeccionMap = dynamic(() => import('./ColeccionMap'), { ssr: false })

/** Detalle de una propiedad de la selección, en modal sobre la grilla. */
export function PropiedadDetalle({
  item,
  numero,
  modo,
  agente,
  calificando,
  onClose,
  onCalificar,
}: {
  item: ColeccionItem
  numero: number
  modo: 'agente' | 'cliente'
  agente: Agente
  calificando?: boolean
  onClose: () => void
  onCalificar?: (c: Calificacion | null) => void
}) {
  const p = item.property

  // Ref: `onClose` llega nuevo en cada render del padre. Si fuera dependencia
  // del efecto, se re-ejecutaría, guardaría `hidden` como overflow "previo" y
  // al cerrar el modal la página quedaría sin scroll.
  const cerrar = useRef(onClose)
  useEffect(() => {
    cerrar.current = onClose
  }, [onClose])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && cerrar.current()
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [])

  const datos: { label: string; value: string }[] = []
  if (p.tipo_propiedad) datos.push({ label: 'Tipo', value: capitalizar(p.tipo_propiedad) })
  if (p.ambientes != null) datos.push({ label: 'Ambientes', value: String(p.ambientes) })
  if (p.banos != null) datos.push({ label: 'Baños', value: String(p.banos) })
  if (p.m2_total != null) datos.push({ label: 'Área total', value: `${p.m2_total} m²` })
  if (p.cocheras != null) datos.push({ label: 'Cocheras', value: String(p.cocheras) })
  if (p.piso != null) datos.push({ label: 'Piso', value: String(p.piso) })
  if (p.antiguedad != null) datos.push({ label: 'Antigüedad', value: p.antiguedad === 0 ? 'A estrenar' : `${p.antiguedad} años` })
  if (p.expensas != null) {
    datos.push({
      label: 'Expensas',
      value: new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', maximumFractionDigits: 0 }).format(p.expensas),
    })
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-0 sm:p-6"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={p.titulo ?? p.direccion}
    >
      <div
        className="relative w-full max-w-5xl bg-background shadow-xl sm:rounded-3xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-background/95 px-4 py-3 backdrop-blur sm:rounded-t-3xl">
          <button
            type="button"
            onClick={onClose}
            className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground"
          >
            <X className="size-4" />
            Cerrar
          </button>
          <span className="text-sm font-semibold text-muted-foreground">#{numero}</span>
        </div>

        <div className="space-y-6 p-4 sm:p-6">
          <FichaGallery images={p.imagenes ?? []} title={p.titulo ?? p.direccion} />

          <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="min-w-0 space-y-6">
              <div>
                <p className="text-3xl font-semibold tracking-tight">{fmtPrice(p)}</p>
                {p.titulo && <p className="mt-1 text-base font-medium">{p.titulo}</p>}
                {p.direccion && (
                  <p className="mt-1 flex items-start gap-1.5 text-sm text-muted-foreground">
                    <MapPin className="mt-0.5 size-4 shrink-0" />
                    {p.direccion}
                  </p>
                )}
              </div>

              {datos.length > 0 && (
                <div className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(7rem,1fr))]">
                  {datos.map((d) => (
                    <div key={d.label} className="min-w-0 rounded-xl bg-muted/60 px-3 py-2.5">
                      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{d.label}</p>
                      <p className="mt-0.5 break-words text-sm font-semibold">{d.value}</p>
                    </div>
                  ))}
                </div>
              )}

              {p.destacados && p.destacados.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {p.destacados.map((d) => (
                    <span key={`${d.label}-${d.value}`} className="flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-xs">
                      <Sparkles className="size-3 text-muted-foreground" />
                      <span className="font-medium">{d.value}</span>
                      <span className="text-muted-foreground">{d.label}</span>
                    </span>
                  ))}
                </div>
              )}

              {item.comentario && (
                <div className="rounded-2xl border border-border bg-muted/40 p-4 text-sm">
                  <p className="mb-1 font-medium">Comentario de {agente.nombre}</p>
                  <p className="whitespace-pre-line text-muted-foreground">{item.comentario}</p>
                </div>
              )}

              {p.descripcion && (
                <section>
                  <h3 className="mb-2 text-lg font-semibold">Descripción</h3>
                  <p className="whitespace-pre-line break-words text-sm leading-7 text-muted-foreground">{p.descripcion}</p>
                </section>
              )}

              {p.amenities && p.amenities.length > 0 && (
                <section>
                  <h3 className="mb-2 text-lg font-semibold">Características</h3>
                  <div className="flex flex-wrap gap-2">
                    {p.amenities.map((a) => (
                      <span key={a} className="rounded-full border border-border bg-muted/50 px-3 py-1 text-sm">{a}</span>
                    ))}
                  </div>
                </section>
              )}

              {estaUbicada(p) && (
                <section>
                  <h3 className="mb-2 text-lg font-semibold">Ubicación</h3>
                  <div className="h-72 overflow-hidden rounded-2xl border border-border">
                    <ColeccionMap propiedades={[p]} activa={p.id} />
                  </div>
                  <a
                    href={`https://www.google.com/maps/search/?api=1&query=${p.lat},${p.lng}`}
                    target="_blank"
                    rel="noopener"
                    className="mt-2 inline-block text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                  >
                    Abrir en Google Maps
                  </a>
                </section>
              )}
            </div>

            <aside className="min-w-0 space-y-4 lg:sticky lg:top-20">
              {modo === 'cliente' && onCalificar ? (
                <div className="rounded-2xl border border-border bg-card p-3">
                  <BotonesCalificar valor={item.calificacion} onChange={onCalificar} disabled={calificando} grande />
                </div>
              ) : (
                <div className="flex items-center justify-between rounded-2xl border border-border bg-card p-3 text-sm">
                  <span className="text-muted-foreground">Calificación del cliente</span>
                  <EstadoCliente c={item.calificacion} />
                </div>
              )}
              <AgenteCard
                a={agente}
                mensaje={`Hola ${agente.nombre}, me interesa la propiedad #${numero} "${p.titulo ?? p.direccion}" (${fmtPrice(p)}). ¿Podemos coordinar una visita?`}
              />
            </aside>
          </div>
        </div>
      </div>
    </div>
  )
}
