'use client'
import { useCallback, useEffect, useState } from 'react'
import { FICHA_TEXTOS_DEFAULT, getFichaTextos, updateFichaTextos, type FichaTextos } from '@/lib/ficha'

/**
 * Textos compartidos por TODAS las fichas (presentación, descargo legal, pie).
 *
 * A diferencia de los datos de una propiedad, esto es una configuración del
 * equipo: guardar acá reescribe el pie de todas las fichas, también las ya
 * compartidas con clientes. La UI que use este hook tiene que decirlo.
 *
 * `textos` arranca en los defaults en vez de `null`, así quien lo consume nunca
 * renderiza un pie vacío mientras carga.
 */
export function useFichaTextos() {
  const [textos, setTextos] = useState<FichaTextos>(FICHA_TEXTOS_DEFAULT)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const ctrl = new AbortController()
    void (async () => {
      const t = await getFichaTextos(ctrl.signal)
      if (!ctrl.signal.aborted) {
        setTextos(t)
        setLoading(false)
      }
    })()
    return () => ctrl.abort()
  }, [])

  /** Devuelve `true` si se guardó. El llamador muestra el error — un fallo acá
   *  no puede pasar en silencio, porque el usuario cree que ya quedó. */
  const guardar = useCallback(async (patch: Partial<FichaTextos>): Promise<boolean> => {
    const updated = await updateFichaTextos(patch)
    if (!updated) return false
    setTextos(updated)
    return true
  }, [])

  return { textos, loading, guardar }
}
