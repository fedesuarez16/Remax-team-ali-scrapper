"""Production must state which proxy it is actually using.

Same commit, same 8-attempt budget, same search: locally the first request
returns 200 and the run yields 257 properties; in production all EIGHT
attempts return 403 and the run yields zero. At the ~20% block rate measured
locally, eight failures in a row is a 1-in-400,000 event — so production is
not drawing from the same pool.

The one input that can differ is `SCRAPER_PROXY_URL`, which lives in Railway
and cannot be read from here. So the scraper says what it is using: host and
the username OPTIONS (`groups-RESIDENTIAL`, `country-AR`, …), which is what
decides the exit IP — and never the password.
"""
import logging

import pytest

from app.services.apify import _proxy_fingerprint


async def _noop(*_a: object) -> None:
    return None


class TestItNamesWhatDecidesTheExitIp:
    def test_the_options_are_shown(self):
        fp = _proxy_fingerprint(
            'http://groups-RESIDENTIAL,country-AR:s3cr3t@proxy.apify.com:8000')
        assert 'groups-RESIDENTIAL' in fp
        assert 'country-AR' in fp

    def test_the_host_is_shown(self):
        assert 'proxy.apify.com:8000' in _proxy_fingerprint(
            'http://user:pw@proxy.apify.com:8000')

    def test_a_missing_country_is_visible_by_its_absence(self):
        """THE case under suspicion: without `country-AR` the exit IP can be
        any country, and ZonaProp blocks far more aggressively."""
        fp = _proxy_fingerprint('http://groups-RESIDENTIAL:pw@proxy.apify.com:8000')
        assert 'country-AR' not in fp
        assert 'groups-RESIDENTIAL' in fp


class TestItNeverLeaksTheSecret:
    @pytest.mark.parametrize('url', [
        'http://groups-RESIDENTIAL,country-AR:apify_proxy_S3CRET@proxy.apify.com:8000',
        'http://user:S3CRET@proxy.local:3128',
    ])
    def test_the_password_is_absent(self, url: str):
        assert 'S3CRET' not in _proxy_fingerprint(url)

    def test_no_proxy_says_so(self):
        assert 'sin proxy' in _proxy_fingerprint('')
        assert 'sin proxy' in _proxy_fingerprint(None)


class TestItIsSaidOncePerSearch:
    async def test_the_scraper_logs_it(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        import httpx

        from app.core.config import settings
        from app.models.property import ScrapingFilters
        from app.services.apify import _scrape_zonaprop_direct

        monkeypatch.setattr(
            settings, 'SCRAPER_PROXY_URL',
            'http://groups-RESIDENTIAL,country-AR:pw@proxy.apify.com:8000')

        class _Client:
            def __init__(self, *a, **k) -> None: pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def get(self, url, *a, **k):
                return httpx.Response(403, text='no', request=httpx.Request('GET', url))

        monkeypatch.setattr(httpx, 'AsyncClient', _Client)

        with caplog.at_level(logging.INFO, logger='app.services.apify'):
            await _scrape_zonaprop_direct(
                ScrapingFilters(zona='La Plata', zona_pedida='La Plata'), _noop)

        blob = ' '.join(r.getMessage() for r in caplog.records)
        assert 'proxy=' in blob
        assert 'country-AR' in blob
