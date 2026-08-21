'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const CLEANUP_URL = `${API}/api/v1/cleanup`

/** Contadores de la corrida en curso (o de la última). `unknown` son las
 * verificaciones que NO concluyeron (429/403/timeout): nunca borran nada. */
export type CleanupState = {
  running: boolean
  origen: string | null
  dry_run: boolean
  total: number
  checked: number
  alive: number
  dead: number
  unknown: number
  deleted: number
  error: string | null
  started_at: string | null
  finished_at: string | null
}

export type CleanupSchedule = {
  enabled: boolean
  interval_days: number
  last_run_at: string | null
  next_run_at: string | null
}

export type DeletedProperty = {
  id: string
  titulo: string | null
  direccion: string | null
  fuente: string | null
  url_origen: string
  motivo: string
}

export type CleanupRun = {
  id: string
  origen: 'manual' | 'scheduled'
  dry_run: boolean
  revisadas: number
  activas: number
  caidas: number
  indeterminadas: number
  eliminadas_count: number
  eliminadas: DeletedProperty[]
  error: string | null
  started_at: string
  finished_at: string | null
}

/** Un link verificado de una lista pegada a mano. */
export type CheckedLink = {
  url: string
  motivo: string
}

/** Resultado de verificar una lista. `sin_definir` son los que el portal no
 * dejó verificar — NO son links rotos. */
export type LinkCheckResult = {
  activos: CheckedLink[]
  rotos: CheckedLink[]
  sin_definir: CheckedLink[]
  total: number
  error?: string
}

/** Resultado de borrar de la base los links rotos.
 *
 * `conservadas` son las que al re-verificar NO dieron muertas: el backend
 * vuelve a entrar a cada aviso antes de borrar, así que una lista vieja o un
 * portal que se destrabó no se lleva puesta una propiedad viva. */
export type LinkDeleteResult = {
  eliminadas: DeletedProperty[]
  conservadas: CheckedLink[]
  no_encontradas: string[]
  total: number
  error?: string
}

// El backend re-verifica cada aviso DENTRO del request y por eso topea la
// lista; mandamos de a tandas para que un informe largo igual se pueda borrar.
const DELETE_BATCH = 50

const EMPTY_DELETE: LinkDeleteResult = {
  eliminadas: [],
  conservadas: [],
  no_encontradas: [],
  total: 0,
}

/** Borra las propiedades detrás de `urls`, de a tandas.
 *
 * Cada tanda ya borró cuando vuelve: si una falla a mitad, lo acumulado hasta
 * ahí es lo que realmente se borró — nunca se reporta menos de lo que se fue. */
export async function deleteBrokenLinks(
  urls: string[],
  onProgress?: (done: number, total: number) => void,
): Promise<LinkDeleteResult> {
  const pending = [...new Set(urls.map((u) => u.trim()).filter(Boolean))]
  if (pending.length === 0) return { ...EMPTY_DELETE, error: 'No hay links para borrar' }

  const merged: LinkDeleteResult = { ...EMPTY_DELETE, eliminadas: [], conservadas: [], no_encontradas: [] }
  for (let i = 0; i < pending.length; i += DELETE_BATCH) {
    const batch = pending.slice(i, i + DELETE_BATCH)
    let data: LinkDeleteResult
    try {
      const res = await fetch(`${CLEANUP_URL}/delete-links`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ urls: batch }),
      })
      data = (await res.json()) as LinkDeleteResult
    } catch {
      return { ...merged, error: 'No se pudo conectar con el servidor' }
    }
    merged.eliminadas.push(...(data.eliminadas ?? []))
    merged.conservadas.push(...(data.conservadas ?? []))
    merged.no_encontradas.push(...(data.no_encontradas ?? []))
    merged.total += data.total ?? batch.length
    onProgress?.(Math.min(i + DELETE_BATCH, pending.length), pending.length)
    if (data.error) return { ...merged, error: data.error }
  }
  return merged
}

const EMPTY_STATE: CleanupState = {
  running: false,
  origen: null,
  dry_run: false,
  total: 0,
  checked: 0,
  alive: 0,
  dead: 0,
  unknown: 0,
  deleted: 0,
  error: null,
  started_at: null,
  finished_at: null,
}

const EMPTY_SCHEDULE: CleanupSchedule = {
  enabled: false,
  interval_days: 7,
  last_run_at: null,
  next_run_at: null,
}

// Mientras el bot corre se sondea seguido para ver avanzar los contadores;
// en reposo, cada 30s alcanza para reflejar una limpieza programada.
const POLL_RUNNING_MS = 2000
const POLL_IDLE_MS = 30000

