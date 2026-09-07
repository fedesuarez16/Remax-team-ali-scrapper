"""A CONFIRMED portal ref steers the search instead of the candidate chain.

THE GAP THIS CLOSES, measured live 2026-09-07. The probe found that Argenprop
has Grand Bell as `CodigoBarrio=GRAND-BELL`, but the SEARCH never reached it:

    Argenprop, resolving each candidate of the search chain:
      Grand Bell, City Bell, La Plata  ->  None        ← no match
      City Bell, La Plata              ->  city-bell   ← lands here
      La Plata                         ->  la-plata-buenos-aires

The two paths degrade differently on purpose — `zona_candidates` shortens the
HEAD (dropping the barrio), `barrio_probe_forms` shortens the TAIL (keeping
it) — so the search chain hits the same administrative-level mismatch the
probe cascade was built to survive: Argenprop files gated communities under the
partido, and its resolver needs every comma part of the query in the label.

The result was correct but wasteful: the search walked all of City Bell and
threw away ~95% by name, paying pages for listings it discarded — and a paging
ceiling can cut real Grand Bell listings before the filter ever sees them.

WHY ONLY CONFIRMED REFS. An unconfirmed ref is a guess, and the alias filter
cannot catch a wrong one: a ref pointing at the "Los Ceibos" in TIGRE returns
listings that genuinely say "Los Ceibos", so they sail through. Confirmation is
the only thing standing between a curated catalogue and plausible garbage, so
it is what gates the override. Everything unconfirmed keeps today's behaviour —
the candidate chain plus the alias filter, correct if wide.
"""
from __future__ import annotations

import pytest

from app.models.property import ScrapingFilters
from app.services import apify


def _filters(**over) -> ScrapingFilters:
    base = {
        'zona': 'Grand Bell, City Bell, La Plata',
        'tipo_operacion': 'venta',
        'barrio_aliases': ['grand bell'],
    }
    return ScrapingFilters(**(base | over))


class TestElOverrideGanaSobreElResolver:
    @pytest.mark.parametrize(('portal', 'ref'), [
        ('argenprop', 'grand-bell'),
        ('inmobusqueda', 'haras-del-sur'),
        ('remax', 'in::::::2439:'),
        ('century21', '/en-pais_argentina/en-division_grand-bell'),
    ])
    def test_una_ref_confirmada_se_usa_tal_cual(self, portal: str, ref: str) -> None:
        f = _filters(barrio_portal_refs={portal: ref})
        assert apify._barrio_portal_ref(f, portal) == ref

    def test_sin_refs_no_hay_override(self) -> None:
        """El camino de siempre: una búsqueda de zona común no tiene barrio."""
        assert apify._barrio_portal_ref(_filters(), 'argenprop') is None

    def test_la_ref_de_otro_portal_no_se_cruza(self) -> None:
        """`in::::::2439:` en el slug de Argenprop sería una URL inventada."""
        f = _filters(barrio_portal_refs={'remax': 'in::::::2439:'})
        assert apify._barrio_portal_ref(f, 'argenprop') is None

    def test_una_ref_vacia_no_cuenta_como_override(self) -> None:
        """Una fila `localidad` guarda `ref: null`. Leerla como override
        mandaría al portal a buscar la cadena vacía — el listado nacional."""
        f = _filters(barrio_portal_refs={'argenprop': ''})
        assert apify._barrio_portal_ref(f, 'argenprop') is None


class TestElResolverNoSeLlamaSiHayRef:
    """El punto del ejercicio: además de acertar la zona, ahorrar la llamada."""

    async def test_argenprop_no_consulta_el_autocomplete(self, monkeypatch) -> None:
        llamadas: list[str] = []

        async def _spy(zona):
            llamadas.append(zona)
            return 'city-bell'

        monkeypatch.setattr(apify, '_argenprop_resolve_zona_slug', _spy)
        f = _filters(barrio_portal_refs={'argenprop': 'grand-bell'})
        assert await apify._resolve_argenprop_zona(f, f.zona or '') == 'grand-bell'
        assert llamadas == []

    async def test_sin_ref_el_resolver_sigue_corriendo(self, monkeypatch) -> None:
        llamadas: list[str] = []

        async def _spy(zona):
            llamadas.append(zona)
            return 'city-bell'

        monkeypatch.setattr(apify, '_argenprop_resolve_zona_slug', _spy)
        f = _filters()
        assert await apify._resolve_argenprop_zona(f, f.zona or '') == 'city-bell'
        assert llamadas == ['Grand Bell, City Bell, La Plata']


class TestLaRefNoDesactivaElFiltroDeAlias:
    def test_los_alias_siguen_filtrando_con_ref_nativa(self) -> None:
        """Cinturón y tiradores. Una ref confirmada acota del lado del portal,
        pero el portal puede servir de más igual (ZonaProp lee un slug
        compuesto como UNIÓN y mete el partido entero). El filtro de salida es
        lo único que garantiza que ensanchar la consulta no ensanche la
        respuesta, así que corre siempre."""
        from app.models.property import RawProperty

        props = [
            RawProperty(fuente='argenprop', titulo='Casa en Grand Bell',
                        direccion='Grand Bell', precio=1.0, tipo_operacion='venta'),
            RawProperty(fuente='argenprop', titulo='Casa en City Bell centro',
                        direccion='Calle 470', precio=1.0, tipo_operacion='venta'),
        ]
        assert [p.titulo for p in apify._keep_barrio_cerrado(props, ['grand bell'])] == [
            'Casa en Grand Bell']
