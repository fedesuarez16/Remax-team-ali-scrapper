'use client'
import { useMemo, useState } from 'react'
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Download,
  Fence,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import {
  useBarrioImport,
  useBarriosCerrados,
  type BarrioCandidate,
  type BarrioCerrado,
  type BarrioKind,
  type ImportResult,
  type PortalRef,
} from '@/hooks/useBarriosCerrados'

const KINDS: { value: BarrioKind; label: string }[] = [
  { value: 'barrio_cerrado', label: 'Barrio cerrado' },
  { value: 'club_de_campo', label: 'Club de campo' },
  { value: 'country', label: 'Country' },
  { value: 'barrio_privado', label: 'Barrio privado' },
]

const LOCALIDADES_SUGERIDAS = [
  'City Bell, La Plata',
  'Gonnet, La Plata',
  'Villa Elisa, La Plata',
  'La Plata',
  'Hudson, Berazategui',
]

export default function BarriosPage() {
  const { barrios, loading, refresh, addBarrio, updateBarrio, deleteBarrio, probeBarrio, setRef } =
    useBarriosCerrados()
  const [modo, setModo] = useState<'uno' | 'importar'>('uno')

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-background text-foreground">
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto w-full max-w-3xl">
          <h1 className="text-xl font-semibold tracking-tight">Barrios cerrados</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Countries, clubes de campo y barrios privados, cargados a mano y buscables igual que una
            zona. La <strong className="font-medium text-foreground">localidad</strong> es lo más
            importante de cada ficha: es la que le da a la búsqueda por dónde seguir cuando un portal
            no encuentra el barrio.
          </p>
        </div>
      </header>

      <datalist id="localidades-barrios">
        {[...new Set([...barrios.map((b) => b.localidad), ...LOCALIDADES_SUGERIDAS])].map((l) => (
          <option key={l} value={l} />
        ))}
      </datalist>

      <div className="mx-auto w-full max-w-3xl flex-1 space-y-6 p-6">
        <div className="space-y-3 rounded-2xl border border-border bg-card p-4 shadow-sm">
          <div className="flex gap-1 rounded-xl bg-muted p-1">
            <ModoTab active={modo === 'uno'} onClick={() => setModo('uno')}>
              Cargar uno
            </ModoTab>
            <ModoTab active={modo === 'importar'} onClick={() => setModo('importar')}>
              Importar un partido
            </ModoTab>
          </div>
          {modo === 'uno' ? <AltaForm onSubmit={addBarrio} /> : <ImportPanel onImported={refresh} />}
        </div>

        {loading ? (
          <p className="py-8 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : barrios.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border px-6 py-10 text-center">
            <Fence className="mx-auto size-6 text-muted-foreground" />
            <p className="mt-2 text-sm text-muted-foreground">
              Todavía no cargaste ningún barrio cerrado.
            </p>
          </div>
        ) : (
          <ul className="space-y-3">
            {barrios.map((b) => (
              <BarrioCard
                key={b.id}
                barrio={b}
                onProbe={() => probeBarrio(b.id)}
                onToggle={() => updateBarrio(b.id, { activo: !b.activo })}
                onDelete={() => deleteBarrio(b.id)}
                onSetRef={(portal, cambios) => setRef(b.id, portal, cambios)}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

// ── Alta de a uno ──────────────────────────────────────────────────────────

function AltaForm({
  onSubmit,
}: {
  onSubmit: (nuevo: { nombre: string; localidad: string; kind: BarrioKind }) => Promise<string | null>
}) {
  const [nombre, setNombre] = useState('')
  const [localidad, setLocalidad] = useState('')
  const [kind, setKind] = useState<BarrioKind>('barrio_cerrado')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const handleSubmit = async () => {
    setSaving(true)
    const err = await onSubmit({ nombre, localidad, kind })
    setSaving(false)
    if (err) {
      setError(err)
      return
    }
    setError(null)
    setNombre('')
    // `localidad` and `kind` stay: loading five countries from the same
    // localidad in a row shouldn't mean retyping it five times.
  }

  return (
    <div className="space-y-2">
      <input
        type="text"
        value={nombre}
        onChange={(e) => setNombre(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && !saving && handleSubmit()}
        placeholder="Nombre (ej: Grand Bell)"
        className={inputCls}
      />
      <input
        type="text"
        list="localidades-barrios"
        value={localidad}
        onChange={(e) => setLocalidad(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && !saving && handleSubmit()}
        placeholder="Localidad que lo contiene (ej: City Bell, La Plata)"
        className={inputCls}
      />
      <div className="flex flex-wrap gap-1">
        {KINDS.map((k) => (
          <button
            key={k.value}
            onClick={() => setKind(k.value)}
            className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
              kind === k.value
                ? 'bg-foreground text-background'
                : 'bg-muted text-muted-foreground hover:text-foreground'
            }`}
          >
            {k.label}
          </button>
        ))}
      </div>
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
      <button
        onClick={handleSubmit}
        disabled={saving || !nombre.trim()}
        className={primaryBtnCls}
      >
        {saving ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
        Agregar barrio
      </button>
      <p className="text-xs text-muted-foreground">
        Escribí el nombre sin el tipo adelante — “Grand Bell”, no “Barrio Cerrado Grand Bell”. Las
        variantes con el tipo se generan solas para reconocer los avisos.
      </p>
    </div>
  )
}

// ── Import masivo ──────────────────────────────────────────────────────────

function ImportPanel({ onImported }: { onImported: () => Promise<void> }) {
  // `refresh` arrives as a prop, never from a second `useBarriosCerrados()`.
  // That hook owns its own state, so a nested instance would refetch into a
  // list nobody renders and leave the real one stale right after an import —
  // the one moment the list definitely changed.
  const { preview, loading, error, buscar, importar, limpiar } = useBarrioImport()
  const [partido, setPartido] = useState('')
  const [localidad, setLocalidad] = useState('')
  const [deep, setDeep] = useState(false)
  const [elegidos, setElegidos] = useState<Set<string>>(new Set())
  const [resultado, setResultado] = useState<ImportResult | null>(null)

  const importables = useMemo(
    () => (preview?.barrios ?? []).filter((b) => !b.ya_existe),
    [preview]
  )

  const handleBuscar = async () => {
    setResultado(null)
    setElegidos(new Set())
    await buscar(partido, { localidad, deep })
  }

  const toggle = (ref: string) =>
    setElegidos((prev) => {
      const next = new Set(prev)
      if (next.has(ref)) next.delete(ref)
      else next.add(ref)
      return next
    })

  const handleImportar = async () => {
    const seleccion = importables.filter((b) => elegidos.has(b.argenprop_ref))
    const res = await importar(seleccion)
    setResultado(res)
    if (res && res.creados > 0) {
      await onImported()
      limpiar()
      setElegidos(new Set())
    }
  }

  return (
    <div className="space-y-2">
      <input
        type="text"
        value={partido}
        onChange={(e) => setPartido(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && !loading && handleBuscar()}
        placeholder="Partido (ej: La Plata, Pilar, Tigre)"
        className={inputCls}
      />
      <input
        type="text"
        list="localidades-barrios"
        value={localidad}
        onChange={(e) => setLocalidad(e.target.value)}
        placeholder="Localidad para todos (opcional — si no, se usa el partido)"
        className={inputCls}
      />
      <label className="flex items-start gap-2 px-1 text-xs text-muted-foreground">
        <input
          type="checkbox"
          checked={deep}
          onChange={(e) => setDeep(e.target.checked)}
          className="mt-0.5"
        />
        <span>
          <strong className="font-medium text-foreground">Búsqueda profunda</strong> — Argenprop
          devuelve como máximo 30 resultados por consulta, así que en partidos grandes la lista sale
          cortada. Esto hace ~20 consultas más y las junta. Medido: Pilar pasa de 29 a 120 barrios.
        </span>
      </label>
      <button onClick={handleBuscar} disabled={loading || !partido.trim()} className={primaryBtnCls}>
        {loading ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
        Buscar en Argenprop
      </button>

      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}

      {resultado && (
        <div className="space-y-1 rounded-xl border border-border bg-background px-3 py-2 text-xs">
          <p className="font-medium text-foreground">
            {resultado.creados} cargado{resultado.creados === 1 ? '' : 's'}
            {resultado.salteados > 0 && `, ${resultado.salteados} ya estaban`}
          </p>
          {resultado.errores.map((e) => (
            <p key={e} className="text-amber-700 dark:text-amber-400">
              {e}
            </p>
          ))}
        </div>
      )}

      {preview && (
        <div className="space-y-2 pt-1">
          {preview.warning && (
            <div className="flex gap-2 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/40 dark:text-amber-200">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <span>{preview.warning}</span>
            </div>
          )}

          {preview.barrios.length === 0 ? (
            <p className="px-1 text-xs text-muted-foreground">
              Argenprop no devolvió barrios cerrados para ese partido.
            </p>
          ) : (
            <>
              <div className="flex items-center justify-between px-1">
                <p className="text-xs text-muted-foreground">
                  {importables.length} para cargar
                  {preview.barrios.length !== importables.length &&
                    ` · ${preview.barrios.length - importables.length} ya estaban`}
                </p>
                <button
                  onClick={() =>
                    setElegidos(
                      elegidos.size === importables.length
                        ? new Set()
                        : new Set(importables.map((b) => b.argenprop_ref))
                    )
                  }
                  className="text-xs font-medium text-foreground underline-offset-2 hover:underline"
                >
                  {elegidos.size === importables.length ? 'Ninguno' : 'Todos'}
                </button>
              </div>

              <ul className="max-h-80 space-y-1 overflow-y-auto rounded-xl border border-border p-1">
                {preview.barrios.map((b) => (
                  <CandidatoRow
                    key={b.argenprop_ref}
                    candidato={b}
                    elegido={elegidos.has(b.argenprop_ref)}
                    onToggle={() => toggle(b.argenprop_ref)}
                  />
                ))}
              </ul>

              <button
                onClick={handleImportar}
                disabled={loading || elegidos.size === 0}
                className={primaryBtnCls}
              >
                {loading ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Download className="size-4" />
                )}
                Cargar {elegidos.size} barrio{elegidos.size === 1 ? '' : 's'}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function CandidatoRow({
  candidato,
  elegido,
  onToggle,
}: {
  candidato: BarrioCandidate
  elegido: boolean
  onToggle: () => void
}) {
  return (
    <li>
      <button
        onClick={onToggle}
        disabled={candidato.ya_existe}
        className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left transition ${
          candidato.ya_existe ? 'opacity-40' : 'hover:bg-muted'
        }`}
      >
        <span
          className={`flex size-4 shrink-0 items-center justify-center rounded border ${
            elegido ? 'border-foreground bg-foreground' : 'border-border'
          }`}
        >
          {elegido && <Check className="size-3 text-background" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-foreground">{candidato.nombre}</span>
          {/* The portal's own label. This is the answer to "cómo figura ahí",
              and the only thing a human can actually check before accepting. */}
          <span className="block truncate text-xs text-muted-foreground">{candidato.label}</span>
        </span>
        {candidato.ya_existe && (
          <span className="shrink-0 text-xs text-muted-foreground">ya está</span>
        )}
      </button>
    </li>
  )
}

// ── Ficha de un barrio ─────────────────────────────────────────────────────

function BarrioCard({
  barrio,
  onProbe,
  onToggle,
  onDelete,
  onSetRef,
}: {
  barrio: BarrioCerrado
  onProbe: () => Promise<string | null>
  onToggle: () => Promise<string | null>
  onDelete: () => Promise<void>
  onSetRef: (
    portal: string,
    cambios: { ref?: string | null; confirmed?: boolean }
  ) => Promise<string | null>
}) {
  const [abierto, setAbierto] = useState(false)
  const [probing, setProbing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const nativos = barrio.portal_refs.filter((r) => r.strategy === 'native').length
  const confirmados = barrio.portal_refs.filter((r) => r.confirmed).length

  const handleProbe = async () => {
    setProbing(true)
    setError(await onProbe())
    setProbing(false)
    setAbierto(true)
  }

  return (
    <li className={`rounded-2xl border border-border bg-card shadow-sm ${barrio.activo ? '' : 'opacity-55'}`}>
      <div className="flex items-center gap-3 p-4">
        <Fence className="size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">{barrio.nombre}</p>
          {/* The composite the pipeline actually walks, shown verbatim: it is
              what makes "por dónde va a degradar esta búsqueda" auditable
              instead of a black box. */}
          <p className="truncate font-mono text-xs text-muted-foreground">{barrio.zona}</p>
        </div>
        <button
          onClick={handleProbe}
          disabled={probing}
          title="Preguntarle a cada portal cómo se llama este barrio"
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-foreground transition hover:bg-muted disabled:opacity-40"
        >
          {probing ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RefreshCw className="size-3.5" />
          )}
          Consultar portales
        </button>
        <button
          onClick={onToggle}
          title={barrio.activo ? 'Desactivar' : 'Activar'}
          className="shrink-0 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition hover:text-foreground"
        >
          {barrio.activo ? 'Activo' : 'Inactivo'}
        </button>
        <button
          onClick={onDelete}
          title="Borrar"
          className="shrink-0 rounded-lg p-1.5 text-muted-foreground transition hover:text-red-600"
        >
          <Trash2 className="size-3.5" />
        </button>
        <button
          onClick={() => setAbierto((v) => !v)}
          aria-label={abierto ? 'Cerrar detalle' : 'Abrir detalle'}
          className="shrink-0 rounded-lg p-1.5 text-muted-foreground transition hover:text-foreground"
        >
          <ChevronDown className={`size-4 transition ${abierto ? 'rotate-180' : ''}`} />
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 px-4 pb-3 text-xs text-muted-foreground">
        {barrio.portal_refs.length === 0 ? (
          <span>Sin consultar todavía</span>
        ) : (
          <>
            <span>
              {nativos} de {barrio.portal_refs.length} portales lo tienen como ubicación propia
            </span>
            <span>·</span>
            {/* Confirming is not bookkeeping: a confirmed ref is what the
                scraper filters on server-side. Unconfirmed, the search falls
                back to walking the localidad and filtering by name — correct,
                but it pays pages it throws away. The copy has to say that, or
                nobody clicks the button. */}
            <span className={confirmados === 0 ? 'text-amber-700 dark:text-amber-400' : ''}>
              {confirmados === 0
                ? `${nativos} sin confirmar — confirmalos para que la búsqueda los use`
                : `${confirmados} confirmado${confirmados === 1 ? '' : 's'} · la búsqueda los usa`}
            </span>
          </>
        )}
      </div>

      {error && <p className="px-4 pb-3 text-xs text-red-600 dark:text-red-400">{error}</p>}

      {abierto && (
        <div className="space-y-3 border-t border-border p-4">
          <div>
            <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Se reconoce en los avisos como
            </p>
            <div className="flex flex-wrap gap-1">
              {barrio.aliases_efectivos.map((a) => (
                <span
                  key={a}
                  className="rounded-md bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground"
                >
                  {a}
                </span>
              ))}
            </div>
          </div>

          {barrio.portal_refs.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Cómo figura en cada portal
              </p>
              <ul className="space-y-1">
                {[...barrio.portal_refs]
                  .sort((a, b) => a.portal.localeCompare(b.portal))
                  .map((r) => (
                    <RefRow
                      key={r.portal}
                      refe={r}
                      onConfirm={() => onSetRef(r.portal, { confirmed: !r.confirmed })}
                    />
                  ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  )
}

function RefRow({ refe, onConfirm }: { refe: PortalRef; onConfirm: () => Promise<string | null> }) {
  const nativo = refe.strategy === 'native'
  return (
    <li className="flex items-start gap-2.5 rounded-xl border border-border bg-background px-3 py-2">
      <span
        className={`mt-0.5 shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium uppercase ${
          nativo
            ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300'
            : 'bg-muted text-muted-foreground'
        }`}
      >
        {nativo ? 'propio' : 'localidad'}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-foreground">{refe.portal}</p>
        {refe.ref && (
          <p className="truncate font-mono text-xs text-muted-foreground">{refe.ref}</p>
        )}
        {/* Why the probe decided what it decided. Without it the operator is
            confirming a code they have no way to judge. */}
        {refe.note && <p className="mt-0.5 text-xs text-muted-foreground">{refe.note}</p>}
      </div>
      {nativo && (
        <button
          onClick={onConfirm}
          title={
            refe.confirmed
              ? 'Quitar confirmación — la búsqueda va a volver a buscar por localidad y filtrar por nombre'
              : 'Confirmar que es este barrio — la búsqueda va a filtrar con esta referencia en el portal'
          }
          className={`flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium transition ${
            refe.confirmed
              ? 'bg-emerald-600 text-white hover:bg-emerald-700'
              : 'border border-border text-muted-foreground hover:text-foreground'
          }`}
        >
          {refe.confirmed ? <Check className="size-3" /> : <X className="size-3" />}
          {refe.confirmed ? 'Confirmado' : 'Confirmar'}
        </button>
      )}
    </li>
  )
}

// ── Bits compartidos ───────────────────────────────────────────────────────

const inputCls =
  'w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground/20'

const primaryBtnCls =
  'flex w-full items-center justify-center gap-2 rounded-xl bg-foreground px-3 py-2 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:opacity-40'

function ModoTab({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={`flex-1 rounded-lg px-3 py-1.5 text-sm font-medium transition ${
        active ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
      }`}
    >
      {children}
    </button>
  )
}
