"""A gated-community search that DEGRADES must not answer with the localidad.

THE WHOLE POINT OF THE CATALOGUE, in one behaviour. `scrape_source` walks
`zona_candidates`, so with the catalogue supplying the containment a Grand Bell
search now degrades:

    Grand Bell, City Bell, La Plata  →  City Bell, La Plata  →  La Plata

That chain is what makes a country findable on the portals that do not own the
entity (InmoBusqueda nests it under a localidad with no page of its own; Mudafy
has no gated-community pages at all — measured 2026-09-04). But the degraded
candidate is a REAL zona search: it comes back with every house in City Bell.
Handing that back is a worse answer than zero, because it looks correct.

So the barrio filter runs in `scrape_source` — the single entry point every
portal shares — and it runs on the way OUT, on the results, not on the query.
It has to be there and not inside each scraper for the same reason the
candidate walk is: seven portals, one rule, and the rule only makes sense
relative to the ORIGINAL request rather than the candidate currently in flight.
"""
from __future__ import annotations

import pytest

from app.models.property import RawProperty, ScrapingFilters
from app.services.apify import ApifyService


def _prop(titulo: str, direccion: str = 'Calle 1 100') -> RawProperty:
    return RawProperty(
        fuente='zonaprop', titulo=titulo, direccion=direccion,
        precio=100000.0, tipo_operacion='venta',
    )


_GRAND_BELL = _prop('Casa en Grand Bell', 'Grand Bell, City Bell')
_OTRO_KIND = _prop('Lote en Barrio Cerrado Grand Bell', 'City Bell')
_VECINO = _prop('Casa en City Bell centro', 'Calle 470 1500, City Bell')
_OTRO_COUNTRY = _prop('Casa en Haras del Sur', 'Haras del Sur, La Plata')


class _Service(ApifyService):
    """Stub the per-candidate scrape; this file is about what `scrape_source`
    does with the results, not about how they were fetched."""

    def __init__(self, por_candidato: dict[str, list[RawProperty]]) -> None:
        self._por_candidato = por_candidato
        self.pedidos: list[str] = []

    async def _scrape_source_once(self, source, filters, on_progress):
        zona = filters.zona or ''
        self.pedidos.append(zona)
        return list(self._por_candidato.get(zona, []))


async def _noop(*_a, **_kw) -> None:
    return None


def _filters(**over) -> ScrapingFilters:
    base = {
        'zona': 'Grand Bell, City Bell, La Plata',
        'tipo_operacion': 'venta',
        'barrio_aliases': ['grand bell', 'barrio cerrado grand bell'],
    }
    return ScrapingFilters(**(base | over))


class TestElFiltroDeBarrioCorreEnLaDegradacion:
    async def test_el_candidato_exacto_pasa_entero(self):
        """Cuando el portal SÍ resuelve el barrio, no hay nada que filtrar."""
        svc = _Service({'Grand Bell, City Bell, La Plata': [_GRAND_BELL, _OTRO_KIND]})
        out = await svc.scrape_source('zonaprop', _filters(), _noop)
        assert len(out) == 2

    async def test_la_localidad_degradada_se_filtra_por_nombre(self):
        """El caso que importa: el portal sirvió todo City Bell y solo una
        propiedad nombra el barrio."""
        svc = _Service({'City Bell, La Plata': [_GRAND_BELL, _VECINO]})
        out = await svc.scrape_source('zonaprop', _filters(), _noop)
        assert [p.titulo for p in out] == ['Casa en Grand Bell']

    async def test_un_aviso_con_el_kind_adelante_sobrevive(self):
        """"Barrio Cerrado Grand Bell" es el mismo lugar. Los alias generados
        existen exactamente para esto."""
        svc = _Service({'City Bell, La Plata': [_OTRO_KIND, _VECINO]})
        out = await svc.scrape_source('zonaprop', _filters(), _noop)
        assert [p.titulo for p in out] == ['Lote en Barrio Cerrado Grand Bell']

    async def test_otro_country_de_la_misma_localidad_no_pasa(self):
        svc = _Service({'City Bell, La Plata': [_OTRO_COUNTRY]})
        assert await svc.scrape_source('zonaprop', _filters(), _noop) == []

    async def test_si_el_candidato_queda_vacio_sigue_degradando(self):
        """Filtrar hasta cero NO es "encontramos resultados". La cadena tiene
        que seguir, o un portal que sirvió la localidad equivocada corta el
        intento contra el partido, que es donde el aviso sí estaba."""
        svc = _Service({
            'City Bell, La Plata': [_VECINO],          # todo se filtra
            'La Plata': [_GRAND_BELL],                 # acá está
        })
        out = await svc.scrape_source('zonaprop', _filters(), _noop)
        assert [p.titulo for p in out] == ['Casa en Grand Bell']
        assert svc.pedidos == [
            'Grand Bell, City Bell, La Plata', 'City Bell, La Plata', 'La Plata']


class TestSinBarrioNoCambiaNada:
    async def test_una_busqueda_de_zona_normal_no_se_filtra(self):
        """La regresión que hay que evitar: `barrio_aliases` vacío tiene que
        dejar pasar TODO. Una zona común no tiene barrio que filtrar."""
        svc = _Service({'City Bell, La Plata': [_GRAND_BELL, _VECINO, _OTRO_COUNTRY]})
        out = await svc.scrape_source(
            'zonaprop',
            ScrapingFilters(zona='City Bell, La Plata', tipo_operacion='venta'),
            _noop,
        )
        assert len(out) == 3


class TestElFiltroMiraTodoElTextoDelAviso:
    @pytest.mark.parametrize('prop', [
        _prop('Casa 4 amb', 'Grand Bell, City Bell'),
        RawProperty(fuente='zonaprop', titulo='Casa', direccion='Calle 1',
                    descripcion='Excelente casa en Grand Bell', precio=1.0,
                    tipo_operacion='venta'),
    ])
    async def test_el_barrio_puede_estar_en_cualquier_campo(self, prop):
        """Los portales lo publican donde se les ocurre: dirección en uno,
        descripción en otro, título en el tercero."""
        svc = _Service({'City Bell, La Plata': [prop]})
        assert len(await svc.scrape_source('zonaprop', _filters(), _noop)) == 1
