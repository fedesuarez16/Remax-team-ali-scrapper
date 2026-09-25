'use client'

import { useActionState } from 'react'
import { PasswordForm } from '@/components/auth/PasswordForm'
import { unlock } from './actions'

export function UnlockForm({ next }: { next: string }) {
  const [state, action, pending] = useActionState(unlock, undefined)

  return (
    <PasswordForm
      title="Acceso restringido"
      description="Ingresá la contraseña para entrar a la app."
      action={action}
      pending={pending}
      error={state?.error}
    >
      <input type="hidden" name="next" value={next} />
    </PasswordForm>
  )
}
