'use client'
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import {
  Bath, Building2, Calendar, Car, LayoutGrid, Loader2, MapPin, Ruler, Share2, Sparkles,
} from 'lucide-react'
import type { Property } from '@/hooks/useSSEStream'
import { AGENTE, agenteByEmail, type FichaTextos } from '@/lib/ficha'
import { AgenteCard } from '@/components/ficha/AgenteCard'
import { FichaGallery } from '@/components/ficha/FichaGallery'
import { fmtPrice } from '@/components/ficha/PropertyFicha'
import { useFichaTextos } from '@/hooks/useFichaTextos'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

function Feature({ icon: Icon, value, label }: { icon: typeof Bath; value: string; label: string }) {
  // min-w-0 + break-words: los "destacados" traen textos libres del LLM que
  // pueden ser largos — tienen que envolver dentro de la card, nunca pisarse.
  return (
    <div className="flex min-w-0 flex-col items-start gap-2 rounded-2xl bg-muted/60 p-4">
      <Icon className="size-5 shrink-0 text-muted-foreground" />
      <span className="w-full break-words text-sm font-semibold leading-snug text-foreground">{value}</span>
      <span className="w-full break-words text-[11px] leading-tight text-muted-foreground">{label}</span>
    </div>
  )
}

function ContactColumn({ p, textos }: { p: Property; textos: FichaTextos }) {
  // Un único contacto: el agente a cuyo nombre se generó esta ficha.
  const a = agenteByEmail(p.agente_email)
  return (
    <div className="space-y-5">
      <AgenteCard
        a={a}
        mensaje={`Hola ${a.nombre}, me interesa la propiedad "${p.titulo ?? p.direccion}" (${fmtPrice(p)}). ¿Sigue disponible?`}
      />
      <div className="px-2">
        <p className="mb-2 text-xs font-semibold">Una selección para vos</p>
        <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">
          {textos.texto_seleccion}
        </p>
      </div>
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
    <div className="min-h-dvh bg-muted/25 text-foreground">
      {/* Public top bar */}
      <header className="sticky top-0 z-30 border-b border-border bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-5 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex size-8 items-center justify-center rounded-lg bg-foreground">
              <Building2 className="size-4 text-background" />
            </div>
            <div><p className="text-sm font-semibold tracking-tight">Team Alí</p><p className="text-[10px] text-muted-foreground">{AGENTE.inmobiliaria}</p></div>
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

      <main className="mx-auto max-w-6xl px-5 py-7 sm:py-10">
        <div className="mb-7 flex flex-wrap items-end justify-between gap-5">
          <div className="min-w-0 flex-1 basis-96">
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
              <span className="rounded-full bg-foreground px-3 py-1 font-medium text-background">{p.tipo_operacion === 'venta' ? 'En venta' : 'En alquiler'}</span>
              {p.tipo_propiedad && <span className="rounded-full border border-border bg-card px-3 py-1 capitalize">{p.tipo_propiedad}</span>}
            </div>
            <h1 className="break-words text-2xl font-semibold leading-tight tracking-tight sm:text-4xl">{p.titulo || p.direccion || 'Propiedad seleccionada'}</h1>
            {p.direccion && <p className="mt-3 flex items-start gap-1.5 text-sm text-muted-foreground"><MapPin className="mt-0.5 size-4 shrink-0" /><span>{p.direccion}</span></p>}
          </div>
          <div className="shrink-0">
            <p className="text-[10px] font-medium uppercase tracking-[0.15em] text-muted-foreground">{p.tipo_operacion === 'venta' ? 'Valor de venta' : 'Alquiler mensual'}</p>
            <p className="mt-1 text-3xl font-semibold tracking-tight">{fmtPrice(p)}</p>
            {p.expensas != null && <p className="mt-1 text-xs text-muted-foreground">+ {new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', maximumFractionDigits: 0 }).format(p.expensas)} de expensas</p>}
          </div>
        </div>

        <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[minmax(0,1fr)_340px]">
          {/* Main column */}
          <div className="min-w-0 space-y-8">
            <FichaGallery images={p.imagenes ?? []} title={p.titulo ?? p.direccion} />

            {features.length > 0 && (
              /* auto-fit: las cards se reparten según cuántas haya y ninguna
                 queda tan angosta como para que el texto desborde. */
              <div className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(8rem,1fr))]">
                {features.map((f) => <Feature key={`${f.label}-${f.value}`} {...f} />)}
              </div>
            )}

            {p.descripcion && (
              <section className="rounded-3xl border border-border bg-card p-5 sm:p-6">
                <h2 className="mb-2 text-lg font-semibold">Descripción</h2>
                <p className="whitespace-pre-line break-words text-sm leading-7 text-muted-foreground">{p.descripcion}</p>
              </section>
            )}

            {p.amenities && p.amenities.length > 0 && (
              <section className="rounded-3xl border border-border bg-card p-5 sm:p-6">
                <h2 className="mb-2 text-lg font-semibold">Características</h2>
                <div className="flex flex-wrap gap-2">
                  {p.amenities.map((a) => (
                    <span key={a} className="max-w-full break-words rounded-full border border-border bg-muted/50 px-3 py-1 text-sm text-foreground">
                      {a}
                    </span>
                  ))}
                </div>
              </section>
            )}
          </div>

          {/* Contact column */}
          <aside className="min-w-0 lg:sticky lg:top-24">
            <div>
              <ContactColumn p={p} textos={textos} />
            </div>
          </aside>
        </div>
      </main>

      {/* Pie editable desde el editor de ficha. Los textos son del equipo, no de
          esta propiedad: cambiarlos reescribe el pie de todas las fichas. */}
      <footer className="mt-8 border-t border-border">
        <div className="mx-auto max-w-6xl space-y-3 px-4 py-6 text-xs text-muted-foreground">
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
