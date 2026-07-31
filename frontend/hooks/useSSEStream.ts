'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Agency } from '@/components/chat/AgencySelector'
import type { SourceSelection } from '@/lib/sources'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export type Property = {
  id?: string
  titulo: string | null; descripcion: string | null; direccion: string; precio: number | null; moneda: 'USD' | 'ARS'
  tipo_operacion: string; tipo_propiedad: string; ambientes: number | null
  banos: number | null; cocheras: number | null; piso: number | null; expensas: number | null
  m2_total: number | null; antiguedad: number | null; amenities: string[]
  destacados?: { label: string; value: string }[]
  imagenes: string[]; fuente: string; url_origen: string | null
  confianza_extraccion: number
  match_score?: number | null; match_reasons?: string[]
  matches_criteria?: boolean
  lat?: number | null; lng?: number | null
  /** Instante en que se preparó/envió al cliente. null = todavía no enviada. */
  enviada_at?: string | null
}
export type SourceStatus = 'pending' | 'running' | 'done' | 'error'
export type ProgressMap = Record<string, { status: SourceStatus; count: number; message: string }>

export type Message =
  | { id: string; type: 'user'; text: string }
  | { id: string; type: 'agent'; text: string }
  | { id: string; type: 'progress'; progress: ProgressMap; matchedCount: number; totalCount: number }
  | { id: string; type: 'done'; jobId: string; matchedCount: number; totalCount: number }
  | { id: string; type: 'agencies'; agencies: Agency[]; message: string; jobId: string }

export const INITIAL_SOURCES = ['zonaprop', 'mercadolibre', 'googlemaps']

