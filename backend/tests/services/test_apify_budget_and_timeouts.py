"""Bounds on what one ZonaProp search may cost in money and in time.

Both defaults were unbounded in ways that hurt in production:

* `ZONAPROP_MAX_RESULTS = 0` meant "paginate until the portal runs dry", and
  every page is a separate PAID actor run with a browser cold start. A single
  City Bell search was still walking pages 21 minutes in, with the UI showing
  nothing but a spinner.
* The HTTP client used a flat 30s for every Apify API call. A `ReadTimeout`
  mid-pagination killed a run that had already scraped (and been billed for)
  30 listings.
"""
import httpx

from app.core.config import settings
from app.services.apify import _HTTP_TIMEOUT, ApifyService


def test_zonaprop_is_capped_by_default() -> None:
    """A cap the operator never set is the one that has to be sane."""
    assert settings.ZONAPROP_MAX_RESULTS == 200


def test_the_cap_is_a_whole_number_of_pages_worth_of_headroom() -> None:
    """`_scrape_zonaprop_paginated` turns the cap into a page ceiling with
    `ceil(cap / 30)`; 200 buys 7 pages, deep enough for a barrio and shallow
    enough to finish while someone waits."""
    pages = -(-settings.ZONAPROP_MAX_RESULTS // ApifyService._ZP_PAGE_SIZE)
    assert pages == 7


def test_http_timeout_outlasts_a_slow_dataset_fetch() -> None:
    """30s was the old flat value and it was killing live runs mid-scrape."""
    assert _HTTP_TIMEOUT > 30


def test_the_client_uses_that_timeout() -> None:
    service = ApifyService(api_token='dummy-token')
    assert isinstance(service._client, httpx.AsyncClient)
    assert service._client.timeout.read == _HTTP_TIMEOUT


def test_connect_stays_short() -> None:
    """A host that is down should fail fast — only READING a big dataset
    deserves the long leash."""
    service = ApifyService(api_token='dummy-token')
    assert service._client.timeout.connect is not None
    assert service._client.timeout.connect <= 15
