import { Building2, ImageIcon, Mail, MapPin, Phone } from 'lucide-react'
import type { Property } from '@/hooks/useSSEStream'
import { agenteByEmail } from '@/lib/ficha'

const FUENTE_LABEL: Record<string, string> = {
  zonaprop: 'ZonaProp',
  mercadolibre: 'MercadoLibre',
  googlemaps: 'Sitios web',
}

export function fmtPrice(p: Property) {
  if (p.precio == null) return 'Consultar'
  const n = new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 }).format(p.precio)
  return `${p.moneda ?? 'USD'} ${n}${p.tipo_operacion !== 'venta' ? '/mes' : ''}`
}

function Spec({ label, value }: { label: string; value: string }) {
  // min-w-0 + break-words: los "destacados" del LLM traen valores libres que
  // pueden ser largos — envuelven dentro de la card, nunca se pisan entre sí.
  return (
    <div className="min-w-0 rounded-lg border border-border bg-muted/40 px-3 py-2">
      <p className="truncate text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="break-words text-sm font-semibold leading-snug text-foreground">{value}</p>
    </div>
  )
}

export function PropertyFicha({ p }: { p: Property }) {
  const agente = agenteByEmail(p.agente_email)

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
    <article className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm print:break-inside-avoid">
      {/* Branding header — a nombre del agente asignado a ESTA ficha */}
      <header className="flex items-center justify-between gap-3 border-b border-border bg-foreground px-5 py-3.5 text-background">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-background/15">
            <Building2 className="size-4" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold leading-tight">{agente.inmobiliaria}</p>
            <p className="truncate text-[11px] leading-tight opacity-80">{agente.nombre} · {agente.cargo}</p>
          </div>
        </div>
        <span className="shrink-0 rounded-full bg-background/15 px-2.5 py-0.5 text-[11px] font-medium">
          {p.tipo_operacion === 'venta' ? 'En venta' : 'En alquiler'}
        </span>
      </header>

      <div className="grid grid-cols-1 sm:grid-cols-2">
        {/* Image */}
        <div className="relative aspect-[4/3] bg-muted sm:aspect-auto">
          {p.imagenes?.[0] ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={p.imagenes[0]} alt={p.titulo ?? p.direccion} className="h-full w-full object-cover" />
          ) : (
            <div className="flex h-full min-h-48 items-center justify-center">
              <ImageIcon className="size-10 text-muted-foreground/40" />
            </div>
          )}
          {p.imagenes && p.imagenes.length > 1 && (
            <span className="absolute bottom-2 right-2 rounded-full bg-black/60 px-2 py-0.5 text-[11px] font-medium text-white">
              {p.imagenes.length} fotos
            </span>
          )}
        </div>

        {/* Body */}
        <div className="min-w-0 p-5">
          <p className="text-2xl font-bold tracking-tight text-foreground">{fmtPrice(p)}</p>
          {p.titulo && <p className="mt-1 line-clamp-2 break-words text-sm font-medium text-foreground">{p.titulo}</p>}
          {p.direccion && (
            <div className="mt-1.5 flex items-start gap-1 text-muted-foreground">
              <MapPin className="mt-0.5 size-3.5 shrink-0" />
              <p className="break-words text-xs">{p.direccion}</p>
            </div>
          )}

          {specs.length > 0 && (
            <div className="mt-4 grid grid-cols-2 gap-2">
              {specs.map((s) => <Spec key={s.label} {...s} />)}
            </div>
          )}
        </div>
      </div>

      {/* Description + amenities */}
      {(p.descripcion || (p.amenities && p.amenities.length > 0)) && (
        <div className="border-t border-border px-5 py-4">
          {p.descripcion && <p className="line-clamp-6 text-sm leading-relaxed text-muted-foreground">{p.descripcion}</p>}
          {p.amenities && p.amenities.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {p.amenities.map((a) => (
                <span key={a} className="rounded-full border border-border bg-muted/50 px-2.5 py-0.5 text-xs text-muted-foreground">
                  {a}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Contact footer — datos del agente asignado */}
      <footer className="flex flex-wrap items-center gap-x-5 gap-y-1.5 border-t border-border bg-muted/30 px-5 py-3 text-xs text-foreground">
        <span className="flex items-center gap-1.5 font-medium">{agente.nombre}</span>
        <span className="flex items-center gap-1.5"><Phone className="size-3.5 shrink-0 text-muted-foreground" />{agente.telefono}</span>
        <span className="flex min-w-0 items-center gap-1.5"><Mail className="size-3.5 shrink-0 text-muted-foreground" /><span className="truncate">{agente.email}</span></span>
        <span className="ml-auto text-muted-foreground">Fuente: {FUENTE_LABEL[p.fuente] ?? p.fuente}</span>
      </footer>
    </article>
  )
}
