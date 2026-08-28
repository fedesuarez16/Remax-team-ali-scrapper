# ZonaProp: scraping directo, y cómo volver a Apify

## Estado actual

ZonaProp se lee **directo**: HTML del listado + el JSON `window.__PRELOADED_STATE__`,
saliendo por `SCRAPER_PROXY_URL` (proxy residencial de Apify, el mismo que ya usaba
MercadoLibre).

El actor `crawlerbros/zonaprop-scraper` **sigue en el código, intacto**.

## Marcha atrás

```bash
# backend/.env
ZONAPROP_USE_APIFY=true
```

Reiniciar el backend. Eso devuelve ZonaProp al actor, con todo lo que tenía:
paginación por URL, funnel `zonaprop funnel`, retry de página degradada, retry de
slug simple. No hay migración ni cambio de datos: es un `if` en
`ApifyService._scrape_source_once`.

Para volver al directo: sacar la variable (o `false`) y reiniciar.

## Por qué se cambió

El actor bajaba el listado en ~7 s y después abría **una ficha por resultado**
(~85 s). Ahí crasheaba, de forma reproducible:

```
TypeError: Cannot read properties of undefined (reading 'url')
    at FFBrowserContext... coreBundle.js:49624        (Node.js v24.15.0)
Browser.new_page: Connection closed while reading from the driver   ← ×19
Pushed 20 listings (total: 20)
Reached last page (1).
```

Con el browser muerto tampoco podía pedir la página 2, así que **truncaba en
silencio toda búsqueda de más de una página**. Su input schema (`searchUrl`,
`propertyType`, `operationType`, `location`, `maxResults`, `proxyConfiguration`)
no tiene ninguna opción para saltear ese enriquecimiento.

## Qué se gana

| | actor | directo |
|---|---|---|
| tiempo por página | ~95 s | ~7 s |
| costo | un run pago por página | ancho de banda del proxy (~1.3 MB/página) |
| paginación | inferida del tamaño de página | `paging.totalPages`, declarado por el portal |
| pertenencia a la zona | matcheo de texto contra el nombre del barrio | **ID de zona** contra `appliedFilters` |
| redirect de slug desconocido | inferido (¿se rechazó casi todo?) | **verificado** (`appliedFilters` dice qué zona aplicó) |

El cambio de matcheo por texto a ID de zona no es cosmético: los sub-barrios
—*Grand Bell*, *Lomas de City Bell*, *El Quimilar*— están **dentro** de City Bell
y el portal los devuelve a propósito. El guard viejo los tiraba por no deletrear
"City Bell". Cada aviso trae su cadena `location.parent` completa, así que la
pertenencia se comprueba contra la zona que el portal declaró haber aplicado.

## La trampa de `appliedFilters`: usar la zona PEDIDA, no la unión

Una URL de ZonaProp puede aplicar **más de una zona**. Medido en vivo:

```
casas-venta-city-bell-la-plata-450000-500000-dolar.html
  appliedFilters: [{"label": "La Plata",  "type": "city", "min": "1001361"},
                   {"label": "City Bell", "type": "zone", "min": "1001379"}]
  total: 73  (incluye Gonnet, Villa Elisa, Miralagos)
```

Tomar la **unión** de esas zonas acepta el partido entero, porque cada aviso de
Gonnet lleva la ciudad La Plata en su cadena de padres. `_zonaprop_requested_zone_ids()`
se queda **sólo con la opción cuyo label matchea lo que pidió el usuario**.

Resultado sobre esa misma URL: 73 avisos crudos → **20 devueltos**, que es
exactamente lo que da el slug angosto `casas-venta-city-bell-...`. Dos caminos
independientes, el mismo número.

El match es por **label**, no por `type`: si pedís "La Plata, La Plata", el filtro
de tipo `city` ES tu pedido y se conserva.

## Sesión de proxy: pegajosa, no rotativa

El 403 de ZonaProp es **por IP de salida**. `_proxy_with_session()` fija la sesión
de Apify en el username (`groups-RESIDENTIAL,session-<id>`), y una sesión distinta
es una IP distinta.

La regla es **una sesión por búsqueda, rotada sólo cuando deja de funcionar**.
Rotar en cada request era autoinfligido: medido en vivo, casi *todo* primer intento
con IP nueva se comía un 403 y el reintento pasaba — una IP fresca recibe el
challenge, una ya usada no. El actor hacía lo mismo (`Browser launching with proxy
session: zp_71397`).

Efecto sobre la misma búsqueda, sin tocar nada más:

```
rotando siempre   →  30 propiedades  (pagina 3 murió con doble 403)
sesión pegajosa   → 200 propiedades  (un 403 al arranque, después 8 páginas limpias)
```

Se reintenta una vez ante 403/429 (IP quemada) y ante 5xx —el proxy de Apify
contesta `590 UPSTREAM504`— y ante timeouts. Un **404 no se reintenta**: es una
respuesta, no un hipo.

## Riesgos del camino directo

- **El WAF puede cambiar.** Sin `SCRAPER_PROXY_URL` (o con un proxy de datacenter)
  ZonaProp no devuelve `__PRELOADED_STATE__`. El scraper loguea
  `no trajo __PRELOADED_STATE__ ... puede ser el muro anti-bot` — no se queda callado.
- **El ancho de banda residencial se factura por GB.** ~1.3 MB por página.
- **La forma del JSON puede cambiar.** Los tests corren contra un fixture de datos
  reales (`backend/tests/fixtures/zonaprop_city_bell.json`); si el portal cambia el
  esquema, fallan ahí y no en producción.
- **El actor manejaba la rotación de sesiones del proxy.** Ahora es nuestra.
- **El 403 aparece de a ratos.** En una prueba en vivo, `casas-venta-city-bell-...`
  dio 403 mientras `casas-venta-city-bell-la-plata-...` funcionaba en la misma
  corrida. El scraper lo loguea y sigue; no lo confunde con "no hay avisos".

## Detalles que cuestan tiempo si se olvidan

- `__PRELOADED_STATE__` **no se puede extraer con regex**: después del objeto viene
  más script y el JSON se corta (`Extra data: line 1 column 354022`). Hay que
  escanear llaves balanceadas respetando strings y escapes — `_zonaprop_state()`.
- La URL la arma **una sola función**, `_zonaprop_search_url()`, usada por los dos
  caminos. Si se duplica, los slugs se van a desincronizar.
- `-pagina-N` va **al final**, después del segmento de precio:
  `casas-venta-la-plata-la-plata-300000-350000-dolar-pagina-2.html`.
- `dolar` va en **singular**. El rango no lleva palabra clave: `{min}-{max}-dolar`.
  El techo solo sí: `menos-{max}-dolar`. El piso solo (`mas-N-dolar`) **no está
  verificado** y por eso no se emite.
- Una zona compuesta desconocida (`city-bell-la-plata`) **no da 404**: el portal
  contesta con el partido. Por eso existe el reintento con la localidad pelada.
