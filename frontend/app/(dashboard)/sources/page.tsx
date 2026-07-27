'use client'
import { useState } from 'react'
import { Globe, Loader2, Plus, Trash2 } from 'lucide-react'
import { useManualSources } from '@/hooks/useManualSources'

export default function SourcesPage() {
  const { sources, addSource, deleteSource, loading } = useManualSources()
  const [nombre, setNombre] = useState('')
  const [url, setUrl] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const handleSubmit = async () => {
    setSaving(true)
    setError(null)
    const err = await addSource(nombre, url)
    setSaving(false)
    if (err) {
      setError(err)
      return
    }
    setNombre('')
    setUrl('')
  }

  const handleDelete = async (id: string) => {
    setDeletingId(id)
    await deleteSource(id)
    setDeletingId(null)
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-background text-foreground">
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto w-full max-w-2xl">
          <h1 className="text-xl font-semibold tracking-tight">Fuentes</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Cargá inmobiliarias o portales que no aparecen en Google (ej: la web de una oficina RE/MAX).
            Cada búsqueda futura también va a rastrear estos sitios.
          </p>
        </div>
      </header>

      <div className="mx-auto w-full max-w-2xl flex-1 space-y-6 p-6">
        <div className="space-y-2 rounded-2xl border border-border bg-card p-4 shadow-sm">
          <input
            type="text"
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="Nombre (ej: RE/MAX Belgrano)"
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground/20"
          />
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !saving && handleSubmit()}
            placeholder="https://www.remax.com.ar/agencia/belgrano"
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground/20"
          />
          {error && <p className="text-xs text-destructive">{error}</p>}
          <button
            onClick={handleSubmit}
            disabled={saving || !nombre.trim() || !url.trim()}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-foreground px-3 py-2 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:opacity-40"
          >
            {saving ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Agregar fuente
          </button>
        </div>

        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {loading ? 'Cargando...' : `${sources.length} fuente${sources.length === 1 ? '' : 's'} registrada${sources.length === 1 ? '' : 's'}`}
          </p>

          {!loading && sources.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-10 text-center">
              <Globe className="size-8 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">Todavía no cargaste ninguna fuente</p>
            </div>
          ) : (
            <ul className="space-y-2">
              {sources.map((s) => (
                <li
                  key={s.id}
                  className="flex items-center gap-3 rounded-xl border border-border bg-card px-3 py-2.5"
                >
                  <Globe className="size-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-foreground">{s.nombre}</p>
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="truncate text-xs text-muted-foreground hover:text-foreground hover:underline"
                    >
                      {s.url}
                    </a>
                  </div>
                  <button
                    onClick={() => handleDelete(s.id)}
                    disabled={deletingId === s.id}
                    className="shrink-0 rounded-md p-1.5 text-muted-foreground transition hover:bg-muted hover:text-destructive disabled:opacity-40"
                    aria-label={`Eliminar ${s.nombre}`}
                  >
                    {deletingId === s.id ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Trash2 className="size-4" />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
