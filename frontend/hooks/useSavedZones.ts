'use client'
import { useCallback, useEffect, useState } from 'react'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const ZONES_URL = `${API}/api/v1/saved-zones`

/** A named delineation — geometry only. Deliberately NOT a search: no job_id,
 *  no results. The map re-draws `polygon` when the user picks the zone, and
 *  the normal "Buscar en esta zona" flow takes it from there. */
export type SavedZone = {
  id: string
  name: string
  /** `[[lat, lng], ...]` — the exact shape Leaflet's Polygon consumes. */
  polygon: [number, number][]
  created_at: string
}

type SavedZoneRow = {
  id: string
  name: string
  polygon: unknown
  created_at: string
}

/** The server validates geometry on write, but rows predating a bad deploy (or
 *  hand-edited jsonb) shouldn't blow up the map — drop what we can't render. */
function mapRow(row: SavedZoneRow): SavedZone | null {
  const raw = row.polygon
  if (!Array.isArray(raw) || raw.length < 3) return null
  const polygon: [number, number][] = []
  for (const v of raw) {
    if (!Array.isArray(v) || v.length !== 2) return null
    const [lat, lng] = v
    if (typeof lat !== 'number' || typeof lng !== 'number') return null
    polygon.push([lat, lng])
  }
  return { id: row.id, name: row.name, polygon, created_at: row.created_at }
}

export function useSavedZones() {
  const [zones, setZones] = useState<SavedZone[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(ZONES_URL)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (data.error) throw new Error(data.error)
      const rows = (data.zones ?? []) as SavedZoneRow[]
      setZones(rows.map(mapRow).filter((z): z is SavedZone => z !== null))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error desconocido')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const saveZone = useCallback(
    async (name: string, polygon: [number, number][]): Promise<SavedZone | null> => {
      const trimmed = name.trim()
      if (!trimmed || polygon.length < 3) return null
      try {
        const res = await fetch(ZONES_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: trimmed, polygon }),
        })
        const data = await res.json()
        if (data.error || !data.zone) {
          setError(data.error ?? 'No se pudo guardar la zona')
          return null
        }
        await refresh()
        return mapRow(data.zone as SavedZoneRow)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'No se pudo guardar la zona')
        return null
      }
    },
    [refresh]
  )

  const renameZone = useCallback(
    async (id: string, name: string): Promise<void> => {
      const trimmed = name.trim()
      if (!trimmed) return
      try {
        const res = await fetch(`${ZONES_URL}/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: trimmed }),
        })
        const data = await res.json()
        if (data.error) {
          setError(data.error)
          return
        }
        await refresh()
      } catch (e) {
        setError(e instanceof Error ? e.message : 'No se pudo renombrar la zona')
      }
    },
    [refresh]
  )

  const deleteZone = useCallback(
    async (id: string): Promise<void> => {
      try {
        await fetch(`${ZONES_URL}/${encodeURIComponent(id)}`, { method: 'DELETE' })
        await refresh()
      } catch (e) {
        setError(e instanceof Error ? e.message : 'No se pudo borrar la zona')
      }
    },
    [refresh]
  )

  return { zones, loading, error, refresh, saveZone, renameZone, deleteZone }
}
