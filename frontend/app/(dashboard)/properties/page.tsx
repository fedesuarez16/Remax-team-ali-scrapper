'use client'
import { useEffect, useState } from 'react'
import { Database, Loader2, RefreshCw } from 'lucide-react'
import { PropertyCard } from '@/components/chat/PropertyCard'
import type { Property } from '@/hooks/useSSEStream'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

type Filter = { fuente: string; tipo_operacion: string }

const FUENTES = [
  { value: '', label: 'Todas' },
  { value: 'zonaprop', label: 'ZonaProp' },
  { value: 'mercadolibre', label: 'MercadoLibre' },
  { value: 'googlemaps', label: 'Sitios web' },
]

const OPERACIONES = [
  { value: '', label: 'Todas' },
  { value: 'venta', label: 'Venta' },
  { value: 'alquiler', label: 'Alquiler' },
]

export default function PropertiesPage() {
  const [properties, setProperties] = useState<Property[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>({ fuente: '', tipo_operacion: '' })

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ limit: '200' })
      if (filter.fuente) params.set('fuente', filter.fuente)
      if (filter.tipo_operacion) params.set('tipo_operacion', filter.tipo_operacion)
      const res = await fetch(`${API}/api/v1/properties?${params}`)
      const data = await res.json()
      if (data.error) throw new Error(data.error)
      setProperties(data.properties ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error desconocido')
      setProperties([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [filter])

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
              <h1 className="text-sm font-semibold">Propiedades</h1>
              <p className="text-xs text-muted-foreground">
                {loading ? 'Cargando...' : `${properties.length} propiedades`}
              </p>
            </div>
          </div>

          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-muted disabled:opacity-50"
          >
            <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} />
            Actualizar
          </button>
        </div>

        {/* Filters */}
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <FilterSelect
            label="Fuente"
            value={filter.fuente}
            options={FUENTES}
            onChange={(v) => setFilter((f) => ({ ...f, fuente: v }))}
          />
          <FilterSelect
            label="Operación"
            value={filter.tipo_operacion}
            options={OPERACIONES}
            onChange={(v) => setFilter((f) => ({ ...f, tipo_operacion: v }))}
          />
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
        ) : properties.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <Database className="size-10 text-muted-foreground/40" />
            <div>
              <p className="text-sm font-medium text-foreground">Aún no hay propiedades guardadas</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Ejecutá una búsqueda en el chat para empezar a llenar la base.
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {properties.map((p, i) => <PropertyCard key={i} p={p} />)}
          </div>
        )}
      </div>
    </div>
  )
}

function FilterSelect({
  label, value, options, onChange,
}: {
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (v: string) => void
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground">{label}:</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-border bg-card px-2.5 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  )
}
