"""Test-first for `barrio_probe.discover_argenprop_barrios` — enumerating the
gated communities of a partido so they can be loaded in bulk instead of typed
one by one.

WHY ARGENPROP AND NOT ANOTHER PORTAL. It is the only one of the seven that
files gated communities under a SYNTHETIC pseudo-locality — measured 2026-09-04:

    stringBusqueda="countries la plata" →
      {"label": "Countries y Barrios Cerrados en la Plata, Partido de La Plata",
       "value": {"CodigoLocalidad": "LA-PLATA-COUNTRIES-BARRIOS-CERRADOS"}}
      {"label": "Área 60, Partido de La Plata",
       "value": {"CodigoBarrio": "AREA-60-LP",
                 "CodigoLocalidad": "LA-PLATA-COUNTRIES-BARRIOS-CERRADOS"}}
      {"label": "Campos De La Enriqueta, Partido de La Plata", …}

Every country in the partido hangs off that one locality code, so filtering on
it turns a fuzzy text search into an enumeration.

TWO LIMITS THIS FUNCTION MUST BE HONEST ABOUT, because both mislead silently:

1. THE API CAPS ITS RESULTS (30, measured). A partido with more gated
   communities than that comes back truncated with no error and no marker, so
   an importer that trusts it would quietly load a third of Pilar and report
   success. The count is reported back.

2. ARGENPROP DOES NOT KNOW THE LOCALIDAD. It labels Grand Bell "Partido de La
   Plata"; the barrio is actually in City Bell. Our `localidad` column is the
   load-bearing one — it is what `zona_candidates` degrades through — so the
   import can only default it to the PARTIDO and let the operator refine.
   "Grand Bell, La Plata" is a coarser chain than "Grand Bell, City Bell, La
   Plata", but it is a CORRECT one; guessing City Bell would not be.
"""
from __future__ import annotations

import httpx
import pytest

from app.services import apify
from app.services.barrio_probe import (
    ARGENPROP_AUTOCOMPLETE_CAP,
    BarrioCandidate,
    discover_argenprop_barrios,
)

_LOC = 'LA-PLATA-COUNTRIES-BARRIOS-CERRADOS'


def _entry(label: str, *, barrio: str | None = None, loc: str = _LOC) -> dict:
    value: dict = {'CodigoLocalidad': loc, 'NombrePartido': 'Partido de La Plata'}
    if barrio:
        value['CodigoBarrio'] = barrio
    return {'label': label, 'value': value}


# Copied from the live response.
_LA_PLATA = [
    # The pseudo-locality's own entry: a HEADING, not a barrio. It has no
    # `CodigoBarrio`, and importing it would create a "barrio" that is really
    # the whole partido's country listing.
    _entry('Countries y Barrios Cerrados en la Plata, Partido de La Plata'),
    _entry('Área 60, Partido de La Plata', barrio='AREA-60-LP'),
    _entry('Campos De La Enriqueta, Partido de La Plata', barrio='CAMPOS-DE-LA-ENRIQUETA'),
    _entry('Grand Bell, Partido de La Plata', barrio='GRAND-BELL'),
    _entry('Haras Del Sur 2, Partido de La Plata', barrio='HARAS-DEL-SUR-2'),
    # A country in ANOTHER partido that the fuzzy search dragged in. Its
    # locality code is a different pseudo-locality — that is the tell.
    _entry('Barrio Cerrado Los Plátanos, Partido de San Miguel',
           barrio='CERRADO-LOS-PLATANOS', loc='BELLA-VISTA-COUNTRIES-BARRIOS-CERRADOS'),
    # An ordinary barrio (not gated) under the REAL locality, not the
    # pseudo-one. Also excluded, same rule.
    _entry('Barrio Norte, La Plata', barrio='BR-NORTE-LP', loc='LA-PLATA-BUENOS-AIRES'),
]


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    apify._ARGENPROP_SLUG_CACHE.clear()
    respuestas: dict[str, list[dict]] = {'countries la plata': _LA_PLATA}

    class _Resp:
        def __init__(self, payload) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, params=None, **kw):
            q = str((params or {}).get('stringBusqueda', '')).lower()
            return _Resp(respuestas.get(q, []))

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)
    return respuestas


