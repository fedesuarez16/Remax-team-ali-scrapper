'use client'
import { useCallback, useEffect, useState } from 'react'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const BASE = `${API}/api/v1/barrios-cerrados`

/** How one portal names this barrio, and how the scraper asks it.
 *
 *  `native` — the portal has the gated community as its own location entity,
 *  so `ref` is the slug/id it filters on server-side.
 *  `localidad` — it does not, so the search runs on the containing localidad
 *  and the alias filter narrows the results afterwards. Wider, still correct. */
export type PortalStrategy = 'native' | 'localidad'

export type PortalRef = {
  id: string
  portal: string
  strategy: PortalStrategy
  ref: string | null
  label: string | null
  result_count: number | null
  /** False until a human looked at `label` and said "yes, that is my barrio".
   *  It matters: Argenprop alone serves a "Los Ceibos" in Tigre, La Plata,
   *  Córdoba, Corrientes and González Catán. */
  confirmed: boolean
  note: string | null
}

export type BarrioKind = 'barrio_cerrado' | 'club_de_campo' | 'country' | 'barrio_privado'

export type BarrioCerrado = {
  id: string
  nombre: string
  /** The containing localidad — the load-bearing field. It is what gives the
   *  search a chain to degrade through when a portal cannot resolve the
   *  barrio; without it, a country search returns nothing at all. */
  localidad: string
  kind: BarrioKind
  aliases: string[]
  polygon: [number, number][] | null
  activo: boolean
  /** Derived server-side: the composite the zona pipeline actually walks. */
  zona: string
  /** Derived server-side: every spelling the guard will match a listing on. */
  aliases_efectivos: string[]
  portal_refs: PortalRef[]
}

export type BarrioCandidate = {
  nombre: string
  localidad: string
  label: string
  argenprop_ref: string
  kind: BarrioKind
  ya_existe: boolean
}

export type ImportPreview = {
  barrios: BarrioCandidate[]
  total_api: number
  truncated: boolean
  warning?: string
  error: string | null
}

export type ImportResult = {
  creados: number
  salteados: number
  barrios: BarrioCerrado[]
  errores: string[]
  error?: string
}

export type NuevoBarrio = {
  nombre: string
  localidad: string
  kind?: BarrioKind
  aliases?: string[]
}

/** Every mutation returns `string | null` — an error message or nothing.
 *  Same shape `useManualSources` uses, so the pages read alike. The backend
 *  answers 200 with an `error` key rather than raising, so a transport failure
 *  and a validation failure land in the same place. */
async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return (await res.json()) as T
}

