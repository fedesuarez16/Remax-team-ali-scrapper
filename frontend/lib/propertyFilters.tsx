'use client'
// Shared Zonaprop-style filters, reused across three surfaces:
//   - /properties (base list)  → serialized to query params, filtered server-side
//   - /properties?job_id=…     → filtered client-side over the loaded job results
//   - /map                     → filtered client-side to show/hide markers
// The `matchesFilter` predicate mirrors the backend semantics in
// backend/app/api/v1/properties.py::list_properties so client and server agree.

export type Filter = {
  fuente: string
  tipo_operacion: string
  tipo_propiedad: string
  moneda: string
  precio_min: string
  precio_max: string
  ambientes_min: string
  banos_min: string
  cocheras_min: string
  m2_min: string
  m2_max: string
  q: string
  /** '' = todas, 'true' = sólo enviadas, 'false' = sólo pendientes. */
  enviada: string
  /** Gran La Plata locality slug — '' = todas. See `ZONAS`. */
  zona: string
}

export const EMPTY_FILTER: Filter = {
  fuente: '', tipo_operacion: '', tipo_propiedad: '', moneda: '',
  precio_min: '', precio_max: '', ambientes_min: '', banos_min: '',
  cocheras_min: '', m2_min: '', m2_max: '', q: '', enviada: '', zona: '',
}

export const ENVIADAS = [
  { value: '', label: 'Todas' },
  { value: 'false', label: 'Sin enviar' },
  { value: 'true', label: 'Enviadas' },
]

export const FUENTES = [
  { value: '', label: 'Todas' },
  { value: 'zonaprop', label: 'ZonaProp' },
  { value: 'mercadolibre', label: 'MercadoLibre' },
  { value: 'argenprop', label: 'Argenprop' },
  { value: 'remax', label: 'RE/MAX' },
  { value: 'inmobusqueda', label: 'InmoBusqueda' },
  { value: 'mudafy', label: 'Mudafy' },
  { value: 'century21', label: 'CENTURY 21' },
  { value: 'googlemaps', label: 'Sitios web' },
]

export const OPERACIONES = [
  { value: '', label: 'Todas' },
  { value: 'venta', label: 'Venta' },
  { value: 'alquiler', label: 'Alquiler' },
]

export const TIPOS_PROPIEDAD = [
  { value: '', label: 'Todos' },
  { value: 'departamento', label: 'Departamento' },
  { value: 'casa', label: 'Casa' },
  { value: 'ph', label: 'PH' },
  { value: 'local', label: 'Local' },
  { value: 'oficina', label: 'Oficina' },
  { value: 'terreno', label: 'Terreno' },
  { value: 'otro', label: 'Otro' },
]

// Zona catalogue — mirrors backend/app/services/zona.py::ZONA_TERMS. The
// properties table has no locality column, so a zona is whatever locality the
// address itself names.
const ZONA_TERMS: Record<string, string[]> = {
  la_plata: ['la plata'],
  city_bell: ['city bell', 'citybell'],
  gonnet: ['gonnet'],
  villa_elisa: ['villa elisa'],
  hudson: ['hudson'],
}

// "La Plata" is also the partido containing City Bell, Gonnet and Villa Elisa
// ("City Bell, La Plata"), so it has to negate its siblings or it swallows
// them. The reverse never applies: a City Bell address IS a City Bell listing.
const ZONA_EXCLUDES: Record<string, string[]> = {
  la_plata: ['city bell', 'citybell', 'gonnet', 'villa elisa', 'hudson'],
}

export const ZONAS = [
  { value: '', label: 'Todas' },
  { value: 'la_plata', label: 'La Plata' },
  { value: 'city_bell', label: 'City Bell' },
  { value: 'gonnet', label: 'Gonnet' },
  { value: 'villa_elisa', label: 'Villa Elisa' },
  { value: 'hudson', label: 'Hudson' },
]

export const MONEDAS = [
  { value: '', label: 'Todas' },
  { value: 'USD', label: 'USD' },
  { value: 'ARS', label: 'ARS' },
]

const minOptions = (label: string, max: number) => [
  { value: '', label: 'Todos' },
  ...Array.from({ length: max }, (_, i) => ({
    value: String(i + 1),
    label: `${i + 1}+ ${label}`,
  })),
]

export const AMBIENTES = minOptions('amb.', 5)
export const BANOS = minOptions('baños', 4)
export const COCHERAS = minOptions('coch.', 3)

/** Any object carrying the filterable columns — Property, MapProperty, job rows. */
export type FilterableProperty = {
  fuente?: string | null
  tipo_operacion?: string | null
  tipo_propiedad?: string | null
  moneda?: string | null
  precio?: number | null
  ambientes?: number | null
  banos?: number | null
  cocheras?: number | null
  m2_total?: number | null
  titulo?: string | null
  direccion?: string | null
  enviada_at?: string | null
}

const num = (s: string): number | null => {
  const t = s.trim()
  if (!t) return null
  const v = Number(t)
  return Number.isFinite(v) ? v : null
}

const eqStr = (val: string | null | undefined, want: string): boolean =>
  !want || String(val ?? '').toLowerCase() === want.toLowerCase()

// Range bounds mirror PostgREST gte/lte: a null column value never satisfies a
// bound (SQL NULL comparisons are false), so it drops out once a bound is set.
const gte = (val: number | null | undefined, min: number | null): boolean =>
  min == null || (val != null && val >= min)
