'use client'
import { Monitor, Moon, Sun } from 'lucide-react'
import { setThemePreference, useThemePreference, type ThemePreference } from '@/lib/theme'

const OPTIONS: { value: ThemePreference; label: string; Icon: typeof Sun }[] = [
  { value: 'light', label: 'Claro', Icon: Sun },
  { value: 'dark', label: 'Oscuro', Icon: Moon },
  { value: 'system', label: 'Sistema', Icon: Monitor },
]

export function ThemeToggle({ className = '' }: { className?: string }) {
  const preference = useThemePreference()

  return (
    <div
      role="radiogroup"
      aria-label="Tema"
      className={`flex items-center gap-0.5 rounded-lg border border-sidebar-border bg-sidebar-accent/50 p-0.5 ${className}`}
    >
      {OPTIONS.map(({ value, label, Icon }) => {
        const active = preference === value
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            title={label}
            onClick={() => setThemePreference(value)}
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition ${
              active
                ? 'bg-background text-foreground shadow-sm'
                : 'text-sidebar-foreground/60 hover:text-sidebar-foreground'
            }`}
          >
            <Icon className="size-3.5" />
            {label}
          </button>
        )
      })}
    </div>
  )
}
