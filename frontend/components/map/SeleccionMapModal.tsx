'use client'
import { useEffect } from 'react'
import dynamic from 'next/dynamic'
import { Loader2, MapPin, MapPinOff, X } from 'lucide-react'
import type { Property } from '@/hooks/useSSEStream'
import { estaUbicada } from '@/components/map/SeleccionMap'

// Leaflet toca `window` al cargar el módulo — tiene que quedar client-only.
const SeleccionMap = dynamic(() => import('@/components/map/SeleccionMap'), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center">
      <Loader2 className="size-6 animate-spin text-muted-foreground" />
    </div>
  ),
})

/**
 * Overlay con la selección ubicada en el mapa. Es un modal y no una
 * navegación a /map a propósito: el usuario está en medio de elegir qué
 * mandar, y irse a otra ruta le perdería la selección. Acá mira dónde caen,
 * cierra, y sigue eligiendo.
 */
export function SeleccionMapModal({ propiedades, onClose }: { propiedades: Property[]; onClose: () => void }) {
  const ubicadas = propiedades.filter(estaUbicada)
  const sinUbicar = propiedades.filter((p) => !estaUbicada(p))

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-[1200] flex items-center justify-center bg-black/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="flex h-[85vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-xl">
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <MapPin className="size-4 text-foreground" />
            <p className="text-sm font-semibold text-foreground">
              {ubicadas.length} de {propiedades.length} seleccionada{propiedades.length === 1 ? '' : 's'} en el mapa
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Cerrar"
            className="rounded-lg border border-border bg-background p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          <div className="relative min-w-0 flex-1">
            {ubicadas.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
                <MapPinOff className="size-8 text-muted-foreground/40" />
                <p className="text-sm text-foreground">Ninguna de las seleccionadas tiene ubicación todavía.</p>
                <p className="text-xs text-muted-foreground">
                  La geocodificación corre en segundo plano; probá de nuevo en un rato.
                </p>
              </div>
            ) : (
              <SeleccionMap propiedades={ubicadas} />
            )}
          </div>

          <aside className="hidden w-72 shrink-0 flex-col overflow-y-auto border-l border-border sm:flex">
            {ubicadas.map((p, i) => (
              <a
                key={p.id}
                href={`/p/${p.id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-start gap-2 border-b border-border px-3 py-2 hover:bg-muted"
              >
                <span
                  className={`mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold text-white ${
                    p.tipo_operacion === 'alquiler' ? 'bg-[#eab308]' : 'bg-[#16a34a]'
                  }`}
                >
                  {i + 1}
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-xs font-medium text-foreground">{p.titulo ?? p.direccion}</span>
                  <span className="block truncate text-[11px] text-muted-foreground">{p.direccion}</span>
                </span>
              </a>
            ))}
            {sinUbicar.length > 0 && (
              <div className="px-3 py-2">
                <p className="mb-1 flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  <MapPinOff className="size-3" />
                  Sin ubicación ({sinUbicar.length})
                </p>
                {sinUbicar.map((p, i) => (
                  <p key={p.id ?? i} className="truncate py-0.5 text-[11px] text-muted-foreground" title={p.direccion}>
                    {p.titulo ?? p.direccion}
                  </p>
                ))}
              </div>
            )}
          </aside>
        </div>
      </div>
    </div>
  )
}
