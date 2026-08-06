'use client'
import { Suspense, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { ArrowUp, Building2, Sparkles } from 'lucide-react'
import { SearchDoneCard } from '@/components/chat/SearchDoneCard'
import { ProgressBubble } from '@/components/chat/ProgressBubble'
import { AgencySelector } from '@/components/chat/AgencySelector'
import { SourceSelector } from '@/components/chat/SourceSelector'
import { useSSEStream } from '@/hooks/useSSEStream'
import { addSearch } from '@/hooks/useSearchHistory'
import { DEFAULT_SELECTION, describeSelection, isSelectionEmpty, type SourceSelection } from '@/lib/sources'
import { cn } from '@/lib/utils'

function ChatPage() {
  const { messages, isStreaming, lastJobId, startScraping, resumeScraping } = useSSEStream()
  const [input, setInput] = useState('')
  // Where to scrape. Chosen before submit and sent with POST /scraping/start;
  // the default reproduces the pre-selector behaviour (search everything).
  const [selection, setSelection] = useState<SourceSelection>(DEFAULT_SELECTION)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const searchParams = useSearchParams()
  const lastQueryRef = useRef<string>('')

  useEffect(() => {
    const q = searchParams.get('q')
    if (q) {
      setInput(decodeURIComponent(q))
      textareaRef.current?.focus()
    }
  }, [searchParams])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    if (lastJobId && lastQueryRef.current) {
      addSearch(lastQueryRef.current, undefined, lastJobId)
    }
  }, [lastJobId])

  const submit = () => {
    const q = input.trim()
    if (!q || isStreaming || isSelectionEmpty(selection)) return
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
    lastQueryRef.current = q
    addSearch(q)
    void startScraping(q, undefined, undefined, selection)
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`
  }

  const isEmpty = messages.length === 0

  const inputBar = (
    <div className={cn(
      'mx-auto flex w-full max-w-3xl items-end gap-3 rounded-2xl border bg-card px-4 py-3 transition-all',
      isStreaming
        ? 'border-border opacity-60'
        : 'border-border focus-within:border-foreground/30 focus-within:ring-2 focus-within:ring-foreground/10'
    )}>
      <textarea
        ref={textareaRef}
        value={input}
        onChange={handleInput}
        onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
        placeholder={isStreaming ? 'Buscando propiedades...' : 'Ej: Departamentos en Palermo, 2 ambientes, hasta USD 150k'}
        rows={1}
        disabled={isStreaming}
        className="flex-1 resize-none bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed"
        style={{ height: 'auto', minHeight: '24px' }}
      />
      <button
        onClick={submit}
        disabled={isStreaming || !input.trim() || isSelectionEmpty(selection)}
        className={cn(
          'flex size-8 shrink-0 items-center justify-center rounded-xl transition-all',
          input.trim() && !isStreaming && !isSelectionEmpty(selection)
            ? 'bg-foreground text-background hover:bg-foreground/85'
            : 'bg-muted text-muted-foreground cursor-not-allowed'
        )}
      >
        <ArrowUp className="size-4" />
      </button>
    </div>
  )

  // Initial state: input centered vertically, like /search.
  if (isEmpty) {
    return (
      <div className="flex h-full flex-col items-center justify-center bg-background px-6 text-foreground">
        <div className="w-full max-w-3xl">
          <div className="mb-6 text-center">
            <div className="mx-auto mb-4 flex size-16 items-center justify-center rounded-2xl bg-muted ring-1 ring-border">
              <Sparkles className="size-7 text-foreground" />
            </div>
            <h2 className="text-2xl font-semibold tracking-tight text-foreground">¿Qué propiedad buscás?</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Describí en lenguaje natural y elegí dónde quiero buscar.
            </p>
          </div>
          <div className="mb-4">
            <SourceSelector value={selection} onChange={setSelection} disabled={isStreaming} />
          </div>
          {inputBar}
          <p className="mt-2 text-center text-xs text-muted-foreground">
            {isSelectionEmpty(selection)
              ? 'Elegí al menos una fuente para buscar'
              : `Buscando en: ${describeSelection(selection)}`}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col bg-background text-foreground">

      {/* Header */}
      <header className="flex items-center gap-3 border-b border-border px-6 py-4">
        <div className="flex size-8 items-center justify-center rounded-lg bg-foreground">
          <Building2 className="size-4 text-background" />
        </div>
        <div>
          <h1 className="text-sm font-semibold text-foreground">PropSearch AI</h1>
          <p className="text-xs text-muted-foreground">Buscador inteligente de propiedades</p>
        </div>
        <div className="ml-auto flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1">
          <span className="size-1.5 rounded-full bg-foreground" />
          <span className="text-xs font-medium text-foreground">Online</span>
        </div>
      </header>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
            {messages.map((m) => {
              if (m.type === 'user') return (
                <div key={m.id} className="flex justify-end">
                  <div className="max-w-[75%] rounded-2xl rounded-tr-sm bg-foreground px-4 py-2.5 text-sm text-background">
                    {m.text}
                  </div>
                </div>
              )

              if (m.type === 'agent') return (
                <div key={m.id} className="flex items-start gap-3">
                  <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-foreground">
                    <Sparkles className="size-3.5 text-background" />
                  </div>
                  <div className="max-w-[75%] rounded-2xl rounded-tl-sm bg-card px-4 py-2.5 text-sm text-foreground ring-1 ring-border">
                    {m.text}
                  </div>
                </div>
              )

              if (m.type === 'progress') return (
                <div key={m.id} className="flex items-start gap-3">
                  <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-foreground">
                    <Sparkles className="size-3.5 text-background" />
                  </div>
                  <ProgressBubble progress={m.progress} matchedCount={m.matchedCount} totalCount={m.totalCount} />
                </div>
              )

              if (m.type === 'agencies') return (
                <div key={m.id} className="flex items-start gap-3">
                  <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-foreground">
                    <Sparkles className="size-3.5 text-background" />
                  </div>
                  <AgencySelector
                    agencies={m.agencies}
                    message={m.message}
                    onConfirm={(ids) => resumeScraping(m.jobId, ids)}
                    disabled={isStreaming}
                  />
                </div>
              )

              if (m.type === 'done') return (
                <div key={m.id} className="flex items-start gap-3">
                  <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-foreground">
                    <Sparkles className="size-3.5 text-background" />
                  </div>
                  <SearchDoneCard
                    jobId={m.jobId}
                    matchedCount={m.matchedCount}
                    totalCount={m.totalCount}
                    apifyCostUsd={m.apifyCostUsd}
                    apifyCostBreakdown={m.apifyCostBreakdown}
                  />
                </div>
              )

              return null
            })}
            <div ref={bottomRef} />
        </div>
      </div>

      {/* Input */}
      <div className="border-t border-border p-4">
        <div className="mx-auto mb-3 w-full max-w-3xl">
          <SourceSelector value={selection} onChange={setSelection} disabled={isStreaming} />
        </div>
        {inputBar}
        <p className="mt-2 text-center text-xs text-muted-foreground">
          {isSelectionEmpty(selection)
            ? 'Elegí al menos una fuente para buscar'
            : `Buscando en: ${describeSelection(selection)}`}
        </p>
      </div>
    </div>
  )
}

export default function ChatPageWrapper() {
  return (
    <Suspense fallback={null}>
      <ChatPage />
    </Suspense>
  )
}
