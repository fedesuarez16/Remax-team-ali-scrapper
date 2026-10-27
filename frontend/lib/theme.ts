'use client'
import { useSyncExternalStore } from 'react'
import { MEDIA, STORAGE_KEY } from '@/lib/theme-script'

/**
 * Tema claro / oscuro / sistema, sin dependencias.
 *
 * La preferencia vive en localStorage y la clase `.dark` en <html>. El primer
 * pintado lo resuelve `THEME_SCRIPT` (inline en el <head>), así que no hay
 * flash de tema claro antes de hidratar. Este módulo solo se encarga de los
 * cambios posteriores y de avisarle a quien escuche (el mapa cambia de tiles).
 */
export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const listeners = new Set<() => void>()

function readPreference(): ThemePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

function resolve(preference: ThemePreference): ResolvedTheme {
  if (preference !== 'system') return preference
  return window.matchMedia(MEDIA).matches ? 'dark' : 'light'
}

function apply(preference: ThemePreference) {
  const resolved = resolve(preference)
  const root = document.documentElement
  root.classList.toggle('dark', resolved === 'dark')
  root.style.colorScheme = resolved
  listeners.forEach((listener) => listener())
}

export function setThemePreference(preference: ThemePreference) {
  try {
    if (preference === 'system') localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, preference)
  } catch {
    // Sin storage el cambio igual se aplica, solo no sobrevive al reload.
  }
  apply(preference)
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  const media = window.matchMedia(MEDIA)
  const onSystemChange = () => {
    if (readPreference() === 'system') apply('system')
  }
  // Otra pestaña cambió el tema: seguirla.
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) apply(readPreference())
  }
  media.addEventListener('change', onSystemChange)
  window.addEventListener('storage', onStorage)
  return () => {
    listeners.delete(listener)
    media.removeEventListener('change', onSystemChange)
    window.removeEventListener('storage', onStorage)
  }
}

export function useThemePreference(): ThemePreference {
  return useSyncExternalStore(subscribe, readPreference, () => 'system')
}

// Lee la clase real de <html>, que es la fuente de verdad del pintado.
export function useResolvedTheme(): ResolvedTheme {
  return useSyncExternalStore(
    subscribe,
    () => (document.documentElement.classList.contains('dark') ? 'dark' : 'light'),
    () => 'light',
  )
}