const lte = (val: number | null | undefined, max: number | null): boolean =>
  max == null || (val != null && val <= max)

// Mirrors the backend clause. It only reads `direccion` — `direccion_norm` is
// derived from it (lowercase + accent-strip) and no zona term carries an
// accent, so the lowercased raw address is an equivalent haystack.
const matchesZona = (p: FilterableProperty, zona: string): boolean => {
  const terms = ZONA_TERMS[zona]
  if (!terms) return true
  const hay = (p.direccion ?? '').toLowerCase()
  if (!terms.some((t) => hay.includes(t))) return false
  return !(ZONA_EXCLUDES[zona] ?? []).some((t) => hay.includes(t))
}

/** True when the property satisfies every active filter clause. */
export function matchesFilter(p: FilterableProperty, f: Filter): boolean {
  if (!matchesZona(p, f.zona)) return false
  if (!eqStr(p.fuente, f.fuente)) return false
  if (!eqStr(p.tipo_operacion, f.tipo_operacion)) return false
  if (!eqStr(p.tipo_propiedad, f.tipo_propiedad)) return false
  if (!eqStr(p.moneda, f.moneda)) return false
  if (!gte(p.precio, num(f.precio_min))) return false
  if (!lte(p.precio, num(f.precio_max))) return false
  if (!gte(p.ambientes, num(f.ambientes_min))) return false
  if (!gte(p.banos, num(f.banos_min))) return false
  if (!gte(p.cocheras, num(f.cocheras_min))) return false
  if (!gte(p.m2_total, num(f.m2_min))) return false
  if (!lte(p.m2_total, num(f.m2_max))) return false
  // Espeja el `is`/`not.is` de enviada_at que aplica el backend.
  if (f.enviada === 'true' && !p.enviada_at) return false
  if (f.enviada === 'false' && p.enviada_at) return false
  const q = f.q.trim().toLowerCase()
  if (q) {
    const hay = `${p.titulo ?? ''} ${p.direccion ?? ''}`.toLowerCase()
    if (!hay.includes(q)) return false
  }
  return true
}

export const hasActiveFilters = (f: Filter): boolean =>
  Object.values(f).some((v) => v.trim() !== '')

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

function RangeInputs({
  label, min, max, onMin, onMax,
}: {
  label: string
  min: string
  max: string
  onMin: (v: string) => void
  onMax: (v: string) => void
}) {
  const inputCls =
    'w-20 rounded-lg border border-border bg-card px-2 py-1 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20'
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground">{label}:</span>
      <input
        type="number"
        min={0}
        value={min}
        onChange={(e) => onMin(e.target.value)}
        placeholder="Mín"
        className={inputCls}
      />
      <span className="text-xs text-muted-foreground">–</span>
      <input
        type="number"
        min={0}
        value={max}
        onChange={(e) => onMax(e.target.value)}
        placeholder="Máx"
        className={inputCls}
      />
    </div>
  )
}

/** The two-row Zonaprop-style filter bar. Shared by /properties and /map. */
export function FilterBar({
  filter, onChange,
}: {
  filter: Filter
  onChange: (next: Filter) => void
}) {
  const set = (key: keyof Filter) => (v: string) => onChange({ ...filter, [key]: v })
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <input
          type="search"
          value={filter.q}
          onChange={(e) => set('q')(e.target.value)}
          placeholder="Buscar por ubicación o título..."
          className="w-56 rounded-lg border border-border bg-card px-2.5 py-1 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20"
        />
        <FilterSelect label="Zona" value={filter.zona} options={ZONAS} onChange={set('zona')} />
        <FilterSelect label="Fuente" value={filter.fuente} options={FUENTES} onChange={set('fuente')} />
        <FilterSelect label="Operación" value={filter.tipo_operacion} options={OPERACIONES} onChange={set('tipo_operacion')} />
        <FilterSelect label="Tipo" value={filter.tipo_propiedad} options={TIPOS_PROPIEDAD} onChange={set('tipo_propiedad')} />
        <FilterSelect label="Ambientes" value={filter.ambientes_min} options={AMBIENTES} onChange={set('ambientes_min')} />
        <FilterSelect label="Baños" value={filter.banos_min} options={BANOS} onChange={set('banos_min')} />
        <FilterSelect label="Cocheras" value={filter.cocheras_min} options={COCHERAS} onChange={set('cocheras_min')} />
        <FilterSelect label="Envío" value={filter.enviada} options={ENVIADAS} onChange={set('enviada')} />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <FilterSelect label="Moneda" value={filter.moneda} options={MONEDAS} onChange={set('moneda')} />
        <RangeInputs
          label="Precio"
          min={filter.precio_min}
          max={filter.precio_max}
          onMin={set('precio_min')}
          onMax={set('precio_max')}
        />
        <RangeInputs
          label="Superficie (m²)"
          min={filter.m2_min}
          max={filter.m2_max}
          onMin={set('m2_min')}
          onMax={set('m2_max')}
        />
        {hasActiveFilters(filter) && (
          <button
            onClick={() => onChange(EMPTY_FILTER)}
            className="text-xs font-medium text-muted-foreground underline-offset-2 transition hover:text-foreground hover:underline"
          >
            Limpiar filtros
          </button>
        )}
      </div>
    </div>
  )
}
