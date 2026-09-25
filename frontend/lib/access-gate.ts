/** Candado de acceso a la app: una contraseña compartida (APP_PASSWORD) que se
 * pide al abrir el navegador. La cookie guarda un hash de la contraseña, no un
 * "1": así no se puede falsificar a mano y, si se cambia la contraseña, todas
 * las sesiones abiertas quedan invalidadas solas. */

export const ACCESS_COOKIE = 'app_access'

/** Rutas que tienen que seguir abiertas sin contraseña: la pantalla de
 * desbloqueo, las fichas públicas que se comparten con clientes y los webhooks
 * que llaman servicios externos. */
const PUBLIC_PREFIXES = ['/unlock', '/p/', '/api/webhooks/']

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix))
}

// Web Crypto y no `node:crypto`: el middleware puede correr en el runtime edge.
export async function accessToken(password: string): Promise<string> {
  const bytes = new TextEncoder().encode(`app-access:${password}`)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}

export async function hasValidAccess(cookieValue: string | undefined): Promise<boolean> {
  const password = process.env.APP_PASSWORD
  if (!password || !cookieValue) return false
  return cookieValue === (await accessToken(password))
}
