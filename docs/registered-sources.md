# Fuentes con búsqueda integrada

Primer grupo revisado el 7 de septiembre de 2026:

| Fuente | `scrape_source` | Integración |
| --- | --- | --- |
| [InmoBúsqueda](https://www.inmobusqueda.com.ar/) | `inmobusqueda` | Scraper existente del portal; también reconoce su raíz si está cargada como fuente manual. |
| [Mauro Perri](https://www.mauroperribienesraices.com.ar/) | `mauroperri` | Listados y fichas Tokko del dominio de la inmobiliaria. |
| [Urquiza](https://www.urquiza.com.ar/) | `urquiza` | Listados y fichas Tokko del dominio de la inmobiliaria. |

`backend/app/services/source_registry.py` define las integraciones revisadas.
`manual-sources` expone el `scrape_source` resuelto por dominio, y la pantalla
Fuentes identifica estas filas con «Búsqueda integrada». Editar una URL vuelve
a resolver la integración: el identificador no queda desactualizado en la base.
Los dominios parecidos y las URLs de oficinas dentro de InmoBúsqueda no se
convierten en consultas a todo el portal.

## Cómo se busca

Las fuentes seleccionadas siguen entrando desde el registro de inmobiliarias.
Para estas integraciones, `run_website_scraper` llama a `scrape_source` con los
filtros originales, separando localidades y barrios cerrados igual que en los
portales. Los resultados ya estructurados se normalizan y persisten sin pasar
por la extracción con LLM. Una inmobiliaria presente en el registro manual y
en el descubrimiento se consulta una sola vez por búsqueda.

Las otras inmobiliarias conservan el rastreo genérico durante esta incorporación
gradual. Agregar una URL no la convierte automáticamente en una integración
revisada, aunque su web también utilice Tokko.

## Mauro Perri y Urquiza

- El catálogo público embebido `locations_response` permite resolver IDs y
  ascendencia. Las ubicaciones desconocidas o ambiguas no habilitan consultas
  sin zona. Para La Plata se prefiere la ciudad sobre el partido cuando ambos
  figuran en el catálogo.
- `/Buscar?operation=…&ptypes=…&locations=…` aplica operación, tipo y ubicación.
  La paginación usa `p=N`, como el desplazamiento infinito de esas webs.
- Cada ficha `/p/…` aporta ambientes, dormitorios, baños, superficies,
  descripción y fotos. Los dormitorios de las tarjetas no son ambientes.
- El filtro final verifica los criterios solicitados. Un campo solicitado sin
  datos no prueba una coincidencia; la propiedad queda fuera de esta búsqueda.
- La paginación termina en una página vacía o repetida, independientemente de
  cuántas coincidencias produjo cada página. `TOKKO_MAX_PAGES=0` recorre todo
  el listado; un valor positivo limita las páginas de cada consulta.
- Se leen hasta tres fichas simultáneamente por sitio, dentro del límite global
  `WEBSITE_SCRAPE_CONCURRENCY`. Se utiliza `SCRAPER_PROXY_URL` si está configurado.
- No se necesitan claves de Tokko, Apify ni un navegador. Las fotos se toman
  de la galería de cada ficha y no incluyen las de propiedades recomendadas.

El modelo actual de búsqueda no tiene un campo de moneda. Los límites de precio
mantienen la comparación numérica existente; no se convierte entre ARS y USD.
Los filtros por moneda requieren ampliar ese contrato en una tarea posterior.

## InmoBúsqueda

Conserva su resolución de zonas y parser propios. Ahora distingue dormitorios
de ambientes, verifica los criterios finales y no corta la paginación por una
página con pocas coincidencias. Una página repetida tampoco produce un bucle
cuando el filtro de zona rechazó todas sus tarjetas.

En la revisión pública devolvió una verificación «No soy bot» tanto en la home
como en el listado de City Bell. Ese caso y los errores HTTP se informan como
fallos de acceso, en lugar de presentarlos como ausencia de propiedades. Esta
integración no resuelve automáticamente el desafío antibot.

## Puesta en servicio

Aplicar `supabase/migrations/20260907120000_registered_agency_scrapers.sql`
antes de ejecutar búsquedas con la nueva versión del backend. La migración:

1. Permite `mauroperri` y `urquiza` en `properties.fuente`.
2. Agrega las dos inmobiliarias a `manual_sources` si el dominio aún no existe.
3. Respeta los nombres, zonas y estados activo/inactivo de las filas existentes.

Las nuevas filas se crean sin zona administrativa inferida. Sus propiedades
se filtran por la ubicación solicitada; si se elige una zona específica del
registro de inmobiliarias, primero se deben clasificar en esa zona desde Fuentes.
InmoBúsqueda sigue disponible en la selección de portales.

## Verificación

Pruebas con HTML sintético y HTTP simulado:

```sh
cd backend
uv run pytest tests/services/test_registered_agency_scrapers.py \
  tests/services/test_inmobusqueda_search_precision.py \
  tests/graphs/test_registered_sources.py
uv run ruff check app tests
```

La revisión también usó páginas públicas de búsqueda y detalle de ambos sitios,
incluyendo la segunda página de Urquiza y el marcador final `--NoMoreProperties--`.
Las páginas capturadas no forman parte del repositorio.

La prueba HTTP del scraper completo con casas en venta en City Bell, hasta
100.000 y desde tres ambientes devolvió una coincidencia de Urquiza: 95.000 USD,
cinco ambientes, dos dormitorios y 20 fotos. Mauro Perri devolvió cero para
ese límite; las dos casas de su listado consultado publicaban 235.000 y
630.000 USD. La ficha de la primera se leyó además por separado y produjo cinco
ambientes, tres dormitorios y 20 fotos.

## Yacoub, Piazza, Feysulaj, Arrarás y Prado (28 de septiembre de 2026)

Las cinco están disponibles en el selector de inmobiliarias, los indicadores
de progreso y los filtros de propiedades. Se agregan al catálogo predeterminado
(14 inmobiliarias) y pueden seleccionarse individualmente.

| Fuente | `scrape_source` | Catálogo consultado |
| --- | --- | --- |
| [Yacoub](https://yacoub.com.ar/) | `yacoub` | Buscador público `/propiedades/` con el catálogo JSON de ubicaciones de su propia web. |
| [Piazza Propiedades](https://www.piazzapropiedades.com.ar/) | `piazza` | Tokko: `/Buscar` y fichas `/p/`. |
| [Feysulaj Propiedades](https://www.feysulaj.com.ar/) | `feysulaj` | Tokko: `/Buscar` y fichas `/p/`. |
| [Arrarás Propiedades](https://www.japropiedades.com.ar/) | `arraras` | Tokko: tarjetas de cuadrícula, precio anidado y galería de la ficha. |
| [Prado Propiedades](http://www.pradopropiedades.com.ar/) | `prado` | Su [perfil de InmoBúsqueda](https://www.inmobusqueda.com/pradopropiedades), enlazado desde el [Linktree oficial](https://linktr.ee/PradoPropiedades). |

Yacoub resuelve la ubicación con ascendencia y distingue La Plata ciudad del
partido. Envía operación y tipo al buscador, recorre `paginado=N` y lee
la ficha para verificar ambientes, dormitorios, superficies, coordenadas y fotos.
Las galerías excluyen las propiedades recomendadas. Las ubicaciones desconocidas
o ambiguas nunca se convierten en una búsqueda sin zona. El precio se verifica
al final: el sitio mostró paginación inconsistente al combinar límites de precio
con `currency=ANY`, y el modelo de búsqueda no selecciona una moneda.

Piazza y Feysulaj reutilizan las plantillas Tokko anteriores. Arrarás agrega una
variante de tarjetas con precio dentro de `.fp_price p`, tipo en
`.prop-card-red-text` y superficie construida en los datos de la tarjeta.
Se conserva la distinción entre ambientes y dormitorios.

La web propia de Prado muestra «en construcción». Su integración verifica el
perfil público y envía siempre `eid=2134` al formulario de búsqueda de
InmoBúsqueda. Recorre todas sus páginas y aplica los filtros finales por zona,
operación, tipo, precio, dormitorios y superficie. No consulta el catálogo
general del portal. El perfil declara UTF-8 aunque algunas respuestas llegan
en ISO-8859-1; ambas codificaciones están cubiertas.

Las tarjetas de Prado ofrecen descripción abreviada y foto de portada, y no
siempre publican ambientes o superficies. Las fichas individuales devolvieron
un desafío antibot durante esta revisión. No se infieren datos faltantes:
una propiedad sin ambientes no cumple una búsqueda que exija ese campo.
El desafío o un cambio de formato del listado se informa como error de acceso,
no como cero coincidencias.

La migración `supabase/migrations/20260928120000_add_five_agency_sources.sql`
habilita los cinco identificadores en `properties.fuente` e inserta las fuentes
manuales por dominio sin duplicar ni modificar filas existentes. No requiere
credenciales nuevas, Apify ni extracción con LLM.

Pruebas de esta incorporación:

```sh
cd backend
.venv/bin/pytest tests/services/test_registered_agency_scrapers.py \
  tests/services/test_yacoub_scraper.py tests/services/test_prado_scraper.py \
  tests/graphs/test_registered_sources.py tests/graphs/test_route_after_parse_sources.py \
  tests/api/test_scraping_start_source_selection.py
```

La prueba HTTP real devolvió 12 departamentos de Piazza en La Plata hasta
100.000 USD, 4 casas de Feysulaj y 5 de Arrarás en City Bell hasta 200.000 USD,
7 casas de Yacoub en City Bell hasta 100.000 USD y 121 departamentos de Prado
en La Plata hasta 100.000 USD. Son consultas de verificación independientes,
no totales de inventario: en las tres fuentes Tokko se limitó esta prueba a
una página; Yacoub y Prado recorrieron toda la paginación de esas consultas.
No se guardaron propiedades de prueba ni páginas capturadas en el repositorio.
