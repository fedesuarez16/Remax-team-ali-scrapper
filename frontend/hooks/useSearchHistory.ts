'use client'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const HISTORY_URL = `${API}/api/v1/search-history`

export type SearchEntry = {
  id: string
  query: string
  zona?: string
  job_id?: string
  date: string
}

type SearchHistoryRow = {
  id: string
  query: string
  zona?: string | null
  job_id?: string | null
  created_at: string
}

function mapRow(row: SearchHistoryRow): SearchEntry {
  return {
    id: row.id,
    query: row.query,
    zona: row.zona ?? undefined,
    job_id: row.job_id ?? undefined,
    date: row.created_at,
  }
}

// Module-level subscribers set for same-tab live updates without Context
const subscribers = new Set<() => void>()

// Last known list, kept so a failed fetch degrades gracefully instead of
// wiping the sidebar (server is now the source of truth, but we don't want
// a transient network error to blank the UI).
let lastKnown: SearchEntry[] = []

function notify(): void {
  subscribers.forEach((fn) => fn())
}

async function fetchHistory(): Promise<SearchEntry[]> {
  try {
    const res = await fetch(HISTORY_URL)
    if (!res.ok) return lastKnown
    const data = await res.json()
    const rows = (data.history ?? []) as SearchHistoryRow[]
    lastKnown = rows.map(mapRow)
    return lastKnown
  } catch {
    // Backend unreachable — keep the last known list, never throw into the UI.
    return lastKnown
  }
}

export async function addSearch(query: string, zona?: string, job_id?: string): Promise<void> {
  const trimmed = query.trim()
  if (!trimmed) return

  try {
    const res = await fetch(HISTORY_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: trimmed, zona, job_id }),
    })
    if (!res.ok) return
    notify()
  } catch {
    // Swallow — sidebar keeps its last known list, submit flow is unaffected.
  }
}

import { useEffect, useState } from 'react'

export function useSearchHistory() {
  const [searches, setSearches] = useState<SearchEntry[]>(lastKnown)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    const refresh = () => {
      fetchHistory().then((entries) => {
        if (!cancelled) setSearches(entries)
      })
    }

    setLoading(true)
    fetchHistory().then((entries) => {
      if (!cancelled) {
        setSearches(entries)
        setLoading(false)
      }
    })

    subscribers.add(refresh)

    return () => {
      cancelled = true
      subscribers.delete(refresh)
    }
  }, [])

  return { searches, addSearch, loading }
}
