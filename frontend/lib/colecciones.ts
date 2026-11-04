import type { Property } from '@/hooks/useSSEStream'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const BASE = `${API}/api/v1/ficha-colecciones`

/** Ficha de selección: varias Fichas Propio compartidas en un solo link. */
export type Coleccion = {
  id: string
  cliente: string | null
  titulo: string
  subtitulo: string | null
  agente_email: string | null
  /** Si el cliente ve el mapa con los pines de precio. El agente lo ve siempre. */
  mostrar_mapa: boolean
  enviada_at: string | null
  created_at: string
}

/** Resumen para el listado: cuántas lleva y cuántas calificó el cliente. */
export type ColeccionResumen = Coleccion & {
  total: number
  interesa: number
  no_interesa: number
  portada: string | null
}

export type Calificacion = 'interesa' | 'no_interesa'

export type ColeccionItem = {
  property_id: string
  orden: number
  /** Nota del agente para el cliente sobre esta propiedad. */
  comentario: string | null
  /** Lo que eligió el cliente. null = todavía no la calificó. */
  calificacion: Calificacion | null
  property: Property & { id: string }
}

export type ColeccionDetalle = { coleccion: Coleccion; items: ColeccionItem[] }

/** Link público que se manda al cliente. */
export function coleccionUrl(id: string): string {
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  return `${origin}/s/${id}`
}

async function send<T>(path: string, method: string, body?: unknown): Promise<T | null> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!res.ok) return null
    const data = await res.json()
    if (data?.error) return null
    return data as T
  } catch {
    return null
  }
}

export async function listarColecciones(): Promise<ColeccionResumen[]> {
  const data = await send<{ colecciones: ColeccionResumen[] }>('', 'GET')
  return data?.colecciones ?? []
}

/** Crea la selección con las propiedades en el orden dado. null = falló. */
export async function crearColeccion(input: {
  property_ids: string[]
  cliente: string
  agente_email: string
}): Promise<Coleccion | null> {
  const data = await send<{ coleccion: Coleccion }>('', 'POST', input)
  return data?.coleccion ?? null
}

/** Lanza en 404 para que la página pública distinga "no existe" de "sin red". */
export async function leerColeccion(id: string, signal?: AbortSignal): Promise<ColeccionDetalle> {
  const res = await fetch(`${BASE}/${encodeURIComponent(id)}`, { signal })
  if (res.status === 404) throw new Error('Esta selección no existe o fue eliminada.')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return (await res.json()) as ColeccionDetalle
}

export async function actualizarColeccion(
  id: string,
  patch: Partial<Pick<Coleccion, 'cliente' | 'titulo' | 'subtitulo' | 'agente_email' | 'mostrar_mapa'>>,
): Promise<Coleccion | null> {
  const data = await send<{ coleccion: Coleccion }>(`/${encodeURIComponent(id)}`, 'PATCH', patch)
  return data?.coleccion ?? null
}

export async function reordenarColeccion(id: string, propertyIds: string[]): Promise<boolean> {
  return !!(await send(`/${encodeURIComponent(id)}/orden`, 'PUT', { property_ids: propertyIds }))
}

export async function comentarItem(id: string, propertyId: string, comentario: string): Promise<boolean> {
  return !!(await send(`/${encodeURIComponent(id)}/items/${encodeURIComponent(propertyId)}`, 'PATCH', { comentario }))
}

export async function quitarItem(id: string, propertyId: string): Promise<boolean> {
  return !!(await send(`/${encodeURIComponent(id)}/items/${encodeURIComponent(propertyId)}`, 'DELETE'))
}

export async function calificarItem(
  id: string, propertyId: string, calificacion: Calificacion | null,
): Promise<boolean> {
  return !!(await send(
    `/${encodeURIComponent(id)}/items/${encodeURIComponent(propertyId)}/calificacion`,
    'POST',
    { calificacion },
  ))
}

export async function marcarColeccionEnviada(id: string, enviada = true): Promise<Coleccion | null> {
  const data = await send<{ coleccion: Coleccion }>(`/${encodeURIComponent(id)}/enviada`, 'POST', { enviada })
  return data?.coleccion ?? null
}

export async function borrarColeccion(id: string): Promise<boolean> {
  return !!(await send(`/${encodeURIComponent(id)}`, 'DELETE'))
}
