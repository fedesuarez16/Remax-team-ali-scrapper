'use client'
import { useEffect, useRef, useState } from 'react'
import { Loader2, Search, Sparkles, X } from 'lucide-react'
import { PropertyCard } from '@/components/chat/PropertyCard'
import type { Property } from '@/hooks/useSSEStream'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

type MatchResult = {
  property: Property
  score: number
  match_reasons: string[]
}

export default function SearchPage() {
  const [term, setTerm] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [results, setResults] = useState<MatchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Once a search has been submitted the input docks to the top.
  const active = submitted !== '' || loading

  useEffect(() => {
    const q = submitted.trim()
    if (!q) return

    const ctrl = new AbortController()
    setLoading(true)
    setError(null)

    ;(async () => {
      try {
        const res = await fetch(`${API}/api/v1/properties/match`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: q }),
          signal: ctrl.signal,
        })
        const data = await res.json()
        if (data.error) throw new Error(data.error)
        setResults(data.results ?? [])
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') return
        setError(e instanceof Error ? e.message : 'Error desconocido')
        setResults([])
      } finally {
        setLoading(false)
      }
    })()

    return () => ctrl.abort()
  }, [submitted])

  const runSearch = () => {
    const q = term.trim()
    if (!q) return
    setSubmitted(q)
  }

  const reset = () => {
    setTerm('')
    setSubmitted('')
    setResults([])
    setError(null)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  const SearchBar = (
    <div className="flex items-center gap-2 rounded-2xl border border-border bg-card px-4 py-3 shadow-sm focus-within:border-foreground/30 focus-within:ring-1 focus-within:ring-foreground/20">
      <Sparkles className="size-4 shrink-0 text-muted-foreground" />
      <input
        ref={inputRef}
        autoFocus
        type="text"
        value={term}
        onChange={(e) => setTerm(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && runSearch()}
        placeholder="Describí lo que buscás: “2 ambientes luminoso en Palermo con balcón”"
        className="w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none"
      />
      {term && (
        <button
          onClick={reset}
          className="shrink-0 rounded-md p-0.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
          aria-label="Limpiar"
        >
          <X className="size-4" />
        </button>
      )}
      <button
        onClick={runSearch}
        disabled={!term.trim()}
        className="shrink-0 rounded-xl bg-foreground px-3 py-1.5 text-xs font-medium text-background transition hover:bg-foreground/85 disabled:opacity-40"
      >
        Buscar
      </button>
    </div>
  )

  // Initial state: a single input centered vertically.
  if (!active) {
    return (
      <div className="flex h-full flex-col items-center justify-center bg-background px-6 text-foreground">
        <div className="w-full max-w-2xl">
          <div className="mb-6 text-center">
            <div className="mx-auto mb-3 flex size-11 items-center justify-center rounded-2xl bg-foreground">
              <Sparkles className="size-5 text-background" />
            </div>
            <h1 className="text-xl font-semibold tracking-tight">Búsqueda semántica</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Describí la propiedad en lenguaje natural y encontrá coincidencias en tu base.
            </p>
          </div>
          {SearchBar}
        </div>
      </div>
    )
  }

  // Active state: input docked at top, results below.
  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto w-full max-w-2xl">{SearchBar}</div>
        <p className="mx-auto mt-2 w-full max-w-2xl text-xs text-muted-foreground">
          {loading
            ? 'Analizando coincidencias semánticas...'
            : `${results.length} coincidencia${results.length === 1 ? '' : 's'} para “${submitted}”`}
        </p>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        {loading && results.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <p className="text-sm text-foreground">No se pudo buscar</p>
            <p className="text-xs text-muted-foreground">{error}</p>
          </div>
        ) : results.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <Search className="size-10 text-muted-foreground/40" />
            <div>
              <p className="text-sm font-medium text-foreground">Sin coincidencias para “{submitted}”</p>
              <p className="mt-1 text-xs text-muted-foreground">Probá con otra descripción o barrio.</p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {results.map((r, i) => (
              <div key={i} className="relative">
                <div className="absolute right-2 top-2 z-10 rounded-full bg-foreground px-2 py-0.5 text-xs font-semibold text-background">
                  {r.score}%
                </div>
                <PropertyCard p={r.property} />
                {r.match_reasons.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1 px-1">
                    {r.match_reasons.map((reason, j) => (
                      <span key={j} className="rounded-md bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                        {reason}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
