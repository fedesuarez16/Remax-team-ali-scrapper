"""La tarjeta de inmobiliarias tiene que decir cuánto va a costar.

El selector llega con TODAS las inmobiliarias tildadas. Con 552 en pantalla,
apretar "Continuar" autoriza unos USD 9 de tokens y hasta USD 14 de Apify sin
que nada lo diga. El gasto no es el problema — gastar a ciegas sí.

Ninguna optimización de prompt compite con esto: tildar 50 en vez de 552 es 10x,
no 20%. Pero para que el operador pueda elegir, el número tiene que estar donde
se toma la decisión, no en la factura de fin de mes.

El evento lleva el costo POR SITIO y el cliente lo multiplica por lo que haya
tildado, así el número se actualiza mientras destilda sin volver al backend.
"""
import pytest

from app.core.config import settings
from app.graphs.extraction.nodes import _costo_estimado_por_sitio


@pytest.fixture(autouse=True)
def _base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'WEBSITE_USE_APIFY', False)
    monkeypatch.setattr(settings, 'WEBSITE_MAX_SUBPAGES', 5)
    monkeypatch.setattr(settings, 'WEBSITE_APIFY_MAX_PAGES', 5)


def test_un_sitio_cuesta_algo() -> None:
    """Si diera 0, el total siempre sería 0 y la tarjeta mentiría."""
    assert _costo_estimado_por_sitio() > 0


def test_mas_paginas_por_sitio_cuesta_mas(monkeypatch: pytest.MonkeyPatch) -> None:
    barato = _costo_estimado_por_sitio()
    monkeypatch.setattr(settings, 'WEBSITE_MAX_SUBPAGES', 15)

    assert _costo_estimado_por_sitio() > barato


def test_con_apify_cuesta_mas_que_sin_apify(monkeypatch: pytest.MonkeyPatch) -> None:
    """El actor se paga por página ADEMÁS de los tokens. Si el número no lo
    reflejara, encender Apify se leería como gratis."""
    directo = _costo_estimado_por_sitio()
    monkeypatch.setattr(settings, 'WEBSITE_USE_APIFY', True)

    assert _costo_estimado_por_sitio() > directo


def test_el_orden_de_magnitud_es_el_medido() -> None:
    """~USD 0.0034 por página de LLM, medido sobre el prompt y el texto reales.
    Con 6 páginas por sitio, un sitio ronda los 2 centavos. Un test flojo acá
    dejaría pasar un número inventado, que es peor que no mostrar ninguno."""
    por_sitio = _costo_estimado_por_sitio()

    assert 0.01 < por_sitio < 0.05


def test_552_sitios_dan_el_orden_que_medimos() -> None:
    """La cuenta que motivó todo esto."""
    total = _costo_estimado_por_sitio() * 552

    assert 5 < total < 20
