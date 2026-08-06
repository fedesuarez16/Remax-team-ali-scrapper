'use client'

import { useEffect, useState } from 'react'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const PORTALS_URL = `${API}/api/v1/portals`

/** A fixed-catalog portal (Zonaprop, Argenprop, …) with its on/off state.
 * The catalog itself lives in code — this only carries the persisted flag. */
export type Portal = {
  id: string
  label: string
  activo: boolean
}

// Same live-update pattern as useManualSources: a module-level subscriber set
// so a toggle refreshes every mounted consumer without a Context provider.
const subscribers = new Set<() => void>()
let lastKnown: Portal[] = []

function notify(): void {
  subscribers.forEach((fn) => fn())
}

async function fetchPortals(): Promise<Portal[]> {
  try {
    const res = await fetch(PORTALS_URL)
    if (!res.ok) return lastKnown
    const data = await res.json()
    lastKnown = (data.portals ?? []) as Portal[]
    return lastKnown
  } catch {
    return lastKnown
  }
}

/** Flips a portal on/off. Returns an error message or null on success. */
export async function togglePortal(id: string, activo: boolean): Promise<string | null> {
  try {
    const res = await fetch(`${PORTALS_URL}/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ activo }),
    })
    const data = await res.json()
    if (data.error) return data.error as string
    notify()
    return null
  } catch {
    return 'No se pudo conectar con el servidor'
  }
}

export function usePortals() {
  const [portals, setPortals] = useState<Portal[]>(lastKnown)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    const refresh = () => {
      fetchPortals().then((entries) => {
        if (!cancelled) setPortals(entries)
      })
    }

    fetchPortals().then((entries) => {
      if (!cancelled) {
        setPortals(entries)
        setLoading(false)
      }
    })

    subscribers.add(refresh)

    return () => {
      cancelled = true
      subscribers.delete(refresh)
    }
  }, [])

  return { portals, togglePortal, loading }
}
