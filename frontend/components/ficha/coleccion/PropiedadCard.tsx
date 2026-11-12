'use client'
import { useState } from 'react'
import {
  Bath, ChevronLeft, ChevronRight, Home, ImageIcon, LayoutGrid, Loader2,
  Maximize2, Ruler, ThumbsDown, ThumbsUp, Trash2,
} from 'lucide-react'
import type { Property } from '@/hooks/useSSEStream'
import type { Calificacion, ColeccionItem } from '@/lib/colecciones'
import { fmtPrice } from '@/components/ficha/PropertyFicha'

export function capitalizar(s?: string | null): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''
}

/** Fila de íconos con los datos duros: amb · baños · m². Sólo lo que la
 *  ficha tiene; no hay campo de dormitorios y no se deduce de ambientes. */
export function DatosDuros({ p, className = '' }: { p: Property; className?: string }) {
  const datos: { icon: typeof Bath; label: string }[] = []
  if (p.ambientes != null) datos.push({ icon: LayoutGrid, label: `${p.ambientes} amb.` })
  if (p.banos != null) datos.push({ icon: Bath, label: `${p.banos} ${p.banos === 1 ? 'baño' : 'baños'}` })
  if (p.m2_total != null) datos.push({ icon: Ruler, label: `${p.m2_total} m²` })
  if (datos.length === 0) return null
  return (
    <div className={`flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground ${className}`}>
      {datos.map(({ icon: Icon, label }) => (
        <span key={label} className="flex items-center gap-1">
          <Icon className="size-3.5" />
          {label}
        </span>
      ))}
    </div>
  )
}

/** Botones de calificación del cliente. Tocar el elegido lo deshace. */
export function BotonesCalificar({
  valor,
  onChange,
  disabled,
  grande = false,
}: {
  valor: Calificacion | null
  onChange: (c: Calificacion | null) => void
  disabled?: boolean
  grande?: boolean
}) {
  const base = `flex flex-1 flex-col items-center justify-center gap-1 rounded-xl border font-medium transition disabled:opacity-60 ${grande ? 'py-3 text-sm' : 'py-2.5 text-xs'}`
  const si = valor === 'interesa'
  const no = valor === 'no_interesa'
  return (
    <div className="flex gap-2">
      <button
        type="button"
        disabled={disabled}
        aria-pressed={si}
        onClick={() => onChange(si ? null : 'interesa')}
        className={`${base} ${si
          ? 'border-emerald-600 bg-emerald-600 text-white'
          : 'border-emerald-500/20 bg-emerald-500/10 text-emerald-700 hover:bg-emerald-500/20 dark:text-emerald-400'}`}
      >
        <ThumbsUp className="size-4" />
        Me interesa
      </button>
      <button
        type="button"
        disabled={disabled}
        aria-pressed={no}
        onClick={() => onChange(no ? null : 'no_interesa')}
        className={`${base} ${no
          ? 'border-red-600 bg-red-600 text-white'
          : 'border-red-500/20 bg-red-500/10 text-red-700 hover:bg-red-500/20 dark:text-red-400'}`}
      >
        <ThumbsDown className="size-4" />
        No me interesa
      </button>
    </div>
  )
}

/** Etiqueta que ve el agente con lo que eligió el cliente. */
export function EstadoCliente({ c }: { c: Calificacion | null }) {
  if (c === 'interesa') {
    return <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-400">Le interesa</span>
  }
  if (c === 'no_interesa') {
    return <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] font-medium text-red-700 dark:text-red-400">Descartada</span>
  }
  return <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">Sin calificar</span>
}

