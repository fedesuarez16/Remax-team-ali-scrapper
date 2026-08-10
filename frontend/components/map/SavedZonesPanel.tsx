'use client'
import { useState } from 'react'
import { Bookmark, Check, ChevronDown, Pencil, Trash2, X } from 'lucide-react'
import type { SavedZone } from '@/hooks/useSavedZones'

/**
 * Saved zone delineations panel for the map. Stores GEOMETRY ONLY — no search
 * results, no job — so a zone can be re-drawn and re-searched any number of
 * times. Clicking a zone hands its polygon back to the map, which draws it and
 * enables the usual "Buscar en esta zona" flow.
 */
export function SavedZonesPanel({
  zones,
  loading,
  error,
  activeZoneId,
  canSave,
  onSelect,
  onSave,
  onRename,
  onDelete,
}: {
  zones: SavedZone[]
  loading: boolean
  error: string | null
  activeZoneId: string | null
  /** True when there's a closed delineation on the map worth saving. */
  canSave: boolean
  onSelect: (zone: SavedZone) => void
  onSave: (name: string) => void
  onRename: (id: string, name: string) => void
  onDelete: (id: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')

  const commitSave = () => {
    const trimmed = newName.trim()
    if (!trimmed) return
    onSave(trimmed)
    setNewName('')
  }

  const startEdit = (zone: SavedZone) => {
    setEditingId(zone.id)
    setEditName(zone.name)
  }

  const commitEdit = () => {
    const trimmed = editName.trim()
    if (editingId && trimmed) onRename(editingId, trimmed)
    setEditingId(null)
  }

  return (
    <div className="w-72 overflow-hidden rounded-lg border border-border bg-background/95 shadow-sm">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-xs font-semibold text-foreground hover:bg-muted"
      >
        <Bookmark className="size-3.5" />
        Zonas guardadas
        <span className="text-muted-foreground">({zones.length})</span>
        <ChevronDown
          className={`ml-auto size-3.5 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="border-t border-border">
          {canSave && (
            <div className="flex gap-1 border-b border-border p-2">
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commitSave()
                }}
                placeholder="Nombre de la zona dibujada"
                className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20"
              />
              <button
                onClick={commitSave}
                disabled={!newName.trim()}
                className="rounded-lg bg-foreground px-2 py-1 text-xs font-medium text-background disabled:opacity-40"
              >
                Guardar
              </button>
            </div>
          )}

          <div className="max-h-64 overflow-y-auto">
            {loading && <p className="px-3 py-3 text-xs text-muted-foreground">Cargando...</p>}
            {!loading && zones.length === 0 && (
              <p className="px-3 py-3 text-xs text-muted-foreground">
                Todavía no guardaste ninguna zona. Delineá una y ponele nombre.
              </p>
            )}
            {zones.map((zone) => (
              <div
                key={zone.id}
                className={`flex items-center gap-1 border-b border-border px-2 py-1.5 ${
                  zone.id === activeZoneId ? 'bg-muted' : ''
                }`}
              >
                {editingId === zone.id ? (
                  <>
                    <input
                      autoFocus
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitEdit()
                        if (e.key === 'Escape') setEditingId(null)
                      }}
                      className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20"
                    />
                    <button
                      onClick={commitEdit}
                      title="Guardar nombre"
                      className="rounded-lg p-1 text-foreground hover:bg-muted"
                    >
                      <Check className="size-3.5" />
                    </button>
                    <button
                      onClick={() => setEditingId(null)}
                      title="Cancelar"
                      className="rounded-lg p-1 text-muted-foreground hover:bg-muted"
                    >
                      <X className="size-3.5" />
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      onClick={() => onSelect(zone)}
                      className="min-w-0 flex-1 truncate px-1 py-0.5 text-left text-xs font-medium text-foreground hover:underline"
                      title={`Dibujar ${zone.name}`}
                    >
                      {zone.name}
                      <span className="ml-1 text-[11px] font-normal text-muted-foreground">
                        · {zone.polygon.length} vértices
                      </span>
                    </button>
                    <button
                      onClick={() => startEdit(zone)}
                      title="Renombrar"
                      className="rounded-lg p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <Pencil className="size-3.5" />
                    </button>
                    <button
                      onClick={() => onDelete(zone.id)}
                      title="Borrar zona guardada"
                      className="rounded-lg p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </>
                )}
              </div>
            ))}
          </div>

          {error && <p className="px-3 py-2 text-[11px] text-muted-foreground">{error}</p>}
        </div>
      )}
    </div>
  )
}
