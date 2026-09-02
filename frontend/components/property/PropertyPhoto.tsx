'use client'
import { ImageOff } from 'lucide-react'
import { useState } from 'react'

/** La foto de una propiedad, o el hueco que explica por qué no está.
 *
 * Vive en un componente propio porque los dos casos de ausencia se resuelven
 * igual y aparecen en tres lugares (la tarjeta de resultados y los dos popups
 * del mapa). Duplicar el `onError` en cada uno garantizaba que alguno quedara
 * sin él — de hecho los dos del mapa no lo tenían.
 *
 * Los dos casos:
 *
 * - **No hay foto.** Con un ícono solo, la tarjeta se leía como si todavía
 *   estuviera cargando y el operador esperaba algo que no iba a llegar. El
 *   texto dice que la ausencia es definitiva y de quién es: la ficha del sitio
 *   de la inmobiliaria no publica la imagen, no falló la búsqueda.
 * - **La foto no carga.** Las URLs son de sitios de terceros y se vencen. Sin
 *   `onError` quedaba el ícono roto del navegador, que se lee peor que un
 *   cartel honesto.
 */
export function PropertyPhoto({
  src, alt, imgClassName = '', boxClassName = '',
}: {
  src?: string | null
  alt: string
  /** Clases de la `<img>` cuando hay foto. */
  imgClassName?: string
  /** Clases del hueco cuando no la hay — mismo espacio que ocuparía la foto. */
  boxClassName?: string
}) {
  const [roto, setRoto] = useState(false)

  if (!src || roto) {
    return (
      <div className={`flex flex-col items-center justify-center gap-1.5 bg-muted px-3 text-center ${boxClassName}`}>
        <ImageOff className="size-6 shrink-0 text-muted-foreground/40" />
        <span className="text-[11px] leading-tight text-muted-foreground">
          Sin foto disponible en la web
        </span>
      </div>
    )
  }

  return <img src={src} alt={alt} onError={() => setRoto(true)} className={imgClassName} />
}
