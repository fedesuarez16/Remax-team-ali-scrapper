'use client'
import { Suspense, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { Check, ChevronLeft, ChevronRight, Database, Loader2, MapPin, RefreshCw, Send } from 'lucide-react'
import { PropertyCard } from '@/components/chat/PropertyCard'
import { SelectCheckbox } from '@/components/properties/SelectCheckbox'
import { SelectionBar } from '@/components/properties/SelectionBar'
import type { Property } from '@/hooks/useSSEStream'
import { enrichFicha, guardarSeleccion, marcarEnviadas } from '@/lib/ficha'
import { sortVentaFirst } from '@/lib/operacion'
import { EMPTY_FILTER, FilterBar, matchesFilter, type Filter } from '@/lib/propertyFilters'
import { cn } from '@/lib/utils'

const keyFor = (p: Property, i: number) => p.id ?? String(i)

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

const PAGE_SIZE = 50

function PropertiesPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const jobId = searchParams.get('job_id')

  const [properties, setProperties] = useState<Property[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>(EMPTY_FILTER)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [preparing, setPreparing] = useState(false)

  const toggle = (key: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  // Base list is already filtered server-side; job results arrive unfiltered
  // (full `properties(*)` for one job) so we narrow them client-side here.
  const shown = jobId ? properties.filter((p) => matchesFilter(p, filter)) : properties

  const enviadasCount = shown.filter((p) => p.enviada_at).length

  const allSelected =
    shown.length > 0 && shown.every((p, i) => selected.has(keyFor(p, i)))

  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(shown.map((p, i) => keyFor(p, i))))

  // Sólo las persistidas se pueden borrar: `keyFor` cae al índice cuando no hay id.
  const selectedIds = shown
    .filter((p, i) => selected.has(keyFor(p, i)))
    .map((p) => p.id)
    .filter((id): id is string => Boolean(id))

  const onDeleted = (removed: string[]) => {
    const gone = new Set(removed)
    setProperties((prev) => prev.filter((p) => !(p.id && gone.has(p.id))))
    setTotal((prev) => Math.max(0, prev - gone.size))
    setSelected(new Set())
  }

  const prepararYEnviar = async () => {
    const chosen = shown.filter((p, i) => selected.has(keyFor(p, i)))
    if (chosen.length === 0 || preparing) return
    setPreparing(true)
    try {
      // Parse each description with the LLM (amenities + destacados) before building the ficha.
      const enriched = await Promise.all(chosen.map(enrichFicha))
      // Dejar sellado el envío: al volver a esta búsqueda las enviadas se
      // distinguen de las pendientes. Si falla, el envío sigue igual.
      const marcadas = await marcarEnviadas(chosen.map((p) => p.id ?? ''))
      if (marcadas.length > 0) {
        const sent = new Set(marcadas)
        const stamp = new Date().toISOString()
        setProperties((prev) =>
          prev.map((p) => (p.id && sent.has(p.id) ? { ...p, enviada_at: stamp } : p))
        )
      }
      guardarSeleccion(enriched)
      router.push('/ficha')
    } finally {
      setPreparing(false)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const load = async (targetPage = page) => {
    setLoading(true)
    setError(null)
    try {
      let url: string
      if (jobId) {
        url = `${API}/api/v1/scraping/${encodeURIComponent(jobId)}/properties`
      } else {
        const params = new URLSearchParams({
          limit: String(PAGE_SIZE),
          offset: String(targetPage * PAGE_SIZE),
        })
        for (const [key, value] of Object.entries(filter)) {
          if (value.trim()) params.set(key, value.trim())
        }
        url = `${API}/api/v1/properties?${params}`
      }
      const res = await fetch(url)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (data.error) throw new Error(data.error)
      setProperties(sortVentaFirst<Property>(data.properties ?? []))
      setTotal(data.total ?? 0)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error desconocido')
      setProperties([])
    } finally {
      setLoading(false)
    }
  }

  const goToPage = (p: number) => {
    setPage(p)
    void load(p)
  }

  useEffect(() => {
    setPage(0)
    setSelected(new Set())
    // Debounced so typing in the free-text/numeric filters doesn't fire a
    // request per keystroke.
    const t = setTimeout(() => void load(0), 350)
    return () => clearTimeout(t)
  }, [filter, jobId])

  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      {/* Header */}
      <header className="border-b border-border px-6 py-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex size-8 items-center justify-center rounded-lg bg-foreground">
              <Database className="size-4 text-background" />
            </div>
            <div>
              <h1 className="text-sm font-semibold">
                {jobId ? 'Resultados de búsqueda' : 'Propiedades'}
              </h1>
              <p className="text-xs text-muted-foreground">
                {loading
                  ? 'Cargando...'
                  : `${jobId ? shown.length : total} propiedades${
                      enviadasCount > 0 ? ` · ${enviadasCount} enviada${enviadasCount === 1 ? '' : 's'}` : ''
                    }`}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={toggleAll}
              disabled={loading || shown.length === 0}
              className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-muted disabled:opacity-50"
            >
              <Check className="size-3.5" />
              {allSelected ? 'Deseleccionar todo' : 'Seleccionar todo'}
            </button>
            <button
              onClick={() => load()}
              disabled={loading}
              className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-muted disabled:opacity-50"
            >
              <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} />
              Actualizar
            </button>
          </div>
        </div>

        <div className="mt-4">
          <FilterBar filter={filter} onChange={setFilter} />
        </div>
      </header>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading && properties.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <p className="text-sm text-foreground">No se pudieron cargar las propiedades</p>
            <p className="text-xs text-muted-foreground">{error}</p>
          </div>
        ) : shown.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <Database className="size-10 text-muted-foreground/40" />
            <div>
              <p className="text-sm font-medium text-foreground">
                {properties.length > 0
                  ? 'Ninguna propiedad coincide con los filtros'
                  : jobId
                    ? 'Esta búsqueda no tiene propiedades guardadas'
                    : 'Aún no hay propiedades guardadas'}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {properties.length > 0
                  ? 'Ajustá o limpiá los filtros para ver más resultados.'
                  : jobId
                    ? 'Las propiedades se guardan al completarse la búsqueda.'
                    : 'Ejecutá una búsqueda en el chat para empezar a llenar la base.'}
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {shown.map((p, i) => {
              const key = keyFor(p, i)
              const isSel = selected.has(key)
              return (
                <div
                  key={key}
                  className={cn(
                    'relative rounded-2xl transition',
                    isSel && 'ring-2 ring-foreground ring-offset-2 ring-offset-background'
                  )}
                >
                  <SelectCheckbox selected={isSel} onToggle={() => toggle(key)} />
                  {p.id && p.lat != null && p.lng != null && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        router.push(`/map?focus=${encodeURIComponent(p.id!)}`)
                      }}
                      aria-label="Ver en el mapa"
                      title="Ver en el mapa"
                      className="absolute right-2 top-2 z-20 flex size-6 items-center justify-center rounded-md border border-border bg-background/90 text-foreground shadow-sm backdrop-blur-sm transition hover:border-foreground/40 hover:bg-muted"
                    >
                      <MapPin className="size-3.5" />
                    </button>
                  )}
                  <PropertyCard p={p} />
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Action bar — selection → borrar / ficha */}
      {selected.size > 0 && (
        <SelectionBar
          count={selected.size}
          ids={selectedIds}
          onClear={() => setSelected(new Set())}
          onDeleted={onDeleted}
        >
          <button
            onClick={prepararYEnviar}
            disabled={preparing}
            className="flex items-center gap-2 rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:opacity-60"
          >
            {preparing ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
            {preparing ? 'Preparando fichas...' : 'Preparar y enviar'}
          </button>
        </SelectionBar>
      )}

      {/* Pagination — only when not scoped to a job and there's more than one page */}
      {!jobId && !error && totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 border-t border-border px-6 py-3">
          <button
            onClick={() => goToPage(page - 1)}
            disabled={page === 0 || loading}
            className="flex items-center gap-1 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-muted disabled:opacity-40"
          >
            <ChevronLeft className="size-3.5" />
            Anterior
          </button>

          <span className="text-xs text-muted-foreground">
            Página <span className="font-medium text-foreground">{page + 1}</span> de{' '}
            <span className="font-medium text-foreground">{totalPages}</span>
          </span>

          <button
            onClick={() => goToPage(page + 1)}
            disabled={page >= totalPages - 1 || loading}
            className="flex items-center gap-1 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-muted disabled:opacity-40"
          >
            Siguiente
            <ChevronRight className="size-3.5" />
          </button>
        </div>
      )}
    </div>
  )
}

export default function PropertiesPageWrapper() {
  return (
    <Suspense fallback={null}>
      <PropertiesPage />
    </Suspense>
  )
}
