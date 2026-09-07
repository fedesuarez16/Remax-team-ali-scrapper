"""Alvira loads its gallery from /fichalistarfotos, outside the initial HTML.

The response links originals and displays x100 thumbnails. Fixtures reproduce
that public markup without keeping the page's forms or captcha/session values.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import apify, ficha, importer

_URL = 'https://www.alvirapropiedades.com.ar/ficha/534935'
_ENDPOINT = 'https://www.alvirapropiedades.com.ar/fichalistarfotos?e=0&pid=534935'
_CDN = 'https://fotos55.inmobusqueda.com/2091/534935'
_ICONS = [
    'https://www.dedicado2.com/webib/templates/j/1417722156_twitter-24.png',
    'https://www.dedicado2.com/webib/templates/j/2017.Intsagram.png',
]


def _photos(count: int) -> list[str]:
    return [f'{_CDN}/534935_2091_photo{i}.jpg' for i in range(count)]


def _gallery_html(count: int) -> str:
    return '<div class="popup-gallery">' + ''.join(
        f'<a href="{photo}"><img src="{_CDN}/x100/{photo.rsplit("/", 1)[1]}"></a>'
        for photo in _photos(count)
    ) + '</div>'


@pytest.mark.parametrize('count', [1, 15, 17, 28])
async def test_fetches_every_original_from_the_gallery_endpoint(
    monkeypatch: pytest.MonkeyPatch, count: int,
) -> None:
    fetch = AsyncMock(return_value=(False, _gallery_html(count)))
    render = AsyncMock()
    actor = AsyncMock()
    monkeypatch.setattr(ficha, '_fetch_listing_html', fetch)
    monkeypatch.setattr(apify, 'render_page_html', render)
    monkeypatch.setattr(apify, 'fetch_page_html_via_actor', actor)

    images = await ficha.portal_gallery_from_url(_URL + '/?utm_source=whatsapp')

    assert images == _photos(count)
    fetch.assert_awaited_once_with(_ENDPOINT, None)
    render.assert_not_awaited()
    actor.assert_not_awaited()


async def test_excludes_icons_other_properties_and_duplicate_thumbnails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = _gallery_html(17) + _gallery_html(1) + ''.join(
        f'<a href="{url}"><img src="{url}"></a>' for url in [
            *_ICONS,
            'https://control.inmobusqueda.com.ar/fotos/logo/2091.jpg',
            'https://fotos55.inmobusqueda.com/2091/999999/other.jpg',
            f'{_CDN}/x100/thumb.jpg',
            'https://unrelated.example/2091/534935/photo.jpg',
            'javascript:alert(1)',
        ]
    )
    monkeypatch.setattr(ficha, '_fetch_listing_html', AsyncMock(return_value=(False, html)))

    assert await ficha.portal_gallery_from_url(_URL) == _photos(17)


@pytest.mark.parametrize('url', [
    'https://www.alvirapropiedades.com.ar/',
    'https://www.alvirapropiedades.com.ar/ficha/not-an-id',
    'https://www.alvirapropiedades.com.ar/ficha/534935/other',
    'https://www.alvirapropiedades.com.ar.example.com/ficha/534935',
    'https://another-agency.com.ar/ficha/534935',
])
async def test_only_queries_verified_alvira_listing_urls(
    monkeypatch: pytest.MonkeyPatch, url: str,
) -> None:
    fetch = AsyncMock()
    monkeypatch.setattr(ficha, '_fetch_listing_html', fetch)

    assert await ficha.portal_gallery_from_url(url) == []
    fetch.assert_not_awaited()


@pytest.mark.parametrize('gone, html', [(True, None), (False, '<title>No soy bot</title>')])
async def test_fast_recovery_does_not_escalate_when_the_endpoint_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, gone: bool, html: str | None,
) -> None:
    monkeypatch.setattr(ficha, '_fetch_listing_html', AsyncMock(return_value=(gone, html)))
    render = AsyncMock()
    actor = AsyncMock()
    monkeypatch.setattr(apify, 'render_page_html', render)
    monkeypatch.setattr(apify, 'fetch_page_html_via_actor', actor)

    assert await ficha.portal_gallery_from_url(_URL, allow_escalation=False) == []
    render.assert_not_awaited()
    actor.assert_not_awaited()


@pytest.mark.parametrize('count', [1, 17])
async def test_import_saves_the_property_gallery_even_when_junk_outnumbers_it(
    monkeypatch: pytest.MonkeyPatch, count: int,
) -> None:
    monkeypatch.setattr(importer, '_fetch_page', AsyncMock(return_value=(
        'Casa en alquiler en La Plata. ' * 30, [*_photos(1), *_ICONS],
    )))
    monkeypatch.setattr(importer, '_extract_llm', AsyncMock(return_value=({'titulo': 'Casa'}, None)))
    monkeypatch.setattr(importer, 'record_llm_usage', AsyncMock())
    monkeypatch.setattr(ficha, '_fetch_listing_html', AsyncMock(
        return_value=(False, _gallery_html(count)),
    ))
    harvest = AsyncMock(return_value={_URL: [*_photos(1), *_ICONS]})
    monkeypatch.setattr(importer, 'harvest_page_images', harvest)
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = (
        AsyncMock(return_value=SimpleNamespace(data=[]))
    )
    sb.table.return_value.insert.return_value.execute = AsyncMock(
        return_value=SimpleNamespace(data=[{'id': 'p1'}]),
    )

    await importer.import_property_from_url(sb, _URL)

    saved = sb.table.return_value.insert.call_args.args[0]
    assert saved['imagenes'] == _photos(count)
    harvest.assert_not_awaited()


async def test_an_already_enriched_manual_ficha_replaces_its_old_icons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ficha, '_fetch_listing_html', AsyncMock(
        return_value=(False, _gallery_html(17)),
    ))
    sb = MagicMock()
    sb.table.return_value.update.return_value.eq.return_value.execute = AsyncMock()
    prop = {
        'id': 'p1', 'fuente': 'manual', 'url_origen': _URL,
        'ficha_enriched': True, 'imagenes': [*_photos(1), *_ICONS],
    }

    await ficha.enrich_ficha(prop, sb)

    assert prop['imagenes'] == _photos(17)
    sb.table.return_value.update.assert_called_once_with({'imagenes': _photos(17)})
    assert not ficha._gallery_looks_incomplete(prop)
