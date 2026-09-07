"""A barrio cerrado from the catalogue is a fan-out unit, exactly like a zona.

That equivalence is the feature — "que los tratemos de la misma forma que las
zonas". `route_after_parse` already fans out one branch per (unit × portal);
a catalogued barrio just becomes another kind of unit, carrying two things a
plain zona does not:

  * its COMPOSITE zona ("Grand Bell, City Bell, La Plata"), so that
    `zona_candidates` has a chain to degrade through instead of dead-ending on
    a name no portal outside the catalogue knows;
  * its ALIASES, so the degraded candidate cannot answer a Grand Bell search
    with all of City Bell (see `test_barrio_guard_on_degrade.py`).

Both travel on `ScrapingFilters`, which means every portal branch gets them
without a single scraper knowing that gated communities exist.
"""
from __future__ import annotations

import pytest

from app.graphs.extraction.nodes import route_after_parse
from app.models.property import ScrapingFilters

_GRAND_BELL = {
    'id': 'b1', 'nombre': 'Grand Bell', 'localidad': 'City Bell, La Plata',
    'aliases': [],
}
_LOS_CEIBOS = {
    'id': 'b2', 'nombre': 'Club de Campo Los Ceibos', 'localidad': 'City Bell, La Plata',
    'aliases': ['Los Ceibos LP'],
}


def _state(**over) -> dict:
    base = {
        'filters': ScrapingFilters(zona='City Bell, La Plata', tipo_operacion='venta'),
        'job_id': 'job-1',
        'source_selection': {
            'buscar_portales': True, 'portales': ['zonaprop'],
            'buscar_inmobiliarias': False,
        },
    }
    return base | over


def _branches(sends) -> list[ScrapingFilters]:
    return [
        s.arg['filters'] for s in sends
        if getattr(s, 'node', None) == 'run_portal_scraper'
    ]


@pytest.fixture(autouse=True)
def _sin_descubrimiento(monkeypatch):
    """Agency discovery is not what this file is about, and it depends on env
    gates that differ between machines."""
    from app.graphs.extraction import nodes
    monkeypatch.setattr(nodes, '_hay_que_descubrir_agencias', lambda _s: False)


class TestUnBarrioEsUnaUnidadDeFanOut:
    def test_cada_barrio_genera_su_rama(self):
        sends = route_after_parse(_state(barrios_cerrados=[_GRAND_BELL, _LOS_CEIBOS]))
        assert len(_branches(sends)) == 2

    def test_la_rama_lleva_la_zona_compuesta(self):
        """Sin la localidad pegada atrás, `zona_candidates` no tiene por dónde
        degradar y el barrio es inbuscable en cualquier portal que no lo
        resuelva nativamente."""
        sends = route_after_parse(_state(barrios_cerrados=[_GRAND_BELL]))
        assert _branches(sends)[0].zona == 'Grand Bell, City Bell, La Plata'

    def test_la_rama_lleva_los_alias(self):
        sends = route_after_parse(_state(barrios_cerrados=[_GRAND_BELL]))
        assert 'grand bell' in _branches(sends)[0].barrio_aliases

    def test_los_alias_manuales_llegan_a_la_rama(self):
        sends = route_after_parse(_state(barrios_cerrados=[_LOS_CEIBOS]))
        assert 'los ceibos lp' in _branches(sends)[0].barrio_aliases

    def test_el_kind_no_ensucia_la_zona(self):
        """"Club de Campo Los Ceibos" tiene que viajar como "Los Ceibos, ..." —
        el kind adelante manda la búsqueda al listado nacional en ZonaProp
        (medido: 172.141 resultados)."""
        sends = route_after_parse(_state(barrios_cerrados=[_LOS_CEIBOS]))
        assert _branches(sends)[0].zona == 'Los Ceibos, City Bell, La Plata'

    def test_una_rama_por_barrio_y_portal(self):
        sends = route_after_parse(_state(
            barrios_cerrados=[_GRAND_BELL, _LOS_CEIBOS],
            source_selection={
                'buscar_portales': True, 'portales': ['zonaprop', 'remax'],
                'buscar_inmobiliarias': False,
            },
        ))
        assert len(_branches(sends)) == 4


class TestElBarrioGanaLaUnidadDeFanOut:
    def test_los_barrios_reemplazan_a_la_zona_suelta(self):
        """Pediste barrios cerrados: buscar ADEMÁS toda City Bell devuelve el
        ruido que el catálogo existe para sacar."""
        sends = route_after_parse(_state(barrios_cerrados=[_GRAND_BELL]))
        assert [f.zona for f in _branches(sends)] == ['Grand Bell, City Bell, La Plata']

    def test_los_barrios_ganan_incluso_con_localidades(self):
        """La búsqueda por polígono fanea por localidad. Un barrio cerrado es
        MÁS específico, así que manda — y su rama no arrastra `localidades`,
        que en todos los resolvers pisa a `zona`."""
        sends = route_after_parse(_state(
            barrios_cerrados=[_GRAND_BELL], localidades=['City Bell']))
        rama = _branches(sends)[0]
        assert rama.zona == 'Grand Bell, City Bell, La Plata'
        assert rama.localidades == []


class TestSinBarriosNadaCambia:
    def test_el_fanout_de_zona_sigue_igual(self):
        sends = route_after_parse(_state())
        assert [f.zona for f in _branches(sends)] == ['City Bell, La Plata']
        assert _branches(sends)[0].barrio_aliases == []

    def test_una_lista_vacia_no_cambia_nada(self):
        sends = route_after_parse(_state(barrios_cerrados=[]))
        assert [f.zona for f in _branches(sends)] == ['City Bell, La Plata']

    def test_un_barrio_sin_nombre_se_ignora(self):
        """Una fila rota del catálogo no puede vaciar la búsqueda entera."""
        sends = route_after_parse(_state(
            barrios_cerrados=[{'id': 'x', 'nombre': '  ', 'localidad': 'La Plata'}]))
        assert [f.zona for f in _branches(sends)] == ['City Bell, La Plata']
