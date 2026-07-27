'use client'
import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Clock, Folder as FolderIcon, FolderPlus, Pencil, Search, Trash2 } from 'lucide-react'
import { useSearchHistory, type Folder, type SearchEntry } from '@/hooks/useSearchHistory'

function MovePopover({
  entryId,
  folders,
  onClose,
  onAssign,
  onCreateAndAssign,
}: {
  entryId: string
  folders: Folder[]
  onClose: () => void
  onAssign: (entryId: string, folderId: string | null) => void
  onCreateAndAssign: (entryId: string, name: string) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [onClose])

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full z-10 mt-1 w-44 rounded-lg border border-sidebar-border bg-sidebar p-1 shadow-lg"
    >
      <button
        onClick={() => {
          onAssign(entryId, null)
          onClose()
        }}
        className="block w-full rounded px-2 py-1 text-left text-xs text-sidebar-foreground/70 hover:bg-sidebar-accent"
      >
        Sin carpeta
      </button>
      {folders.map((f) => (
        <button
          key={f.id}
          onClick={() => {
            onAssign(entryId, f.id)
            onClose()
          }}
          className="block w-full truncate rounded px-2 py-1 text-left text-xs text-sidebar-foreground/70 hover:bg-sidebar-accent"
        >
          {f.name}
        </button>
      ))}
      {creating ? (
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && name.trim()) {
              onCreateAndAssign(entryId, name.trim())
              onClose()
            }
            if (e.key === 'Escape') setCreating(false)
          }}
          placeholder="Nombre de carpeta"
          className="mt-1 w-full rounded border border-sidebar-border bg-sidebar-accent/50 px-2 py-1 text-xs text-sidebar-foreground focus:outline-none"
        />
      ) : (
        <button
          onClick={() => setCreating(true)}
          className="mt-1 flex w-full items-center gap-1 rounded px-2 py-1 text-left text-xs text-sidebar-foreground/50 hover:bg-sidebar-accent"
        >
          <FolderPlus className="size-3" />
          Nueva carpeta
        </button>
      )}
    </div>
  )
}

function EntryRow({
  entry,
  folders,
  isMoving,
  onRename,
  onDelete,
  onToggleMove,
  onCloseMove,
  onAssign,
  onCreateAndAssign,
}: {
  entry: SearchEntry
  folders: Folder[]
  isMoving: boolean
  onRename: (id: string, label: string) => void
  onDelete: (id: string) => void
  onToggleMove: (id: string) => void
  onCloseMove: () => void
  onAssign: (entryId: string, folderId: string | null) => void
  onCreateAndAssign: (entryId: string, name: string) => void
}) {
  const router = useRouter()
  const [renaming, setRenaming] = useState(false)
  const [draft, setDraft] = useState(entry.label ?? entry.query)

  const displayLabel = entry.label ?? entry.query

  const commitRename = () => {
    setRenaming(false)
    const trimmed = draft.trim()
    if (trimmed && trimmed !== displayLabel) onRename(entry.id, trimmed)
  }

  const navigate = () =>
    entry.job_id
      ? router.push(`/properties?job_id=${encodeURIComponent(entry.job_id)}`)
      : router.push(`/chat?q=${encodeURIComponent(entry.query)}`)

  if (renaming) {
    return (
      <li>
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitRename()
            if (e.key === 'Escape') {
              setDraft(displayLabel)
              setRenaming(false)
            }
          }}
          className="w-full rounded-lg border border-sidebar-border bg-sidebar-accent/50 px-2 py-1 text-xs text-sidebar-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20"
        />
      </li>
    )
  }

  return (
    <li className="group/entry relative flex items-center gap-1">
      <button
        onClick={navigate}
        className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden rounded-lg px-2 py-1.5 text-left transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        title={entry.query}
      >
        <Search className="size-3 shrink-0 text-sidebar-foreground/30 group-hover/entry:text-sidebar-foreground/60" />
        <span className="truncate text-xs text-sidebar-foreground/70 group-hover/entry:text-sidebar-accent-foreground">
          {displayLabel}
        </span>
      </button>
      <div className="hidden shrink-0 items-center gap-0.5 group-hover/entry:flex">
        <button
          onClick={() => setRenaming(true)}
          title="Renombrar"
          className="rounded p-1 text-sidebar-foreground/40 hover:bg-sidebar-accent hover:text-sidebar-foreground"
        >
          <Pencil className="size-3" />
        </button>
        <button
          onClick={() => onToggleMove(entry.id)}
          title="Mover a carpeta"
          className="rounded p-1 text-sidebar-foreground/40 hover:bg-sidebar-accent hover:text-sidebar-foreground"
        >
          <FolderIcon className="size-3" />
        </button>
        <button
          onClick={() => onDelete(entry.id)}
          title="Eliminar"
          className="rounded p-1 text-sidebar-foreground/40 hover:bg-sidebar-accent hover:text-red-500"
        >
          <Trash2 className="size-3" />
        </button>
      </div>
      {isMoving && (
        <MovePopover
          entryId={entry.id}
          folders={folders}
          onClose={onCloseMove}
          onAssign={onAssign}
          onCreateAndAssign={onCreateAndAssign}
        />
      )}
    </li>
  )
}

export default function SearchHistoryList() {
  const { searches, folders, updateEntry, deleteEntry, createFolder } = useSearchHistory()
  const [movingId, setMovingId] = useState<string | null>(null)

  const grouped = folders
    .map((f) => ({ folder: f, entries: searches.filter((s) => s.folder_id === f.id) }))
    .filter((g) => g.entries.length > 0)
  const ungrouped = searches.filter((s) => !s.folder_id)

  const handleAssign = (entryId: string, folderId: string | null) => {
    updateEntry(entryId, { folder_id: folderId })
  }

  const handleCreateAndAssign = async (entryId: string, name: string) => {
    const folder = await createFolder(name)
    if (folder) updateEntry(entryId, { folder_id: folder.id })
  }

  const renderEntry = (entry: SearchEntry) => (
    <EntryRow
      key={entry.id}
      entry={entry}
      folders={folders}
      isMoving={movingId === entry.id}
      onRename={(id, label) => updateEntry(id, { label })}
      onDelete={deleteEntry}
      onToggleMove={(id) => setMovingId((cur) => (cur === id ? null : id))}
      onCloseMove={() => setMovingId(null)}
      onAssign={handleAssign}
      onCreateAndAssign={handleCreateAndAssign}
    />
  )

  return (
    <div className="flex-1 overflow-y-auto px-3 pt-4">
      <div className="mb-2 flex items-center gap-2">
        <Clock className="size-3.5 text-sidebar-foreground/50" />
        <p className="text-xs font-medium text-sidebar-foreground/60 uppercase tracking-wide">Historial</p>
      </div>

      {searches.length === 0 ? (
        <p className="px-1 text-xs text-sidebar-foreground/40">Sin búsquedas aún</p>
      ) : (
        <div className="space-y-3">
          {grouped.map((g) => (
            <div key={g.folder.id}>
              <p className="mb-1 flex items-center gap-1 px-1 text-[10px] font-medium uppercase tracking-wide text-sidebar-foreground/40">
                <FolderIcon className="size-3" />
                {g.folder.name}
              </p>
              <ul className="space-y-0.5">{g.entries.map(renderEntry)}</ul>
            </div>
          ))}

          {ungrouped.length > 0 && <ul className="space-y-0.5">{ungrouped.map(renderEntry)}</ul>}
        </div>
      )}
    </div>
  )
}
