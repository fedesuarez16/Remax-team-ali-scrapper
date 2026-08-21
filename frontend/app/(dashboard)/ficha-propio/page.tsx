'use client'
import { useCallback, useEffect, useState } from 'react'
import {
  Check, ChevronDown, ChevronUp, Copy, ExternalLink, FileText, Link2, Loader2,
  Pencil, RotateCcw, Send, Sparkles, UserRound,
} from 'lucide-react'
import type { Property } from '@/hooks/useSSEStream'
import { AGENTES, agenteByEmail, enrichFicha, fichaUrl, marcarEnviadas } from '@/lib/ficha'
import { PropertyFicha, fmtPrice } from '@/components/ficha/PropertyFicha'
import { FichaEditor } from '@/components/ficha/FichaEditor'
import { AgenteSelector } from '@/components/ficha/AgenteSelector'
import { SelectionBar } from '@/components/properties/SelectionBar'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

type ImportResult =
  | { url: string; status: 'ok'; created: boolean; property: Property }
  | { url: string; status: 'error'; error: string }

type Stats = { total_fichas: number; gasto_usd: number; llamadas: number }

/** Las fichas se agrupan por el ÚNICO estado que el sistema ya conoce de
 *  verdad: `enviada_at`, el sello que deja "Enviar". No hay un campo inventado
 *  a mano que pueda quedar desfasado — una ficha o se mandó o no se mandó. */
type Estado = 'pendientes' | 'enviadas' | 'todas'

const esEnviada = (p: Property) => !!p.enviada_at

const FILTROS: { id: Estado; label: string }[] = [
  { id: 'pendientes', label: 'Faltan enviar' },
  { id: 'enviadas', label: 'Enviadas' },
  { id: 'todas', label: 'Todas' },
]