class TestEnumeracion:
    async def test_devuelve_los_countries_del_partido(self):
        found = await discover_argenprop_barrios('La Plata')
        assert [c.nombre for c in found.barrios] == [
            'Área 60', 'Campos De La Enriqueta', 'Grand Bell', 'Haras Del Sur 2']

    async def test_trae_el_codigo_de_argenprop_como_ref(self):
        """Es exactamente el `ref` que `barrio_cerrado_portal_refs` necesita
        para Argenprop — importarlo evita un probe por barrio."""
        found = await discover_argenprop_barrios('La Plata')
        grand_bell = next(c for c in found.barrios if c.nombre == 'Grand Bell')
        assert grand_bell.argenprop_ref == 'GRAND-BELL'

    async def test_la_pseudo_localidad_no_se_importa_como_barrio(self):
        """"Countries y Barrios Cerrados en la Plata" es el ENCABEZADO del
        grupo. Sin `CodigoBarrio` no es un lugar: importarlo crearía un
        "barrio" que en realidad es todo el partido."""
        found = await discover_argenprop_barrios('La Plata')
        assert not any('Countries y Barrios' in c.nombre for c in found.barrios)

    async def test_un_country_de_otro_partido_se_descarta(self):
        """La búsqueda es difusa y arrastra vecinos. El código de la
        pseudo-localidad es el corte, no el label."""
        found = await discover_argenprop_barrios('La Plata')
        assert not any('Plátanos' in c.nombre for c in found.barrios)

    async def test_un_barrio_comun_no_es_un_country(self):
        """"Barrio Norte" cuelga de LA-PLATA-BUENOS-AIRES, la localidad REAL.
        Sólo la pseudo-localidad marca "esto es un barrio cerrado"."""
        found = await discover_argenprop_barrios('La Plata')
        assert not any(c.nombre == 'Barrio Norte' for c in found.barrios)


class TestLaLocalidadPorDefecto:
    async def test_la_localidad_es_el_partido(self):
        """Argenprop etiqueta Grand Bell como "Partido de La Plata" y el barrio
        está en City Bell. No se puede derivar, así que el import pone el
        partido: cadena más gruesa, pero CORRECTA."""
        found = await discover_argenprop_barrios('La Plata')
        assert all(c.localidad == 'La Plata' for c in found.barrios)

    async def test_el_operador_puede_fijar_otra_localidad(self):
        """Cuando el operador SABE que todo el lote está en City Bell."""
        found = await discover_argenprop_barrios('La Plata', localidad='City Bell, La Plata')
        assert all(c.localidad == 'City Bell, La Plata' for c in found.barrios)


class TestElTopeSeReporta:
    async def test_una_respuesta_corta_no_esta_truncada(self):
        found = await discover_argenprop_barrios('La Plata')
        assert found.truncated is False

    async def test_una_respuesta_en_el_tope_se_marca_truncada(self, _stub):
        """El fallo silencioso que hay que evitar: la API corta en 30 sin
        error ni marcador. Un import que confía cargaría un tercio de Pilar y
        diría que salió todo bien."""
        _stub['countries pilar'] = [
            _entry(f'Country {i}, Partido de Pilar', barrio=f'C{i}')
            for i in range(ARGENPROP_AUTOCOMPLETE_CAP)
        ]
        found = await discover_argenprop_barrios('Pilar')
        assert found.truncated is True

    async def test_el_total_crudo_se_reporta(self):
        found = await discover_argenprop_barrios('La Plata')
        assert found.total_api == len(_LA_PLATA)


class TestFallas:
    async def test_un_partido_sin_countries_devuelve_vacio(self):
        found = await discover_argenprop_barrios('Chascomús')
        assert found.barrios == []
        assert found.truncated is False

    async def test_un_error_de_red_no_explota(self, monkeypatch):
        """Un import que tira 500 es peor que uno que dice "no encontré nada":
        el operador siempre puede cargar a mano."""
        class _Boom:
            def __init__(self, *a, **kw) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, *a, **kw):
                raise RuntimeError('portal caído')

        monkeypatch.setattr(httpx, 'AsyncClient', _Boom)
        found = await discover_argenprop_barrios('La Plata')
        assert found.barrios == []
        assert found.error


class TestFormaDelCandidato:
    async def test_es_serializable_para_la_ui(self):
        found = await discover_argenprop_barrios('La Plata')
        assert isinstance(found.barrios[0], BarrioCandidate)
        assert set(found.barrios[0].as_dict()) >= {
            'nombre', 'localidad', 'kind', 'argenprop_ref', 'label'}

    async def test_conserva_el_label_del_portal(self):
        """Es la respuesta a "cómo figura ahí" y lo único que el humano puede
        revisar antes de aceptar el import."""
        found = await discover_argenprop_barrios('La Plata')
        grand_bell = next(c for c in found.barrios if c.nombre == 'Grand Bell')
        assert grand_bell.label == 'Grand Bell, Partido de La Plata'


