'use client'

import { Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'

type Props = {
  title: string
  description: string
  action: (formData: FormData) => void
  pending: boolean
  error?: string
  children?: React.ReactNode
}

/** Pantalla de contraseña compartida entre el candado de la app y el de métricas:
 * cambia qué se hace al enviar, no cómo se ve. */
export function PasswordForm({ title, description, action, pending, error, children }: Props) {
  return (
    <div className="flex min-h-full flex-1 items-center justify-center p-6">
      <form action={action} className="w-full max-w-sm space-y-4 rounded-xl border border-border bg-card p-6">
        <div className="flex items-center gap-2">
          <Lock className="size-4 text-muted-foreground" />
          <h1 className="text-base font-semibold">{title}</h1>
        </div>
        <p className="text-sm text-muted-foreground">{description}</p>
        {children}
        <input
          type="password"
          name="password"
          autoFocus
          required
          autoComplete="current-password"
          placeholder="Contraseña"
          aria-invalid={error ? true : undefined}
          className="h-9 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 aria-invalid:border-destructive"
        />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button type="submit" size="lg" className="w-full" disabled={pending}>
          {pending ? 'Verificando…' : 'Entrar'}
        </Button>
      </form>
    </div>
  )
}
