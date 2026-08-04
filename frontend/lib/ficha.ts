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

/**
 * Marcar propiedades como ENVIADAS al cliente (o desmarcarlas con `enviada:
 * false`). Se llama al preparar y enviar una selección, para que al volver a la
 * misma búsqueda se distingan de las que todavía no se mandaron.
 * Devuelve los ids efectivamente marcados; en un fallo devuelve [] y el flujo
 * de fichas sigue igual — la marca es informativa, no bloquea el envío.
 */
export async function marcarEnviadas(ids: string[], enviada = true): Promise<string[]> {
  const clean = [...new Set(ids.filter(Boolean))]
  if (clean.length === 0) return []
  try {
    const res = await fetch(`${API}/api/v1/properties/mark-sent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: clean, enviada }),
    })
    if (!res.ok) return []
    const data = await res.json()
    if (data.error) return []
    return ((data.properties ?? []) as Property[]).map((p) => p.id!).filter(Boolean)
  } catch {
    return []
  }
}

export type Agente = {
  nombre: string
  inmobiliaria: string
  /** Rol tal como debe figurar en la ficha (matrícula incluida si corresponde). */
  cargo: string
  telefono: string
  email: string
  /** `true` ⇒ la ficha muestra los botones de Instagram y Facebook. */
  redes: boolean
  /** Perfil PERSONAL del agente. `null` ⇒ el botón usa el de la inmobiliaria
   *  (`REDES_INMOBILIARIA`), que es real y verificado. Nunca se adivina un
   *  handle: un usuario inventado puede ser el de un tercero, y este link viaja
   *  en una ficha que el equipo le manda a sus clientes. */
  instagram: string | null
  facebook: string | null
}

/** Cuentas oficiales de RE/MAX Diagonal II, tomadas del sitio de la oficina
 *  (remax-diagonal2.com.ar/nosotros/contacto/). Son el destino por defecto de
 *  los botones de redes: la inmobiliaria a la que pertenece el agente. */
export const REDES_INMOBILIARIA = {
  instagram: 'https://www.instagram.com/remaxdiagonal2',
  facebook: 'https://www.facebook.com/remaxdiagonal2',
} as const

/** Equipo Alí — RE/MAX Diagonal II. El orden es el de la ficha. */
export const AGENTES: readonly Agente[] = [
  {
    nombre: 'Andrés Alí',
    inmobiliaria: 'RE/MAX Diagonal II',
    cargo: 'Corredor Inmobiliario Col. 7428',
    telefono: '+54 9 221 477 0660',
    email: 'aliandres@remax.com.ar',
    redes: true,
    // Sin perfil personal publicado: los botones usan el oficial de la oficina.
    // Si Andrés tiene cuenta propia, pegarla acá y el botón la prefiere.
    instagram: null,
    facebook: null,
  },
  {
    nombre: 'Nahir Alí',
    inmobiliaria: 'RE/MAX Diagonal II',
    cargo: 'Agente Inmobiliario',
    telefono: '+54 9 221 477 0661',
    email: 'nali@remax.com.ar',
    redes: true,
    // Sin perfil personal publicado: los botones usan el oficial de la oficina.
    instagram: null,
    facebook: null,
  },
  {
    nombre: 'Ahmed Alí',
    inmobiliaria: 'RE/MAX Diagonal II',
    cargo: 'Team Alí',
    telefono: '+54 9 221 477 0671',
    email: 'menchuali2003@gmail.com',
    redes: false, // el pedido no lista redes para Ahmed: sólo teléfono y correo
    instagram: null,
    facebook: null,
  },
] as const

/** Agente asignado a una propiedad, resuelto por email (el identificador
 *  estable del equipo). Fichas viejas sin agente ⇒ el titular (AGENTES[0]). */
export function agenteByEmail(email?: string | null): Agente {
  return AGENTES.find((a) => a.email === email) ?? AGENTES[0]
}

/** Titular de la ficha: el Corredor Matriculado responsable. Es quien firma el
 *  pie legal, y el contacto por defecto donde sólo entra un agente. */
export const AGENTE = {
  nombre: AGENTES[0].nombre,
  inmobiliaria: AGENTES[0].inmobiliaria,
  telefono: AGENTES[0].telefono,
  email: AGENTES[0].email,
  matricula: AGENTES[0].cargo,
  colegiatura: 'C.D.C.P.D.J.L.P. 7428',
  firma: 'Andrés Alí | Diagonal II',
} as const

/** Texto de presentación de la selección. Obligatorio en la Ficha Propio. */
export const TEXTO_SELECCION =
  'Esta selección de propiedades reúne las oportunidades relevadas en el mercado que ' +
  'mejor se ajustan a tus criterios de búsqueda. Si alguna opción resulta de tu interés, ' +
  'comunícate para coordinar una visita o solicitar más información.'

/** Descargo legal obligatorio. No editar sin consultar: es texto normativo. */
export const DISCLAIMER_LEGAL =
  '⚖️ En cumplimiento de las normas legales aplicables, informamos que los Agentes NO ' +
  'ejercen el Corretaje Inmobiliario. Todas las operaciones inmobiliarias son concluidas ' +
  'por los Corredores Matriculados responsables en cada oficina.'

/** Link de WhatsApp listo para usar (wa.me exige el número sin símbolos). */
export function whatsappUrl(telefono: string, texto: string): string {
  return `https://wa.me/${telefono.replace(/\D/g, '')}?text=${encodeURIComponent(texto)}`
}

/** Instagram del agente: su perfil personal si está cargado, si no el oficial
 *  de la inmobiliaria. Siempre resuelve a un perfil real. */
export function instagramUrl(a: Agente): string {
  return a.instagram ?? REDES_INMOBILIARIA.instagram
}

/** Ídem para Facebook. */
export function facebookUrl(a: Agente): string {
  return a.facebook ?? REDES_INMOBILIARIA.facebook
}

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
