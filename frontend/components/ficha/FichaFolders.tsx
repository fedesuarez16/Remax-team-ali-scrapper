'use client'
import { useEffect, useRef, useState } from 'react'
import { Folder as FolderIcon, FolderInput, FolderPlus, Loader2, Trash2, X } from 'lucide-react'
import type { FichaFolder } from '@/hooks/useFichaFolders'

/** Filtro de carpeta activo: todas, sin carpeta, o el id de una carpeta. */
export type CarpetaFiltro = 'todas' | 'sin' | string

function NuevaCarpetaInput({
  onCreate,
  onCancel,
  compact,
}: {
  onCreate: (name: string) => Promise<unknown>
  onCancel: () => void
  compact?: boolean
}) {
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)

  const commit = async () => {
    const trimmed = name.trim()
    if (!trimmed || saving) return
    setSaving(true)
    await onCreate(trimmed)
    setSaving(false)
  }

  return (
    <div className="flex items-center gap-1">
      <input
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') void commit()
          if (e.key === 'Escape') onCancel()
        }}
        placeholder="Nombre de carpeta"
        disabled={saving}
        className={`rounded-lg border border-border bg-background px-2 py-1 text-xs text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground/20 ${
          compact ? 'w-full' : 'w-40'
        }`}
      />
      {saving && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
    </div>
  )
}

/**
 * Fila de chips para filtrar las fichas por carpeta, con alta y baja de
 * carpetas inline. Mismo lenguaje visual que el filtro de estado
 * (Faltan enviar / Enviadas / Todas) que ya vive en la página.
 */
