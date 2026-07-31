'use client'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Agency } from '@/components/chat/AgencySelector'
import type { MapProperty } from '@/components/map/PropertyMap'
import { samplePolygon } from '@/lib/geo'
import { addSearch } from '@/hooks/useSearchHistory'
import { INITIAL_SOURCES, useSSEStream, type ProgressMap } from '@/hooks/useSSEStream'
import type { SourceSelection } from '@/lib/sources'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export type ZonePhase = 'idle' | 'resolving' | 'confirm' | 'running' | 'agencies_review' | 'done'

// Raw shape of a row returned by GET /scraping/{job_id}/properties — lat/lng
// may be null for rows not yet geocoded (the "ungeocoded bucket"); in_polygon
// is server-classified (inside/outside/null-for-ungeocoded) when the job has
// a stored polygon — the single source of truth, no client-side re-derivation.
type RawJobProperty = Omit<MapProperty, 'lat' | 'lng'> & {
  lat: number | null
  lng: number | null
  in_polygon?: boolean | null
}

type JobCounts = { inside: number; outside: number; ungeocoded: number; total: number }

const BACKFILL_POLL_MS = 3000
// Nominatim geocodes ~1 row/1.1s, so a big scrape (200+ rows) needs several
// minutes — the poll window must cover it: 160 polls × 3s ≈ 8 min hard cap.
const BACKFILL_POLL_CAP = 160
// Each backfill kick processes up to `limit` rows then finishes; re-kick while
// progress is being made, up to this many times.
const BACKFILL_KICK_CAP = 5

/**
 * useZoneSearch — owns the map-native zone-search run state machine
 * (idle → resolving → confirm → running → [agencies_review → running]? → done).
 * Wraps `useSSEStream` (reused verbatim for the run/interrupt/resume pipeline)
 * and projects its chat-shaped `messages` into a map-shaped view-model. Chat
 * (`/chat`) is untouched — this hook is only invoked from `PropertyMap`.
 */
