"""El scraper de CENTURY 21: su propia API pública, sin Apify.

century21.com.ar es una SPA — el HTML de `/v/resultados/...` no trae un solo
`/propiedad/`, así que parsear la página es inútil. Pero la MISMA URL con
`?json=true` devuelve el JSON que la SPA consume (verificado en vivo, sin
auth, sin WAF: `curl` pelado responde 200). Es el mismo camino que RE/MAX y
por eso el diseño se le parece.

Dos endpoints, ambos públicos:

  GET /v/busqueda?q={texto}
      → {"propiedades":[…], "direcciones":[{tipoPK, label, slug}], …}
        `direcciones` es el autocompletado de UBICACIÓN y su `slug` ES el
        tramo de path del buscador:
          MUNICIPIO  → /en-pais_argentina/en-estado_gba-sur/en-municipio_gba-sur-la-plata
          COLONIA    → …/en-municipio_gba-sur-la-plata/en-colonia_city-bell
          COLONIA2   → …/en-colonia_melchor-romero/en-division_lomas-de-city-bell
        Que el barrio y el barrio CERRADO tengan slug propio es lo que hace a
        C21 servible para las búsquedas de este proyecto sin adivinar nada.

  GET /v/resultados{filtros}{ubicacion}[/pagina_N]?json=true
      → {"totalHits":"5.138", "results":[…100…], "filtros":[…]}

Los filtros son tramos `clave_valor` del path, en cualquier orden, con `-o-`
como separador de alternativas (`/tipo_departamento-o-ph`). El vocabulario
viaja en la respuesta (`filtros[].validValues`), así que no hay que adivinarlo:

  operacion → venta | alquiler       (OJO: el id interno es `renta`, pero la
                                      URL usa `alquiler` — `/operacion_renta`
                                      responde "Parámetro operacion
                                      opcionesInvalidas")
  tipo      → casa, casa-duplex, departamento, ph, terreno, local, oficinas, …
  moneda + precio-desde + precio-hasta

Y el techo, medido en vivo: `/pagina_15` responde, `/pagina_16` contesta
"Parámetro pagina incorrecto". 15 × 100 = 1500 avisos por consulta. No es un
knob de costo como en RE/MAX: es una pared del portal, y por eso vive en una
constante y no en `settings`.
"""
import httpx
import pytest

from app.core.config import settings
from app.models.property import ScrapingFilters
from app.services import apify
from app.services.apify import (
    _C21_MAX_PAGE,
    _c21_search_url,
    _century21_matches_zona,
    _norm_century21,
    _scrape_century21,
)

# ── Fixtures capturados en vivo ───────────────────────────────────────────────

# Recortado de GET /v/busqueda?q=City+Bell
_AUTOCOMPLETE_CITY_BELL = [
    {'weight': 0, 'tipoPK': 'COLONIA',
     'label': 'City Bell, La Plata, GBA Sur, Argentina',
     'slug': '/en-pais_argentina/en-estado_gba-sur/en-municipio_gba-sur-la-plata'
             '/en-colonia_city-bell'},
    {'weight': 0, 'tipoPK': 'COLONIA2',
     'label': 'Lomas de City Bell, Melchor Romero, La Plata, GBA Sur, Argentina',
     'slug': '/en-pais_argentina/en-estado_gba-sur/en-municipio_gba-sur-la-plata'
             '/en-colonia_melchor-romero/en-division_lomas-de-city-bell'},
]

_MUNICIPIO_LA_PLATA = (
    '/en-pais_argentina/en-estado_gba-sur/en-municipio_gba-sur-la-plata'
)

# Recortado de un `results[]` real de
# /v/resultados/operacion_venta{_MUNICIPIO_LA_PLATA}?json=true
_RESULT = {
    'id': '377754',
    'urlCorrectaPropiedad':
        '/propiedad/377754_departamento-en-venta-en-la-plata-de-1-dormitorio',
    'encabezado': 'Departamento en venta en La Plata de 1 dormitorio',
    'calle': 'Calle 45 entre 12 y 13',
    'ocultarCalleInternet': False,
    'colonia': 'La Plata',
    'municipio': 'La Plata',
    'estado': 'GBA Sur',
    'pais': 'Argentina',
    'lat': -34.917, 'lon': -57.96,
    'tipoOperacion': 'venta',
    'precio': '65000',
    'moneda': 'USD',
    'ocultarPrecioInternet': False,
    'tipoPropiedad': 'departamento',
    'm2T': 45, 'm2C': 45, 'unidadDeMedida': 'm2',
    'recamaras': 1, 'banos': 1, 'estacionamientos': None,
    'status': 'enPromocion', 'enInternet': True,
    'nombreAfiliado': 'CENTURY 21 Alianza Urbana S.A.(Gonnet)',
    'fotos': {
        'totalFotos': 22,
        'propiedadThumbnail': [
            'https://cdn.21online.lat/argentina/cache/a/rc/W/uploads/194/propiedades/377754/1.jpg',
            'https://cdn.21online.lat/argentina/cache/a/rc/U/uploads/194/propiedades/377754/2.jpg',
        ],
    },
}


