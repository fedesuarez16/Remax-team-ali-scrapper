'use client'
import { useMemo, useState, type ReactNode } from 'react'
import dynamic from 'next/dynamic'
import { ArrowUpDown, Building2, Check, Inbox, Pencil, ThumbsDown, ThumbsUp } from 'lucide-react'
import { agenteByEmail } from '@/lib/ficha'
import { estaUbicada } from '@/lib/geo'
import type { Calificacion, Coleccion, ColeccionDetalle } from '@/lib/colecciones'
import { AgentAvatar } from '@/components/ficha/AgentAvatar'
import { PropiedadCard } from './PropiedadCard'
import { PropiedadDetalle } from './PropiedadDetalle'
import { OrdenarModal } from './OrdenarModal'

const ColeccionMap = dynamic(() => import('./ColeccionMap'), {
  ssr: false,
  loading: () => <div className="h-full w-full animate-pulse bg-muted" />,
})

export type Modo = 'agente' | 'cliente'
type Solapa = 'todas' | 'pendientes' | 'interesa' | 'no_interesa'

export type ColeccionHandlers = {
  onEditar?: (patch: Partial<Pick<Coleccion, 'titulo' | 'subtitulo' | 'mostrar_mapa'>>) => Promise<boolean>
  onOrdenar?: (ids: string[]) => Promise<boolean>
  onComentar?: (propertyId: string, texto: string) => Promise<boolean>
  onQuitar?: (propertyId: string) => void
  onCalificar?: (propertyId: string, c: Calificacion | null) => void
}

/** Texto editable en el lugar: click → input; Enter o salir del campo guarda. */
function TextoEditable({
  valor,
  placeholder,
  editable,
  className,
  onSave,
}: {
  valor: string
  placeholder: string
  editable: boolean
  className: string
  onSave?: (v: string) => Promise<boolean>
}) {
  const [editando, setEditando] = useState(false)
  const [borrador, setBorrador] = useState(valor)

  if (!editable || !onSave) return valor ? <p className={className}>{valor}</p> : null

  const cerrar = async () => {
    setEditando(false)
    if (borrador.trim() !== valor.trim()) {
      const ok = await onSave(borrador)
      if (!ok) setBorrador(valor)
    }
  }

  if (editando) {
    return (
      <input
        autoFocus
        value={borrador}
        onChange={(e) => setBorrador(e.target.value)}
        onBlur={cerrar}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur()
          if (e.key === 'Escape') {
            setBorrador(valor)
            setEditando(false)
          }
        }}
        placeholder={placeholder}
        className={`${className} w-full rounded-lg border border-border bg-background px-2 py-1 focus:outline-none focus:ring-1 focus:ring-foreground/20`}
      />
    )
  }

  return (
    <button
      type="button"
      onClick={() => {
        setBorrador(valor)
        setEditando(true)
      }}
      className={`${className} group flex items-center gap-2 text-left ${valor ? '' : 'italic text-muted-foreground'}`}
    >
      {valor || placeholder}
      <Pencil className="size-3.5 shrink-0 opacity-50 transition group-hover:opacity-100" />
    </button>
  )
}

/**
 * Ficha de selección. Un único árbol para los dos públicos:
 * - `agente`: personaliza (títulos, comentarios, orden, mapa) y ve qué calificó el cliente.
 * - `cliente`: califica cada propiedad y ve el detalle.
 * Sin estado de servidor acá: la página contenedora decide qué persiste.
 */
