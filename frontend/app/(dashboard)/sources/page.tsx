'use client'
import { useState } from 'react'
import {
  Building2,
  Check,
  Globe,
  Layers,
  ListPlus,
  Loader2,
  MapPin,
  Pencil,
  Plus,
  Trash2,
  X,
} from 'lucide-react'
import {
  useManualSources,
  useSourceZonas,
  type BulkResult,
  type ManualSource,
} from '@/hooks/useManualSources'
import { usePortals } from '@/hooks/usePortals'

const ZONA_SUGERIDAS = [
  'City Bell', 'Gonnet', 'Villa Elisa', 'Casco La Plata',
  'Los Hornos', 'Ensenada', 'Berisso', 'Hudson',
]

export default function SourcesPage() {
  const { sources, addSource, addSourcesBulk, deleteSource, toggleSource, updateSource, loading } =
    useManualSources()
  const { portals, togglePortal, loading: loadingPortals } = usePortals()
  const { zonas } = useSourceZonas()
  const [nombre, setNombre] = useState('')
  const [url, setUrl] = useState('')
  const [zona, setZona] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editNombre, setEditNombre] = useState('')
  const [editUrl, setEditUrl] = useState('')
  const [editZona, setEditZona] = useState('')
  const [editError, setEditError] = useState<string | null>(null)
  const [savingEdit, setSavingEdit] = useState(false)
  const [modo, setModo] = useState<'una' | 'varias'>('una')
  const [bulkText, setBulkText] = useState('')
  const [bulkSaving, setBulkSaving] = useState(false)
  const [bulkResult, setBulkResult] = useState<BulkResult | null>(null)

  const portalesActivos = portals.filter((p) => p.activo).length
  const portalesInactivos = portals.length - portalesActivos
  const inmoActivas = sources.filter((s) => s.activo).length
  const inmoInactivas = sources.length - inmoActivas

  const handleSubmit = async () => {
    setSaving(true)
    setError(null)
    const err = await addSource(nombre, url, zona)
    setSaving(false)
    if (err) {
      setError(err)
      return
    }
    setNombre('')
    setUrl('')
    // Keep `zona` so loading several inmobiliarias into the same zona in a
    // row doesn't mean retyping it every time.
  }

  const handleBulkSubmit = async () => {
    setBulkSaving(true)
    setBulkResult(null)
    const result = await addSourcesBulk(bulkText)
    setBulkSaving(false)
    setBulkResult(result)
    // Only clear on a clean run: if something bounced, the user needs the
    // original paste on screen to see which lines the report is talking about.
    if (result.agregadas > 0 && result.invalidas.length === 0 && result.duplicadas.length === 0) {
      setBulkText('')
    }
  }

  const urlsPegadas = bulkText.split(/[\s,;]+/).filter(Boolean).length

  const startEdit = (s: ManualSource) => {
    setEditingId(s.id)
    setEditNombre(s.nombre)
    setEditUrl(s.url)
    setEditZona(s.zona ?? '')
    setEditError(null)
  }

  const cancelEdit = () => {
    setEditingId(null)
    setEditNombre('')
    setEditUrl('')
    setEditZona('')
    setEditError(null)
  }

  const handleSaveEdit = async (id: string) => {
    setSavingEdit(true)
    setEditError(null)
    // All three always travel: the form is seeded with the current values, so
    // an untouched field just re-sends what's already stored.
    const err = await updateSource(id, { nombre: editNombre, url: editUrl, zona: editZona })
    setSavingEdit(false)
    if (err) {
      setEditError(err)
      return
    }
    cancelEdit()
  }

  const handleDelete = async (id: string) => {
    setDeletingId(id)
    await deleteSource(id)
    setDeletingId(null)
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-background text-foreground">
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto w-full max-w-2xl">
          <h1 className="text-xl font-semibold tracking-tight">Fuentes</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Cargá inmobiliarias o portales que no aparecen en Google (ej: la web de una oficina RE/MAX).
            Cada búsqueda futura también va a rastrear estos sitios. La zona que asignes acá es la que
            después se puede elegir al buscar — la clasificación la hacemos nosotros, no el sistema.
          </p>
        </div>
      </header>

      {/* Shared by the alta form and every inline zona editor in the list, so
          it lives outside both — a datalist inside a conditional branch stops
          autocompleting the moment that branch unmounts. */}
      <datalist id="zonas-cargadas">
        {[...new Set([...zonas.map((z) => z.zona), ...ZONA_SUGERIDAS])].map((z) => (
          <option key={z} value={z} />
        ))}
      </datalist>

      <div className="mx-auto w-full max-w-2xl flex-1 space-y-6 p-6">
        {/* ── Resumen de fuentes ────────────────────────────────────────── */}
        <div className="grid gap-3 sm:grid-cols-2">
          <SummaryCard
            icon={<Layers className="size-4 text-muted-foreground" />}
            title="Portales Inmobiliarios"
            activas={portalesActivos}
            inactivas={portalesInactivos}
            loading={loadingPortals}
          />
          <SummaryCard
            icon={<Building2 className="size-4 text-muted-foreground" />}
            title="Inmobiliarias Tradicionales"
            activas={inmoActivas}
            inactivas={inmoInactivas}
            loading={loading}
          />
        </div>

        {/* ── Portales del catálogo ─────────────────────────────────────── */}
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Portales inmobiliarios
          </p>
          <ul className="space-y-2">
            {portals.map((p) => (
              <li
                key={p.id}
                className="flex items-center gap-3 rounded-xl border border-border bg-card px-3 py-2.5"
              >
                <Layers className="size-4 shrink-0 text-muted-foreground" />
                <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                  {p.label}
                </p>
                <Toggle
                  active={p.activo}
                  onClick={() => togglePortal(p.id, !p.activo)}
                  label={`${p.activo ? 'Desactivar' : 'Activar'} ${p.label}`}
                />
              </li>
            ))}
          </ul>
        </div>

        {/* ── Alta de inmobiliaria / portal manual ──────────────────────── */}
        <div className="space-y-2 rounded-2xl border border-border bg-card p-4 shadow-sm">
          <div className="flex gap-1 rounded-xl bg-muted p-1">
            <ModoTab active={modo === 'una'} onClick={() => setModo('una')}>
              Una fuente
            </ModoTab>
            <ModoTab active={modo === 'varias'} onClick={() => setModo('varias')}>
              Pegar varias URLs
            </ModoTab>
          </div>

          {modo === 'varias' ? (
            <>
              <textarea
                value={bulkText}
                onChange={(e) => setBulkText(e.target.value)}
                rows={8}
                placeholder={'Pegá una URL por línea:\nhttps://www.remax.com.ar/agencia/belgrano\nhttps://www.remax.com.ar/agencia/palermo\n...'}
                aria-label="URLs para agregar en lote"
                className="w-full resize-y rounded-xl border border-border bg-background px-3 py-2 font-mono text-xs text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground/20"
              />
              <p className="text-xs text-muted-foreground">
                Se cargan sin nombre ni zona — el nombre se deriva de cada URL y lo podés editar
                después. Las repetidas se saltean solas.
              </p>
              {bulkResult && <BulkReport result={bulkResult} />}
              <button
                onClick={handleBulkSubmit}
                disabled={bulkSaving || urlsPegadas === 0}
                className="flex w-full items-center justify-center gap-2 rounded-xl bg-foreground px-3 py-2 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:opacity-40"
              >
                {bulkSaving ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <ListPlus className="size-4" />
                )}
                {urlsPegadas === 0
                  ? 'Agregar fuentes'
                  : `Agregar ${urlsPegadas} fuente${urlsPegadas === 1 ? '' : 's'}`}
              </button>
            </>
          ) : (
            <>
          <input
            type="text"
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="Nombre (ej: RE/MAX Belgrano)"
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground/20"
          />
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !saving && handleSubmit()}
            placeholder="https://www.remax.com.ar/agencia/belgrano"
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground/20"
          />
          <input
            type="text"
            value={zona}
            onChange={(e) => setZona(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !saving && handleSubmit()}
            list="zonas-cargadas"
            placeholder="Zona (ej: City Bell) — opcional"
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground/20"
          />
          {error && <p className="text-xs text-destructive">{error}</p>}
          <button
            onClick={handleSubmit}
            disabled={saving || !nombre.trim() || !url.trim()}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-foreground px-3 py-2 text-sm font-medium text-background transition hover:bg-foreground/85 disabled:opacity-40"
          >
            {saving ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Agregar fuente
          </button>
            </>
          )}
        </div>

        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {loading ? 'Cargando...' : `${sources.length} fuente${sources.length === 1 ? '' : 's'} registrada${sources.length === 1 ? '' : 's'}`}
          </p>

          {!loading && sources.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-10 text-center">
              <Globe className="size-8 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">Todavía no cargaste ninguna fuente</p>
            </div>
          ) : (
            <ul className="space-y-2">
              {sources.map((s) => (
                <li
                  key={s.id}
                  className={`flex gap-3 rounded-xl border border-border bg-card px-3 py-2.5 ${
                    editingId === s.id ? 'items-start' : 'items-center'
                  } ${s.activo ? '' : 'opacity-55'}`}
                >
                  <Globe className={`size-4 shrink-0 text-muted-foreground ${
                    editingId === s.id ? 'mt-1.5' : ''
                  }`} />
                  <div className="min-w-0 flex-1">
                    {editingId === s.id ? (
                      <div className="space-y-1.5">
                        <EditField
                          autoFocus
                          value={editNombre}
                          onChange={setEditNombre}
                          onSave={() => handleSaveEdit(s.id)}
                          onCancel={cancelEdit}
                          disabled={savingEdit}
                          placeholder="Nombre"
                          ariaLabel={`Nombre de ${s.nombre}`}
                        />
                        <EditField
                          type="url"
                          value={editUrl}
                          onChange={setEditUrl}
                          onSave={() => handleSaveEdit(s.id)}
                          onCancel={cancelEdit}
                          disabled={savingEdit}
                          placeholder="https://..."
                          ariaLabel={`URL de ${s.nombre}`}
                        />
                        <EditField
                          value={editZona}
                          onChange={setEditZona}
                          onSave={() => handleSaveEdit(s.id)}
                          onCancel={cancelEdit}
                          disabled={savingEdit}
                          list="zonas-cargadas"
                          placeholder="Zona — vacío la deja sin zona"
                          ariaLabel={`Zona de ${s.nombre}`}
                        />
                        {editError && <p className="text-xs text-destructive">{editError}</p>}
                      </div>
                    ) : (
                      <>
                        <p className="flex items-center gap-2 truncate text-sm font-medium text-foreground">
                          {s.nombre}
                          {s.zona ? (
                            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] font-normal text-muted-foreground">
                              <MapPin className="size-3" />
                              {s.zona}
                            </span>
                          ) : (
                            <span className="shrink-0 text-[11px] font-normal text-muted-foreground/60">
                              sin zona
                            </span>
                          )}
                        </p>
                        <a
                          href={s.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="truncate text-xs text-muted-foreground hover:text-foreground hover:underline"
                        >
                          {s.url}
                        </a>
                      </>
                    )}
                  </div>
                  {editingId === s.id ? (
                    <>
                      <button
                        onClick={() => handleSaveEdit(s.id)}
                        disabled={savingEdit || !editNombre.trim() || !editUrl.trim()}
                        className="shrink-0 rounded-md p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-40"
                        aria-label={`Guardar cambios de ${s.nombre}`}
                      >
                        {savingEdit ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : (
                          <Check className="size-4" />
                        )}
                      </button>
                      <button
                        onClick={cancelEdit}
                        disabled={savingEdit}
                        className="shrink-0 rounded-md p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-40"
                        aria-label={`Cancelar edición de ${s.nombre}`}
                      >
                        <X className="size-4" />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={() => startEdit(s)}
                        className="shrink-0 rounded-md p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
                        aria-label={`Editar ${s.nombre}`}
                      >
                        <Pencil className="size-4" />
                      </button>
                      <Toggle
                        active={s.activo}
                        onClick={() => toggleSource(s.id, !s.activo)}
                        label={`${s.activo ? 'Desactivar' : 'Activar'} ${s.nombre}`}
                      />
                      <button
                        onClick={() => handleDelete(s.id)}
                        disabled={deletingId === s.id}
                        className="shrink-0 rounded-md p-1.5 text-muted-foreground transition hover:bg-muted hover:text-destructive disabled:opacity-40"
                        aria-label={`Eliminar ${s.nombre}`}
                      >
                        {deletingId === s.id ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : (
                          <Trash2 className="size-4" />
                        )}
                      </button>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}

/** Segmented control for the "una fuente" / "pegar varias" alta modes. */
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
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`flex-1 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
        active
          ? 'bg-background text-foreground shadow-sm'
          : 'text-muted-foreground hover:text-foreground'
      }`}
    >
      {children}
    </button>
  )
}

/** What a bulk paste actually did. A batch is partially successful by design,
 * so the skipped URLs are listed verbatim — telling the user "40 de 70" with
 * no way to see WHICH 30 bounced is not a report, it's a riddle. */
function BulkReport({ result }: { result: BulkResult }) {
  if (result.error) {
    return <p className="text-xs text-destructive">{result.error}</p>
  }

  return (
    <div className="space-y-1.5 rounded-xl bg-muted/60 px-3 py-2 text-xs">
      <p className="font-medium text-foreground">
        {result.agregadas} fuente{result.agregadas === 1 ? '' : 's'} agregada
        {result.agregadas === 1 ? '' : 's'}
      </p>
      {result.duplicadas.length > 0 && (
        <details>
          <summary className="cursor-pointer text-muted-foreground">
            {result.duplicadas.length} ya estaban cargadas
          </summary>
          <ul className="mt-1 space-y-0.5 break-all font-mono text-[11px] text-muted-foreground">
            {result.duplicadas.map((u) => (
              <li key={u}>{u}</li>
            ))}
          </ul>
        </details>
      )}
      {result.invalidas.length > 0 && (
        <details open>
          <summary className="cursor-pointer text-destructive">
            {result.invalidas.length} no son URLs válidas
          </summary>
          <ul className="mt-1 space-y-0.5 break-all font-mono text-[11px] text-destructive/80">
            {result.invalidas.map((u) => (
              <li key={u}>{u}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

/** One row of the inline edit form. Enter saves the whole row, Escape aborts —
 * the three fields travel together in a single PATCH. */
function EditField({
  value,
  onChange,
  onSave,
  onCancel,
  disabled,
  placeholder,
  ariaLabel,
  type = 'text',
  list,
  autoFocus,
}: {
  value: string
  onChange: (v: string) => void
  onSave: () => void
  onCancel: () => void
  disabled: boolean
  placeholder: string
  ariaLabel: string
  type?: string
  list?: string
  autoFocus?: boolean
}) {
  return (
    <input
      type={type}
      list={list}
      autoFocus={autoFocus}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && !disabled) onSave()
        if (e.key === 'Escape') onCancel()
      }}
      placeholder={placeholder}
      aria-label={ariaLabel}
      className="w-full rounded-lg border border-border bg-background px-2 py-1 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground/20 disabled:opacity-50"
    />
  )
}

/** CRM-style summary card: title + Activas / Inactivas side by side. */
function SummaryCard({
  icon,
  title,
  activas,
  inactivas,
  loading,
}: {
  icon: React.ReactNode
  title: string
  activas: number
  inactivas: number
  loading: boolean
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <div className="flex items-center gap-2">
        {icon}
        <h2 className="text-sm font-semibold">{title}</h2>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <div>
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">Activas</p>
          <p className="text-2xl font-semibold text-foreground">{loading ? '—' : activas}</p>
        </div>
        <div>
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">Inactivas</p>
          <p className="text-2xl font-semibold text-muted-foreground">{loading ? '—' : inactivas}</p>
        </div>
      </div>
    </div>
  )
}

/** Compact on/off switch used for both portales and inmobiliarias. */
function Toggle({
  active,
  onClick,
  label,
}: {
  active: boolean
  onClick: () => void
  label: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={active}
      aria-label={label}
      onClick={onClick}
      className={`relative h-5 w-9 shrink-0 rounded-full transition ${
        active ? 'bg-foreground' : 'bg-border'
      }`}
    >
      <span
        className={`absolute top-0.5 size-4 rounded-full bg-background transition-all ${
          active ? 'left-[1.125rem]' : 'left-0.5'
        }`}
      />
    </button>
  )
}