export function useCleanup() {
  const [state, setState] = useState<CleanupState>(EMPTY_STATE)
  const [schedule, setSchedule] = useState<CleanupSchedule>(EMPTY_SCHEDULE)
  const [runs, setRuns] = useState<CleanupRun[]>([])
  const [error, setError] = useState<string | null>(null)
  const wasRunning = useRef(false)

  const refreshRuns = useCallback(async () => {
    try {
      const res = await fetch(`${CLEANUP_URL}/runs`)
      if (!res.ok) return
      const data = await res.json()
      setRuns((data.runs ?? []) as CleanupRun[])
    } catch {
      // El historial es secundario: si falla, la pantalla sigue viva.
    }
  }, [])

  const refreshStatus = useCallback(async () => {
    try {
      const res = await fetch(`${CLEANUP_URL}/status`)
      if (!res.ok) return
      const data = await res.json()
      const next = (data.state ?? EMPTY_STATE) as CleanupState
      setState(next)
      setSchedule((data.schedule ?? EMPTY_SCHEDULE) as CleanupSchedule)
      // La corrida recién terminó → traer el detalle de lo que borró.
      if (wasRunning.current && !next.running) void refreshRuns()
      wasRunning.current = next.running
    } catch {
      // Backend caído: se conserva el último estado conocido.
    }
  }, [refreshRuns])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>

    const tick = async () => {
      await refreshStatus()
      if (cancelled) return
      timer = setTimeout(tick, wasRunning.current ? POLL_RUNNING_MS : POLL_IDLE_MS)
    }

    void tick()
    void refreshRuns()

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [refreshStatus, refreshRuns])

  /** Dispara una limpieza manual. `dryRun` reporta sin borrar. */
  const runNow = useCallback(
    async (options?: { dryRun?: boolean; limit?: number }) => {
      setError(null)
      try {
        const res = await fetch(`${CLEANUP_URL}/run`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            dry_run: options?.dryRun ?? false,
            ...(options?.limit ? { limit: options.limit } : {}),
          }),
        })
        const data = await res.json()
        if (data.error) {
          setError(data.error as string)
          return
        }
        wasRunning.current = true
        setState((prev) => ({ ...prev, running: true }))
        void refreshStatus()
      } catch {
        setError('No se pudo conectar con el servidor')
      }
    },
    [refreshStatus],
  )

  /** Programa la limpieza automática: cada `intervalDays` días. */
  const saveSchedule = useCallback(
    async (enabled: boolean, intervalDays: number): Promise<string | null> => {
      try {
        const res = await fetch(`${CLEANUP_URL}/schedule`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled, interval_days: intervalDays }),
        })
        const data = await res.json()
        if (data.error) return data.error as string
        setSchedule(data.schedule as CleanupSchedule)
        return null
      } catch {
        return 'No se pudo conectar con el servidor'
      }
    },
    [],
  )

  /** Borra las propiedades de `urls` (lo que una simulación marcó como caído).
   * El backend re-verifica antes de borrar: la lista es una intención. */
  const deleteUrls = useCallback(
    async (urls: string[]): Promise<LinkDeleteResult> => {
      setError(null)
      const result = await deleteBrokenLinks(urls)
      if (result.error) setError(result.error)
      // El historial cambió (quedó registrado el borrado) y la base también.
      void refreshRuns()
      void refreshStatus()
      return result
    },
    [refreshRuns, refreshStatus],
  )

  return {
    state,
    schedule,
    runs,
    error,
    runNow,
    saveSchedule,
    deleteUrls,
    refresh: refreshStatus,
  }
}

/** Verifica una lista de links pegada a mano. Independiente de `useCleanup`:
 * no toca la base ni comparte estado con la limpieza. */
export function useLinkChecker() {
  const [result, setResult] = useState<LinkCheckResult | null>(null)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleted, setDeleted] = useState<LinkDeleteResult | null>(null)

  const check = useCallback(async (raw: string) => {
    setChecking(true)
    setError(null)
    setDeleted(null)
    try {
      const res = await fetch(`${CLEANUP_URL}/check-links`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // El backend parsea el bloque pegado tal cual: no hace falta splitear acá.
        body: JSON.stringify({ urls: raw }),
      })
      const data = (await res.json()) as LinkCheckResult
      if (data.error) {
        setError(data.error)
        setResult(null)
        return
      }
      setResult(data)
    } catch {
      setError('No se pudo conectar con el servidor')
      setResult(null)
    } finally {
      setChecking(false)
    }
  }, [])

  /** Saca de la base las propiedades detrás de los links rotos.
   *
   * Manda SÓLO los que siguen sin borrar: repetir el botón no reintenta lo que
   * ya se fue. El backend re-verifica cada aviso antes de tocar la base. */
  const deleteBroken = useCallback(async (): Promise<LinkDeleteResult | null> => {
    if (!result) return null
    const gone = new Set((deleted?.eliminadas ?? []).map((p) => p.url_origen))
    const pending = result.rotos.map((l) => l.url).filter((url) => !gone.has(url))
    if (pending.length === 0) return null

    setDeleting(true)
    setError(null)
    const outcome = await deleteBrokenLinks(pending)
    setDeleting(false)

    if (outcome.error && outcome.eliminadas.length === 0) {
      setError(outcome.error)
      return null
    }
    if (outcome.error) setError(outcome.error)
    // Se acumula con lo borrado antes: la lista de rotos no se vacía (sirve
    // para avisarle al cliente), sólo se marca qué ya salió de la base.
    setDeleted((prev) => ({
      eliminadas: [...(prev?.eliminadas ?? []), ...outcome.eliminadas],
      conservadas: outcome.conservadas,
      no_encontradas: outcome.no_encontradas,
      total: (prev?.total ?? 0) + outcome.total,
    }))
    return outcome
  }, [result, deleted])

  const reset = useCallback(() => {
    setResult(null)
    setError(null)
    setDeleted(null)
  }, [])

  return { result, checking, error, check, reset, deleteBroken, deleting, deleted }
}