export function CarpetaChips({
  folders,
  value,
  onChange,
  counts,
  onCreate,
  onDelete,
}: {
  folders: FichaFolder[]
  value: CarpetaFiltro
  onChange: (f: CarpetaFiltro) => void
  /** Fichas por carpeta; `todas` y `sin` incluidas. */
  counts: Record<string, number>
  onCreate: (name: string) => Promise<FichaFolder | null>
  onDelete: (id: string) => Promise<boolean>
}) {
  const [creating, setCreating] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const chip = (id: CarpetaFiltro, label: string, icon?: boolean) => {
    const active = value === id
    return (
      <button
        key={id}
        onClick={() => onChange(id)}
        className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
          active
            ? 'border-foreground bg-foreground text-background'
            : 'border-border bg-background text-muted-foreground hover:bg-muted'
        }`}
      >
        {icon && <FolderIcon className="size-3" />}
        {label}
        <span className={`font-mono tabular-nums ${active ? 'opacity-70' : 'opacity-60'}`}>
          {counts[id] ?? 0}
        </span>
      </button>
    )
  }

  const activeFolder = folders.find((f) => f.id === value)

  const borrar = async (id: string) => {
    if (deleting) return
    setDeleting(true)
    const ok = await onDelete(id)
    setDeleting(false)
    setConfirmDelete(null)
    if (ok && value === id) onChange('todas')
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {chip('todas', 'Todas las carpetas')}
      {folders.map((f) => chip(f.id, f.name, true))}
      {chip('sin', 'Sin carpeta')}

      {creating ? (
        <NuevaCarpetaInput
          onCreate={async (name) => {
            const folder = await onCreate(name)
            setCreating(false)
            if (folder) onChange(folder.id)
          }}
          onCancel={() => setCreating(false)}
        />
      ) : (
        <button
          onClick={() => setCreating(true)}
          className="flex items-center gap-1 rounded-full border border-dashed border-border px-3 py-1 text-xs font-medium text-muted-foreground transition hover:bg-muted hover:text-foreground"
        >
          <FolderPlus className="size-3" />
          Nueva carpeta
        </button>
      )}

      {activeFolder &&
        (confirmDelete === activeFolder.id ? (
          <div className="flex items-center gap-1.5 rounded-full border border-destructive/40 bg-destructive/10 px-2 py-0.5">
            <span className="text-xs text-foreground">¿Borrar «{activeFolder.name}»? Las fichas quedan sin carpeta.</span>
            <button
              onClick={() => setConfirmDelete(null)}
              disabled={deleting}
              className="rounded px-1.5 py-0.5 text-xs text-muted-foreground transition hover:text-foreground disabled:opacity-50"
            >
              No
            </button>
            <button
              onClick={() => void borrar(activeFolder.id)}
              disabled={deleting}
              className="flex items-center gap-1 rounded bg-destructive px-2 py-0.5 text-xs font-medium text-white transition hover:bg-destructive/85 disabled:opacity-60"
            >
              {deleting ? <Loader2 className="size-3 animate-spin" /> : <Trash2 className="size-3" />}
              Sí, borrar
            </button>
          </div>
        ) : (
          <button
            onClick={() => setConfirmDelete(activeFolder.id)}
            title={`Borrar la carpeta «${activeFolder.name}»`}
            className="flex items-center gap-1 rounded-full border border-border px-2 py-1 text-xs text-muted-foreground transition hover:border-destructive/40 hover:text-destructive"
          >
            <X className="size-3" />
          </button>
        ))}
    </div>
  )
}

/**
 * Acción "Mover a carpeta" de la barra de selección. Abre un popover con las
 * carpetas existentes, "Sin carpeta", y alta inline — igual que el historial.
 */
export function MoverACarpeta({
  folders,
  ids,
  onAssign,
  onCreate,
}: {
  folders: FichaFolder[]
  ids: string[]
  /** Se resuelve con los ids realmente movidos. */
  onAssign: (ids: string[], folderId: string | null) => Promise<string[]>
  onCreate: (name: string) => Promise<FichaFolder | null>
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [moving, setMoving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
        setCreating(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  const mover = async (folderId: string | null) => {
    if (moving || ids.length === 0) return
    setMoving(true)
    setError(null)
    const moved = await onAssign(ids, folderId)
    setMoving(false)
    if (moved.length === 0) {
      setError('No se pudo mover. Probá de nuevo.')
      return
    }
    setOpen(false)
    setCreating(false)
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={ids.length === 0}
        className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-muted disabled:opacity-40"
      >
        {moving ? <Loader2 className="size-3.5 animate-spin" /> : <FolderInput className="size-3.5" />}
        Mover a carpeta
      </button>

      {open && (
        <div className="absolute bottom-full right-0 z-20 mb-1 w-56 rounded-lg border border-border bg-card p-1 shadow-lg">
          {error && <p className="px-2 py-1 text-xs text-destructive">{error}</p>}
          {folders.map((f) => (
            <button
              key={f.id}
              onClick={() => void mover(f.id)}
              disabled={moving}
              className="flex w-full items-center gap-2 truncate rounded px-2 py-1.5 text-left text-xs text-foreground hover:bg-muted disabled:opacity-50"
            >
              <FolderIcon className="size-3 shrink-0 text-muted-foreground" />
              <span className="truncate">{f.name}</span>
            </button>
          ))}
          <button
            onClick={() => void mover(null)}
            disabled={moving}
            className="block w-full rounded px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
          >
            Sin carpeta
          </button>
          <div className="my-1 border-t border-border" />
          {creating ? (
            <div className="px-1 py-1">
              <NuevaCarpetaInput
                compact
                onCreate={async (name) => {
                  const folder = await onCreate(name)
                  if (!folder) {
                    setError('No se pudo crear la carpeta.')
                    return
                  }
                  await mover(folder.id)
                }}
                onCancel={() => setCreating(false)}
              />
            </div>
          ) : (
            <button
              onClick={() => setCreating(true)}
              className="flex w-full items-center gap-1 rounded px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-muted"
            >
              <FolderPlus className="size-3" />
              Nueva carpeta…
            </button>
          )}
        </div>
      )}
    </div>
  )
}
