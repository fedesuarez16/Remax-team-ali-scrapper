'use client'
import { useCallback, useEffect, useRef, useState } from 'react'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export type Property = {
  titulo: string | null; direccion: string; precio: number | null; moneda: 'USD' | 'ARS'
  tipo_operacion: string; tipo_propiedad: string; ambientes: number | null
  m2_total: number | null; amenities: string[]; imagenes: string[]; fuente: string
  url_origen: string | null; confianza_extraccion: number
}
export type SourceStatus = 'pending' | 'running' | 'done' | 'error'
export type ProgressMap = Record<string, { status: SourceStatus; count: number; message: string }>

export type Message =
  | { id: string; type: 'user'; text: string }
  | { id: string; type: 'agent'; text: string }
  | { id: string; type: 'progress'; progress: ProgressMap }
  | { id: string; type: 'properties'; properties: Property[] }

const SOURCES = ['zonaprop', 'mercadolibre', 'googlemaps']

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
        : m))
  }, [])

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

    const pid = crypto.randomUUID(); progressMsgId.current = pid
    const initial: ProgressMap = Object.fromEntries(
      SOURCES.map((s) => [s, { status: 'pending' as SourceStatus, count: 0, message: '' }]))
    setMessages((p) => [...p, { id: pid, type: 'progress', progress: initial }])

    const url = `${API}/api/v1/scraping/${jobId}/stream?query=${encodeURIComponent(query)}`
    const es = new EventSource(url); esRef.current = es

    es.addEventListener('progress', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      upsertProgress(d.source, d.status, d.count, d.message)
    })
    es.addEventListener('property_batch', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      setMessages((p) => [...p, { id: crypto.randomUUID(), type: 'properties', properties: d.properties }])
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
  }, [close, upsertProgress])

  return { messages, isStreaming, startScraping }
}