async def _noop_progress(_src: str, _status: str, _count: int) -> None:
    return None


# ── La URL de búsqueda ────────────────────────────────────────────────────────

def test_la_url_lleva_ubicacion_operacion_y_json() -> None:
    url = _c21_search_url(
        ScrapingFilters(zona='La Plata', tipo_operacion='venta'),
        _MUNICIPIO_LA_PLATA, page=1,
    )
    assert url.startswith('https://century21.com.ar/v/resultados/')
    assert '/operacion_venta' in url
    assert _MUNICIPIO_LA_PLATA in url
    assert url.endswith('?json=true')


def test_alquiler_usa_el_slug_alquiler_no_renta() -> None:
    """El id interno del filtro es `renta` pero la URL sólo acepta
    `alquiler`: `/operacion_renta` devuelve "opcionesInvalidas". Medido."""
    url = _c21_search_url(
        ScrapingFilters(zona='La Plata', tipo_operacion='alquiler'),
        _MUNICIPIO_LA_PLATA, page=1,
    )
    assert '/operacion_alquiler' in url
    assert 'operacion_renta' not in url


def test_la_primera_pagina_no_lleva_tramo_pagina() -> None:
    url = _c21_search_url(ScrapingFilters(zona='La Plata'), _MUNICIPIO_LA_PLATA, page=1)
    assert '/pagina_' not in url


def test_las_siguientes_paginas_van_por_path() -> None:
    url = _c21_search_url(ScrapingFilters(zona='La Plata'), _MUNICIPIO_LA_PLATA, page=3)
    assert '/pagina_3' in url


def test_los_tipos_pedidos_viajan_separados_por_o() -> None:
    """`-o-` es el `separator` que el propio portal declara en `filtros`."""
    url = _c21_search_url(
        ScrapingFilters(zona='La Plata', tipos_propiedad=['departamento', 'ph']),
        _MUNICIPIO_LA_PLATA, page=1,
    )
    tipo = next(p for p in url.split('?')[0].split('/') if p.startswith('tipo_'))
    valores = set(tipo.removeprefix('tipo_').split('-o-'))
    assert {'departamento', 'ph'} <= valores


def test_sin_tipos_no_hay_tramo_tipo() -> None:
    """Un `tipo_` vacío o inventado hace fallar la request entera con
    "opcionesInvalidas"; sin el tramo, el portal devuelve todos los tipos."""
    url = _c21_search_url(ScrapingFilters(zona='La Plata'), _MUNICIPIO_LA_PLATA, page=1)
    assert '/tipo_' not in url


# ── Resolver la zona contra el autocompletado ─────────────────────────────────

async def test_resuelve_la_zona_al_slug_del_autocompletado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_autocomplete(monkeypatch, _AUTOCOMPLETE_CITY_BELL)
    apify._C21_LOCATION_CACHE.clear()
    slug = await apify._c21_resolve_location('City Bell')
    assert slug == _AUTOCOMPLETE_CITY_BELL[0]['slug']


async def test_prefiere_el_candidato_con_head_exacto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Lomas de City Bell" CONTIENE "city bell" y es más profundo, pero es
    otro lugar — un barrio cerrado adentro de Melchor Romero. El head del
    label tiene que coincidir, misma regla que el resolver de RE/MAX."""
    _stub_autocomplete(monkeypatch, list(reversed(_AUTOCOMPLETE_CITY_BELL)))
    apify._C21_LOCATION_CACHE.clear()
    slug = await apify._c21_resolve_location('City Bell')
    assert slug == _AUTOCOMPLETE_CITY_BELL[0]['slug']


async def test_una_zona_compuesta_exige_todas_sus_partes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_autocomplete(monkeypatch, _AUTOCOMPLETE_CITY_BELL)
    apify._C21_LOCATION_CACHE.clear()
    assert await apify._c21_resolve_location('City Bell, Rosario') is None


async def test_sin_match_devuelve_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin ubicación no hay búsqueda: el portal sin filtro de zona sirve el
    inventario nacional entero (21.064 avisos medidos), y el techo de 15
    páginas lo vuelve una muestra sin relación con lo que se pidió. Devolver
    None deja que la cadena de `zona_candidates` pruebe la zona más ancha."""
    _stub_autocomplete(monkeypatch, [])
    apify._C21_LOCATION_CACHE.clear()
    assert await apify._c21_resolve_location('Barrio Que No Existe') is None


