'use client'
import { Mail, MessageCircle, Phone } from 'lucide-react'
import { whatsappUrl, type Agente } from '@/lib/ficha'
import { AgentAvatar } from './AgentAvatar'

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

/** Tarjeta de contacto del agente: la comparten la ficha individual y la de
 *  selección. `mensaje` es el texto que precarga el WhatsApp. */
export function AgenteCard({ a, mensaje }: { a: Agente; mensaje: string }) {
  const waDigits = a.telefono.replace(/\D/g, '')
  const waUrl = whatsappUrl(a.telefono, mensaje)

  return (
    <div className="space-y-5 rounded-3xl border border-border bg-card p-5 shadow-sm sm:p-6">
      <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">Conversemos sobre tu próximo lugar</p>
      <div className="flex items-center gap-4">
        <AgentAvatar agente={a} className="size-20" />
        <div className="min-w-0">
          <p className="text-xl font-semibold tracking-tight">{a.nombre}</p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{a.cargo}</p>
          <p className="mt-1 text-xs text-muted-foreground">{a.inmobiliaria}</p>
        </div>
      </div>

      <a
        href={waUrl}
        target="_blank"
        rel="noopener"
        className="flex w-full items-center justify-center gap-2 rounded-xl bg-foreground px-4 py-3.5 text-sm font-medium text-background transition hover:bg-foreground/85"
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
        <a href={`tel:+${waDigits}`} className="flex items-center gap-2 text-foreground transition hover:text-muted-foreground">
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
