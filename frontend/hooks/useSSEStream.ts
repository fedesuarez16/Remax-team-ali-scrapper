'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Agency } from '@/components/chat/AgencySelector'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export type Property = {
  titulo: string | null; direccion: string; precio: number | null; moneda: 'USD' | 'ARS'
  tipo_operacion: string; tipo_propiedad: string; ambientes: number | null
  m2_total: number | null; antiguedad: number | null; amenities: string[]
  imagenes: string[]; fuente: string; url_origen: string | null
  confianza_extraccion: number
}
export type SourceStatus = 'pending' | 'running' | 'done' | 'error'
export type ProgressMap = Record<string, { status: SourceStatus; count: number; message: string }>

export type Message =
  | { id: string; type: 'user'; text: string }
  | { id: string; type: 'agent'; text: string }
  | { id: string; type: 'progress'; progress: ProgressMap }
  | { id: string; type: 'properties'; properties: Property[] }
  | { id: string; type: 'agencies'; agencies: Agency[]; message: string; jobId: string }

const INITIAL_SOURCES = ['zonaprop', 'mercadolibre', 'googlemaps']

export function useSSEStream() {
  const [messages, setMessages] = useState<Message[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  const progressMsgId = useRef<string | null>(null)

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

  const openSSE = useCallback((url: string) => {
    const es = new EventSource(url)
    esRef.current = es

    es.addEventListener('progress', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      upsertProgress(d.source, d.status, d.count, d.message)
    })

    es.addEventListener('property_batch', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      setMessages((p) => [...p, { id: crypto.randomUUID(), type: 'properties', properties: d.properties }])
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

    es.addEventListener('done', () => close())

    es.addEventListener('error', (e) => {
      const me = e as MessageEvent
      if (me.data) {
        const d = JSON.parse(me.data)
        setMessages((p) => [...p, { id: crypto.randomUUID(), type: 'agent', text: `Error: ${d.message}` }])
      }
      close()
    })

    return es
  }, [close, upsertProgress])

  const startScraping = useCallback(async (query: string) => {
    setMessages((p) => [...p, { id: crypto.randomUUID(), type: 'user', text: query }])
    setIsStreaming(true)

    let jobId: string
    try {
      const res = await fetch(`${API}/api/v1/scraping/start`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      })
      jobId = (await res.json()).job_id
    } catch { setIsStreaming(false); return }

    const pid = crypto.randomUUID()
    progressMsgId.current = pid
    const initial: ProgressMap = Object.fromEntries(
      INITIAL_SOURCES.map((s) => [s, { status: 'pending' as SourceStatus, count: 0, message: '' }])
    )
    setMessages((p) => [...p, { id: pid, type: 'progress', progress: initial }])
    openSSE(`${API}/api/v1/scraping/${jobId}/stream?query=${encodeURIComponent(query)}`)
  }, [openSSE])

  const resumeScraping = useCallback(async (jobId: string, selectedAgencyIds: string[]) => {
    setIsStreaming(true)

    const pid = crypto.randomUUID()
    progressMsgId.current = pid
    setMessages((p) => [...p, { id: pid, type: 'progress', progress: {} }])

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
      if (line.startsWith('event:')) return  // store event name if needed
      if (!line.startsWith('data:')) return
      try {
        const d = JSON.parse(line.slice(5).trim())
        if (d.event === 'progress') upsertProgress(d.source, d.status, d.count, d.message)
        else if (d.event === 'property_batch')
          setMessages((p) => [...p, { id: crypto.randomUUID(), type: 'properties', properties: d.properties }])
        else if (d.event === 'done') setIsStreaming(false)
        else if (d.event === 'error')
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
  }, [upsertProgress])

  return { messages, isStreaming, startScraping, resumeScraping }
}
