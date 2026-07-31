'use client'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const SOURCES_URL = `${API}/api/v1/manual-sources`

export type ManualSource = {
  id: string
  nombre: string
  url: string
  activo: boolean
  /** The zona WE classified this source into. null = no zona bucket. */
  zona: string | null
  date: string
}

/** A zona that actually has inmobiliarias loaded — what the pre-search
 * "elegí la zona" step renders. */
export type SourceZona = {
  zona: string
  zona_norm: string
  count: number
}

type ManualSourceRow = {
  id: string
  nombre: string
  url: string
  activo: boolean
  zona?: string | null
  created_at: string
}

function mapRow(row: ManualSourceRow): ManualSource {
  return {
    id: row.id,
    nombre: row.nombre,
    url: row.url,
    activo: row.activo,
    zona: row.zona ?? null,
    date: row.created_at,
  }
}

// Module-level subscribers set for same-tab live updates without Context
const subscribers = new Set<() => void>()

// Last known list, kept so a failed fetch degrades gracefully instead of
// wiping the tab (server is the source of truth, but a transient network
// error shouldn't blank the UI).
let lastKnown: ManualSource[] = []

function notify(): void {
  subscribers.forEach((fn) => fn())
}

async function fetchSources(): Promise<ManualSource[]> {
  try {
    const res = await fetch(SOURCES_URL)
    if (!res.ok) return lastKnown
    const data = await res.json()
    const rows = (data.sources ?? []) as ManualSourceRow[]
    lastKnown = rows.map(mapRow)
    return lastKnown
  } catch {
    // Backend unreachable — keep the last known list, never throw into the UI.
    return lastKnown
  }
}

/** Registers a source (agency/portal website) so future searches also
 * crawl it — see backend/app/graphs/extraction/nodes.py route_after_review.
 * `zona` is the manual classification: a search scoped to that zona will
 * only consult the sources filed under it.
 * Returns an error message on failure, or null on success. */
export async function addSource(nombre: string, url: string, zona?: string): Promise<string | null> {
  const trimmedNombre = nombre.trim()
  const trimmedUrl = url.trim()
  const trimmedZona = (zona ?? '').trim()
  if (!trimmedNombre || !trimmedUrl) return 'Completá nombre y URL'

  try {
    const res = await fetch(SOURCES_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        nombre: trimmedNombre,
        url: trimmedUrl,
        ...(trimmedZona ? { zona: trimmedZona } : {}),
      }),
    })
    const data = await res.json()
    if (data.error) return data.error as string
    notify()
    return null
  } catch {
    return 'No se pudo conectar con el servidor'
  }
}

export async function deleteSource(id: string): Promise<void> {
  try {
    const res = await fetch(`${SOURCES_URL}/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!res.ok) return
    notify()
  } catch {
    // Swallow — list keeps its last known state
  }
}

import { useEffect, useState } from 'react'

export function useManualSources() {
  const [sources, setSources] = useState<ManualSource[]>(lastKnown)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    const refresh = () => {
      fetchSources().then((entries) => {
        if (!cancelled) setSources(entries)
      })
    }

    setLoading(true)
    fetchSources().then((entries) => {
      if (!cancelled) {
        setSources(entries)
        setLoading(false)
      }
    })

    subscribers.add(refresh)

    return () => {
      cancelled = true
      subscribers.delete(refresh)
    }
  }, [])

  return { sources, addSource, deleteSource, loading }
}

// ── Zonas ────────────────────────────────────────────────────────────────────

let lastKnownZonas: SourceZona[] = []

async function fetchZonas(): Promise<SourceZona[]> {
  try {
    const res = await fetch(`${SOURCES_URL}/zonas`)
    if (!res.ok) return lastKnownZonas
    const data = await res.json()
    lastKnownZonas = (data.zonas ?? []) as SourceZona[]
    return lastKnownZonas
  } catch {
    return lastKnownZonas
  }
}

/** Zonas that have inmobiliarias loaded. Subscribes to the same notify() bus
 * as the source list, so loading a source in the Fuentes tab immediately
 * refreshes the zona step of the search selector. */
export function useSourceZonas() {
  const [zonas, setZonas] = useState<SourceZona[]>(lastKnownZonas)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    const refresh = () => {
      fetchZonas().then((entries) => {
        if (!cancelled) setZonas(entries)
      })
    }

    // `loading` already starts true and this effect runs once on mount, so
    // there is nothing to re-set here.
    fetchZonas().then((entries) => {
      if (!cancelled) {
        setZonas(entries)
        setLoading(false)
      }
    })

    subscribers.add(refresh)

    return () => {
      cancelled = true
      subscribers.delete(refresh)
    }
  }, [])

  return { zonas, loading }
}