function FiltroEstado({
  value,
  onChange,
  counts,
}: {
  value: Estado
  onChange: (e: Estado) => void
  counts: Record<Estado, number>
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {FILTROS.map((f) => {
        const active = value === f.id
        return (
          <button
            key={f.id}
            onClick={() => onChange(f.id)}
            className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
              active
                ? 'border-foreground bg-foreground text-background'
                : 'border-border bg-background text-muted-foreground hover:bg-muted'
            }`}
          >
            {f.label}
            <span className={`font-mono tabular-nums ${active ? 'opacity-70' : 'opacity-60'}`}>
              {counts[f.id]}
            </span>
          </button>
        )
      })}
    </div>
  )
}

/** Contadores automáticos: ambos números los deriva el backend (fichas =
 *  propiedades `fuente='manual'`, gasto = suma real de tokens facturados), así
 *  que no hay nada que mantener a mano ni que se pueda desfasar. */
function StatsBar({ stats, loading }: { stats: Stats | null; loading: boolean }) {
  const cells: { label: string; value: string; hint?: string }[] = [
    {
      label: 'Fichas Propio generadas',
      value: loading || !stats ? '—' : String(stats.total_fichas),
    },
    {
      label: 'Gasto acumulado',
      value: loading || !stats ? '—' : `US$ ${stats.gasto_usd.toFixed(4)}`,
      hint: stats ? `${stats.llamadas} llamada${stats.llamadas === 1 ? '' : 's'} al modelo` : undefined,
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-3">
      {cells.map((c) => (
        <div key={c.label} className="rounded-2xl border border-border bg-card p-4">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {c.label}
          </p>
          <p className="mt-1 font-mono text-2xl font-semibold tabular-nums text-foreground">
            {c.value}
          </p>
          {c.hint && <p className="mt-0.5 text-[11px] text-muted-foreground">{c.hint}</p>}
        </div>
      ))}
    </div>
  )
}

function FichaRow({
  p,
  defaultOpen,
  selected,
  onSelect,
  onSaved,
  onEnviada,
}: {
  p: Property
  defaultOpen: boolean
  selected: boolean
  onSelect: (checked: boolean) => void
  onSaved: (updated: Property) => void
  onEnviada: (id: string, enviada: boolean) => void
}) {
  const url = fichaUrl(p.id)
  const [open, setOpen] = useState(defaultOpen)
  const [editing, setEditing] = useState(false)
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard no disponible */
    }
  }

  // Firma del mensaje = el agente asignado a ESTA ficha, no el titular global.
  const agente = agenteByEmail(p.agente_email)

  const enviada = esEnviada(p)

  // El `window.open` va PRIMERO y sin await: si esperamos la marca antes de
  // abrir, el navegador ya no lo cuenta como gesto del usuario y bloquea el
  // popup. La marca es informativa — si falla, el envío igual salió.
  const enviarWhatsApp = () => {
    const texto = `Hola! Te comparto esta propiedad:\n\n• ${p.titulo ?? p.direccion} — ${fmtPrice(p)}\n  ${url}\n\n${agente.nombre} · ${agente.inmobiliaria}\n${agente.telefono}`
    window.open(`https://wa.me/?text=${encodeURIComponent(texto)}`, '_blank', 'noopener')
    if (p.id) void marcarEnviadas([p.id]).then((ok) => ok.length > 0 && onEnviada(p.id!, true))
  }

  // Mismo criterio que al marcar: la fila sólo cambia de solapa si el backend
  // confirmó. Si el server no contestó, la ficha sigue figurando como enviada
  // — mentirle al usuario sobre lo que ya mandó es peor que no mover nada.
  const desmarcar = () => {
    if (!p.id) return
    void marcarEnviadas([p.id], false).then((ok) => ok.length > 0 && onEnviada(p.id!, false))
  }

  return (
    <div className="rounded-2xl border border-border bg-card shadow-sm">
      <div className="flex items-center gap-3 px-4 py-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={(e) => onSelect(e.target.checked)}
          disabled={!p.id}
          title={p.id ? 'Seleccionar para eliminar' : 'Esta ficha todavía no está guardada'}
          className="size-4 shrink-0 accent-foreground disabled:opacity-30"
        />
        <FileText className="size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">
            {p.titulo ?? p.direccion}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {fmtPrice(p)}
            {p.direccion ? ` · ${p.direccion}` : ''}
          </p>
        </div>
        <span className="hidden shrink-0 items-center gap-1 rounded-full border border-border bg-muted/50 px-2 py-0.5 text-[11px] text-muted-foreground sm:flex">
          <UserRound className="size-3" />
          {agente.nombre}
        </span>
        {enviada && (
          <span
            title={`Enviada el ${new Date(p.enviada_at!).toLocaleString('es-AR')}`}
            className="hidden shrink-0 items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-600 sm:flex dark:text-emerald-400"
          >
            <Check className="size-3" />
            Enviada
          </span>
        )}
        <button
          onClick={enviarWhatsApp}
          className="flex shrink-0 items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground transition hover:bg-muted"
        >
          <Send className="size-3.5" />
          {enviada ? 'Reenviar' : 'Enviar'}
        </button>
        {enviada && (
          <button
            onClick={desmarcar}
            title="Volver a «Faltan enviar»"
            className="flex shrink-0 items-center gap-1 rounded-lg border border-border bg-background px-2 py-1 text-xs font-medium text-muted-foreground transition hover:bg-muted hover:text-foreground"
          >
            <RotateCcw className="size-3.5" />
          </button>
        )}
        <button
          onClick={() => {
            setEditing(true)
            setOpen(true)
          }}
          className="flex shrink-0 items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground transition hover:bg-muted"
        >
          <Pencil className="size-3.5" />
          Editar
        </button>
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex shrink-0 items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground transition hover:bg-muted"
        >
          {open ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
          {open ? 'Ocultar' : 'Ver ficha'}
        </button>
      </div>

      {/* Link propio + link original */}
      <div className="space-y-1.5 border-t border-border px-4 py-2.5">
        <div className="flex items-center gap-2 rounded-xl border border-border bg-muted/40 px-3 py-2">
          <Link2 className="size-4 shrink-0 text-foreground" />
          <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Ficha propia
          </span>
          <code className="flex-1 truncate text-xs text-foreground">{url}</code>
          <button
            onClick={copy}
            className="flex shrink-0 items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground transition hover:bg-muted"
          >
            {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
            {copied ? 'Copiado' : 'Copiar link'}
          </button>
          <a
            href={url}
            target="_blank"
            rel="noopener"
            className="flex shrink-0 items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground transition hover:bg-muted"
          >
            <ExternalLink className="size-3.5" />
            Abrir
          </a>
        </div>
        {p.url_origen && (
          <div className="flex items-center gap-2 px-3">
            <ExternalLink className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Original
            </span>
            <a
              href={p.url_origen}
              target="_blank"
              rel="noopener"
              className="flex-1 truncate text-xs text-muted-foreground underline-offset-2 transition hover:text-foreground hover:underline"
            >
              {p.url_origen}
            </a>
          </div>
        )}
      </div>

      {open && (
        <div className="border-t border-border p-4">
          {editing && p.id ? (
            <FichaEditor
              p={p}
              onSaved={(updated) => {
                onSaved(updated)
                setEditing(false)
              }}
              onCancel={() => setEditing(false)}
            />
          ) : (
            <PropertyFicha p={p} />
          )}
        </div>
      )}
    </div>
  )
}

export default function FichaPropioPage() {
  const [items, setItems] = useState<Property[]>([])
  const [newIds, setNewIds] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [raw, setRaw] = useState('')
  // Agente a cuyo nombre se genera la ficha. Titular por defecto.
  const [agenteEmail, setAgenteEmail] = useState<string>(AGENTES[0].email)
  const [importing, setImporting] = useState(false)
  const [errors, setErrors] = useState<{ url: string; error: string }[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [statsLoading, setStatsLoading] = useState(true)
  const [estado, setEstado] = useState<Estado>('todas')
  const [selected, setSelected] = useState<Set<string>>(new Set())

  // Se llama al montar y después de cada generación: así los contadores se
  // actualizan solos, sin que el usuario tenga que refrescar.
  const refreshStats = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/v1/properties/ficha-propio/stats`)
      if (res.ok) setStats((await res.json()) as Stats)
    } catch {
      /* backend no disponible — el contador queda con el último valor conocido */
    } finally {
      setStatsLoading(false)
    }
  }, [])

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API}/api/v1/properties?fuente=manual&limit=100`)
        if (res.ok) {
          const data = await res.json()
          setItems((data.properties as Property[]) ?? [])
        }
      } catch {
        /* backend no disponible */
      } finally {
        setLoading(false)
      }
    }
    load()
    refreshStats()
  }, [refreshStats])

  const counts: Record<Estado, number> = {
    pendientes: items.filter((p) => !esEnviada(p)).length,
    enviadas: items.filter(esEnviada).length,
    todas: items.length,
  }
  const visibles =
    estado === 'todas' ? items : items.filter((p) => (estado === 'enviadas' ? esEnviada(p) : !esEnviada(p)))

  // Sólo se puede borrar lo que está guardado Y a la vista: si el usuario
  // cambia de solapa, lo que dejó marcado en la otra no se lleva puesto.
  const selectedIds = visibles.map((p) => p.id).filter((id): id is string => !!id && selected.has(id))

  const toggleSelect = (id: string, checked: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })

  const marcarLocal = (id: string, enviada: boolean) =>
    setItems((prev) =>
      prev.map((it) =>
        it.id === id ? { ...it, enviada_at: enviada ? new Date().toISOString() : null } : it,
      ),
    )

  const urls = Array.from(
    new Set(
      raw
        .split(/[\n\s,]+/)
        .map((u) => u.trim())
        .filter((u) => /^https?:\/\//.test(u)),
    ),
  )

  const generar = async () => {
    if (urls.length === 0 || importing) return
    setImporting(true)
    setErrors([])
    try {
      const res = await fetch(`${API}/api/v1/properties/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ urls: urls.slice(0, 10), agente_email: agenteEmail }),
      })
      if (!res.ok) {
        const detail = (await res.json().catch(() => null))?.detail
        setErrors([{ url: '', error: detail ?? 'No se pudo importar. Probá de nuevo.' }])
        return
      }
      const data = await res.json()
      const results = (data.results as ImportResult[]) ?? []

      const failed = results.filter((r): r is Extract<ImportResult, { status: 'error' }> => r.status === 'error')
      setErrors(failed.map((r) => ({ url: r.url, error: r.error })))

      const ok = results.filter((r): r is Extract<ImportResult, { status: 'ok' }> => r.status === 'ok')
      // Enriquecer descripción → amenities/destacados, igual que el flujo de fichas
      const enriched = await Promise.all(ok.map((r) => enrichFicha(r.property)))

      setItems((prev) => {
        const known = new Set(prev.map((p) => p.id))
        const nuevos = enriched.filter((p) => p.id && !known.has(p.id))
        const updated = prev.map((p) => enriched.find((e) => e.id === p.id) ?? p)
        return [...nuevos, ...updated]
      })
      setNewIds(new Set(enriched.map((p) => p.id).filter((id): id is string => !!id)))
      if (failed.length === 0) setRaw('')
      // El import y el enrich ya gastaron tokens: recontamos para que el gasto
      // que se muestra sea el de recién, no el de la carga de página.
      refreshStats()
    } catch {
      setErrors([{ url: '', error: 'No se pudo conectar con el servidor.' }])
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-background text-foreground">
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto w-full max-w-3xl">
          <h1 className="text-xl font-semibold tracking-tight">Ficha Propio</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Pegá links de propiedades de cualquier portal (Zonaprop, Argenprop, MercadoLibre,
            RE/MAX…) y generá fichas con tu marca y tus datos, listas para compartir.
          </p>
        </div>
      </header>

      <div className="mx-auto w-full max-w-3xl flex-1 space-y-6 p-6">
        <StatsBar stats={stats} loading={statsLoading} />

        {/* Carga de links */}
        <div className="space-y-3 rounded-2xl border border-border bg-card p-4 shadow-sm">
          <AgenteSelector selected={agenteEmail} onSelect={setAgenteEmail} disabled={importing} />
          <textarea
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            rows={3}
            placeholder={'https://www.zonaprop.com.ar/propiedades/...\nhttps://www.argenprop.com/...\nUn link por línea (máx. 10)'}
            className="w-full resize-y rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground/20"
          />
          {errors.map((e, i) => (
            <p key={i} className="text-xs text-destructive">
              {e.url ? `${e.url}: ` : ''}
              {e.error}
            </p>
          ))}
          <button
            onClick={generar}
            disabled={importing || urls.length === 0}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-foreground px-3 py-2 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:opacity-40"
          >
            {importing ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
            {importing
              ? `Generando ${urls.length === 1 ? 'ficha' : `${urls.length} fichas`}… puede tardar unos segundos`
              : `Generar Ficha Propio${urls.length > 1 ? ` (${urls.length})` : ''} · ${agenteByEmail(agenteEmail).nombre}`}
          </button>
        </div>

        {/* Fichas generadas */}
        <div>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {loading
                ? 'Cargando...'
                : `${visibles.length} ficha${visibles.length === 1 ? '' : 's'} propia${visibles.length === 1 ? '' : 's'}`}
            </p>
            {!loading && items.length > 0 && (
              <FiltroEstado value={estado} onChange={setEstado} counts={counts} />
            )}
          </div>

          {!loading && items.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-10 text-center">
              <FileText className="size-8 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                Todavía no generaste ninguna ficha. Pegá un link arriba para empezar.
              </p>
            </div>
          ) : !loading && visibles.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-10 text-center">
              <FileText className="size-8 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                {estado === 'enviadas'
                  ? 'Todavía no enviaste ninguna ficha.'
                  : 'No queda ninguna ficha pendiente de enviar.'}
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {visibles.map((p, i) => (
                <FichaRow
                  key={p.id ?? i}
                  p={p}
                  defaultOpen={!!p.id && newIds.has(p.id)}
                  selected={!!p.id && selected.has(p.id)}
                  onSelect={(checked) => p.id && toggleSelect(p.id, checked)}
                  onSaved={(updated) =>
                    setItems((prev) => prev.map((it) => (it.id === updated.id ? updated : it)))
                  }
                  onEnviada={marcarLocal}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {selectedIds.length > 0 && (
        <div className="sticky bottom-0 z-10">
          <SelectionBar
            count={selectedIds.length}
            ids={selectedIds}
            onClear={() => setSelected(new Set())}
            onDeleted={(ids) => {
              const gone = new Set(ids)
              setItems((prev) => prev.filter((it) => !it.id || !gone.has(it.id)))
              setSelected(new Set())
              // El contador de "Fichas Propio generadas" lo deriva el backend
              // de las propiedades `fuente='manual'`: si borramos, hay que
              // volver a pedirlo o queda mostrando fichas que ya no existen.
              refreshStats()
            }}
          />
        </div>
      )}
    </div>
  )
}
