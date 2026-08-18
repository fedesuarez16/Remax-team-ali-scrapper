"""Gallery recovery is decoupled from the text-enrichment gate.

A ficha whose gallery fetch was swallowed by a transient portal failure (WAF
challenge, timeout) at scrape time gets locked on the lone search-feed thumbnail:
`ficha_enriched` is set by the *text* pass, so the old `if not ficha_enriched`
guard meant the gallery was never re-fetched. `enrich_ficha` now re-attempts the
gallery whenever the stored one is still incomplete (<=1 image), independently of
that text gate — and skips the fetch entirely for a healthy gallery, so repeat
ficha opens cost nothing. This pins that behaviour without hitting the network.
"""
from __future__ import annotations

import app.services.ficha as ficha
from app.services.ficha import _gallery_looks_incomplete, enrich_ficha


def test_gallery_looks_incomplete_only_for_zero_or_one_image() -> None:
    assert _gallery_looks_incomplete({'imagenes': []}) is True
    assert _gallery_looks_incomplete({'imagenes': ['a']}) is True
    assert _gallery_looks_incomplete({}) is True
    assert _gallery_looks_incomplete({'imagenes': ['a', 'b']}) is False


async def test_enriched_ficha_stuck_on_thumbnail_recovers_gallery(monkeypatch) -> None:
    full = [f'https://cdn/{i}.jpg' for i in range(19)]

    async def fake_fetch(prop):
        return full

    monkeypatch.setattr(ficha, '_fetch_full_gallery', fake_fetch)

    # Already text-enriched, but carrying only the feed thumbnail.
    prop = {
        'id': None,  # sb is None below, so nothing persists — pure in-memory check
        'fuente': 'zonaprop',
        'ficha_enriched': True,
        'imagenes': ['https://cdn/thumb.jpg?isFirstImage=true'],
    }

    out = await enrich_ficha(prop, sb=None)

    # Thumbnail kept first, full gallery merged in behind it.
    assert out['imagenes'][0] == 'https://cdn/thumb.jpg?isFirstImage=true'
    assert set(full).issubset(set(out['imagenes']))
    assert len(out['imagenes']) == 20


async def test_healthy_gallery_is_never_refetched(monkeypatch) -> None:
    calls = {'n': 0}

    async def fake_fetch(prop):
        calls['n'] += 1
        return ['x']

    monkeypatch.setattr(ficha, '_fetch_full_gallery', fake_fetch)

    prop = {
        'id': None,
        'fuente': 'zonaprop',
        'ficha_enriched': True,
        'imagenes': ['a', 'b', 'c'],
    }

    await enrich_ficha(prop, sb=None)

    assert calls['n'] == 0