export function ColeccionView({
  detalle,
  modo,
  calificando,
  pie,
  ...h
}: ColeccionHandlers & {
  detalle: ColeccionDetalle
  modo: Modo
  /** propertyId con una calificación en vuelo (deshabilita sus botones). */
  calificando?: string | null
  /** Pie legal, lo pone el contenedor. */
  pie?: ReactNode
}) {
  const { coleccion, items } = detalle
  const agente = agenteByEmail(coleccion.agente_email)
  const [solapa, setSolapa] = useState<Solapa>(modo === 'agente' ? 'todas' : 'pendientes')
  const [abierta, setAbierta] = useState<string | null>(null)
  const [ordenando, setOrdenando] = useState(false)

  const numero = useMemo(() => new Map(items.map((it, i) => [it.property_id, i + 1])), [items])
  const ubicadas = useMemo(() => items.map((i) => i.property).filter(estaUbicada), [items])

  const counts: Record<Solapa, number> = {
    todas: items.length,
    pendientes: items.filter((i) => !i.calificacion).length,
    interesa: items.filter((i) => i.calificacion === 'interesa').length,
    no_interesa: items.filter((i) => i.calificacion === 'no_interesa').length,
  }

  // El cliente arranca en "Para calificar"; cuando la vacía no lo dejamos
  // mirando una solapa en blanco — salta a lo que le interesó.
  const solapaEfectiva: Solapa =
    modo === 'cliente' && solapa === 'pendientes' && counts.pendientes === 0 && items.length > 0
      ? counts.interesa > 0 ? 'interesa' : 'no_interesa'
      : solapa

  const visibles = items.filter((i) =>
    solapaEfectiva === 'todas' ? true
      : solapaEfectiva === 'pendientes' ? !i.calificacion
        : i.calificacion === solapaEfectiva,
  )

  const solapas: { id: Solapa; label: string; icon: typeof Inbox }[] = [
    ...(modo === 'agente' ? [{ id: 'todas' as const, label: 'Todas', icon: Inbox }] : []),
    { id: 'pendientes', label: modo === 'agente' ? 'Sin calificar' : 'Para calificar', icon: Inbox },
    { id: 'interesa', label: modo === 'agente' ? 'Le interesan' : 'Me interesan', icon: ThumbsUp },
    { id: 'no_interesa', label: 'Descartadas', icon: ThumbsDown },
  ]

  const mostrarMapa = ubicadas.length > 0 && (modo === 'agente' || coleccion.mostrar_mapa)
  const calificadas = items.length - counts.pendientes
  const itemAbierto = items.find((i) => i.property_id === abierta) ?? null

  return (
    <div className="mx-auto w-full max-w-5xl">
      <div className="rounded-b-3xl border-x border-b border-border bg-card px-5 pb-4 pt-6 shadow-sm sm:rounded-3xl sm:border sm:px-8">
        <div className="mb-6 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="flex size-10 items-center justify-center rounded-xl bg-foreground">
              <Building2 className="size-5 text-background" />
            </div>
            <div>
              <p className="text-sm font-semibold tracking-tight">Team Alí</p>
              <p className="text-[11px] text-muted-foreground">{agente.inmobiliaria}</p>
            </div>
          </div>
          <div className="flex items-center gap-2.5">
            <AgentAvatar agente={agente} className="size-9" />
            <div className="hidden text-right sm:block">
              <p className="text-sm font-medium">{agente.nombre}</p>
              <p className="text-[11px] text-muted-foreground">{agente.cargo}</p>
            </div>
          </div>
        </div>

        <TextoEditable
          valor={coleccion.titulo}
          placeholder="Título de la selección"
          editable={modo === 'agente'}
          className="text-2xl font-semibold tracking-tight sm:text-3xl"
          onSave={h.onEditar && ((v) => h.onEditar!({ titulo: v.trim() || coleccion.titulo }))}
        />
        <div className="mt-1.5">
          <TextoEditable
            valor={coleccion.subtitulo ?? ''}
            placeholder="Click para agregar subtítulo de la búsqueda…"
            editable={modo === 'agente'}
            className="text-sm text-muted-foreground"
            onSave={h.onEditar && ((v) => h.onEditar!({ subtitulo: v }))}
          />
        </div>

        {modo === 'cliente' && items.length > 0 && (
          <div className="mt-5">
            <div className="mb-1.5 flex justify-between text-xs text-muted-foreground">
              <span>{calificadas === 0 ? 'Comenzá calificando propiedades' : `Calificaste ${calificadas} de ${items.length}`}</span>
              <span className="font-mono">{counts.pendientes === 0 ? '¡Listo!' : `${counts.pendientes} restante${counts.pendientes === 1 ? '' : 's'}`}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-foreground transition-all" style={{ width: `${(calificadas / items.length) * 100}%` }} />
            </div>
          </div>
        )}

        {mostrarMapa && (
          <div className="relative mt-5 h-56 overflow-hidden rounded-2xl border border-border sm:h-64">
            <ColeccionMap propiedades={ubicadas} activa={abierta} onSelect={setAbierta} />
            {modo === 'agente' && h.onEditar && (
              <label className="absolute right-3 top-3 z-[500] flex cursor-pointer items-center gap-2 rounded-full border border-border bg-background/95 px-3 py-1.5 text-xs font-medium shadow-sm">
                <input
                  type="checkbox"
                  checked={coleccion.mostrar_mapa}
                  onChange={(e) => void h.onEditar!({ mostrar_mapa: e.target.checked })}
                  className="accent-foreground"
                />
                Mostrar mapa al cliente
              </label>
            )}
          </div>
        )}
        {modo === 'agente' && ubicadas.length < items.length && (
          <p className="mt-2 text-[11px] text-muted-foreground">
            {items.length - ubicadas.length} propiedad{items.length - ubicadas.length === 1 ? '' : 'es'} sin ubicación no aparece{items.length - ubicadas.length === 1 ? '' : 'n'} en el mapa.
          </p>
        )}

        <div className="mt-5 flex flex-wrap items-end justify-between gap-2 border-b border-border">
          <div className="flex flex-wrap gap-1">
            {solapas
              .filter((s) => modo === 'agente' || s.id === 'pendientes' || counts[s.id] > 0)
              .map((s) => {
                const activa = solapaEfectiva === s.id
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => setSolapa(s.id)}
                    className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition ${activa ? 'border-foreground font-medium text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
                  >
                    <s.icon className="size-3.5" />
                    {s.label} · {counts[s.id]}
                  </button>
                )
              })}
          </div>
          {modo === 'agente' && h.onOrdenar && items.length > 1 && (
            <button
              type="button"
              onClick={() => setOrdenando(true)}
              className="mb-1.5 flex items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium transition hover:bg-muted"
            >
              <ArrowUpDown className="size-3.5" />
              Ordenar
            </button>
          )}
        </div>
      </div>

      <div className="px-4 py-6 sm:px-0">
        {visibles.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-12 text-center">
            <Check className="size-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">
              {items.length === 0 ? 'Esta selección no tiene propiedades.' : 'No hay propiedades en esta solapa.'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {visibles.map((it) => (
              <PropiedadCard
                key={it.property_id}
                item={it}
                numero={numero.get(it.property_id) ?? 0}
                modo={modo}
                agenteNombre={agente.nombre}
                calificando={calificando === it.property_id}
                onOpen={() => setAbierta(it.property_id)}
                onCalificar={h.onCalificar && ((c) => h.onCalificar!(it.property_id, c))}
                onComentar={h.onComentar && ((t) => h.onComentar!(it.property_id, t))}
                onQuitar={h.onQuitar && (() => h.onQuitar!(it.property_id))}
              />
            ))}
          </div>
        )}
      </div>

      {pie}

      {itemAbierto && (
        <PropiedadDetalle
          item={itemAbierto}
          numero={numero.get(itemAbierto.property_id) ?? 0}
          modo={modo}
          agente={agente}
          calificando={calificando === itemAbierto.property_id}
          onClose={() => setAbierta(null)}
          onCalificar={h.onCalificar && ((c) => h.onCalificar!(itemAbierto.property_id, c))}
        />
      )}

      {ordenando && h.onOrdenar && (
        <OrdenarModal items={items} onClose={() => setOrdenando(false)} onSave={h.onOrdenar} />
      )}
    </div>
  )
}
