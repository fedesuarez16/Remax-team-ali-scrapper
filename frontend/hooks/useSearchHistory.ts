'use client'

const STORAGE_KEY = 'prop_search_history'
const MAX = 20

export type SearchEntry = {
  id: string
  query: string
  zona?: string
  job_id?: string
  date: string
}

// Module-level subscribers set for same-tab live updates without Context
const subscribers = new Set<() => void>()

function readFromStorage(): SearchEntry[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as SearchEntry[]) : []
  } catch {
    return []
  }
}

function writeToStorage(entries: SearchEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries))
  } catch {
    // Ignore storage errors (private mode, quota exceeded, etc.)
  }
}

function notify(): void {
  subscribers.forEach((fn) => fn())
}

export function addSearch(query: string, zona?: string, job_id?: string): void {
  if (typeof window === 'undefined') return

  const trimmed = query.trim()
  if (!trimmed) return

  const existing = readFromStorage()

  // Dedupe case-insensitive: remove any previous entry with same query
  const deduped = existing.filter(
    (e) => e.query.toLowerCase() !== trimmed.toLowerCase()
  )

  const next: SearchEntry = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    query: trimmed,
    zona,
    ...(job_id ? { job_id } : {}),
    date: new Date().toISOString(),
  }

  // Prepend and cap at MAX
  const updated = [next, ...deduped].slice(0, MAX)

  writeToStorage(updated)
  notify()
}

import { useEffect, useState } from 'react'

export function useSearchHistory() {
  const [searches, setSearches] = useState<SearchEntry[]>([])

  useEffect(() => {
    // Initial read (SSR-safe — runs only in browser)
    setSearches(readFromStorage())

    // Subscribe to same-tab updates
    const refresh = () => setSearches(readFromStorage())
    subscribers.add(refresh)

    // Cross-tab sync via storage event
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) {
        setSearches(readFromStorage())
      }
    }
    window.addEventListener('storage', onStorage)

    return () => {
      subscribers.delete(refresh)
      window.removeEventListener('storage', onStorage)
    }
  }, [])

  return { searches, addSearch }
}
