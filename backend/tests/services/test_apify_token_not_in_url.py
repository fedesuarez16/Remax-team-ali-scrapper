"""The Apify token must never ride in a URL.

`_run_actor` passed it as `?token=...`, so httpx put it in every error it
raised, and those reach the funnel's `stop_reason` and the log. A real backend
log contained:

    stop=actor_error: HTTPStatusError: Client error '403 Forbidden' for url
    'https://api.apify.com/v2/acts/crawlerbros~zonaprop-scraper/runs?token=apify_api_...'

The credential leaked because of how it was TRANSPORTED, so that is what
changes: Apify accepts `Authorization: Bearer`, which no error message
formats.
"""
from typing import Any

import httpx
import pytest

from app.services.apify import ApifyService

_TOKEN = 'apify_api_SECRET_DO_NOT_LEAK'


@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token=_TOKEN)


def test_the_client_carries_the_token_as_a_header(service: ApifyService) -> None:
    assert service._client.headers.get('authorization') == f'Bearer {_TOKEN}'


async def test_no_request_url_contains_the_token(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls: list[str] = []

    class _Resp:
        def __init__(self, payload: Any) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            return self._payload

    async def fake_post(url: str, **kw: Any) -> _Resp:
        urls.append(str(httpx.URL(url).copy_merge_params(kw.get('params') or {})))
        return _Resp({'data': {'id': 'run-1'}})

    async def fake_get(url: str, **kw: Any) -> _Resp:
        urls.append(str(httpx.URL(url).copy_merge_params(kw.get('params') or {})))
        if 'dataset' in url:
            return _Resp([])
        return _Resp({'data': {'status': 'SUCCEEDED', 'usageTotalUsd': 0.0}})

    monkeypatch.setattr(service._client, 'post', fake_post)
    monkeypatch.setattr(service._client, 'get', fake_get)
    monkeypatch.setattr('app.services.apify._POLL_INTERVAL', 0.0)

    await service._run_actor('zonaprop', 'actor~x', {'searchUrl': 'https://z/x'})

    assert urls, 'the fake transport was never exercised'
    for url in urls:
        assert _TOKEN not in url


async def test_an_http_error_message_stays_clean(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What actually leaked: the 403 that httpx formats with the full URL."""
    request = httpx.Request('POST', 'https://api.apify.com/v2/acts/x/runs')
    response = httpx.Response(403, request=request)

    async def boom(url: str, **kw: Any):
        raise httpx.HTTPStatusError('403 Forbidden', request=request, response=response)

    monkeypatch.setattr(service._client, 'post', boom)

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await service._run_actor('zonaprop', 'actor~x', {'searchUrl': 'https://z/x'})

    assert _TOKEN not in str(exc.value)
