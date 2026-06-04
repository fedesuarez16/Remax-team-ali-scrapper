'use client'
import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Building2, MapPin, Sparkles } from 'lucide-react'
import { PropertyCard } from '@/components/chat/PropertyCard'
import { ProgressBubble } from '@/components/chat/ProgressBubble'
import { useSSEStream } from '@/hooks/useSSEStream'
import { cn } from '@/lib/utils'

const SUGGESTIONS = [
  'Departamentos en Palermo, 2 ambientes',
  'Casas en Belgrano hasta USD 300.000',
  'PH en Villa Crespo 3 ambientes',
  'Departamentos en Recoleta con cochera',
]

export default function Home() {
  const { messages, isStreaming, startScraping } = useSSEStream()
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const submit = () => {
    const q = input.trim()
    if (!q || isStreaming) return
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
    void startScraping(q)
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`
  }

  const isEmpty = messages.length === 0

  return (
    <div className="flex h-dvh flex-col bg-[#0a0a0a] text-zinc-100">

      {/* Header */}
      <header className="flex items-center gap-3 border-b border-white/[0.06] px-6 py-4">
        <div className="flex size-8 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600 shadow-lg shadow-violet-500/20">
          <Building2 className="size-4 text-white" />
        </div>
        <div>
          <h1 className="text-sm font-semibold text-zinc-100">PropSearch AI</h1>
          <p className="text-xs text-zinc-500">Buscador inteligente de propiedades</p>
        </div>
        <div className="ml-auto flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1">
          <span className="size-1.5 rounded-full bg-emerald-500" />
          <span className="text-xs font-medium text-emerald-400">Online</span>
        </div>
      </header>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto">
        {isEmpty ? (
          <div className="flex h-full flex-col items-center justify-center gap-8 px-4">
            <div className="text-center">
              <div className="mx-auto mb-4 flex size-16 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500/20 to-indigo-600/20 ring-1 ring-white/10">
                <Sparkles className="size-7 text-violet-400" />
              </div>
              <h2 className="text-2xl font-semibold text-zinc-100">¿Qué propiedad buscás?</h2>
              <p className="mt-2 text-sm text-zinc-500">
                Describí en lenguaje natural y busco en todos los portales.
              </p>
            </div>
            <div className="grid w-full max-w-lg grid-cols-1 gap-2 sm:grid-cols-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => { setInput(s); textareaRef.current?.focus() }}
                  className="flex items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-left text-sm text-zinc-400 transition hover:border-white/[0.16] hover:bg-white/[0.06] hover:text-zinc-200"
                >
                  <MapPin className="size-3.5 shrink-0 text-violet-400" />
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
            {messages.map((m) => {
              if (m.type === 'user') return (
                <div key={m.id} className="flex justify-end">
                  <div className="max-w-[75%] rounded-2xl rounded-tr-sm bg-violet-600 px-4 py-2.5 text-sm text-white shadow-lg shadow-violet-500/10">
                    {m.text}
                  </div>
                </div>
              )

              if (m.type === 'agent') return (
                <div key={m.id} className="flex items-start gap-3">
                  <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600 shadow shadow-violet-500/20">
                    <Sparkles className="size-3.5 text-white" />
                  </div>
                  <div className="max-w-[75%] rounded-2xl rounded-tl-sm bg-white/[0.06] px-4 py-2.5 text-sm text-zinc-200 ring-1 ring-white/[0.08]">
                    {m.text}
                  </div>
                </div>
              )

              if (m.type === 'progress') return (
                <div key={m.id} className="flex items-start gap-3">
                  <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600 shadow shadow-violet-500/20">
                    <Sparkles className="size-3.5 text-white" />
                  </div>
                  <ProgressBubble progress={m.progress} />
                </div>
              )

              return (
                <div key={m.id} className="space-y-3">
                  <div className="flex items-center gap-2 text-xs text-zinc-500">
                    <div className="h-px flex-1 bg-white/[0.06]" />
                    <span>{m.properties.length} propiedades encontradas</span>
                    <div className="h-px flex-1 bg-white/[0.06]" />
                  </div>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {m.properties.map((p, i) => <PropertyCard key={i} p={p} />)}
                  </div>
                </div>
              )
            })}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-white/[0.06] p-4">
        <div className={cn(
          'mx-auto flex w-full max-w-3xl items-end gap-3 rounded-2xl border bg-white/[0.04] px-4 py-3 transition-all',
          isStreaming
            ? 'border-white/[0.06] opacity-60'
            : 'border-white/[0.10] focus-within:border-violet-500/40 focus-within:bg-white/[0.06]'
        )}>
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleInput}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
            placeholder={isStreaming ? 'Buscando propiedades...' : 'Ej: Departamentos en Palermo, 2 ambientes, hasta USD 150k'}
            rows={1}
            disabled={isStreaming}
            className="flex-1 resize-none bg-transparent text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none disabled:cursor-not-allowed"
            style={{ height: 'auto', minHeight: '24px' }}
          />
          <button
            onClick={submit}
            disabled={isStreaming || !input.trim()}
            className={cn(
              'flex size-8 shrink-0 items-center justify-center rounded-xl transition-all',
              input.trim() && !isStreaming
                ? 'bg-violet-600 text-white shadow-lg shadow-violet-500/20 hover:bg-violet-500'
                : 'bg-white/[0.06] text-zinc-600 cursor-not-allowed'
            )}
          >
            <ArrowUp className="size-4" />
          </button>
        </div>
        <p className="mt-2 text-center text-xs text-zinc-700">
          Shift + Enter para nueva línea
        </p>
      </div>
    </div>
  )
}
