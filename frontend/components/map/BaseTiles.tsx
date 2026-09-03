'use client'
import { TileLayer } from 'react-leaflet'

/**
 * El fondo del mapa, en UN solo lugar.
 *
 * Existe por lo que pasó con CARTO: la URL estaba duplicada en `PropertyMap`
 * y en `SeleccionMap`, y cuando `basemaps.cartocdn.com` empezó a exigir API
 * key los dos mapas se llenaron de un tile gris que dice "API KEY REQUIRED".
 * Ojo con el modo de falla: CARTO NO responde 403 — responde 200 con ese
 * cartel dibujado adentro del PNG, así que ningún `onerror` se entera y la
 * app no tiene forma de detectarlo sola. Se ve, y nada más.
 *
 * Esri sirve el Light Gray Canvas sin key. La contra es que separa el dibujo
 * de las etiquetas en dos servicios, y en un mapa inmobiliario los nombres de
 * calle no son decoración: sin `Reference` encima, La Plata es una grilla de
 * líneas grises sin un solo nombre. Por eso van los dos, en este orden — la
 * capa de etiquetas es transparente y tiene que quedar ARRIBA del fondo.
 *
 * Se mantiene en escala de grises a propósito: los marcadores codifican la
 * operación por color (verde = venta, amarillo = alquiler) y sobre un fondo
 * de calles coloreadas esa señal se pierde.
 *
 * Si Esri también se cierra algún día, este archivo es el único que se toca.
 */
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas'
// Esri ordena la ruta {z}/{y}/{x} — al revés que el {z}/{x}/{y} de CARTO/OSM.
// Invertirlo devuelve tiles de "Map data not yet available", no un error.
const BASE = `${ESRI}/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`
const LABELS = `${ESRI}/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`

const ATTRIBUTION = '&copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors'

// Esri tiene tiles hasta z16; de z17 para arriba devuelve un placeholder de
// "Map data not yet available" (2.5 KB, medido) — y otra vez con status 200,
// así que se vería como un mapa en blanco sin un solo error en consola.
//
// `maxNativeZoom` es justo la herramienta para eso: Leaflet deja de PEDIR
// tiles arriba de 16 y reescala los de 16, así que acercarse se ve borroso
// pero se ve. `maxZoom` queda alto a propósito: si lo clavábamos en 16, el
// usuario no podría acercarse a una propiedad — y `PropertyMap` hace
// `fitBounds` sobre el polígono SIN tope, así que un barrio chico se pasa de
// 16 solo.
const MAX_NATIVE_ZOOM = 16
const MAX_ZOOM = 19

export function BaseTiles() {
  return (
    <>
      <TileLayer
        url={BASE}
        attribution={ATTRIBUTION}
        maxZoom={MAX_ZOOM}
        maxNativeZoom={MAX_NATIVE_ZOOM}
      />
      <TileLayer url={LABELS} maxZoom={MAX_ZOOM} maxNativeZoom={MAX_NATIVE_ZOOM} />
    </>
  )
}
