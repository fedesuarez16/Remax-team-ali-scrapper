'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowLeft, Building2, Check, Copy, ExternalLink, Link2, Pencil, Printer, Send } from 'lucide-react'
import type { Property } from '@/hooks/useSSEStream'
import { agenteByEmail, fichaUrl, guardarSeleccion, leerSeleccion } from '@/lib/ficha'
import { PropertyFicha, fmtPrice } from '@/components/ficha/PropertyFicha'
import { FichaEditor } from '@/components/ficha/FichaEditor'

function FichaLinkBar({ p, onEdit }: { p: Property; onEdit: () => void }) {
  const url = fichaUrl(p.id)
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard no disponible */
    }
  }

  if (!p.id) return null

  return (
    <div className="space-y-1.5 print:hidden">
      {/* Nuevo link propio */}
      <div className="flex items-center gap-2 rounded-xl border border-border bg-muted/40 px-3 py-2">
        <Link2 className="size-4 shrink-0 text-foreground" />
        <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Ficha propia</span>
        <code className="flex-1 truncate text-xs text-foreground">{url}</code>
        <button
          onClick={copy}
          className="flex shrink-0 items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground transition hover:bg-muted"
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          {copied ? 'Copiado' : 'Copiar link'}
        </button>
        <a
          href={url}
          target="_blank"
          rel="noopener"
          className="flex shrink-0 items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground transition hover:bg-muted"
        >
          <ExternalLink className="size-3.5" />
          Abrir
        </a>
        <button
          onClick={onEdit}
          className="flex shrink-0 items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground transition hover:bg-muted"
        >
          <Pencil className="size-3.5" />
          Editar
        </button>
      </div>

      {/* Link original del portal */}
      {p.url_origen && (
        <div className="flex items-center gap-2 px-3">
          <ExternalLink className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Original</span>
          <a
            href={p.url_origen}
            target="_blank"
            rel="noopener"
            className="flex-1 truncate text-xs text-muted-foreground underline-offset-2 transition hover:text-foreground hover:underline"
          >
            {p.url_origen}
          </a>
        </div>
      )}
    </div>
  )
}

export default function FichaPage() {
  const router = useRouter()
  const [props, setProps] = useState<Property[]>([])
  const [editingId, setEditingId] = useState<string | null>(null)

  useEffect(() => {
    setProps(leerSeleccion())
  }, [])

  const onSaved = (updated: Property) => {
    setProps((prev) => {
      const next = prev.map((p) => (p.id === updated.id ? updated : p))
      guardarSeleccion(next)
      return next
    })
    setEditingId(null)
  }

  const enviarWhatsApp = () => {
    const lineas = props.map((p) => {
      const url = fichaUrl(p.id) || p.url_origen || ''
      return `• ${p.titulo ?? p.direccion} — ${fmtPrice(p)}${url ? `\n  ${url}` : ''}`
    })
    // Firma = el perfil con el que se prepararon estas fichas, no el titular
    // global: el mensaje y el contacto de la ficha tienen que coincidir.
    const a = agenteByEmail(props[0]?.agente_email)
    const texto = `Hola! Te comparto ${props.length} ${props.length === 1 ? 'propiedad' : 'propiedades'} seleccionadas:\n\n${lineas.join('\n\n')}\n\n${a.nombre} · ${a.inmobiliaria}\n${a.telefono}`
    window.open(`https://wa.me/?text=${encodeURIComponent(texto)}`, '_blank', 'noopener')
  }

  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      {/* Toolbar — hidden on print */}
      <header className="flex items-center justify-between gap-3 border-b border-border px-6 py-4 print:hidden">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.back()}
            className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-muted"
          >
            <ArrowLeft className="size-3.5" />
            Volver
          </button>
          <div>
            <h1 className="text-sm font-semibold">Fichas generadas</h1>
            <p className="text-xs text-muted-foreground">
              {props.length} {props.length === 1 ? 'propiedad' : 'propiedades'} · link propio por ficha
            </p>
          </div>
        </div>
        {props.length > 0 && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => window.print()}
              className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-muted"
            >
              <Printer className="size-3.5" />
              Imprimir / PDF
            </button>
            <button
              onClick={enviarWhatsApp}
              className="flex items-center gap-2 rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background transition hover:bg-foreground/85"
            >
              <Send className="size-4" />
              Enviar
            </button>
          </div>
        )}
      </header>

      {/* Sheets */}
      <div className="flex-1 overflow-y-auto p-6 print:overflow-visible print:p-0">
        {props.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <Building2 className="size-10 text-muted-foreground/40" />
            <div>
              <p className="text-sm font-medium text-foreground">No hay propiedades seleccionadas</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Volvé a los resultados, seleccioná propiedades y tocá “Preparar y enviar”.
              </p>
            </div>
            <button
              onClick={() => router.push('/properties')}
              className="mt-1 rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background transition hover:bg-foreground/85"
            >
              Ir a propiedades
            </button>
          </div>
        ) : (
          <div className="mx-auto grid max-w-3xl gap-6">
            {props.map((p, i) => (
              <div key={p.id ?? i} className="space-y-2">
                <FichaLinkBar p={p} onEdit={() => setEditingId(p.id ?? null)} />
                {p.id && editingId === p.id ? (
                  <FichaEditor p={p} onSaved={onSaved} onCancel={() => setEditingId(null)} />
                ) : (
                  <PropertyFicha p={p} />
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
