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
