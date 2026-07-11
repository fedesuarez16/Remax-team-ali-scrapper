import type { Property } from '@/hooks/useSSEStream'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

/**
 * Ask the backend to parse the property's free-text description with an LLM,
 * distributing amenities + destacados into structured fields. Idempotent and cached
 * server-side. On any failure returns the original property untouched.
 */
export async function enrichFicha(p: Property): Promise<Property> {
  if (!p.id) return p
  try {
    const res = await fetch(`${API}/api/v1/properties/${encodeURIComponent(p.id)}/enrich`, {
      method: 'POST',
    })
    if (!res.ok) return p
    const data = await res.json()
    return (data.property as Property) ?? p
  } catch {
    return p
  }
}

/**
 * Persist manual ficha edits (curated images, fixed title/price, etc.).
 * Only whitelisted fields are applied server-side. Returns the updated
 * property, or null if the request failed.
 */
export async function updateProperty(id: string, patch: Partial<Property>): Promise<Property | null> {
  try {
    const res = await fetch(`${API}/api/v1/properties/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
    if (!res.ok) return null
    const data = await res.json()
    if (data.error) return null
    return (data.property as Property) ?? null
  } catch {
    return null
  }
}

// Datos del agente / inmobiliaria. Placeholder — reemplazar por datos reales o config del usuario.
export const AGENTE = {
  nombre: 'Federico Suárez',
  inmobiliaria: 'Suárez Propiedades',
  telefono: '+54 9 11 5555-1234',
  email: 'contacto@suarezpropiedades.com',
  matricula: 'CUCICBA Mat. 1234',
  sitio: 'www.suarezpropiedades.com',
} as const

/** URL pública y compartible de la ficha propia de una propiedad (reemplaza la del portal). */
export function fichaUrl(id: string | undefined): string {
  if (!id) return ''
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  return `${origin}/p/${id}`
}

const STORAGE_KEY = 'ficha:seleccion'

export function guardarSeleccion(props: Property[]): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(props))
  } catch {
    /* sessionStorage no disponible */
  }
}

export function leerSeleccion(): Property[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as Property[]) : []
  } catch {
    return []
  }
}
