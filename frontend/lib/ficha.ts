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
 * Asignar el agente del equipo a cuyo nombre salen estas fichas, ANTES de
 * enviarlas. Tiene que quedar persistido y no sólo en la sesión: la ficha
 * pública (`/p/[id]`) resuelve el contacto leyendo `agente_email` de la base,
 * así que si el PATCH no entra, el cliente recibe el teléfono equivocado.
 * Por eso el fallo se propaga (`fallidas`) en vez de tragarse: mandar la ficha
 * a nombre de otro agente es peor que no mandarla.
 */
export async function asignarAgente(
  props: Property[],
  email: string,
): Promise<{ props: Property[]; fallidas: Property[] }> {
  const fallidas: Property[] = []
  const actualizadas = await Promise.all(
    props.map(async (p) => {
      if (!p.id || p.agente_email === email) return p
      const updated = await updateProperty(p.id, { agente_email: email })
      if (!updated) {
        fallidas.push(p)
        return p
      }
      return updated
    }),
  )
  return { props: actualizadas, fallidas }
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
  /** URL del perfil, o `null` si el agente no tiene cuenta en esa red. Cada red
   *  se decide por separado: no todos tienen las dos, y un botón que lleva a
   *  otro lado es peor que no tener botón. */
  instagram: string | null
  facebook: string | null
}

/** Equipo Alí — RE/MAX Diagonal II. El orden es el de la ficha. */
export const AGENTES: readonly Agente[] = [
  {
    nombre: 'Andrés Alí',
    inmobiliaria: 'RE/MAX Diagonal II',
    cargo: 'Corredor Inmobiliario Col. 7428',
    telefono: '+54 9 221 477 0660',
    email: 'aliandres@remax.com.ar',
    instagram: 'https://www.instagram.com/andresaliremax',
    facebook: 'https://www.facebook.com/AndresAliRemax',
  },
  {
    nombre: 'Nahir Alí',
    inmobiliaria: 'RE/MAX Diagonal II',
    cargo: 'Agente Inmobiliario',
    telefono: '+54 9 221 477 0661',
    email: 'nali@remax.com.ar',
    instagram: 'https://www.instagram.com/nahiraliremax',
    facebook: null, // Nahir no tiene Facebook: el botón no se muestra
  },
  {
    nombre: 'Ahmed Alí',
    inmobiliaria: 'RE/MAX Diagonal II',
    cargo: 'Team Alí',
    telefono: '+54 9 221 477 0671',
    email: 'menchuali2003@gmail.com',
    instagram: 'https://www.instagram.com/teamaliremax', // cuenta del Team Alí
    facebook: null, // Ahmed no tiene Facebook: el botón no se muestra
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

/**
 * Textos editables de la ficha. Son GLOBALES del equipo: se editan una vez y
 * cambian en todas las fichas, publicadas y futuras. Viven en el backend
 * (`ficha_settings`, fila única) porque eso es lo que ya eran acá: constantes
 * compartidas por todas las fichas, no datos de una propiedad.
 */
export type FichaTextos = {
  /** Presentación, arriba de la tarjeta del agente. */
  texto_seleccion: string
  /** Descargo normativo del pie. Obligatorio en toda ficha publicada. */
  disclaimer_legal: string
  /** Firma del pie: corredor responsable. */
  firma: string
  /** Matrícula / colegiatura, debajo de la firma. */
  colegiatura: string
  /** Cierre del pie ("Publicación generada por…"). */
  pie_publicacion: string
}

/** Espejo de `DEFAULT_TEXTOS` del backend. Es el fallback cuando el backend no
 *  responde: una ficha pública con el pie vacío es peor que una desactualizada,
 *  y el descargo legal tiene que aparecer siempre. */
export const FICHA_TEXTOS_DEFAULT: FichaTextos = {
  texto_seleccion: TEXTO_SELECCION,
  disclaimer_legal: DISCLAIMER_LEGAL,
  firma: AGENTES[0].nombre + ' | Diagonal II',
  colegiatura: 'C.D.C.P.D.J.L.P. 7428',
  pie_publicacion:
    `Publicación generada por ${AGENTES[0].inmobiliaria}. La información puede estar sujeta a ` +
    'modificaciones sin previo aviso.',
}

/** Lee los textos del equipo. Nunca falla: ante cualquier problema devuelve los
 *  defaults, para que la ficha pública siempre tenga pie. */
export async function getFichaTextos(signal?: AbortSignal): Promise<FichaTextos> {
  try {
    const res = await fetch(`${API}/api/v1/ficha-settings`, { signal })
    if (!res.ok) return FICHA_TEXTOS_DEFAULT
    const data = await res.json()
    return { ...FICHA_TEXTOS_DEFAULT, ...(data.settings ?? {}) } as FichaTextos
  } catch {
    return FICHA_TEXTOS_DEFAULT
  }
}

/** Guarda uno o más textos del equipo. A diferencia de la lectura, acá el fallo
 *  SÍ se propaga (`null`): el usuario tiene que enterarse de que no se guardó. */
export async function updateFichaTextos(patch: Partial<FichaTextos>): Promise<FichaTextos | null> {
  try {
    const res = await fetch(`${API}/api/v1/ficha-settings`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
    if (!res.ok) return null
    const data = await res.json()
    if (data.error || !data.settings) return null
    return data.settings as FichaTextos
  } catch {
    return null
  }
}

/** Link de WhatsApp listo para usar (wa.me exige el número sin símbolos). */
export function whatsappUrl(telefono: string, texto: string): string {
  return `https://wa.me/${telefono.replace(/\D/g, '')}?text=${encodeURIComponent(texto)}`
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
