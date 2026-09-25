'use client'

import { useActionState } from 'react'
import { verifyPassword } from '@/app/unlock/actions'
import { PasswordForm } from './PasswordForm'

type GateState = { unlocked: boolean; error?: string }

/** Candado por pantalla: el desbloqueo vive solo en el estado del componente,
 * así que al salir y volver a entrar se pide la contraseña otra vez. */
export function PasswordGate({ title, children }: { title: string; children: React.ReactNode }) {
  const [state, action, pending] = useActionState(
    async (_prev: GateState, formData: FormData): Promise<GateState> =>
      (await verifyPassword(String(formData.get('password') ?? '')))
        ? { unlocked: true }
        : { unlocked: false, error: 'Contraseña incorrecta' },
    { unlocked: false },
  )

  if (state.unlocked) return <>{children}</>
  return (
    <PasswordForm
      title={title}
      description="Esta sección pide la contraseña cada vez que entrás."
      action={action}
      pending={pending}
      error={state.error}
    />
  )
}
