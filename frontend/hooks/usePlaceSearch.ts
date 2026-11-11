'use client'
import { useEffect, useState } from 'react'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const DEBOUNCE_MS = 300
const MIN_CHARS = 3

export type PlaceKind = 'address' | 'street' | 'area' | 'poi'

/** A Photon suggestion, served by GET /geo/search. */
export type Place = {
  id: string
  name: string
  label: string
  kind: PlaceKind
  lat: number
  lng: number
  osm_type: string
  osm_id: number
  city: string | null
}

export type PlaceGeometry = {
  polygon: [number, number][] | null
  lines: [number, number][][] | null
}

/** Search-as-you-type over GET /geo/search. Debounced, and every new query
 *  aborts the previous request so a slow answer never overwrites a newer one. */
export function usePlaceSearch(query: string) {
  const [places, setPlaces] = useState<Place[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const q = query.trim()
  const enabled = q.length >= MIN_CHARS

  useEffect(() => {
    if (!enabled) return
    const ctrl = new AbortController()
    const timer = setTimeout(async () => {
      setLoading(true)
      setError(null)
      try {
        const res = await fetch(`${API}/api/v1/geo/search?q=${encodeURIComponent(q)}`, {
          signal: ctrl.signal,
        })
        if (!res.ok) throw new Error(res.status === 503 ? 'Buscador de lugares no disponible' : `HTTP ${res.status}`)
        const data = (await res.json()) as { places: Place[] }
        setPlaces(data.places ?? [])
      } catch (e) {
        if (ctrl.signal.aborted) return
        setPlaces([])
        setError(e instanceof Error ? e.message : 'Error buscando lugares')
      } finally {
        if (!ctrl.signal.aborted) setLoading(false)
      }
    }, DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      ctrl.abort()
    }
  }, [q, enabled])

  return {
    places: enabled ? places : [],
    loading: enabled && loading,
    error: enabled ? error : null,
  }
}

/** Boundary (area) or every way (street) of a picked place. Never throws:
 *  on failure the caller falls back to a radius around the point. */
export async function fetchPlaceGeometry(place: Place): Promise<PlaceGeometry> {
  if (place.kind !== 'area' && place.kind !== 'street') return { polygon: null, lines: null }
  const params = new URLSearchParams({
    kind: place.kind,
    osm_type: place.osm_type,
    osm_id: String(place.osm_id),
    name: place.name,
    lat: String(place.lat),
    lng: String(place.lng),
  })
  if (place.city) params.set('city', place.city)
  try {
    const res = await fetch(`${API}/api/v1/geo/place-geometry?${params}`)
    if (!res.ok) return { polygon: null, lines: null }
    return (await res.json()) as PlaceGeometry
  } catch {
    return { polygon: null, lines: null }
  }
}