function Carrusel({ images, alt, numero, onOpen }: {
  images: string[]; alt: string; numero: number; onOpen: () => void
}) {
  const [i, setI] = useState(0)
  const [rotas, setRotas] = useState<string[]>([])
  const fotos = images.filter((src) => src && !rotas.includes(src)).slice(0, 8)
  const actual = fotos.length ? i % fotos.length : 0
  const mover = (paso: number) => setI((v) => (v + paso + fotos.length) % fotos.length)

  return (
    <div className="group relative aspect-[4/3] overflow-hidden bg-muted">
      {fotos.length ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={fotos[actual]}
          alt={`${alt} · Foto ${actual + 1}`}
          loading="lazy"
          onClick={onOpen}
          onError={() => setRotas((prev) => [...prev, fotos[actual]])}
          className="h-full w-full cursor-pointer object-cover"
        />
      ) : (
        <button type="button" onClick={onOpen} className="flex h-full w-full items-center justify-center text-muted-foreground">
          <ImageIcon className="size-8 opacity-40" />
        </button>
      )}
      <span className="pointer-events-none absolute bottom-2 left-3 text-3xl font-bold text-white drop-shadow-[0_2px_6px_rgba(0,0,0,.6)]">
        #{numero}
      </span>
      <button
        type="button"
        onClick={onOpen}
        aria-label="Ver detalle"
        className="absolute right-2 top-2 flex size-8 items-center justify-center rounded-full bg-white/95 text-neutral-900 shadow-sm transition hover:bg-white"
      >
        <Maximize2 className="size-3.5" />
      </button>
      {fotos.length > 1 && (
        <>
          <button type="button" onClick={() => mover(-1)} aria-label="Foto anterior" className="absolute left-2 top-1/2 flex size-8 -translate-y-1/2 items-center justify-center rounded-full bg-white/90 text-neutral-900 opacity-0 shadow-sm transition group-hover:opacity-100 focus-visible:opacity-100">
            <ChevronLeft className="size-4" />
          </button>
          <button type="button" onClick={() => mover(1)} aria-label="Foto siguiente" className="absolute right-2 top-1/2 flex size-8 -translate-y-1/2 items-center justify-center rounded-full bg-white/90 text-neutral-900 opacity-0 shadow-sm transition group-hover:opacity-100 focus-visible:opacity-100">
            <ChevronRight className="size-4" />
          </button>
          <div className="pointer-events-none absolute bottom-3 left-1/2 flex -translate-x-1/2 gap-1">
            {fotos.map((src, k) => (
              <span key={`${src}-${k}`} className={`size-1.5 rounded-full ${k === actual ? 'bg-white' : 'bg-white/50'}`} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

/** Comentario del agente: se guarda al salir del campo, sólo si cambió. */
function ComentarioAgente({ inicial, onSave }: { inicial: string; onSave: (t: string) => Promise<boolean> }) {
  const [texto, setTexto] = useState(inicial)
  const [guardado, setGuardado] = useState(inicial)
  const [estado, setEstado] = useState<'idle' | 'saving' | 'error'>('idle')

  const guardar = async () => {
    if (texto.trim() === guardado.trim()) return
    setEstado('saving')
    const ok = await onSave(texto)
    if (ok) {
      setGuardado(texto)
      setEstado('idle')
    } else setEstado('error')
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <label className="text-[11px] font-medium text-muted-foreground">Tu comentario para el cliente</label>
        {estado === 'saving' && <Loader2 className="size-3 animate-spin text-muted-foreground" />}
        {estado === 'error' && <span className="text-[11px] text-destructive">No se guardó</span>}
      </div>
      <textarea
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        onBlur={guardar}
        rows={2}
        placeholder="Agregá un comentario…"
        className="w-full resize-none rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground/20"
      />
    </div>
  )
}

/** Botón visible para sacar la propiedad de la selección, con un paso de
 *  confirmación: un click suelto no se puede llevar una propiedad del link
 *  que ya tiene el cliente. */
function QuitarItem({ onQuitar }: { onQuitar: () => void }) {
  const [confirmando, setConfirmando] = useState(false)
  const base = 'rounded-lg px-2.5 py-1.5 text-xs font-medium transition'

  if (confirmando) {
    return (
      <div className="flex items-center justify-between gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-2.5 py-1.5">
        <span className="text-xs text-foreground">¿Quitar de la selección?</span>
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => setConfirmando(false)} className={`${base} text-muted-foreground hover:text-foreground`}>
            No
          </button>
          <button type="button" onClick={onQuitar} className={`${base} bg-destructive text-white hover:bg-destructive/90`}>
            Quitar
          </button>
        </div>
      </div>
    )
  }

  return (
    <button
      type="button"
      onClick={() => setConfirmando(true)}
      className={`${base} flex w-full items-center justify-center gap-1.5 border border-border text-muted-foreground hover:border-destructive/50 hover:bg-destructive/5 hover:text-destructive`}
    >
      <Trash2 className="size-3.5" />
      Quitar de la selección
    </button>
  )
}

export function PropiedadCard({
  item,
  numero,
  modo,
  agenteNombre,
  calificando,
  onOpen,
  onCalificar,
  onComentar,
  onQuitar,
}: {
  item: ColeccionItem
  numero: number
  modo: 'agente' | 'cliente'
  agenteNombre: string
  calificando?: boolean
  onOpen: () => void
  onCalificar?: (c: Calificacion | null) => void
  onComentar?: (texto: string) => Promise<boolean>
  onQuitar?: () => void
}) {
  const p = item.property
  return (
    <article className="flex flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
      <Carrusel images={p.imagenes ?? []} alt={p.titulo ?? p.direccion} numero={numero} onOpen={onOpen} />
      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <p className="text-lg font-semibold tracking-tight">{fmtPrice(p)}</p>
          {modo === 'agente' && <EstadoCliente c={item.calificacion} />}
        </div>
        <button type="button" onClick={onOpen} className="text-left text-sm font-medium leading-snug hover:underline">
          {p.titulo || p.direccion}
        </button>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {p.tipo_propiedad && (
            <span className="flex items-center gap-1"><Home className="size-3.5" />{capitalizar(p.tipo_propiedad)}</span>
          )}
          {p.titulo && p.direccion && <span className="line-clamp-1">{p.direccion}</span>}
        </div>
        <DatosDuros p={p} />

        <div className="mt-auto space-y-3 border-t border-border pt-3">
          {modo === 'agente' ? (
            <>
              {onComentar && <ComentarioAgente inicial={item.comentario ?? ''} onSave={onComentar} />}
              {onQuitar && <QuitarItem onQuitar={onQuitar} />}
            </>
          ) : (
            <>
              {item.comentario && (
                <div className="rounded-lg bg-muted/60 px-3 py-2 text-xs">
                  <p className="mb-0.5 font-medium text-foreground">{agenteNombre} comentó:</p>
                  <p className="whitespace-pre-line text-muted-foreground">{item.comentario}</p>
                </div>
              )}
              {onCalificar && (
                <BotonesCalificar valor={item.calificacion} onChange={onCalificar} disabled={calificando} />
              )}
            </>
          )}
        </div>
      </div>
    </article>
  )
}
