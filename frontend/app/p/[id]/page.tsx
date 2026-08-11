'use client'
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import {
  Bath, Building2, Calendar, Car, ImageIcon, LayoutGrid, Loader2,
  Mail, MapPin, MessageCircle, Phone, Ruler, Share2, Sparkles,
} from 'lucide-react'
import type { Property } from '@/hooks/useSSEStream'
import {
  AGENTE, agenteByEmail, whatsappUrl, type Agente, type FichaTextos,
} from '@/lib/ficha'
import { useFichaTextos } from '@/hooks/useFichaTextos'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

function fmtPrice(p: Property) {
  if (p.precio == null) return 'Consultar precio'
  const n = new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 }).format(p.precio)
  return `${p.moneda ?? 'USD'} ${n}${p.tipo_operacion !== 'venta' ? '/mes' : ''}`
}

function Feature({ icon: Icon, value, label }: { icon: typeof Bath; value: string; label: string }) {
  // min-w-0 + break-words: los "destacados" traen textos libres del LLM que
  // pueden ser largos — tienen que envolver dentro de la card, nunca pisarse.
  return (
    <div className="flex min-w-0 flex-col items-center justify-center gap-1 rounded-xl border border-border bg-card px-2.5 py-3 text-center">
      <Icon className="size-5 shrink-0 text-foreground" />
      <span className="w-full break-words text-sm font-semibold leading-snug text-foreground">{value}</span>
      <span className="w-full break-words text-[11px] leading-tight text-muted-foreground">{label}</span>
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

// lucide-react 1.x sacó los íconos de marca, así que van inline en vez de sumar
// una dependencia sólo por dos glifos.
function InstagramIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <rect width="20" height="20" x="2" y="2" rx="5" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="17.5" cy="6.5" r="1" fill="currentColor" stroke="none" />
    </svg>
  )
}

function FacebookIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <path d="M18 2h-3a5 5 0 0 0-5 5v3H7v4h3v8h4v-8h3l1-4h-4V7a1 1 0 0 1 1-1h3z" />
    </svg>
  )
}

function AgenteCard({ a, p }: { a: Agente; p: Property }) {
  const waDigits = a.telefono.replace(/\D/g, '')
  const waUrl = whatsappUrl(
    a.telefono,
    `Hola ${a.nombre}, me interesa la propiedad "${p.titulo ?? p.direccion}" (${fmtPrice(p)}). ¿Sigue disponible?`,
  )

  return (
    <div className="space-y-3 rounded-2xl border border-border bg-card p-5">
      <div className="flex items-center gap-3">
        <div className="flex size-11 items-center justify-center rounded-xl bg-foreground">
          <Building2 className="size-5 text-background" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">
            {a.nombre} <span className="font-normal text-muted-foreground">| {a.inmobiliaria}</span>
          </p>
          <p className="truncate text-xs text-muted-foreground">{a.cargo}</p>
        </div>
      </div>

      <a
        href={waUrl}
        target="_blank"
        rel="noopener"
        className="flex w-full items-center justify-center gap-2 rounded-xl bg-foreground px-4 py-2.5 text-sm font-medium text-background transition hover:bg-foreground/85"
      >
        <MessageCircle className="size-4" />
        Consultar por WhatsApp
      </a>

      {/* Cada red se evalúa por separado: Nahir y Ahmed no tienen Facebook, y
          un botón que lleva a otro perfil es peor que no tener botón. */}
      {(a.instagram || a.facebook) && (
        <div className="flex gap-2">
          {a.instagram && (
            <a
              href={a.instagram}
              target="_blank"
              rel="noopener"
              title={`Instagram de ${a.nombre}`}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-xl border border-border bg-background px-3 py-2 text-xs font-medium text-foreground transition hover:bg-muted"
            >
              <InstagramIcon className="size-3.5" />
              Instagram
            </a>
          )}
          {a.facebook && (
            <a
              href={a.facebook}
              target="_blank"
              rel="noopener"
              title={`Facebook de ${a.nombre}`}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-xl border border-border bg-background px-3 py-2 text-xs font-medium text-foreground transition hover:bg-muted"
            >
              <FacebookIcon className="size-3.5" />
              Facebook
            </a>
          )}
        </div>
      )}

      <div className="space-y-1.5 text-sm">
        <a href={`tel:${waDigits}`} className="flex items-center gap-2 text-foreground transition hover:text-muted-foreground">
          <Phone className="size-4 shrink-0 text-muted-foreground" />
          {a.telefono}
        </a>
        <a href={`mailto:${a.email}`} className="flex items-center gap-2 text-foreground transition hover:text-muted-foreground">
          <Mail className="size-4 shrink-0 text-muted-foreground" />
          <span className="truncate">{a.email}</span>
        </a>
      </div>
    </div>
  )
}

function ContactColumn({ p, textos }: { p: Property; textos: FichaTextos }) {
  // Un único contacto: el agente a cuyo nombre se generó esta ficha.
  const a = agenteByEmail(p.agente_email)
  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-border bg-muted/40 p-4">
        <p className="whitespace-pre-line text-sm leading-relaxed text-foreground">
          {textos.texto_seleccion}
        </p>
      </div>
      <AgenteCard a={a} p={p} />
    </div>
  )
}

export default function PublicListingPage() {
  const params = useParams<{ id: string }>()
  const id = params?.id
  const [p, setP] = useState<Property | null>(null)
  // Textos del equipo. El hook arranca en los defaults, así el pie nunca se
  // renderiza vacío mientras carga ni si el backend no responde.
  const { textos } = useFichaTextos()
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
              /* auto-fit: las cards se reparten según cuántas haya y ninguna
                 queda tan angosta como para que el texto desborde. */
              <div className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(6.5rem,1fr))]">
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
              <ContactColumn p={p} textos={textos} />
            </div>
          </aside>
        </div>
      </main>

      {/* Pie editable desde el editor de ficha. Los textos son del equipo, no de
          esta propiedad: cambiarlos reescribe el pie de todas las fichas. */}
      <footer className="mt-8 border-t border-border">
        <div className="mx-auto max-w-5xl space-y-3 px-4 py-6 text-xs text-muted-foreground">
          <div>
            <p className="font-medium text-foreground">{textos.firma}</p>
            <p className="mt-0.5">{textos.colegiatura}</p>
          </div>
          {/* Descargo normativo — obligatorio en toda Ficha Propio. */}
          <p className="whitespace-pre-line leading-relaxed">{textos.disclaimer_legal}</p>
          <p className="whitespace-pre-line">{textos.pie_publicacion}</p>
        </div>
      </footer>
    </div>
  )
}