class TestBarridoProfundo:
    """`deep=True` — para los partidos donde el tope de 30 corta de verdad.

    La query base devuelve el head alfabético y nada más: medido en vivo, Pilar
    corta en "El Zorzal" y se pierde todo de la F a la Z. Pero un seed de 3+
    letras SÍ mueve la ventana — `"countries pilar san"` devolvió 21 filas
    (San Francisco, Santa Silvina, Santa Rosa) que la base jamás alcanza.

    Así que el barrido repite la consulta con prefijos de topónimo argentino y
    fusiona. NO garantiza completitud — ningún endpoint fuzzy con tope puede —
    y por eso `truncated` sigue viajando en la respuesta.
    """

    async def test_fusiona_los_resultados_de_todos_los_seeds(self, _stub):
        _stub['countries pilar'] = [
            _entry('Altos Del Golf, Partido de Pilar', barrio='ALTOS-DEL-GOLF',
                   loc='PILAR-COUNTRIES-BARRIOS-CERRADOS'),
        ]
        _stub['countries pilar san'] = [
            _entry('San Francisco, Partido de Pilar', barrio='SAN-FRANCISCO',
                   loc='PILAR-COUNTRIES-BARRIOS-CERRADOS'),
        ]
        found = await discover_argenprop_barrios('Pilar', deep=True)
        assert {c.nombre for c in found.barrios} >= {'Altos Del Golf', 'San Francisco'}

    async def test_no_duplica_lo_que_aparece_en_varios_seeds(self, _stub):
        repetido = [_entry('Santa Rosa, Partido de Pilar', barrio='SANTA-ROSA',
                           loc='PILAR-COUNTRIES-BARRIOS-CERRADOS')]
        _stub['countries pilar'] = repetido
        _stub['countries pilar san'] = repetido
        _stub['countries pilar santa'] = repetido
        found = await discover_argenprop_barrios('Pilar', deep=True)
        assert [c.nombre for c in found.barrios] == ['Santa Rosa']

    async def test_sale_ordenado_por_nombre(self, _stub):
        """Fusionar N respuestas rompe el orden alfabético que traía cada una,
        y el operador revisa esta lista a ojo."""
        _stub['countries pilar'] = [
            _entry('Zorzal, Partido de Pilar', barrio='Z', loc='PILAR-COUNTRIES-BARRIOS-CERRADOS')]
        _stub['countries pilar san'] = [
            _entry('Altos, Partido de Pilar', barrio='A', loc='PILAR-COUNTRIES-BARRIOS-CERRADOS')]
        found = await discover_argenprop_barrios('Pilar', deep=True)
        assert [c.nombre for c in found.barrios] == ['Altos', 'Zorzal']

    async def test_un_seed_que_falla_no_tumba_el_barrido(self, monkeypatch, _stub):
        """20 requests: que una se caiga es normal. Perder las otras 19 no."""
        import httpx as _httpx

        _stub['countries pilar'] = [
            _entry('Altos Del Golf, Partido de Pilar', barrio='ALTOS-DEL-GOLF',
                   loc='PILAR-COUNTRIES-BARRIOS-CERRADOS'),
        ]
        original = _httpx.AsyncClient

        class _Flaky(original):  # type: ignore[misc, valid-type]
            async def get(self, url, params=None, **kw):
                if 'san' in str((params or {}).get('stringBusqueda', '')):
                    raise RuntimeError('timeout')
                return await original.get(self, url, params=params, **kw)

        monkeypatch.setattr(_httpx, 'AsyncClient', _Flaky)
        found = await discover_argenprop_barrios('Pilar', deep=True)
        assert [c.nombre for c in found.barrios] == ['Altos Del Golf']

    async def test_sin_deep_solo_se_hace_la_query_base(self, _stub):
        """El barrido son ~20 requests. No se pagan salvo que se pidan."""
        _stub['countries pilar'] = [
            _entry('Altos Del Golf, Partido de Pilar', barrio='ALTOS-DEL-GOLF',
                   loc='PILAR-COUNTRIES-BARRIOS-CERRADOS'),
        ]
        _stub['countries pilar san'] = [
            _entry('San Francisco, Partido de Pilar', barrio='SAN-FRANCISCO',
                   loc='PILAR-COUNTRIES-BARRIOS-CERRADOS'),
        ]
        found = await discover_argenprop_barrios('Pilar')
        assert [c.nombre for c in found.barrios] == ['Altos Del Golf']
