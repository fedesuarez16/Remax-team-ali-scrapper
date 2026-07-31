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

  return { state, schedule, runs, error, runNow, saveSchedule, refresh: refreshStatus }
}
