# Revisión de ubicaciones de una búsqueda

El mapa usa `properties.lat/lng`. El geocodificador anterior quitaba las entrecalles
y la altura y aceptaba el primer resultado, incluso fuera de la localidad. RE/MAX
tampoco conservaba `geoLabel` cuando se buscaba como portal. Una nueva búsqueda
podía reutilizar esas filas y conservar las coordenadas incorrectas.

La corrección conserva localidad y altura, guarda los puntos publicados por
RE/MAX, los mapas de fichas Tokko y los dos adaptadores Dacal, y sólo acepta un
resultado de Nominatim si coinciden calle, altura y localidad. Si sólo se conoce
una esquina o entrecalles y no hay coordenadas publicadas, queda sin ubicar.
Las ubicaciones publicadas pueden ser aproximadas: Urquiza, por ejemplo, muestra
un círculo de 400 m. No se deben presentar como una entrada exacta al inmueble.

## Revisar datos anteriores después de desplegar el backend

No requiere migración. Cambiar el código no modifica automáticamente las filas
que ya tienen `geocoded_at`. La ruta existente de backfill permite ahora revisar
una búsqueda explícita, incluyendo propiedades reutilizadas de búsquedas anteriores:

```http
POST /api/v1/properties/geocode/backfill?job_id=<UUID>&recheck=true&dry_run=true&limit=1000
GET /api/v1/properties/geocode/status
```

Usar la autenticación habitual. Esperar `running=false`; revisar `aborted` y
`changes` (id, coordenadas anteriores y propuestas). `dry_run=true` no escribe
propiedades. `recheck` exige un `job_id`; nunca recalcula toda la base por accidente.
El límite cuenta propiedades únicas; esta operación está pensada para búsquedas
de hasta 1000 propiedades. El estado se guarda en memoria por proceso, como antes.
No iniciar una segunda corrida mientras `running=true`.

Para aplicar las correcciones de esa búsqueda:

```http
POST /api/v1/properties/geocode/backfill?job_id=<UUID>&recheck=true&limit=1000
```

La revisión recupera ubicaciones desde los avisos RE/MAX/Tokko que siguen públicos;
para el resto usa la dirección completa. Si no puede validar una ubicación, borra
el punto incorrecto y deja la propiedad sin ubicar. Los errores temporales no
sobrescriben coordenadas y se informan en `aborted`. La operación modifica la fila
compartida de la propiedad, por lo que todas las búsquedas que la usan reciben
la corrección. Recargar los resultados al terminar.

`force=true` conserva su significado anterior: reintenta propiedades sin latitud.
No sirve por sí solo para reparar puntos incorrectos ya guardados.
