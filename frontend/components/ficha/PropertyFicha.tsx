'use client'

import { ArrowUpRight, Mail, MapPin, MessageCircle, Phone } from 'lucide-react'
import type { Property } from '@/hooks/useSSEStream'
import { agenteByEmail, whatsappUrl } from '@/lib/ficha'
import { AgentAvatar } from './AgentAvatar'
import { FichaGallery } from './FichaGallery'
import { useFichaTextos } from '@/hooks/useFichaTextos'

export function fmtPrice(p: Property) {
  if (p.precio == null) return 'Consultar precio'
  const n = new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 }).format(p.precio)
  return `${p.moneda ?? 'USD'} ${n}${p.tipo_operacion !== 'venta' ? '/mes' : ''}`
}

function Spec({ label, value }: { label: string; value: string }) {
  // min-w-0 + break-words: los "destacados" del LLM traen valores libres que
  // pueden ser largos — envuelven dentro de la card, nunca se pisan entre sí.
  return (
    <div className="min-w-0 rounded-xl bg-muted/60 px-3 py-3">
      <p className="break-words text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="mt-1 break-words text-base font-semibold leading-snug text-foreground">{value}</p>
    </div>
  )
}

export function PropertyFicha({ p }: { p: Property }) {
  const agente = agenteByEmail(p.agente_email)
  const { textos } = useFichaTextos()

  const specs: { label: string; value: string }[] = []
  if (p.tipo_propiedad) specs.push({ label: 'Tipo', value: p.tipo_propiedad.charAt(0).toUpperCase() + p.tipo_propiedad.slice(1) })
  if (p.ambientes != null) specs.push({ label: 'Ambientes', value: String(p.ambientes) })
  if (p.banos != null) specs.push({ label: 'Baños', value: String(p.banos) })
  if (p.cocheras != null) specs.push({ label: 'Cocheras', value: String(p.cocheras) })
  if (p.m2_total != null) specs.push({ label: 'Superficie', value: `${p.m2_total} m²` })
  if (p.piso != null) specs.push({ label: 'Piso', value: String(p.piso) })
  if (p.antiguedad != null) specs.push({ label: 'Antigüedad', value: p.antiguedad === 0 ? 'A estrenar' : `${p.antiguedad} años` })
  if (p.expensas != null) specs.push({ label: 'Expensas', value: new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', maximumFractionDigits: 0 }).format(p.expensas) })
  // Highlights parsed from the free-text description by the LLM — same boxes as the m² etc.
  for (const d of p.destacados ?? []) specs.push({ label: d.label, value: d.value })

  return (
    <article className="overflow-hidden rounded-3xl border border-border bg-card shadow-sm print:overflow-visible print:break-inside-avoid print:shadow-none">
      <header className="flex flex-wrap items-center justify-between gap-2 px-5 py-4 sm:px-6">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em]">Team Alí</p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">{agente.inmobiliaria}</p>
        </div>
        <span className="rounded-full bg-foreground px-3 py-1.5 text-[11px] font-medium text-background">
          {p.tipo_operacion === 'venta' ? 'En venta' : 'En alquiler'}
        </span>
      </header>

      <FichaGallery images={p.imagenes ?? []} title={p.titulo ?? p.direccion} compact />

      <div className="space-y-5 p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div className="min-w-0 flex-1 basis-56">
            <h2 className="break-words text-xl font-semibold leading-snug tracking-tight sm:text-2xl">{p.titulo || p.direccion || 'Propiedad seleccionada'}</h2>
            {p.direccion && (
              <p className="mt-2 flex items-start gap-1.5 text-sm text-muted-foreground">
                <MapPin className="mt-0.5 size-4 shrink-0" /><span className="break-words">{p.direccion}</span>
              </p>
            )}
          </div>
          <div>
            <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">{p.tipo_operacion === 'venta' ? 'Valor de venta' : 'Alquiler mensual'}</p>
            <p className="mt-1 text-2xl font-semibold tracking-tight">{fmtPrice(p)}</p>
          </div>
        </div>
        {specs.length > 0 && (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {specs.map((spec, index) => <Spec key={`${spec.label}-${index}`} {...spec} />)}
          </div>
        )}
        {p.descripcion && (
          <div>
            <h3 className="mb-2 text-sm font-semibold">Sobre esta propiedad</h3>
            <p className="line-clamp-6 whitespace-pre-line break-words text-sm leading-relaxed text-muted-foreground print:line-clamp-none">{p.descripcion}</p>
          </div>
        )}
        {!!p.amenities?.length && (
          <div className="flex flex-wrap gap-2">
            {p.amenities.map((amenity) => <span key={amenity} className="max-w-full break-words rounded-full border border-border px-3 py-1 text-xs text-muted-foreground">{amenity}</span>)}
          </div>
        )}
      </div>

      <footer className="border-t border-border bg-muted/30 p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <AgentAvatar agente={agente} className="size-16" />
            <div className="min-w-0">
              <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Tu contacto</p>
              <p className="mt-0.5 text-base font-semibold">{agente.nombre}</p>
              <p className="text-xs text-muted-foreground">{agente.cargo}</p>
            </div>
          </div>
          <a href={whatsappUrl(agente.telefono, `Hola ${agente.nombre}, me interesa la propiedad "${p.titulo ?? p.direccion}" (${fmtPrice(p)}). ¿Sigue disponible?`)} target="_blank" rel="noopener noreferrer" className="flex items-center gap-2 rounded-xl bg-foreground px-4 py-3 text-xs font-medium text-background transition hover:bg-foreground/85 print:hidden">
            <MessageCircle className="size-4" />Consultar<ArrowUpRight className="size-3.5" />
          </a>
        </div>
        <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
          <a href={`tel:+${agente.telefono.replace(/\D/g, '')}`} className="flex items-center gap-2 hover:text-foreground"><Phone className="size-3.5 shrink-0" />{agente.telefono}</a>
          <a href={`mailto:${agente.email}`} className="flex min-w-0 items-center gap-2 hover:text-foreground"><Mail className="size-3.5 shrink-0" /><span className="break-all">{agente.email}</span></a>
        </div>
        <div className="mt-5 hidden space-y-2 border-t border-border pt-4 text-[10px] leading-relaxed text-muted-foreground print:block">
          <p>{textos.texto_seleccion}</p>
          <p>{textos.firma} · {textos.colegiatura}</p>
          <p>{textos.disclaimer_legal}</p>
          <p>{textos.pie_publicacion}</p>
        </div>
      </footer>
    </article>
  )
}
