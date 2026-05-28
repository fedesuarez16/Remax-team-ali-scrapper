'use client'
import { useState } from 'react'
import { Send } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { PropertyCard } from '@/components/chat/PropertyCard'
import { ProgressBubble } from '@/components/chat/ProgressBubble'
import { useSSEStream } from '@/hooks/useSSEStream'

export default function Home() {
  const { messages, isStreaming, startScraping } = useSSEStream()
  const [input, setInput] = useState('')

  const submit = () => {
    const q = input.trim()
    if (!q || isStreaming) return
    setInput('')
    void startScraping(q)
  }

  return (
    <div className="mx-auto flex h-dvh w-full max-w-3xl flex-col">
      <header className="border-b border-border px-4 py-3">
        <h1 className="text-lg font-semibold">Buscador de propiedades</h1>
        <p className="text-sm text-muted-foreground">Describí qué buscás en lenguaje natural.</p>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {messages.length === 0 && (
          <p className="pt-12 text-center text-sm text-muted-foreground">
            Ej: &quot;Departamentos de 2 ambientes en alquiler en Palermo&quot;
          </p>
        )}
        {messages.map((m) => {
          if (m.type === 'user')
            return <div key={m.id} className="ml-auto max-w-md rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">{m.text}</div>
          if (m.type === 'agent')
            return <div key={m.id} className="max-w-md rounded-lg bg-muted px-3 py-2 text-sm">{m.text}</div>
          if (m.type === 'progress')
            return <ProgressBubble key={m.id} progress={m.progress} />
          return (
            <div key={m.id} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {m.properties.map((p, i) => <PropertyCard key={i} p={p} />)}
            </div>
          )
        })}
      </div>

      <div className="border-t border-border p-3">
        <div className="flex items-end gap-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
            placeholder="Buscar propiedades..."
            rows={1}
            disabled={isStreaming}
            className="min-h-10 resize-none"
          />
          <Button onClick={submit} disabled={isStreaming || !input.trim()} size="icon">
            <Send className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
