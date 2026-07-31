'use client'
import { Suspense, useEffect, useState } from 'react'
import dynamic from 'next/dynamic'
import { useSearchParams } from 'next/navigation'
import { Loader2, MapPin, SlidersHorizontal } from 'lucide-react'
import type { CarteraProperty, MapProperty } from '@/components/map/PropertyMap'
import { EMPTY_FILTER, FilterBar, hasActiveFilters, type Filter } from '@/lib/propertyFilters'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

// Leaflet touches `window` at module load — must stay client-only.
const PropertyMap = dynamic(() => import('@/components/map/PropertyMap'), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center">
      <Loader2 className="size-6 animate-spin text-muted-foreground" />
    </div>
  ),
})

function MapPage() {
  const searchParams = useSearchParams()
  const focusId = searchParams.get('focus')
  const [properties, setProperties] = useState<MapProperty[]>([])
  const [cartera, setCartera] = useState<CarteraProperty[]>([])
  const [missing, setMissing] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>(EMPTY_FILTER)
  const [filtersOpen, setFiltersOpen] = useState(false)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      setError(null)
      try {
        const res = await fetch(`${API}/api/v1/properties/map`)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json()
        if (data.error) throw new Error(data.error)
        setProperties(data.properties ?? [])
        setCartera(data.cartera ?? [])
        setMissing((data.missing ?? 0) + (data.cartera_missing ?? 0))
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Error desconocido')
        setProperties([])
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [])

  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      <header className="border-b border-border px-6 py-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex size-8 items-center justify-center rounded-lg bg-foreground">
              <MapPin className="size-4 text-background" />
            </div>
            <div>
              <h1 className="text-sm font-semibold">Mapa</h1>
              <p className="text-xs text-muted-foreground">
                {loading
                  ? 'Cargando...'
                  : `${properties.length} scrapeadas · ${cartera.length} cartera · ${missing} sin ubicación`}
              </p>
            </div>
          </div>

          <button
            onClick={() => setFiltersOpen((v) => !v)}
            className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
              filtersOpen || hasActiveFilters(filter)
                ? 'border-foreground bg-foreground text-background'
                : 'border-border bg-card text-foreground hover:bg-muted'
            }`}
          >
            <SlidersHorizontal className="size-3.5" />
            Filtros{hasActiveFilters(filter) ? ' · activos' : ''}
          </button>
        </div>

        {filtersOpen && (
          <div className="mt-4">
            <FilterBar filter={filter} onChange={setFilter} />
          </div>
        )}
      </header>

      <div className="relative flex-1">
        {error ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <p className="text-sm text-foreground">No se pudo cargar el mapa</p>
            <p className="text-xs text-muted-foreground">{error}</p>
          </div>
        ) : loading ? (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <PropertyMap properties={properties} cartera={cartera} focusId={focusId ?? undefined} filter={filter} />
        )}
      </div>
    </div>
  )
}

export default function MapPageWrapper() {
  return (
    <Suspense fallback={null}>
      <MapPage />
    </Suspense>
  )
}
