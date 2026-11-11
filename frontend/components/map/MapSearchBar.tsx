'use client'
import { useMemo, useRef, useState } from 'react'
import {
  Building2,
  Crosshair,
  Hexagon,
  Landmark,
  Loader2,
  LocateFixed,
  MapPin,
  Route,
  Search,
  Shapes,
  Type,
  X,
} from 'lucide-react'
import { FUENTES, ZONAS } from '@/lib/propertyFilters'
import { usePlaceSearch, type Place, type PlaceKind } from '@/hooks/usePlaceSearch'
import type { SavedZone } from '@/hooks/useSavedZones'
import type { BarrioCerrado } from '@/hooks/useBarriosCerrados'

/** What the user chose in the search bar — the map decides how to apply it. */
export type SearchPick =
  | { type: 'place'; place: Place }
  | { type: 'polygon'; label: string; polygon: [number, number][]; savedZone?: SavedZone }
  | { type: 'zona'; value: string; label: string }
  | { type: 'fuente'; value: string; label: string }
  | { type: 'palabra'; value: string }
  | { type: 'my-location' }
  | { type: 'pick-on-map' }

type Item = { key: string; section: string; label: string; hint?: string; icon: React.ReactNode; pick: SearchPick }

const LOCAL_LIMIT = 4
const ICON = 'size-4 shrink-0 text-muted-foreground'

const PLACE_ICONS: Record<PlaceKind, React.ReactNode> = {
  address: <MapPin className={ICON} />,
  street: <Route className={ICON} />,
  area: <Hexagon className={ICON} />,
  poi: <Landmark className={ICON} />,
}
const PLACE_HINTS: Record<PlaceKind, string> = {
  address: 'Dirección',
  street: 'Calle',
  area: 'Barrio / localidad',
  poi: 'Lugar',
}

const norm = (s: string) =>
  s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim()

function localMatches<T>(list: T[], q: string, text: (t: T) => string): T[] {
  return list.filter((t) => norm(text(t)).includes(q)).slice(0, LOCAL_LIMIT)
}

export function MapSearchBar({
  savedZones,
  barrios,
  onPick,
}: {
  savedZones: SavedZone[]
  barrios: BarrioCerrado[]
  onPick: (pick: SearchPick) => void
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const places = usePlaceSearch(query)

  const items = useMemo<Item[]>(() => {
    const q = norm(query)
    if (!q) {
      return [
        { key: 'me', section: 'Ubicación', label: 'Usar mi ubicación', icon: <LocateFixed className={ICON} />, pick: { type: 'my-location' } },
        { key: 'click', section: 'Ubicación', label: 'Elegir un punto en el mapa', icon: <Crosshair className={ICON} />, pick: { type: 'pick-on-map' } },
      ]
    }
    const out: Item[] = []
    for (const place of places.places) {
      out.push({
        key: `place-${place.kind}-${place.id}`,
        section: 'Lugares',
        label: place.label,
        hint: PLACE_HINTS[place.kind],
        icon: PLACE_ICONS[place.kind],
        pick: { type: 'place', place },
      })
    }
    for (const b of localMatches(barrios.filter((b) => b.polygon && b.polygon.length >= 3), q, (b) => [b.nombre, ...b.aliases].join(' '))) {
      out.push({
        key: `barrio-${b.id}`,
        section: 'Barrios cerrados',
        label: b.nombre,
        hint: b.localidad,
        icon: <Shapes className={ICON} />,
        pick: { type: 'polygon', label: b.nombre, polygon: b.polygon! },
      })
    }
    for (const z of localMatches(savedZones, q, (z) => z.name)) {
      out.push({
        key: `saved-${z.id}`,
        section: 'Zonas guardadas',
        label: z.name,
        icon: <Hexagon className={ICON} />,
        pick: { type: 'polygon', label: z.name, polygon: z.polygon, savedZone: z },
      })
    }
    for (const z of localMatches(ZONAS.filter((z) => z.value), q, (z) => z.label)) {
      out.push({
        key: `zona-${z.value}`,
        section: 'Zonas',
        label: z.label,
        hint: 'Filtrar por zona',
        icon: <MapPin className={ICON} />,
        pick: { type: 'zona', value: z.value, label: z.label },
      })
    }
    for (const f of localMatches(FUENTES.filter((f) => f.value), q, (f) => `${f.label} ${f.value}`)) {
      out.push({
        key: `fuente-${f.value}`,
        section: 'Inmobiliarias',
        label: f.label,
        hint: 'Filtrar por inmobiliaria',
        icon: <Building2 className={ICON} />,
        pick: { type: 'fuente', value: f.value, label: f.label },
      })
    }
    out.push({
      key: 'palabra',
      section: 'Palabras',
      label: `Buscar “${query.trim()}”`,
      hint: 'En títulos y direcciones',
      icon: <Type className={ICON} />,
      pick: { type: 'palabra', value: query.trim() },
    })
    return out
  }, [query, places.places, barrios, savedZones])

  const choose = (item: Item | undefined) => {
    if (!item) return
    onPick(item.pick)
    setOpen(false)
    setQuery(item.pick.type === 'place' || item.pick.type === 'polygon' ? item.label : '')
    inputRef.current?.blur()
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setOpen(true)
      setActive((i) => Math.min(i + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      choose(items[active])
    } else if (e.key === 'Escape') {
      setOpen(false)
      inputRef.current?.blur()
    }
  }

  return (
    <div className="relative w-full">
      <div className="flex items-center gap-2 rounded-xl border border-border bg-background px-3 py-2 shadow-md focus-within:ring-1 focus-within:ring-foreground/20">
        <Search className="size-4 shrink-0 text-muted-foreground" />
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls="map-search-results"
          aria-label="Buscar en el mapa"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setActive(0)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          // Delay so a click on a suggestion lands before the list unmounts.
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={onKeyDown}
          placeholder="Buscar calle, barrio, dirección, inmobiliaria..."
          className="min-w-0 flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
        />
        {places.loading && <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" />}
        {query && (
          <button
            type="button"
            aria-label="Limpiar búsqueda"
            onClick={() => {
              setQuery('')
              setActive(0)
              inputRef.current?.focus()
            }}
            className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        )}
      </div>

      {open && items.length > 0 && (
        <ul
          id="map-search-results"
          role="listbox"
          className="absolute left-0 right-0 top-full mt-1 max-h-[60vh] overflow-y-auto rounded-xl border border-border bg-background py-1 shadow-lg"
        >
          {items.map((item, i) => (
            <li key={item.key} role="option" aria-selected={i === active}>
              {(i === 0 || items[i - 1].section !== item.section) && (
                <p className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {item.section}
                </p>
              )}
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(item)}
                className={`flex w-full items-center gap-2.5 px-3 py-1.5 text-left ${i === active ? 'bg-muted' : ''}`}
              >
                {item.icon}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-foreground">{item.label}</span>
                  {item.hint && <span className="block truncate text-[11px] text-muted-foreground">{item.hint}</span>}
                </span>
              </button>
            </li>
          ))}
          {places.error && (
            <li className="px-3 py-1.5 text-[11px] text-muted-foreground">{places.error}</li>
          )}
        </ul>
      )}
    </div>
  )
}
