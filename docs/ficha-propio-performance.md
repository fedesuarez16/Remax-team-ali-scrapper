# Generación de Ficha Propio desde ZonaProp

El flujo de `/ficha-propio` importa una URL y luego llama a `/properties/{id}/enrich`
para preparar el texto. La importación descargaba el aviso sin el proxy configurado,
volvía a descargarlo para leer las fotos y el enriquecimiento podía repetir la
recuperación. Los bloqueos podían alcanzar el actor de Apify, cuyo polling admite
300 segundos.

La corrección utiliza el proxy también en la primera descarga. El parser de
ZonaProp extrae la galería del mismo HTML que se usa para leer el texto. Una
importación nueva llama al enriquecimiento con `refresh_gallery=false`, porque
acaba de resolver las fotos; las propiedades ya guardadas conservan la recuperación.

Para ZonaProp, la lectura inicial tiene un presupuesto de 15 segundos, incluyendo
un posible intento con navegador y proxy. Ese flujo interactivo no inicia un
actor de Apify. Recuperar fotos de una ficha existente permite solo HTTP y hasta
tres segundos, conservando las imágenes disponibles si no responde. Las llamadas
a Anthropic de importación y enriquecimiento tienen timeout de 15 segundos y
no agregan reintentos automáticos del SDK.

La medición del 7 de septiembre de 2026 del aviso `58532575` obtuvo texto y 20
fotos en 3,06 segundos. Es una medición de la lectura del portal, no del tiempo
total con IA y base de datos. No se modificó la base durante esa prueba.

La regresión está cubierta en `backend/tests/services/test_ficha_propio_zonaprop_latency.py`:
una sola descarga, galería completa, proxy en HTTP y navegador, cancelación por
deadline, ausencia de actores y omisión de la segunda recuperación de fotos.
Esta corrección no necesita migraciones.

## Fichas guardadas con miniaturas de ZonaProp

El aviso `58514344` permitió verificar otro caso: la fila existente, creada el
8 de julio de 2026, tenía ocho fotos del feed a 360 × 266 y ya estaba marcada
como enriquecida. Pegar la URL reutilizaba esa fila. La recuperación sólo miraba
si había seis fotos o menos, así que no volvía a consultar la galería.

El HTML del aviso contiene las 19 fotos en `pictures`. El parser existente
obtiene sus URLs `resizeUrl1200x1200`; la primera imagen descargada mide realmente
1200 × 900, conservando su proporción. No es necesario ampliar miniaturas ni
agregar otra descarga a las importaciones nuevas.

La recuperación ahora detecta también los tamaños inferiores a 1200 píxeles en
las rutas de fotos del CDN de ZonaProp, aunque la fila tenga ocho o más imágenes.
Al generar nuevamente una ficha existente o abrir su enlace público, recupera y
guarda la galería de mayor resolución. Las visitas siguientes reutilizan esas
fotos. Se mantienen los límites de tiempo de los pedidos interactivos.

`backend/tests/services/test_zonaprop_cached_gallery.py` cubre la reutilización
de una fila con ocho miniaturas, su recuperación a 19 fotos grandes y la ausencia
de descargas adicionales al volver a generar o abrir la ficha. No requiere migración.
