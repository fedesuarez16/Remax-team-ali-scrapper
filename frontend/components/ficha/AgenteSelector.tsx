'use client'
import { Check, UserRound } from 'lucide-react'
import { AGENTES } from '@/lib/ficha'

/** Selector del agente a cuyo nombre sale la ficha. Es EXCLUYENTE (uno solo):
 *  la ficha pública muestra el contacto de un único agente del equipo. */
export function AgenteSelector({
  selected,
  onSelect,
  disabled,
  legend = 'Agente de la ficha',
}: {
  selected: string
  onSelect: (email: string) => void
  disabled: boolean
  /** Título del bloque. Cambia según el momento en que se elige el perfil. */
  legend?: string
}) {
  return (
    <fieldset disabled={disabled}>
      <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {legend}
      </legend>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {AGENTES.map((a) => {
          const active = a.email === selected
          return (
            <button
              key={a.email}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onSelect(a.email)}
              className={`relative flex items-start gap-2.5 rounded-xl border p-3 text-left transition disabled:opacity-60 ${
                active
                  ? 'border-foreground bg-foreground/5 ring-1 ring-foreground'
                  : 'border-border bg-background hover:bg-muted/50'
              }`}
            >
              <div
                className={`flex size-8 shrink-0 items-center justify-center rounded-lg ${
                  active ? 'bg-foreground text-background' : 'bg-muted text-muted-foreground'
                }`}
              >
                <UserRound className="size-4" />
              </div>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-foreground">{a.nombre}</p>
                <p className="truncate text-[11px] text-muted-foreground">{a.cargo}</p>
                <p className="truncate text-[11px] text-muted-foreground">{a.telefono}</p>
              </div>
              {active && (
                <span className="absolute right-2 top-2 flex size-4 items-center justify-center rounded-full bg-foreground">
                  <Check className="size-3 text-background" />
                </span>
              )}
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}