export function useSSEStream() {
  const [messages, setMessages] = useState<Message[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [lastJobId, setLastJobId] = useState<string | null>(null)
  const esRef = useRef<EventSource | null>(null)
  const progressMsgId = useRef<string | null>(null)
  const matchedCountRef = useRef(0)
  const totalCountRef = useRef(0)

  const close = useCallback(() => {
    esRef.current?.close(); esRef.current = null; setIsStreaming(false)
  }, [])

  useEffect(() => () => close(), [close])

  const upsertProgress = useCallback((source: string, status: SourceStatus, count: number, message: string) => {
    setMessages((prev) => prev.map((m) =>
      m.id === progressMsgId.current && m.type === 'progress'
        ? { ...m, progress: { ...m.progress, [source]: { status, count, message } } }
        : m
    ))
  }, [])

  const addMatched = useCallback((count: number, total?: number) => {
    matchedCountRef.current += count
    totalCountRef.current += total ?? count
    setMessages((prev) => prev.map((m) =>
      m.id === progressMsgId.current && m.type === 'progress'
        ? { ...m, matchedCount: matchedCountRef.current, totalCount: totalCountRef.current }
        : m
    ))
  }, [])

  const openSSE = useCallback((url: string) => {
    const es = new EventSource(url)
    esRef.current = es

    es.addEventListener('progress', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      upsertProgress(d.source, d.status, d.count, d.message)
    })

    es.addEventListener('property_batch', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      addMatched(d.count, d.total)
    })

    es.addEventListener('agencies_review', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      const jobId = url.match(/scraping\/([^/]+)\//)?.[1] ?? ''
      setMessages((p) => [...p, {
        id: crypto.randomUUID(),
        type: 'agencies',
        agencies: d.agencies,
        message: d.message,
        jobId,
      }])
      // Stream pauses here (interrupt) — close the SSE, user will resume manually
      es.close(); esRef.current = null
      setIsStreaming(false)
    })

    es.addEventListener('clarification', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      setMessages((p) => [...p, { id: crypto.randomUUID(), type: 'agent', text: d.message }])
      close()
    })

    es.addEventListener('done', (e) => {
      const me = e as MessageEvent
      let jobId = url.match(/scraping\/([^/]+)\//)?.[1] ?? ''
      if (me.data) {
        try {
          const d = JSON.parse(me.data)
          if (d.job_id) jobId = d.job_id
        } catch { /* ignore */ }
      }
      setLastJobId(jobId)
      setMessages((p) => [...p, { id: crypto.randomUUID(), type: 'done', jobId, matchedCount: matchedCountRef.current, totalCount: totalCountRef.current }])
      close()
    })

    es.addEventListener('error', (e) => {
      const me = e as MessageEvent
      if (me.data) {
        const d = JSON.parse(me.data)
        setMessages((p) => [...p, { id: crypto.randomUUID(), type: 'agent', text: `Error: ${d.message}` }])
      }
      close()
    })

    return es
  }, [close, upsertProgress, addMatched])

  const startScraping = useCallback(async (
    query: string,
    polygon?: [number, number][],
    localidades?: string[],
    // Where to scrape, picked before submit. Omitted → backend default
    // (every portal + all inmobiliarias), i.e. the pre-selector behaviour.
    sourceSelection?: SourceSelection
  ) => {
    setMessages((p) => [...p, { id: crypto.randomUUID(), type: 'user', text: query }])
    setIsStreaming(true)
    setLastJobId(null)
    matchedCountRef.current = 0
    totalCountRef.current = 0

    let jobId: string
    try {
      const res = await fetch(`${API}/api/v1/scraping/start`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query,
          ...(polygon ? { polygon } : {}),
          ...(localidades && localidades.length > 0 ? { localidades } : {}),
          ...(sourceSelection ? { source_selection: sourceSelection } : {}),
        }),
      })
      const data = await res.json()
      if (!res.ok || !data.job_id) {
        // 400 = the source selection can't produce a search (e.g. no track
        // enabled). Surface the backend's reason instead of hanging on a
        // stream that will never open.
        setMessages((p) => [...p, {
          id: crypto.randomUUID(), type: 'agent',
          text: data?.detail ?? 'No se pudo iniciar la búsqueda.',
        }])
        setIsStreaming(false)
        return
      }
      jobId = data.job_id
    } catch { setIsStreaming(false); return }

    const pid = crypto.randomUUID()
    progressMsgId.current = pid
    const initial: ProgressMap = Object.fromEntries(
      INITIAL_SOURCES.map((s) => [s, { status: 'pending' as SourceStatus, count: 0, message: '' }])
    )
    setMessages((p) => [...p, { id: pid, type: 'progress', progress: initial, matchedCount: 0, totalCount: 0 }])
    openSSE(`${API}/api/v1/scraping/${jobId}/stream?query=${encodeURIComponent(query)}`)
  }, [openSSE])

  const resumeScraping = useCallback(async (jobId: string, selectedAgencyIds: string[]) => {
    setIsStreaming(true)

    // POST /resume returns a StreamingResponse — consume it with fetch
    let resp: Response
    try {
      resp = await fetch(`${API}/api/v1/scraping/${jobId}/resume`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selected_agency_ids: selectedAgencyIds }),
      })
    } catch { setIsStreaming(false); return }

    if (!resp.body) { setIsStreaming(false); return }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''

    const processLine = (line: string) => {
      if (!line.startsWith('data:')) return
      try {
        const d = JSON.parse(line.slice(5).trim())
        if (d.event === 'progress') upsertProgress(d.source, d.status, d.count, d.message)
        else if (d.event === 'property_batch')
          addMatched(d.count, d.total)
        else if (d.event === 'agent_message')
          setMessages((p) => [...p, { id: crypto.randomUUID(), type: 'agent', text: d.message }])
        else if (d.event === 'done') {
          const resolvedJobId = d.job_id ?? jobId
          setLastJobId(resolvedJobId)
          setMessages((p) => [...p, { id: crypto.randomUUID(), type: 'done', jobId: resolvedJobId, matchedCount: matchedCountRef.current, totalCount: totalCountRef.current }])
          setIsStreaming(false)
        } else if (d.event === 'error')
          setMessages((p) => [...p, { id: crypto.randomUUID(), type: 'agent', text: `Error: ${d.message}` }])
      } catch { /* ignore parse errors */ }
    }

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''
        lines.forEach(processLine)
      }
    } finally {
      setIsStreaming(false)
    }
  }, [upsertProgress, addMatched])

  return { messages, isStreaming, lastJobId, startScraping, resumeScraping }
}