# ── Normalización ─────────────────────────────────────────────────────────────

def test_normaliza_un_aviso_real() -> None:
    prop = _norm_century21(_RESULT, 'La Plata')
    assert prop is not None
    assert prop.fuente == 'century21'
    assert prop.precio == 65000.0
    assert prop.moneda == 'USD'
    assert prop.tipo_operacion == 'venta'
    assert prop.tipo_propiedad == 'departamento'
    assert prop.ambientes == 1
    assert prop.banos == 1
    assert prop.m2_total == 45
    assert prop.m2_cubiertos == 45
    assert prop.titulo == 'Departamento en venta en La Plata de 1 dormitorio'


def test_la_url_de_origen_es_absoluta() -> None:
    """`urlCorrectaPropiedad` viene relativa; guardarla así rompe el dedup por
    URL y deja links muertos en la ficha."""
    prop = _norm_century21(_RESULT, 'La Plata')
    assert prop is not None
    assert prop.url_origen == (
        'https://century21.com.ar/propiedad/'
        '377754_departamento-en-venta-en-la-plata-de-1-dormitorio'
    )


def test_la_direccion_junta_calle_y_barrio() -> None:
    prop = _norm_century21(_RESULT, 'La Plata')
    assert prop is not None
    assert 'Calle 45 entre 12 y 13' in prop.direccion
    assert 'La Plata' in prop.direccion


def test_una_calle_oculta_no_se_publica() -> None:
    """`ocultarCalleInternet` es una decisión del anunciante y el portal la
    respeta en su propia ficha; la dirección cae al barrio."""
    prop = _norm_century21({**_RESULT, 'ocultarCalleInternet': True}, 'La Plata')
    assert prop is not None
    assert 'Calle 45' not in prop.direccion
    assert 'La Plata' in prop.direccion


def test_las_fotos_salen_del_thumbnail() -> None:
    prop = _norm_century21(_RESULT, 'La Plata')
    assert prop is not None
    assert len(prop.imagenes) == 2
    assert all(u.startswith('https://cdn.21online.lat/') for u in prop.imagenes)


def test_un_aviso_con_precio_oculto_se_descarta() -> None:
    assert _norm_century21({**_RESULT, 'ocultarPrecioInternet': True}, 'La Plata') is None


def test_un_aviso_sin_precio_se_descarta() -> None:
    assert _norm_century21({**_RESULT, 'precio': None}, 'La Plata') is None


@pytest.mark.parametrize('c21,esperado', [
    ('casa', 'casa'), ('casa_duplex', 'casa'), ('quinta', 'casa'),
    ('departamento', 'departamento'), ('penthouse', 'departamento'),
    ('loft', 'departamento'), ('ph', 'ph'),
    ('terreno', 'terreno'), ('campo', 'terreno'),
    ('local', 'local'), ('fondo_de_comercio', 'local'),
    ('oficinas', 'oficina'),
    ('galpon', 'otro'), ('cochera', 'otro'), ('lo-que-sea', 'otro'),
])
def test_mapea_el_tipo_de_propiedad(c21: str, esperado: str) -> None:
    prop = _norm_century21({**_RESULT, 'tipoPropiedad': c21}, 'La Plata')
    assert prop is not None
    assert prop.tipo_propiedad == esperado


# ── La guarda de zona ─────────────────────────────────────────────────────────

def test_la_guarda_acepta_lo_que_esta_en_la_zona() -> None:
    filters = ScrapingFilters(zona='La Plata', zona_pedida='La Plata')
    assert _century21_matches_zona(_RESULT, filters)


def test_la_guarda_rechaza_otra_localidad() -> None:
    """El filtro `en-municipio_` es server-side y confiable, pero la cadena de
    `zona_candidates` ensancha la URL cuando el barrio no resuelve — y ahí la
    respuesta trae el partido entero. La guarda contesta la pregunta que se
    hizo, no la que se terminó consultando."""
    filters = ScrapingFilters(zona='La Plata', zona_pedida='City Bell, La Plata')
    assert not _century21_matches_zona(_RESULT, filters)


