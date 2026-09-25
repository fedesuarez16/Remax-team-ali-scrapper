'use server'

import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import { ACCESS_COOKIE, accessToken } from '@/lib/access-gate'

export type UnlockState = { error: string } | undefined

/** Compara hashes y no strings crudos: ambos lados tienen el mismo largo y la
 * contraseña real nunca viaja al cliente. */
export async function verifyPassword(password: string): Promise<boolean> {
  const expected = process.env.APP_PASSWORD
  if (!expected) return false
  return (await accessToken(password)) === (await accessToken(expected))
}

/** Solo se aceptan rutas internas como destino, para que `?next=` no sirva de
 * open redirect hacia otro dominio. */
function safeNext(next: FormDataEntryValue | null): string {
  return typeof next === 'string' && next.startsWith('/') && !next.startsWith('//') ? next : '/'
}

export async function unlock(_prev: UnlockState, formData: FormData): Promise<UnlockState> {
  const password = String(formData.get('password') ?? '')
  if (!(await verifyPassword(password))) return { error: 'Contraseña incorrecta' }

  // Cookie de sesión (sin maxAge): se borra al cerrar el navegador, así que
  // cada vez que se vuelve a entrar se pide la contraseña de nuevo.
  const cookieStore = await cookies()
  cookieStore.set(ACCESS_COOKIE, await accessToken(password), {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
  })
  redirect(safeNext(formData.get('next')))
}
