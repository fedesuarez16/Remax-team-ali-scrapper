'use client'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const SOURCES_URL = `${API}/api/v1/manual-sources`

export type ManualSource = {
  id: string
  nombre: string
  url: string
  activo: boolean
  date: string
}

type ManualSourceRow = {
  id: string
  nombre: string
  url: string
  activo: boolean
  created_at: string
}

function mapRow(row: ManualSourceRow): ManualSource {
  return { id: row.id, nombre: row.nombre, url: row.url, activo: row.activo, date: row.created_at }
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
 * Returns an error message on failure, or null on success. */
export async function addSource(nombre: string, url: string): Promise<string | null> {
  const trimmedNombre = nombre.trim()
  const trimmedUrl = url.trim()
  if (!trimmedNombre || !trimmedUrl) return 'Completá nombre y URL'

  try {
    const res = await fetch(SOURCES_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nombre: trimmedNombre, url: trimmedUrl }),
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