def test_la_guarda_acepta_el_barrio_por_colonia() -> None:
    item = {**_RESULT, 'colonia': 'City Bell', 'municipio': 'La Plata'}
    filters = ScrapingFilters(zona='City Bell, La Plata',
                              zona_pedida='City Bell, La Plata')
    assert _century21_matches_zona(item, filters)


# ── Paginación ────────────────────────────────────────────────────────────────

def test_el_techo_de_paginas_es_el_del_portal() -> None:
    """`/pagina_16` responde "Parámetro pagina incorrecto" (medido en vivo).
    15 × 100 = 1500 avisos por consulta."""
    assert _C21_MAX_PAGE == 15


async def test_pagina_hasta_agotar_los_resultados(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _stub_search(monkeypatch, total_pages=4)
    monkeypatch.setattr(settings, 'CENTURY21_MAX_PAGES', 0)
    results = await _scrape_century21(
        ScrapingFilters(zona='La Plata', tipo_operacion='venta'), _noop_progress,
    )
    assert len(captured['urls']) == 4
    assert len(results) == 4 * 100


async def test_nunca_pide_mas_alla_del_techo_del_portal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin este corte el scraper gasta una request en `/pagina_16` para
    recibir un texto de error que ni siquiera es JSON."""
    captured = _stub_search(monkeypatch, total_pages=99)
    monkeypatch.setattr(settings, 'CENTURY21_MAX_PAGES', 0)
    await _scrape_century21(ScrapingFilters(zona='La Plata'), _noop_progress)
    assert len(captured['urls']) == _C21_MAX_PAGE
    assert not any('/pagina_16' in u for u in captured['urls'])


def test_total_hits_llega_formateado_en_es_ar() -> None:
    """"5.138" NO es 5.138: es el separador de MILES. Leerlo como float da 5,
    la paginación corta en la primera página y la búsqueda devuelve 100 de
    5138 avisos sin que nada falle a la vista."""
    from app.services.apify import _c21_total_hits
    assert _c21_total_hits({'totalHits': '5.138'}) == 5138
    assert _c21_total_hits({'totalHits': '294'}) == 294
    assert _c21_total_hits({'totalHits': 294}) == 294
    assert _c21_total_hits({}) is None


async def test_no_gasta_una_request_para_descubrir_el_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`totalHits` viaja en la misma respuesta: cuatro páginas justas se
    resuelven en cuatro requests, no en cinco."""
    captured = _stub_search(monkeypatch, total_pages=4)
    monkeypatch.setattr(settings, 'CENTURY21_MAX_PAGES', 0)
    await _scrape_century21(ScrapingFilters(zona='La Plata'), _noop_progress)
    assert len(captured['urls']) == 4


async def test_el_cap_de_settings_recorta_antes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _stub_search(monkeypatch, total_pages=99)
    monkeypatch.setattr(settings, 'CENTURY21_MAX_PAGES', 3)
    await _scrape_century21(ScrapingFilters(zona='La Plata'), _noop_progress)
    assert len(captured['urls']) == 3


async def test_sin_ubicacion_no_pide_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub_search(monkeypatch, total_pages=4, location=None)
    results = await _scrape_century21(ScrapingFilters(zona='Nada'), _noop_progress)
    assert captured['urls'] == []
    assert results == []


def test_el_default_viene_sin_cap_propio() -> None:
    """El techo real ya lo pone el portal; el setting existe para recortarlo
    a mano, no para volver a inventar el bug del `100 items` de RE/MAX."""
    assert settings.CENTURY21_MAX_PAGES == 0


# ── El User-Agent ─────────────────────────────────────────────────────────────

def test_no_sale_como_propsearchbot() -> None:
    """C21 filtra por UA y `PropSearchBot/1.0` recibe el texto `Blocked Bot`
    CON STATUS 200 (medido). Ese 200 es lo peligroso: `raise_for_status()` lo
    deja pasar, revienta recién en `.json()` y el `except` lo convierte en
    `[]` — la búsqueda entera vuelve vacía sin un motivo visible. Es el mismo
    modo de falla que ya documentó el track de sitios de inmobiliarias."""
    from app.services.apify import _c21_headers
    ua = _c21_headers()['User-Agent']
    assert 'PropSearchBot' not in ua
    assert ua.startswith('Mozilla/5.0 (')
    assert 'Chrome/' in ua


# ── La galería completa, desde la ficha ───────────────────────────────────────

# Recortado de GET /propiedad/377754_...?json=true — la MISMA API que el
# listado, sobre la ficha. `fotos[]` trae las 22, el listado sólo 10.
_FICHA = {
    'fotos': [
        {'orden': 1, 'width': 1200, 'height': 800,
         'large': 'https://cdn.21online.lat/argentina/cache/a/rc/W/uploads/194/propiedades/377754/1.jpg',
         'path': '/uploads/194/propiedades/377754/1.jpg'},
        {'orden': 2,
         'large': 'https://cdn.21online.lat/argentina/cache/a/rc/b/uploads/194/propiedades/377754/2.jpg'},
        {'orden': 3, 'large': ''},
    ],
    'fotosNew': [],
}


async def test_la_ficha_devuelve_la_galeria_completa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.apify import century21_gallery_from_url

    captured: dict = {}

    class _Client:
        def __init__(self, *a, **k) -> None: ...
        async def __aenter__(self) -> '_Client': return self
        async def __aexit__(self, *a) -> None: return None
        async def get(self, url, **k) -> _FakeResponse:
            captured['url'] = url
            return _FakeResponse(_FICHA)

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)
    urls = await century21_gallery_from_url(
        'https://century21.com.ar/propiedad/377754_departamento-en-la-plata',
    )
    assert captured['url'].endswith('?json=true')
    assert urls == [
        'https://cdn.21online.lat/argentina/cache/a/rc/W/uploads/194/propiedades/377754/1.jpg',
        'https://cdn.21online.lat/argentina/cache/a/rc/b/uploads/194/propiedades/377754/2.jpg',
    ]


