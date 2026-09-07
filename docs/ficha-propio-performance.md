# Generación de Ficha Propio desde ZonaProp

`POST /api/v1/properties/import` verifica la galería del aviso de ZonaProp en
cada generación explícita. Esto incluye las URLs que ya existen en `properties`
por una búsqueda anterior. Se conserva el mismo ID y los datos editados; se
actualiza `imagenes` con la galería obtenida del aviso.

El parser lee todas las fotos de `pictures` y elige la mayor resolución disponible.
ZonaProp no usa el límite genérico de 40 fotos. El mismo HTML sirve para extraer
el texto cuando la propiedad es nueva: no se vuelve a descargar para las imágenes.
Si no se puede leer la galería nativa o guardar su actualización, la importación
devuelve un error por URL. No devuelve una ficha parcial con estado `ok`.

La respuesta incluye `gallery_complete: true` cuando la importación verificó las
fotos. El frontend llama entonces al enriquecimiento de texto con
`refresh_gallery=false`, evitando otra recuperación incluso para una fila existente.

## Tiempos y recuperación

La lectura de ZonaProp tiene un presupuesto total de 15 segundos. Usa el proxy
configurado y, si la conexión con ese proxy falla, permite un intento HTTP directo
dentro del mismo presupuesto. Un bloqueo puede pasar a navegador con proxy. Este
flujo interactivo no inicia actores de Apify, cuyo polling puede alcanzar 300 segundos.
Las llamadas de IA tienen timeout de 15 segundos y no suman reintentos del SDK.

La recuperación opcional de `/enrich` conserva su límite de tres segundos para
ZonaProp. Detecta tanto pocas imágenes como fotos de baja resolución del CDN,
pero la generación de `/ficha-propio` ya no depende de esa recuperación opcional.
Abrir una ficha pública con una galería sana reutiliza las fotos guardadas. Volver
a generar explícitamente desde la URL consulta el aviso para incluir fotos agregadas.

## Casos verificados el 7 de septiembre de 2026

- `58532575`: lectura del texto y 20 fotos en 3,06 segundos, sin IA ni base.
- `58514344`: la fila tenía ocho miniaturas de 360 × 266; el HTML contiene 19 fotos.
  La primera versión grande descargada mide 1200 × 900.
- `58742865`: la fila del buscador tenía ocho fotos de 720 × 532. El HTML contiene
  23. La detección de miniaturas era correcta, pero una recuperación fallida seguía
  permitiendo presentar la galería vieja como terminada. Con el import corregido,
  el primer intento falló y devolvió un error; el reintento por la ruta FastAPI
  local, con red y Supabase reales, devolvió y guardó las 23 fotos en 1,75 segundos.
  Esa medición reutilizó el texto existente y no hizo llamadas a IA. Se verificó
  también el enriquecimiento posterior y la persistencia; sólo cambió `imagenes`.

## Validación

`backend/tests/api/test_zonaprop_import_gallery.py` prueba el endpoint completo
con filas nuevas y existentes, galerías de 23 y 47 fotos, recuperación que excede
el presupuesto opcional y fallos que deben impedir un falso éxito. Los tests de
latencia mantienen los límites de espera y la ausencia de actores pagos.

No requiere migraciones. Desplegar backend y frontend para aplicar el contrato
`gallery_complete` al flujo de generación.

## Un 403 del proxy no siempre es un bloqueo del portal

Si httpx lanza `ProxyError: 403 Forbidden`, el túnel HTTPS fue rechazado antes
de recibir una respuesta del aviso. El endpoint de estado de Apify,
`http://proxy.apify.com/?format=json`, consultado por el mismo proxy, permite
distinguir un límite de cuenta de un bloqueo del sitio.

Se verificó este caso con el aviso `59871047`: la página pública contiene 50
fotos, pero el proxy respondía `connected: false` y
`connectionError: "Monthly usage hard limit exceeded"`. Cambiar el parser,
rotar sesiones o repetir el navegador no resuelve un límite mensual agotado.

`proxy_access.check_apify_proxy_limit` consulta ese estado sólo después de un
`ProxyError`, con un máximo de dos segundos y sin exponer credenciales. Cuando
se confirma ese motivo, la importación informa el límite de Apify y corta los
intentos. La recuperación de galerías tampoco inicia navegador o actor contra
esa cuenta bloqueada. Una consulta de estado inconclusa mantiene el tratamiento
habitual del error, sin inventar una causa.

Restablecer el acceso requiere resolver el límite de consumo de la cuenta de
Apify. Los cambios de gasto requieren autorización del titular; no se modifican
automáticamente desde la generación de fichas.
