"""The funnel must survive the failure it is most needed for.

`_run_actor` raises on a failed/aborted Apify run, on a non-2xx from the API,
on a missing token. Every one of those propagates out of the pagination loop,
`run_portal_scraper` catches it into `state['errors']`, and the UI renders a
bare "0 props" — the exact symptom of a search that found nothing legitimately.

Logging the funnel only on the happy path means the two are indistinguishable
from the logs. So the summary is emitted whatever happens, and the actor error
becomes the recorded `stop_reason`.
"""
from typing import Any

import pytest

from app.models.property import ScrapingFilters
from app.services.apify import ApifyService

# These exercise the Apify actor path, kept as the documented fallback
# (`ZONAPROP_USE_APIFY=true`). Production reads ZonaProp directly.
pytestmark = pytest.mark.usefixtures('apify_zonaprop')



@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token='dummy-token')


@pytest.fixture(autouse=True)
def no_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ZONAPROP_MAX_RESULTS', 0)


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='City Bell', tipo_operacion='venta',
                           tipos_propiedad=['departamento'],
                           precio_min=200000, precio_max=300000)


def _item(i: int) -> dict[str, Any]:
    return {
        'title': f'Departamento {i} en City Bell', 'url': f'https://z/{i}',
        'listingId': str(i), 'neighborhood': 'City Bell', 'city': 'La Plata',
        'address': f'Calle {i}', 'propertyType': 'apartment',
        'price': 250000, 'currency': 'USD',
    }


async def test_actor_failure_still_logs_the_funnel(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def boom(src: str, actor: str, input_data: dict) -> list:
        raise RuntimeError('Apify run abc123 ended with status FAILED')

    monkeypatch.setattr(service, '_run_actor', boom)

    with caplog.at_level('INFO', logger='app.services.apify'):
        with pytest.raises(RuntimeError):
            await service._scrape_zonaprop_paginated('actor', _filters())

    blob = ' '.join(r.getMessage() for r in caplog.records)
    assert 'zonaprop funnel' in blob
    assert 'stop=actor_error' in blob
    assert 'status FAILED' in blob


async def test_a_total_failure_still_propagates(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing was salvaged, so the exception must reach `run_portal_scraper`
    and be recorded in `state['errors']` — a broken run must never read as a
    clean "found nothing"."""
    async def boom(src: str, actor: str, input_data: dict) -> list:
        raise RuntimeError('boom')

    monkeypatch.setattr(service, '_run_actor', boom)

    with pytest.raises(RuntimeError, match='boom'):
        await service._scrape_zonaprop_paginated('actor', _filters())


async def test_pages_already_paid_for_survive_a_later_failure(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE data-loss bug, from a real run: page 1 returned 30 City Bell
    listings, page 2 hit a ReadTimeout, the exception propagated, and
    `run_portal_scraper` turned all 30 into `collected_properties: []`.

    Every page is a PAID actor run. Throwing away what already came back
    because a LATER page failed is the worst possible trade — a partial
    result is not a broken one."""
    calls: list[int] = []

    async def flaky(src: str, actor: str, input_data: dict) -> list:
        calls.append(1)
        if len(calls) == 1:
            return [_item(i) for i in range(1000, 1030)]
        raise RuntimeError('ReadTimeout')

    monkeypatch.setattr(service, '_run_actor', flaky)

    results, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert len(results) == 30
    assert funnel.stop_reason.startswith('actor_error')


async def test_the_partial_result_is_still_reported_as_a_failure(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Salvaging the data must not hide that the scrape was cut short — the
    page count is short and the log says why."""
    calls: list[int] = []

    async def flaky(src: str, actor: str, input_data: dict) -> list:
        calls.append(1)
        if len(calls) == 1:
            return [_item(i) for i in range(1000, 1030)]
        raise RuntimeError('ReadTimeout')

    monkeypatch.setattr(service, '_run_actor', flaky)

    with caplog.at_level('WARNING', logger='app.services.apify'):
        await service._scrape_zonaprop_paginated('actor', _filters())

    blob = ' '.join(r.getMessage() for r in caplog.records)
    assert 'stop=actor_error' in blob
    assert 'ReadTimeout' in blob


async def test_a_mid_pagination_failure_keeps_the_pages_already_counted(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Page 1 succeeded and page 2 blew up: the log must still show page 1's
    haul, so a partial scrape is not mistaken for a total failure."""
    calls: list[int] = []

    async def flaky(src: str, actor: str, input_data: dict) -> list:
        calls.append(1)
        if len(calls) == 1:
            return [_item(i) for i in range(1000, 1030)]
        raise RuntimeError('429 Too Many Requests')

    monkeypatch.setattr(service, '_run_actor', flaky)

    with caplog.at_level('INFO', logger='app.services.apify'):
        await service._scrape_zonaprop_paginated('actor', _filters())

    blob = ' '.join(r.getMessage() for r in caplog.records)
    assert 'kept=30' in blob
    assert 'stop=actor_error' in blob


async def test_happy_path_still_reports_its_own_stop_reason(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[int] = []

    async def short(src: str, actor: str, input_data: dict) -> list:
        calls.append(1)
        # Page 1 sets the page size; page 2 is short against it.
        return [_item(i) for i in range(1000 * len(calls), 1000 * len(calls) + 30)] \
            if len(calls) == 1 else [_item(i) for i in range(9000, 9012)]

    monkeypatch.setattr(service, '_run_actor', short)

    with caplog.at_level('INFO', logger='app.services.apify'):
        await service._scrape_zonaprop_paginated('actor', _filters())

    assert 'stop=short_page' in ' '.join(r.getMessage() for r in caplog.records)
