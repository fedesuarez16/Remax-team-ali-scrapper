'use client'
import { useCallback, useEffect, useState } from 'react'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const FOLDERS_URL = `${API}/api/v1/ficha-folders`

export type FichaFolder = {
  id: string
  name: string
  created_at: string
}

async function fetchFolders(): Promise<FichaFolder[] | null> {
  try {
    const res = await fetch(FOLDERS_URL)
    if (!res.ok) return null
    const data = await res.json()
    return (data.folders ?? []) as FichaFolder[]
  } catch {
    return null
  }
}

/**
 * Crear una carpeta. Devuelve la fila creada, o null si no se pudo: el
 * llamador tiene que enterarse, porque después va a querer mover fichas ahí.
 */
export async function createFichaFolder(name: string): Promise<FichaFolder | null> {
  const trimmed = name.trim()
  if (!trimmed) return null
  try {
    const res = await fetch(FOLDERS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: trimmed }),
    })
    if (!res.ok) return null
    const data = await res.json()
    return (data.folder ?? null) as FichaFolder | null
  } catch {
    return null
  }
}

/** Borrar una carpeta. Las fichas NO se pierden: el backend las deja "Sin carpeta". */
export async function deleteFichaFolder(id: string): Promise<boolean> {
  try {
    const res = await fetch(`${FOLDERS_URL}/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!res.ok) return false
    const data = await res.json()
    return data.deleted === true
  } catch {
    return false
  }
}

/**
 * Mover fichas a una carpeta (o sacarlas con `folderId = null`). Devuelve los
 * ids efectivamente movidos, así la página sólo actualiza lo que el backend
 * confirmó — mostrar una ficha en una carpeta donde no está es peor que no
 * moverla.
 */
export async function assignFichasToFolder(ids: string[], folderId: string | null): Promise<string[]> {
  const clean = [...new Set(ids.filter(Boolean))]
  if (clean.length === 0) return []
  try {
    const res = await fetch(`${FOLDERS_URL}/assign`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: clean, folder_id: folderId }),
    })
    if (!res.ok) return []
    const data = await res.json()
    return ((data.properties ?? []) as { id?: string }[]).map((p) => p.id!).filter(Boolean)
  } catch {
    return []
  }
}

/**
 * Carpetas de la pestaña Ficha Propio. Estado local a la página: a diferencia
 * del historial (que vive en el sidebar y se ve desde todas las rutas), acá
 * hay un solo consumidor, así que no hace falta el bus de suscriptores.
 */
export function useFichaFolders() {
  const [folders, setFolders] = useState<FichaFolder[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    fetchFolders().then((f) => {
      if (cancelled) return
      if (f) setFolders(f)
      setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const create = useCallback(async (name: string) => {
    const folder = await createFichaFolder(name)
    if (folder) setFolders((prev) => [folder, ...prev])
    return folder
  }, [])

  const remove = useCallback(async (id: string) => {
    const ok = await deleteFichaFolder(id)
    if (ok) setFolders((prev) => prev.filter((f) => f.id !== id))
    return ok
  }, [])

  return { folders, loading, create, remove, assign: assignFichasToFolder }
}
