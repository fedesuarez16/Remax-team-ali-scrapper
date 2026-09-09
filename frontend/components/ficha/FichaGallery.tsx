'use client'

import { useState } from 'react'
import { ChevronLeft, ChevronRight, ImageIcon } from 'lucide-react'

export function FichaGallery({ images, title, compact = false }: {
  images: string[]; title: string; compact?: boolean
}) {
  const [selected, setSelected] = useState<string | null>(null)
  const [failed, setFailed] = useState<string[]>([])
  const photos = images.filter((src) => src && !failed.includes(src))
  const active = Math.max(0, photos.indexOf(selected ?? ''))
  const move = (step: number) => setSelected(photos[(active + step + photos.length) % photos.length])

  return (
    <div className="min-w-0 space-y-3">
      <div
        className={`relative overflow-hidden bg-muted ${compact ? 'aspect-[16/10] print:aspect-[3/1]' : 'aspect-[4/3] rounded-3xl sm:aspect-[16/10]'}`}
        role="region"
        aria-label="Fotos de la propiedad"
      >
        {photos.length ? (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={photos[active]} alt={`${title} · Foto ${active + 1}`} className="h-full w-full object-cover" onError={() => setFailed((prev) => [...prev, photos[active]])} />
            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-black/40 to-transparent" />
            <span className="absolute bottom-4 right-4 flex items-center gap-2 rounded-full bg-black/55 px-3 py-1.5 text-xs font-medium text-white backdrop-blur-sm" aria-live="polite">
              <ImageIcon className="size-3.5" />{active + 1} / {photos.length}
            </span>
            {photos.length > 1 && (
              <div className="absolute inset-x-3 top-1/2 flex -translate-y-1/2 justify-between print:hidden">
                <button type="button" onClick={() => move(-1)} aria-label="Foto anterior" className="flex size-10 items-center justify-center rounded-full bg-white/95 text-neutral-900 shadow-sm transition hover:bg-white focus-visible:outline-2 focus-visible:outline-offset-2"><ChevronLeft className="size-5" /></button>
                <button type="button" onClick={() => move(1)} aria-label="Foto siguiente" className="flex size-10 items-center justify-center rounded-full bg-white/95 text-neutral-900 shadow-sm transition hover:bg-white focus-visible:outline-2 focus-visible:outline-offset-2"><ChevronRight className="size-5" /></button>
              </div>
            )}
          </>
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground">
            <ImageIcon className="size-10 opacity-40" />
            <p className="text-sm">Fotos no disponibles</p>
          </div>
        )}
      </div>
      {!compact && photos.length > 1 && (
        <div className="flex gap-2 overflow-x-auto p-1 print:hidden" aria-label="Elegir foto">
          {photos.map((src, index) => (
            <button key={`${src}-${index}`} type="button" onClick={() => setSelected(src)} aria-label={`Ver foto ${index + 1}`} aria-pressed={active === index} className={`aspect-[4/3] w-20 shrink-0 overflow-hidden rounded-xl transition sm:w-24 ${active === index ? 'ring-2 ring-foreground ring-offset-2 ring-offset-background' : 'opacity-60 hover:opacity-100'}`}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={src} alt="" loading="lazy" className="h-full w-full object-cover" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