export function useBarriosCerrados() {
  const [barrios, setBarrios] = useState<BarrioCerrado[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(BASE)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (data.error) throw new Error(data.error)
      setBarrios((data.barrios ?? []) as BarrioCerrado[])
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error desconocido')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const addBarrio = useCallback(
    async (nuevo: NuevoBarrio): Promise<string | null> => {
      const nombre = nuevo.nombre.trim()
      const localidad = nuevo.localidad.trim()
      if (!nombre) return 'Poné el nombre del barrio'
      // Checked here as well as server-side so the message can say WHY instead
      // of reading like a form-validation nag.
      if (!localidad)
        return 'Falta la localidad: es la que le da a la búsqueda por dónde seguir cuando un portal no encuentra el barrio'
      try {
        const data = await post<{ barrio: BarrioCerrado | null; error?: string }>(BASE, {
          nombre,
          localidad,
          kind: nuevo.kind ?? 'barrio_cerrado',
          aliases: nuevo.aliases ?? [],
        })
        if (data.error || !data.barrio) return data.error ?? 'No se pudo guardar el barrio'
        await refresh()
        return null
      } catch (e) {
        return e instanceof Error ? e.message : 'No se pudo guardar el barrio'
      }
    },
    [refresh]
  )

  const updateBarrio = useCallback(
    async (id: string, cambios: Partial<NuevoBarrio & { activo: boolean }>): Promise<string | null> => {
      try {
        const res = await fetch(`${BASE}/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(cambios),
        })
        const data = await res.json()
        if (data.error) return data.error as string
        await refresh()
        return null
      } catch (e) {
        return e instanceof Error ? e.message : 'No se pudo actualizar el barrio'
      }
    },
    [refresh]
  )

  const deleteBarrio = useCallback(
    async (id: string): Promise<void> => {
      try {
        await fetch(`${BASE}/${encodeURIComponent(id)}`, { method: 'DELETE' })
        await refresh()
      } catch (e) {
        setError(e instanceof Error ? e.message : 'No se pudo borrar el barrio')
      }
    },
    [refresh]
  )

  /** Ask every portal how it names this barrio and persist the answers.
   *  Re-runnable: a re-probe keeps a human's confirmation only when the ref
   *  comes back identical. */
  const probeBarrio = useCallback(
    async (id: string): Promise<string | null> => {
      try {
        const data = await post<{ portal_refs: PortalRef[]; error?: string }>(
          `${BASE}/${encodeURIComponent(id)}/probe`,
          {}
        )
        if (data.error) return data.error
        await refresh()
        return null
      } catch (e) {
        return e instanceof Error ? e.message : 'No se pudo consultar los portales'
      }
    },
    [refresh]
  )

  const setRef = useCallback(
    async (
      id: string,
      portal: string,
      cambios: { ref?: string | null; strategy?: PortalStrategy; confirmed?: boolean }
    ): Promise<string | null> => {
      try {
        const res = await fetch(
          `${BASE}/${encodeURIComponent(id)}/refs/${encodeURIComponent(portal)}`,
          {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cambios),
          }
        )
        const data = await res.json()
        if (data.error) return data.error as string
        await refresh()
        return null
      } catch (e) {
        return e instanceof Error ? e.message : 'No se pudo actualizar la referencia'
      }
    },
    [refresh]
  )

  return {
    barrios,
    loading,
    error,
    refresh,
    addBarrio,
    updateBarrio,
    deleteBarrio,
    probeBarrio,
    setRef,
  }
}

/** The bulk import, kept separate from the catalogue hook.
 *
 *  Two steps on purpose: `preview` reads Argenprop and writes nothing,
 *  `importar` loads only the subset the operator ticked. Argenprop can list a
 *  partido's gated communities but not PLACE them — it labels Grand Bell
 *  "Partido de La Plata" while the barrio is in City Bell — and its
 *  autocomplete caps at 30 rows with no error, so both facts have to reach a
 *  human before any of it becomes catalogue. */
export function useBarrioImport() {
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const buscar = useCallback(
    async (partido: string, opciones?: { localidad?: string; deep?: boolean }) => {
      const p = partido.trim()
      if (!p) return
      setLoading(true)
      setError(null)
      try {
        const qs = new URLSearchParams({ partido: p })
        if (opciones?.localidad?.trim()) qs.set('localidad', opciones.localidad.trim())
        if (opciones?.deep) qs.set('deep', 'true')
        const res = await fetch(`${BASE}/import/preview?${qs}`)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = (await res.json()) as ImportPreview
        setPreview(data)
        if (data.error) setError(data.error)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'No se pudo consultar Argenprop')
        setPreview(null)
      } finally {
        setLoading(false)
      }
    },
    []
  )

  const importar = useCallback(
    async (seleccionados: BarrioCandidate[]): Promise<ImportResult | null> => {
      if (seleccionados.length === 0) return null
      setLoading(true)
      try {
        return await post<ImportResult>(`${BASE}/import`, {
          barrios: seleccionados.map((c) => ({
            nombre: c.nombre,
            localidad: c.localidad,
            kind: c.kind,
            argenprop_ref: c.argenprop_ref,
            label: c.label,
          })),
        })
      } catch (e) {
        setError(e instanceof Error ? e.message : 'No se pudo importar')
        return null
      } finally {
        setLoading(false)
      }
    },
    []
  )

  const limpiar = useCallback(() => {
    setPreview(null)
    setError(null)
  }, [])

  return { preview, loading, error, buscar, importar, limpiar }
}
