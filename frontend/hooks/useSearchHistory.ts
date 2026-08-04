'use client'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const HISTORY_URL = `${API}/api/v1/search-history`
const FOLDERS_URL = `${HISTORY_URL}/folders`

/** Per-source Apify tally: `{ zonaprop: { usd, runs } }`. ZonaProp runs one
 *  actor per listing page, so `runs` > 1 is normal. A source missing from the
 *  object never reached Apify (direct scrape or cache hit). */
export type ApifyCostBreakdown = Record<string, { usd: number; runs: number }>

export type SearchEntry = {
  id: string
  query: string
  zona?: string
  job_id?: string
  label?: string | null
  folder_id?: string | null
  date: string
  /** USD billed by Apify for this search. `0` means it really was free;
   *  `null`/undefined means unknown (chat entry, or job predates the column). */
  apify_cost_usd?: number | null
  apify_cost_breakdown?: ApifyCostBreakdown | null
}

export type Folder = {
  id: string
  name: string
  created_at: string
}

type SearchHistoryRow = {
  id: string
  query: string
  zona?: string | null
  job_id?: string | null
  label?: string | null
  folder_id?: string | null
  created_at: string
  apify_cost_usd?: number | string | null
  apify_cost_breakdown?: ApifyCostBreakdown | null
}

function mapRow(row: SearchHistoryRow): SearchEntry {
  // Postgres `numeric` arrives as a string over PostgREST — coerce, but keep
  // null/undefined distinct from 0 ("unknown" vs "this search was free").
  const cost = row.apify_cost_usd
  return {
    id: row.id,
    query: row.query,
    zona: row.zona ?? undefined,
    job_id: row.job_id ?? undefined,
    label: row.label ?? null,
    folder_id: row.folder_id ?? null,
    date: row.created_at,
    apify_cost_usd: cost === null || cost === undefined ? null : Number(cost),
    apify_cost_breakdown: row.apify_cost_breakdown ?? null,
  }
}

// Module-level subscribers set for same-tab live updates without Context
const subscribers = new Set<() => void>()

// Last known lists, kept so a failed fetch degrades gracefully instead of
// wiping the sidebar (server is now the source of truth, but we don't want
// a transient network error to blank the UI).
let lastKnown: SearchEntry[] = []
let lastKnownFolders: Folder[] = []

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

async function fetchFolders(): Promise<Folder[]> {
  try {
    const res = await fetch(FOLDERS_URL)
    if (!res.ok) return lastKnownFolders
    const data = await res.json()
    lastKnownFolders = (data.folders ?? []) as Folder[]
    return lastKnownFolders
  } catch {
    return lastKnownFolders
  }
}

export async function addSearch(
  query: string,
  zona?: string,
  job_id?: string,
  folder_id?: string | null,
): Promise<string | null> {
  const trimmed = query.trim()
  if (!trimmed) return null

  try {
    const body: Record<string, unknown> = { query: trimmed, zona, job_id }
    if (folder_id !== undefined) body.folder_id = folder_id
    const res = await fetch(HISTORY_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) return null
    const data = await res.json()
    await fetchHistory()
    notify()
    // Returned so callers (the map's inline save panel) can PATCH label/folder
    // on the just-saved row. Upsert-by-query returns the same id on re-runs.
    return (data.entry?.id as string) ?? null
  } catch {
    // Swallow — sidebar keeps its last known list, submit flow is unaffected.
    return null
  }
}

export async function updateEntry(
  id: string,
  changes: { label?: string | null; folder_id?: string | null },
): Promise<void> {
  try {
    const res = await fetch(`${HISTORY_URL}/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    })
    if (!res.ok) return
    await fetchHistory()
    notify()
  } catch {
    // Swallow — keep last known state, never throw into the UI.
  }
}

export async function deleteEntry(id: string): Promise<void> {
  try {
    const res = await fetch(`${HISTORY_URL}/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!res.ok) return
    await fetchHistory()
    notify()
  } catch {
    // Swallow — keep last known state, never throw into the UI.
  }
}

export async function createFolder(name: string): Promise<Folder | null> {
  const trimmed = name.trim()
  if (!trimmed) return null

  try {
    const res = await fetch(FOLDERS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: trimmed }),
    })
    if (!res.ok) return null
    const data = await res.json()
    await fetchFolders()
    notify()
    return (data.folder ?? null) as Folder | null
  } catch {
    return null
  }
}

import { useEffect, useState } from 'react'

export function useSearchHistory() {
  const [searches, setSearches] = useState<SearchEntry[]>(lastKnown)
  const [folders, setFolders] = useState<Folder[]>(lastKnownFolders)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    const refresh = () => {
      fetchHistory().then((entries) => {
        if (!cancelled) setSearches(entries)
      })
      fetchFolders().then((f) => {
        if (!cancelled) setFolders(f)
      })
    }

    setLoading(true)
    Promise.all([fetchHistory(), fetchFolders()]).then(([entries, f]) => {
      if (!cancelled) {
        setSearches(entries)
        setFolders(f)
        setLoading(false)
      }
    })

    subscribers.add(refresh)

    return () => {
      cancelled = true
      subscribers.delete(refresh)
    }
  }, [])

  return { searches, folders, addSearch, updateEntry, deleteEntry, createFolder, loading }
}