export function useZoneSearch() {
  const { messages, isStreaming, lastJobId, startScraping, resumeScraping } = useSSEStream()

  const [phase, setPhase] = useState<ZonePhase>('idle')
  const [zonas, setZonas] = useState<string[]>([])
  const [localidades, setLocalidades] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [stalled, setStalled] = useState(false)
  const [polygon, setPolygon] = useState<[number, number][] | null>(null)
  const [jobProperties, setJobProperties] = useState<MapProperty[]>([])
  const [ungeocodedCount, setUngeocodedCount] = useState(0)
  const [insideCount, setInsideCount] = useState(0)
  const [draining, setDraining] = useState(false)
  const resumedAgenciesIdRef = useRef<string | null>(null)

  // Map-search history persistence. `runQuery`/`runZona` are captured at
  // confirmRun so the auto-save survives a later cancel() (which resets zonas).
  // `savedEntryId` is the just-saved row's id, handed to the inline name/folder
  // panel on the done card. A per-jobId ref stops the job_id upsert re-firing.
  const [savedEntryId, setSavedEntryId] = useState<string | null>(null)
  const [runQuery, setRunQuery] = useState('')
  const runZonaRef = useRef<string>('')
  const savedJobRef = useRef<string | null>(null)
  // Serializes the initial save against the job_id upsert: both POST via
  // SELECT-by-query then UPDATE/INSERT, so firing them concurrently could
  // INSERT a duplicate before the first row exists. The effect awaits this.
  const firstSaveRef = useRef<Promise<unknown>>(Promise.resolve())

  const agenciesMsg = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      if (m.type === 'agencies') return m
    }
    return null
  }, [messages])

  const progressMsg = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      if (m.type === 'progress') return m
    }
    return null
  }, [messages])

  const progress: ProgressMap = progressMsg?.progress ?? {}
  const matchedCount = progressMsg?.matchedCount ?? 0
  const agencies: Agency[] | null = agenciesMsg?.agencies ?? null
  const agenciesMessage: string | null = agenciesMsg?.message ?? null
  const jobId = lastJobId ?? agenciesMsg?.jobId ?? null
  const estimatedActors = zonas.length * INITIAL_SOURCES.length + zonas.length

  // Derive agencies_review / done from the SSE-projected messages while running.
  // Guarded against re-triggering on a stale 'agencies' message that was already
  // resumed (resumedAgenciesIdRef) and against flipping mid-stream (!isStreaming),
  // matching the design doc's documented condition.
  useEffect(() => {
    if (phase !== 'running') return
    const last = messages[messages.length - 1]
    if (!last) return
    if (last.type === 'agencies' && last.id !== resumedAgenciesIdRef.current && !isStreaming) {
      setStalled(false)
      setPhase('agencies_review')
    } else if (last.type === 'done') {
      setStalled(false)
      setPhase('done')
    } else if (last.type === 'agent' && !isStreaming) {
      // Backend surfaced an error/clarification and the stream ended — show it
      // instead of leaving the user on an eternal spinner + stalled overlay.
      setStalled(false)
      setError(last.text)
      setPhase('idle')
    }
  }, [messages, phase, isStreaming])

  // SSE-drop stalled state: stream closed mid-run without a terminal message.
  useEffect(() => {
    if (phase !== 'running') return
    if (isStreaming) { setStalled(false); return }
    const last = messages[messages.length - 1]
    const terminal = last && (last.type === 'done' || last.type === 'agencies')
    if (!terminal) setStalled(true)
  }, [isStreaming, phase, messages])

  const resolveZone = useCallback(async (vertices: [number, number][]) => {
    setError(null)
    setPhase('resolving')
    const points = samplePolygon(vertices).map(([lat, lng]) => ({ lat, lng }))
    try {
      const res = await fetch(`${API}/api/v1/geo/reverse-zonas`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ points }),
      })
      if (res.status === 503) {
        setError('No pudimos detectar los barrios (servicio saturado). Probá de nuevo.')
        setPhase('idle')
        return
      }
      if (!res.ok) {
        setError('No pudimos conectar con el servidor. Intentá de nuevo.')
        setPhase('idle')
        return
      }
      const data: { zonas: string[]; localidades?: string[] } = await res.json()
      if (!data.zonas || data.zonas.length === 0) {
        setError('No detectamos barrios, ajustá el polígono.')
        setPhase('idle')
        return
      }
      setZonas(data.zonas)
      setLocalidades(data.localidades ?? [])
      setPhase('confirm')
    } catch {
      setError('No pudimos conectar con el servidor. Intentá de nuevo.')
      setPhase('idle')
    }
  }, [])

  const confirmRun = useCallback((vertices: [number, number][], selection?: SourceSelection) => {
    setPolygon(vertices)
    setJobProperties([])
    setUngeocodedCount(0)
    setInsideCount(0)
    setStalled(false)
    setSavedEntryId(null)
    savedJobRef.current = null
    setPhase('running')
    const zona = zonas.join(', ')
    const query = `Propiedades en ${zona}`
    setRunQuery(query)
    runZonaRef.current = zona
    // Save immediately so a map search is never lost even before the job id
    // exists; the effect below upserts the same row with job_id once it does.
    firstSaveRef.current = addSearch(query, zona).then((id) => {
      if (id) setSavedEntryId(id)
    })
    void startScraping(query, vertices, localidades, selection)
  }, [zonas, localidades, startScraping])

  // Attach job_id to the saved history row once the job exists (upsert-by-query
  // returns the same id, preserving any label/folder the user set on the card).
  useEffect(() => {
    if (!jobId || !runQuery || savedJobRef.current === jobId) return
    savedJobRef.current = jobId
    void firstSaveRef.current
      .then(() => addSearch(runQuery, runZonaRef.current || undefined, jobId))
      .then((id) => id && setSavedEntryId(id))
  }, [jobId, runQuery])

  const confirmAgencies = useCallback((ids: string[]) => {
    if (!jobId) return
    if (agenciesMsg) resumedAgenciesIdRef.current = agenciesMsg.id
    setStalled(false)
    setPhase('running')
    void resumeScraping(jobId, ids)
  }, [jobId, agenciesMsg, resumeScraping])

  const cancel = useCallback(() => {
    setPhase('idle')
    setZonas([])
    setError(null)
  }, [])

  // Not part of the design's documented API shape but needed to satisfy T-6.4
  // ("check job status" action on a stalled overlay) — additive, non-breaking.
  const checkJobStatus = useCallback(async () => {
    if (!jobId) return
    try {
      const res = await fetch(`${API}/api/v1/scraping/${jobId}/properties`)
      if (!res.ok) return
      const data: { properties?: RawJobProperty[] } = await res.json()
      if ((data.properties?.length ?? 0) > 0) {
        setPhase('done')
      }
    } catch {
      // keep stalled — user can retry
    }
  }, [jobId])

  // Done-reveal: fetch job properties, classify geocoded vs ungeocoded, retain
  // polygon (reload-safe), then drain the ungeocoded bucket via backfill+poll.
  useEffect(() => {
    if (phase !== 'done' || !jobId) return
    let stopped = false
    let pollCount = 0
    let intervalId: ReturnType<typeof setInterval> | null = null

    const stopPolling = () => {
      if (intervalId) clearInterval(intervalId)
      intervalId = null
      if (!stopped) setDraining(false)
    }

    const refetch = async (): Promise<number | null> => {
      try {
        const res = await fetch(`${API}/api/v1/scraping/${jobId}/properties`)
        if (!res.ok) return null
        const data: {
          properties?: RawJobProperty[]
          polygon?: [number, number][] | null
          counts?: JobCounts | null
        } = await res.json()
        if (stopped) return null
        const all = data.properties ?? []
        const geocoded = all.filter((p): p is RawJobProperty & { lat: number; lng: number } =>
          p.lat != null && p.lng != null
        )
        setJobProperties(geocoded as MapProperty[])
        // Server `counts` (from polygon classification) is the source of truth
        // when present; fall back to the client-derived ungeocoded count only
        // when the job has no polygon (shouldn't happen on this flow, but keeps
        // the drain-poll loop working if it ever does).
        const ungeocoded = data.counts ? data.counts.ungeocoded : all.length - geocoded.length
        setUngeocodedCount(ungeocoded)
        setInsideCount(data.counts ? data.counts.inside : geocoded.length)
        if (data.polygon) setPolygon(data.polygon)
        return ungeocoded
      } catch {
        return null
      }
    }

    const kickBackfill = async () => {
      try {
        await fetch(`${API}/api/v1/geocode/backfill`, { method: 'POST' })
      } catch {
        // best-effort trigger — poll loop below still checks progress
      }
    }

    // `remaining` counts rows with lat null, which includes addresses that
    // FAILED geocoding (they keep lat null forever) — so it never has to reach
    // 0. The honest stop condition is: backfill idle AND no progress since the
    // last kick. While each finished kick made progress, re-kick (a kick
    // processes up to its row limit, big scrapes need several rounds).
    let lastRemaining = Number.POSITIVE_INFINITY
    let kicks = 1

    const poll = async () => {
      pollCount += 1
      let status: { running?: boolean; aborted?: string | null; finished_at?: string | null } | null = null
      try {
        const res = await fetch(`${API}/api/v1/geocode/status`)
        if (res.ok) status = await res.json()
      } catch {
        // ignore — keep polling until the hard cap
      }
      const remaining = await refetch()
      if (stopped) return
      if (remaining === 0 || remaining === null) { stopPolling(); return }
      if (status?.aborted) { stopPolling(); return }
      if (status?.running === false && status?.finished_at) {
        if (remaining < lastRemaining && kicks < BACKFILL_KICK_CAP) {
          kicks += 1
          lastRemaining = remaining
          void kickBackfill()
        } else {
          stopPolling()
          return
        }
      }
      if (pollCount >= BACKFILL_POLL_CAP) { stopPolling(); return }
    }

    const init = async () => {
      const remaining = await refetch()
      if (stopped || remaining == null || remaining === 0) return
      lastRemaining = remaining
      setDraining(true)
      await kickBackfill()
      intervalId = setInterval(() => { void poll() }, BACKFILL_POLL_MS)
    }
    void init()

    return () => {
      stopped = true
      if (intervalId) clearInterval(intervalId)
    }
  }, [phase, jobId])

  return {
    phase,
    isStreaming,
    zonas,
    estimatedActors,
    progress,
    matchedCount,
    agencies,
    agenciesMessage,
    jobId,
    jobProperties,
    ungeocodedCount,
    insideCount,
    draining,
    stalled,
    polygon,
    savedEntryId,
    runQuery,
    resolveZone,
    confirmRun,
    confirmAgencies,
    cancel,
    checkJobStatus,
    error,
  }
}
