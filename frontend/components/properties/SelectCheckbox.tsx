'use client'
import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'

type Props = {
  selected: boolean
  onToggle: () => void
  className?: string
}

/** Casilla flotante sobre una PropertyCard. Presentacional: no sabe qué se
 *  hace con la selección, sólo la refleja. */
export function SelectCheckbox({ selected, onToggle, className }: Props) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation()
        onToggle()
      }}
      aria-pressed={selected}
      aria-label={selected ? 'Quitar de la selección' : 'Seleccionar propiedad'}
      className={cn(
        'absolute left-2 top-2 z-20 flex size-6 items-center justify-center rounded-md border shadow-sm transition',
        selected
          ? 'border-foreground bg-foreground text-background'
          : 'border-border bg-background/90 text-transparent backdrop-blur-sm hover:border-foreground/40',
        className
      )}
    >
      <Check className="size-4" strokeWidth={3} />
    </button>
  )
}
