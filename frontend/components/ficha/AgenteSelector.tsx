'use client'
import { Check } from 'lucide-react'
import { AGENTES } from '@/lib/ficha'
import { AgentAvatar } from './AgentAvatar'

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
    <fieldset disabled={disabled} className="disabled:opacity-60">
      <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {legend}
      </legend>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {AGENTES.map((a) => {
          const active = a.email === selected
          return (
            <label
              key={a.email}
              className={`relative flex cursor-pointer items-center gap-3 rounded-2xl border p-3.5 text-left transition focus-within:ring-2 focus-within:ring-foreground disabled:opacity-60 ${
                active
                  ? 'border-foreground bg-foreground/5 ring-1 ring-foreground'
                  : 'border-border bg-background hover:bg-muted/50'
              }`}
            >
              <input
                type="radio"
                name="ficha-agente"
                value={a.email}
                checked={active}
                onChange={() => onSelect(a.email)}
                className="sr-only"
              />
              <AgentAvatar agente={a} className="size-11" />
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
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}
