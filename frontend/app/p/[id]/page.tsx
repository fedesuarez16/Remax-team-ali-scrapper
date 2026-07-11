'use client'
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import {
  Bath, Building2, Calendar, Car, ImageIcon, LayoutGrid, Loader2,
  Mail, MapPin, MessageCircle, Phone, Ruler, Share2, Sparkles,
} from 'lucide-react'
import type { Property } from '@/hooks/useSSEStream'
import { AGENTE } from '@/lib/ficha'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

function fmtPrice(p: Property) {
  if (p.precio == null) return 'Consultar precio'
  const n = new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 }).format(p.precio)
  return `${p.moneda ?? 'USD'} ${n}${p.tipo_operacion !== 'venta' ? '/mes' : ''}`
}

function Feature({ icon: Icon, value, label }: { icon: typeof Bath; value: string; label: string }) {
  return (
    <div className="flex flex-col items-center gap-1 rounded-xl border border-border bg-card px-3 py-3 text-center">
      <Icon className="size-5 text-foreground" />
      <span className="text-sm font-semibold text-foreground">{value}</span>
      <span className="text-[11px] text-muted-foreground">{label}</span>
    </div>
  )
}

function Gallery({ imgs, title }: { imgs: string[]; title: string }) {
  const [active, setActive] = useState(0)

  if (imgs.length === 0) {
    return (
      <div className="flex aspect-[16/9] w-full items-center justify-center rounded-2xl bg-muted">
        <ImageIcon className="size-12 text-muted-foreground/40" />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="aspect-[16/9] w-full overflow-hidden rounded-2xl bg-muted">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={imgs[active]} alt={title} className="h-full w-full object-cover" />
      </div>
      {imgs.length > 1 && (
        <div className="flex gap-2 overflow-x-auto pb-1">
          {imgs.map((src, i) => (
            <button
              key={i}
              onClick={() => setActive(i)}
              className={`aspect-[4/3] w-24 shrink-0 overflow-hidden rounded-lg border-2 transition ${
                i === active ? 'border-foreground' : 'border-transparent opacity-70 hover:opacity-100'
              }`}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={src} alt={`${title} ${i + 1}`} className="h-full w-full object-cover" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function ContactCard({ p }: { p: Property }) {
  const waDigits = AGENTE.telefono.replace(/\D/g, '')
  const waText = encodeURIComponent(`Hola ${AGENTE.nombre}, me interesa la propiedad "${p.titulo ?? p.direccion}" (${fmtPrice(p)}). ¿Sigue disponible?`)

  return (
    <div className="space-y-4 rounded-2xl border border-border bg-card p-5">
      <div className="flex items-center gap-3">
        <div className="flex size-11 items-center justify-center rounded-xl bg-foreground">
          <Building2 className="size-5 text-background" />
        </div>
        <div>
          <p className="text-sm font-semibold text-foreground">{AGENTE.inmobiliaria}</p>
          <p className="text-xs text-muted-foreground">{AGENTE.nombre} · {AGENTE.matricula}</p>
        </div>
      </div>

      <a
        href={`https://wa.me/${waDigits}?text=${waText}`}
        target="_blank"
        rel="noopener"
        className="flex w-full items-center justify-center gap-2 rounded-xl bg-foreground px-4 py-2.5 text-sm font-medium text-background transition hover:bg-foreground/85"
      >
        <MessageCircle className="size-4" />
        Consultar por WhatsApp
      </a>

      <div className="space-y-2 text-sm">
        <a href={`tel:${waDigits}`} className="flex items-center gap-2 text-foreground transition hover:text-muted-foreground">
          <Phone className="size-4 text-muted-foreground" />{AGENTE.telefono}
        </a>
        <a href={`mailto:${AGENTE.email}`} className="flex items-center gap-2 text-foreground transition hover:text-muted-foreground">
          <Mail className="size-4 text-muted-foreground" />{AGENTE.email}
        </a>
      </div>
    </div>
  )
}

export default function PublicListingPage() {
  const params = useParams<{ id: string }>()
  const id = params?.id
  const [p, setP] = useState<Property | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [shared, setShared] = useState(false)

  useEffect(() => {
    if (!id) return
    const ctrl = new AbortController()
    setLoading(true)
    setError(null)
    ;(async () => {
      try {
        const res = await fetch(`${API}/api/v1/properties/${encodeURIComponent(id)}`, { signal: ctrl.signal })
        if (res.status === 404) throw new Error('Esta publicación no existe o fue dada de baja.')
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json()
        if (data.error) throw new Error(data.error)
        setP(data.property)
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') return
        setError(e instanceof Error ? e.message : 'Error desconocido')
      } finally {
        setLoading(false)
      }
    })()
    return () => ctrl.abort()
  }, [id])

  const share = async () => {
    const url = typeof window !== 'undefined' ? window.location.href : ''
    try {
      if (navigator.share) await navigator.share({ title: p?.titulo ?? 'Propiedad', url })
      else {
        await navigator.clipboard.writeText(url)
        setShared(true)
        setTimeout(() => setShared(false), 1500)
      }
    } catch { /* cancelado */ }
  }

  if (loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error || !p) {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-2 bg-background px-6 text-center">
        <Building2 className="size-10 text-muted-foreground/40" />
        <p className="text-sm font-medium text-foreground">No se pudo cargar la publicación</p>
        <p className="text-xs text-muted-foreground">{error ?? 'Propiedad no encontrada.'}</p>
      </div>
    )
  }

  const features: { icon: typeof Bath; value: string; label: string }[] = []
  if (p.ambientes != null) features.push({ icon: LayoutGrid, value: String(p.ambientes), label: 'ambientes' })
  if (p.banos != null) features.push({ icon: Bath, value: String(p.banos), label: 'baños' })
  if (p.cocheras != null) features.push({ icon: Car, value: String(p.cocheras), label: 'cocheras' })
  if (p.m2_total != null) features.push({ icon: Ruler, value: `${p.m2_total}`, label: 'm² totales' })
  if (p.piso != null) features.push({ icon: Building2, value: String(p.piso), label: 'piso' })
  if (p.antiguedad != null) features.push({ icon: Calendar, value: p.antiguedad === 0 ? '0' : String(p.antiguedad), label: p.antiguedad === 0 ? 'a estrenar' : 'años' })
  // Highlights parsed from the description by the LLM — rendered as boxes like the rest.
  for (const d of p.destacados ?? []) features.push({ icon: Sparkles, value: d.value, label: d.label })

  return (
    <div className="min-h-dvh bg-background text-foreground">
      {/* Public top bar */}
      <header className="sticky top-0 z-30 border-b border-border bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-2.5">
            <div className="flex size-8 items-center justify-center rounded-lg bg-foreground">
              <Building2 className="size-4 text-background" />
            </div>
            <span className="text-sm font-semibold">{AGENTE.inmobiliaria}</span>
          </div>
          <button
            onClick={share}
            className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-muted"
          >
            <Share2 className="size-3.5" />
            {shared ? 'Link copiado' : 'Compartir'}
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-4 py-6">
        {/* Price + title header */}
        <div className="mb-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-foreground px-2.5 py-0.5 text-xs font-medium text-background">
              {p.tipo_operacion === 'venta' ? 'Venta' : 'Alquiler'}
            </span>
            {p.tipo_propiedad && (
              <span className="rounded-full border border-border bg-muted px-2.5 py-0.5 text-xs font-medium text-foreground">
                {p.tipo_propiedad.charAt(0).toUpperCase() + p.tipo_propiedad.slice(1)}
              </span>
            )}
          </div>
          <h1 className="mt-2 text-2xl font-bold tracking-tight sm:text-3xl">{fmtPrice(p)}</h1>
          {p.expensas != null && (
            <p className="text-sm text-muted-foreground">
              + {new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', maximumFractionDigits: 0 }).format(p.expensas)} expensas
            </p>
          )}
          {p.titulo && <p className="mt-2 text-lg font-medium text-foreground">{p.titulo}</p>}
          {p.direccion && (
            <div className="mt-1 flex items-start gap-1.5 text-muted-foreground">
              <MapPin className="mt-0.5 size-4 shrink-0" />
              <p className="text-sm">{p.direccion}</p>
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          {/* Main column */}
          <div className="space-y-6 lg:col-span-2">
            <Gallery imgs={p.imagenes ?? []} title={p.titulo ?? p.direccion} />

            {features.length > 0 && (
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
                {features.map((f) => <Feature key={f.label} {...f} />)}
              </div>
            )}

            {p.descripcion && (
              <section>
                <h2 className="mb-2 text-lg font-semibold">Descripción</h2>
                <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">{p.descripcion}</p>
              </section>
            )}

            {p.amenities && p.amenities.length > 0 && (
              <section>
                <h2 className="mb-2 text-lg font-semibold">Características</h2>
                <div className="flex flex-wrap gap-2">
                  {p.amenities.map((a) => (
                    <span key={a} className="rounded-full border border-border bg-muted/50 px-3 py-1 text-sm text-foreground">
                      {a}
                    </span>
                  ))}
                </div>
              </section>
            )}
          </div>

          {/* Contact column */}
          <aside className="lg:col-span-1">
            <div className="lg:sticky lg:top-20">
              <ContactCard p={p} />
            </div>
          </aside>
        </div>
      </main>

      <footer className="mt-8 border-t border-border">
        <div className="mx-auto max-w-5xl px-4 py-6 text-xs text-muted-foreground">
          <p className="font-medium text-foreground">{AGENTE.inmobiliaria}</p>
          <p className="mt-0.5">{AGENTE.nombre} · {AGENTE.matricula} · {AGENTE.telefono}</p>
          <p className="mt-2">Publicación generada por {AGENTE.inmobiliaria}. La información puede estar sujeta a modificaciones sin previo aviso.</p>
        </div>
      </footer>
    </div>
  )
}