async def test_la_galeria_ignora_una_url_que_no_es_de_c21() -> None:
    from app.services.apify import century21_gallery_from_url
    assert await century21_gallery_from_url('https://zonaprop.com.ar/x') == []


async def test_la_ficha_de_c21_esta_enganchada_al_despacho_por_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El despacho de galerías va por HOST, no por `fuente`: Ficha Propio
    guarda todo con `fuente='manual'` y ahí la URL es el único dato honesto."""
    from app.services import ficha

    async def _fake(url: str) -> list[str]:
        return ['https://cdn.21online.lat/x.jpg']

    monkeypatch.setattr('app.services.apify.century21_gallery_from_url', _fake)
    urls = await ficha.portal_gallery_from_url(
        'https://century21.com.ar/propiedad/377754_x',
    )
    assert urls == ['https://cdn.21online.lat/x.jpg']


# ── Stubs ─────────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def _stub_autocomplete(monkeypatch: pytest.MonkeyPatch, direcciones: list) -> None:
    class _Client:
        def __init__(self, *a, **k) -> None: ...
        async def __aenter__(self) -> '_Client': return self
        async def __aexit__(self, *a) -> None: return None
        async def get(self, url, params=None, **k) -> _FakeResponse:
            return _FakeResponse({
                'propiedades': [], 'direcciones': direcciones,
                'afiliados': [], 'usuarios': [],
            })

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)


def _stub_search(
    monkeypatch: pytest.MonkeyPatch,
    total_pages: int,
    location: str | None = _MUNICIPIO_LA_PLATA,
) -> dict:
    """Un resultset profundo: lo único que puede frenar la paginación es el
    techo del portal o el cap de settings."""
    captured: dict = {'urls': []}

    async def _resolve(_zona: str) -> str | None:
        return location

    monkeypatch.setattr(apify, '_c21_resolve_location', _resolve)

    class _Client:
        def __init__(self, *a, **k) -> None: ...
        async def __aenter__(self) -> '_Client': return self
        async def __aexit__(self, *a) -> None: return None
        async def get(self, url, **k) -> _FakeResponse:
            captured['urls'].append(url)
            page = 1
            for seg in url.split('?')[0].split('/'):
                if seg.startswith('pagina_'):
                    page = int(seg.removeprefix('pagina_'))
            items = [] if page > total_pages else [
                {**_RESULT, 'id': str(page * 100 + i),
                 'urlCorrectaPropiedad': f'/propiedad/{page * 100 + i}_x'}
                for i in range(100)
            ]
            return _FakeResponse({
                'totalHits': str(total_pages * 100),
                'totalHitsRelation': 'eq',
                'results': items,
                'filtros': [],
            })

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)
    return captured
