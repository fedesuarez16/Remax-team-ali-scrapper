'use client'
import { useState } from 'react'
import { Check, FolderPlus } from 'lucide-react'
import { useSearchHistory } from '@/hooks/useSearchHistory'

/**
 * Inline "guardar búsqueda" panel shown on the map's done card. The search is
 * already auto-saved (useZoneSearch); this only lets the user rename it (sets
 * `label`) and drop it into a folder (sets/creates `folder_id`) right there —
 * the same edits the Historial page offers later. Everything PATCHes the row
 * identified by `entryId`; nothing here can lose the already-saved entry.
 */
export function SaveSearchPanel({
  entryId,
  defaultName,
}: {
  entryId: string
  defaultName: string
}) {
  const { folders, createFolder, updateEntry } = useSearchHistory()
  const [name, setName] = useState(defaultName)
  const [folderId, setFolderId] = useState('') // '' = sin carpeta
  const [creating, setCreating] = useState(false)
  const [newFolder, setNewFolder] = useState('')
  const [renamed, setRenamed] = useState(false)

  const commitName = () => {
    const trimmed = name.trim()
    if (trimmed && trimmed !== defaultName) {
      void updateEntry(entryId, { label: trimmed })
      setRenamed(true)
    }
  }

  const changeFolder = (id: string) => {
    setFolderId(id)
    void updateEntry(entryId, { folder_id: id || null })
  }

  const handleCreate = async () => {
    const n = newFolder.trim()
    if (!n) return
    const folder = await createFolder(n)
    setNewFolder('')
    setCreating(false)
    if (folder) changeFolder(folder.id)
  }

  return (
    <div className="space-y-1.5 border-t border-border pt-2">
      <label className="block text-[11px] font-medium text-muted-foreground">Nombre</label>
      <input
        value={name}
        onChange={(e) => {
          setName(e.target.value)
          setRenamed(false)
        }}
        onBlur={commitName}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commitName()
        }}
        className="w-full rounded-lg border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20"
      />

      <label className="block text-[11px] font-medium text-muted-foreground">Carpeta</label>
      {creating ? (
        <div className="flex gap-1">
          <input
            autoFocus
            value={newFolder}
            onChange={(e) => setNewFolder(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleCreate()
              if (e.key === 'Escape') setCreating(false)
            }}
            placeholder="Nombre de carpeta"
            className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20"
          />
          <button
            onClick={() => void handleCreate()}
            className="rounded-lg bg-foreground px-2 py-1 text-xs font-medium text-background"
          >
            OK
          </button>
        </div>
      ) : (
        <div className="flex gap-1">
          <select
            value={folderId}
            onChange={(e) => changeFolder(e.target.value)}
            className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20"
          >
            <option value="">Sin carpeta</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
          <button
            onClick={() => setCreating(true)}
            title="Nueva carpeta"
            className="flex items-center rounded-lg border border-border px-2 py-1 text-foreground hover:bg-muted"
          >
            <FolderPlus className="size-3.5" />
          </button>
        </div>
      )}

      <p className="flex items-center gap-1 pt-0.5 text-[11px] text-muted-foreground">
        <Check className="size-3" /> Guardado en historial{renamed ? ' · nombre actualizado' : ''}
      </p>
    </div>
  )
}
