'use client'
import { useEffect, useState } from 'react'
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  HelpCircle,
  Loader2,
  Play,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { useCleanup, type CleanupRun } from '@/hooks/useCleanup'
import LinkChecker from '@/components/cleanup/LinkChecker'

const PRESETS = [7, 15, 30, 90]

function formatDate(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('es-AR', { dateStyle: 'medium', timeStyle: 'short' })
}

export default function LimpiezaPage() {
  const { state, schedule, runs, error, runNow, saveSchedule } = useCleanup()
  const [enabled, setEnabled] = useState(false)
  const [intervalDays, setIntervalDays] = useState(7)
  const [saving, setSaving] = useState(false)
  const [scheduleError, setScheduleError] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [openRun, setOpenRun] = useState<string | null>(null)

  // El server manda la verdad; el form la refleja hasta que el usuario toca algo.
  useEffect(() => {
    setEnabled(schedule.enabled)
    setIntervalDays(schedule.interval_days)
  }, [schedule.enabled, schedule.interval_days])

  const handleSave = async () => {
    setSaving(true)
    setScheduleError(null)
    const err = await saveSchedule(enabled, intervalDays)
    setSaving(false)
    if (err) {
      setScheduleError(err)
      return
    }
    setSavedAt(Date.now())
  }

  const progress = state.total > 0 ? Math.round((state.checked / state.total) * 100) : 0

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-background text-foreground">
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto w-full max-w-3xl">
          <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            <Sparkles className="size-5" />
            Bot limpiador
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Verifica si los avisos siguen publicados. Podés pegarle una lista de links sueltos para
            saber cuáles siguen vivos, o dejarlo recorrer toda la base y borrar las propiedades que
            se vendieron, las bajaron del portal o quedaron con el link roto. Cuando el portal no
            responde (bloqueo, timeout) no se toca nada — la duda nunca borra.
          </p>
        </div>
      </header>

      <div className="mx-auto w-full max-w-3xl flex-1 space-y-6 p-6">
        {/* ── Verificar una lista pegada ────────────────────────────────── */}
        <LinkChecker />

        {/* ── Limpieza manual ───────────────────────────────────────────── */}
        <section className="space-y-3 rounded-2xl border border-border bg-card p-4 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold">Limpieza manual</h2>
              <p className="text-xs text-muted-foreground">
                Revisá la base ahora mismo. Empezá por la simulación si es la primera vez.
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              <button
                onClick={() => runNow({ dryRun: true })}
                disabled={state.running}
                className="flex items-center gap-2 rounded-xl border border-border px-3 py-2 text-sm font-medium transition hover:bg-muted disabled:opacity-40"
              >
                <Play className="size-4" />
                Simular
              </button>
              <button
                onClick={() => runNow()}
                disabled={state.running}
                className="flex items-center gap-2 rounded-xl bg-foreground px-3 py-2 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:opacity-40"
              >
                {state.running ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Trash2 className="size-4" />
                )}
                Limpiar ahora
              </button>
            </div>
          </div>

          {error && <p className="text-xs text-destructive">{error}</p>}
          {state.error && (
            <p className="text-xs text-destructive">La última corrida falló: {state.error}</p>
          )}

          {(state.running || state.checked > 0) && (
            <div className="space-y-3 rounded-xl bg-muted/40 p-3">
              {state.dry_run && (
                <p className="text-xs font-medium text-muted-foreground">
                  Modo simulación — no se borró nada.
                </p>
              )}
              <div className="h-1.5 overflow-hidden rounded-full bg-border">
                <div
                  className="h-full rounded-full bg-foreground transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Revisadas" value={`${state.checked}/${state.total}`} />
                <Stat label="Activas" value={state.alive} icon={<CheckCircle2 className="size-3" />} />
                <Stat label="Caídas" value={state.dead} icon={<AlertTriangle className="size-3" />} />
                <Stat
                  label="Sin definir"
                  value={state.unknown}
                  icon={<HelpCircle className="size-3" />}
                  hint="El portal no respondió — no se borró nada"
                />
              </div>
              <p className="text-xs text-muted-foreground">
                {state.running
                  ? 'Revisando avisos...'
                  : `Terminó ${formatDate(state.finished_at)} — ${state.deleted} eliminada${
                      state.deleted === 1 ? '' : 's'
                    }`}
              </p>
            </div>
          )}
        </section>

        {/* ── Limpieza automática ───────────────────────────────────────── */}
        <section className="space-y-3 rounded-2xl border border-border bg-card p-4 shadow-sm">
          <div className="flex items-center gap-2">
            <CalendarClock className="size-4 text-muted-foreground" />
            <h2 className="text-sm font-semibold">Limpieza automática</h2>
          </div>
          <p className="text-xs text-muted-foreground">
            El sistema revisa toda la base cada X días y elimina lo que ya no existe, así la base
            queda permanentemente actualizada.
          </p>

          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="size-4 accent-foreground"
            />
            Activar limpieza programada
          </label>

          <div className="flex flex-wrap items-center gap-2">
            {PRESETS.map((days) => (
              <button
                key={days}
                onClick={() => setIntervalDays(days)}
                className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
                  intervalDays === days
                    ? 'border-foreground bg-foreground text-background'
                    : 'border-border text-muted-foreground hover:bg-muted'
                }`}
              >
                Cada {days} días
              </button>
            ))}
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span>o cada</span>
              <input
                type="number"
                min={1}
                max={365}
                value={intervalDays}
                onChange={(e) => setIntervalDays(Number(e.target.value))}
                className="w-16 rounded-lg border border-border bg-background px-2 py-1 text-center text-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20"
              />
              <span>días</span>
            </div>
          </div>

          {scheduleError && <p className="text-xs text-destructive">{scheduleError}</p>}

          <div className="flex items-center gap-3">
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-2 rounded-xl bg-foreground px-3 py-2 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:opacity-40"
            >
              {saving && <Loader2 className="size-4 animate-spin" />}
              Guardar programación
            </button>
            {savedAt && !scheduleError && (
              <span className="text-xs text-muted-foreground">Guardado</span>
            )}
          </div>

          <div className="grid gap-1 border-t border-border pt-3 text-xs text-muted-foreground">
            <span>Última limpieza: {formatDate(schedule.last_run_at)}</span>
            <span>
              Próxima: {schedule.enabled ? formatDate(schedule.next_run_at) : 'programación apagada'}
            </span>
          </div>
        </section>

        {/* ── Historial ─────────────────────────────────────────────────── */}
        <section className="space-y-2">
          <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Historial de limpiezas
          </h2>

          {runs.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-10 text-center">
              <Sparkles className="size-8 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">Todavía no corrió ninguna limpieza</p>
            </div>
          ) : (
            <ul className="space-y-2">
              {runs.map((run) => (
                <RunRow
                  key={run.id}
                  run={run}
                  open={openRun === run.id}
                  onToggle={() => setOpenRun(openRun === run.id ? null : run.id)}
                />
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}

function Stat({
  label,
  value,
  icon,
  hint,
}: {
  label: string
  value: number | string
  icon?: React.ReactNode
  hint?: string
}) {
  return (
    <div title={hint}>
      <p className="flex items-center gap-1 text-[11px] uppercase tracking-wide text-muted-foreground">
        {icon}
        {label}
      </p>
      <p className="text-sm font-semibold text-foreground">{value}</p>
    </div>
  )
}

function RunRow({
  run,
  open,
  onToggle,
}: {
  run: CleanupRun
  open: boolean
  onToggle: () => void
}) {
  const removed = run.eliminadas ?? []

  return (
    <li className="rounded-xl border border-border bg-card">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-left"
        aria-expanded={open}
      >
        <ChevronDown
          className={`size-4 shrink-0 text-muted-foreground transition ${open ? 'rotate-180' : ''}`}
        />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">
            {formatDate(run.started_at)}
            <span className="ml-2 rounded-full bg-muted px-2 py-0.5 text-[11px] font-normal text-muted-foreground">
              {run.origen === 'scheduled' ? 'automática' : 'manual'}
            </span>
            {run.dry_run && (
              <span className="ml-1 rounded-full bg-muted px-2 py-0.5 text-[11px] font-normal text-muted-foreground">
                simulación
              </span>
            )}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {run.revisadas} revisadas · {run.activas} activas · {run.eliminadas_count} eliminadas ·{' '}
            {run.indeterminadas} sin definir
          </p>
        </div>
      </button>

      {open && (
        <div className="border-t border-border px-3 py-2">
          {removed.length === 0 ? (
            <p className="text-xs text-muted-foreground">No se eliminó ninguna propiedad.</p>
          ) : (
            <ul className="space-y-1.5">
              {removed.map((p) => (
                <li key={p.id} className="text-xs">
                  <p className="font-medium text-foreground">
                    {p.titulo || p.direccion || 'Sin título'}
                  </p>
                  <p className="text-muted-foreground">{p.motivo}</p>
                  <a
                    href={p.url_origen}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="truncate text-muted-foreground/70 hover:text-foreground hover:underline"
                  >
                    {p.url_origen}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  )
}
