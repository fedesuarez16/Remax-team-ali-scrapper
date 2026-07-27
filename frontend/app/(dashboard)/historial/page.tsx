'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Folder as FolderIcon, FolderPlus, Pencil, Search, Trash2 } from 'lucide-react'
import { useSearchHistory, type SearchEntry } from '@/hooks/useSearchHistory'

export default function HistorialPage() {
  const { searches, folders, updateEntry, deleteEntry, createFolder, loading } = useSearchHistory()
  const router = useRouter()
  const [openFolderId, setOpenFolderId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [newFolderName, setNewFolderName] = useState('')
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')

  const ungrouped = searches.filter((s) => !s.folder_id)
  const entriesFor = (folderId: string) => searches.filter((s) => s.folder_id === folderId)

  const handleCreateFolder = async () => {
    const name = newFolderName.trim()
    if (!name) return
    const folder = await createFolder(name)
    setNewFolderName('')
    setCreating(false)
    if (folder) setOpenFolderId(folder.id)
  }

  const navigate = (entry: SearchEntry) =>
    entry.job_id
      ? router.push(`/properties?job_id=${encodeURIComponent(entry.job_id)}`)
      : router.push(`/chat?q=${encodeURIComponent(entry.query)}`)

  const commitRename = (entry: SearchEntry) => {
    setRenamingId(null)
    const trimmed = renameDraft.trim()
    const current = entry.label ?? entry.query
    if (trimmed && trimmed !== current) updateEntry(entry.id, { label: trimmed })
  }

  const renderEntry = (entry: SearchEntry) => {
    const displayLabel = entry.label ?? entry.query
    const isRenaming = renamingId === entry.id

    return (
      <li key={entry.id} className="group flex items-center gap-1 rounded-xl border border-border bg-background px-3 py-2">
        {isRenaming ? (
          <input
            autoFocus
            value={renameDraft}
            onChange={(e) => setRenameDraft(e.target.value)}
            onBlur={() => commitRename(entry)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename(entry)
              if (e.key === 'Escape') setRenamingId(null)
            }}
            className="flex-1 rounded-lg border border-border bg-background px-2 py-1 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20"
          />
        ) : (
          <button onClick={() => navigate(entry)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
            <Search className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate text-sm text-foreground">{displayLabel}</span>
          </button>
        )}
        <div className="hidden shrink-0 items-center gap-1 group-hover:flex">
          <button
            onClick={() => {
              setRenamingId(entry.id)
              setRenameDraft(displayLabel)
            }}
            title="Renombrar"
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <Pencil className="size-3.5" />
          </button>
          <button
            onClick={() => deleteEntry(entry.id)}
            title="Eliminar"
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-destructive"
          >
            <Trash2 className="size-3.5" />
          </button>
        </div>
      </li>
    )
  }

  const isEmpty = !loading && folders.length === 0 && ungrouped.length === 0

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-background text-foreground">
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto flex w-full max-w-3xl items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Carpetas de búsqueda</h1>
            <p className="mt-1 text-sm text-muted-foreground">Organizá y volvé a abrir tus búsquedas guardadas.</p>
          </div>
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-2 rounded-xl bg-foreground px-3 py-2 text-sm font-medium text-background transition hover:bg-foreground/85"
          >
            <FolderPlus className="size-4" />
            Nueva carpeta
          </button>
        </div>
      </header>

      <div className="mx-auto w-full max-w-3xl flex-1 space-y-3 p-6">
        {creating && (
          <div className="flex items-center gap-2 rounded-2xl border border-border bg-card p-3">
            <input
              autoFocus
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCreateFolder()
                if (e.key === 'Escape') {
                  setCreating(false)
                  setNewFolderName('')
                }
              }}
              placeholder="Nombre de la carpeta"
              className="flex-1 rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-foreground/20"
            />
            <button
              onClick={handleCreateFolder}
              disabled={!newFolderName.trim()}
              className="rounded-xl bg-foreground px-3 py-2 text-sm font-medium text-background disabled:opacity-40"
            >
              Crear
            </button>
          </div>
        )}

        {loading ? (
          <p className="text-sm text-muted-foreground">Cargando...</p>
        ) : isEmpty ? (
          <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-10 text-center">
            <FolderIcon className="size-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">Todavía no hay búsquedas guardadas</p>
          </div>
        ) : (
          <>
            {folders.map((f) => {
              const entries = entriesFor(f.id)
              const isOpen = openFolderId === f.id
              return (
                <div key={f.id} className="rounded-2xl border border-border bg-card">
                  <button
                    onClick={() => setOpenFolderId(isOpen ? null : f.id)}
                    className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left"
                  >
                    <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                      <FolderIcon className="size-4 text-muted-foreground" />
                      {f.name}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {entries.length} búsqueda{entries.length === 1 ? '' : 's'}
                    </span>
                  </button>
                  {isOpen && (
                    <ul className="space-y-1.5 border-t border-border p-3">
                      {entries.length === 0 ? (
                        <p className="px-1 text-xs text-muted-foreground">Sin búsquedas en esta carpeta</p>
                      ) : (
                        entries.map(renderEntry)
                      )}
                    </ul>
                  )}
                </div>
              )
            })}

            {ungrouped.length > 0 && (
              <div className="rounded-2xl border border-border bg-card">
                <div className="flex items-center justify-between px-4 py-3">
                  <span className="text-sm font-medium text-foreground">Sin carpeta</span>
                  <span className="text-xs text-muted-foreground">
                    {ungrouped.length} búsqueda{ungrouped.length === 1 ? '' : 's'}
                  </span>
                </div>
                <ul className="space-y-1.5 border-t border-border p-3">{ungrouped.map(renderEntry)}</ul>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
