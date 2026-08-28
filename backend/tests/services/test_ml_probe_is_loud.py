"""A region probe that finds nothing must say so.

It silently falls back to the flat slug — the WRONG region, the one with 179
listings instead of 2202 and no working pagination. That fallback is the right
behaviour (inventing a region would be worse) but it has to be visible: a live
run fell back and the only clue was `fuera_de_zona=220` buried in the funnel,
which reads like a guard problem rather than a resolution failure.
"""
from typing import Any

import httpx
import pytest

from app.models.property import ScrapingFilters
from app.services import apify


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='La Plata', zona_pedida='La Plata',
                           tipo_operacion='venta', tipos_propiedad=['casa'])


def _serve(monkeypatch: pytest.MonkeyPatch, *, status: int = 200,
           body: str = '<html></html>') -> None:
    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None: pass
        async def __aenter__(self) -> '_Client': return self
        async def __aexit__(self, *a: Any) -> None: return None

        async def get(self, url: str, *a: Any, **k: Any) -> httpx.Response:
            return httpx.Response(status, text=body,
                                  request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx, 'AsyncClient', _Client)


async def _probe(monkeypatch: pytest.MonkeyPatch,
                 caplog: pytest.LogCaptureFixture, **kw: Any) -> str:
    _serve(monkeypatch, **kw)
    with caplog.at_level('INFO', logger='app.services.apify'):
        await apify._ml_resolve_region(_filters())
    return ' '.join(r.getMessage() for r in caplog.records)


async def test_an_empty_probe_is_reported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    blob = await _probe(monkeypatch, caplog)

    assert 'no pude resolver la region' in blob
    assert 'La Plata' in blob


async def test_a_blocked_probe_is_reported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """The anti-bot wall arrives as a 200, so it cannot be told from an empty
    zona by status alone — and it is the likelier of the two."""
    blob = await _probe(monkeypatch, caplog,
                        body='<html>account-verification</html>')

    assert 'no pude resolver la region' in blob


async def test_a_failing_probe_is_reported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    blob = await _probe(monkeypatch, caplog, status=403)

    assert 'no pude resolver la region' in blob


async def test_it_says_what_it_falls_back_to(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Naming the consequence is the point: the flat slug lands on the wrong
    region and cannot paginate."""
    blob = await _probe(monkeypatch, caplog)

    assert 'slug plano' in blob


async def test_a_successful_probe_does_not_warn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    body = ('<span class="ui-search-search-result__quantity-results">99 resultados</span>'
            '<ol class="ui-search-layout"><li class="ui-search-layout__item"></li></ol>')
    blob = await _probe(monkeypatch, caplog, body=body)

    assert 'no pude resolver' not in blob
    assert '→' in blob
