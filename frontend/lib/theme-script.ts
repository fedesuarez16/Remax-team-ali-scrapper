/**
 * Separado de `lib/theme.ts` a propósito: aquel es `'use client'`, y un string
 * exportado desde un módulo cliente le llega al layout (Server Component) como
 * client reference, no como texto.
 */
export const STORAGE_KEY = 'theme'
export const MEDIA = '(prefers-color-scheme: dark)'

// Corre antes de que React exista: tiene que ser JS plano y tolerar que
// localStorage tire (modo privado, cookies bloqueadas).
export const THEME_SCRIPT = `(function(){try{var p=localStorage.getItem('${STORAGE_KEY}');var d=p==='dark'||((p!=='light')&&window.matchMedia('${MEDIA}').matches);var r=document.documentElement;r.classList.toggle('dark',d);r.style.colorScheme=d?'dark':'light'}catch(e){}})()`
