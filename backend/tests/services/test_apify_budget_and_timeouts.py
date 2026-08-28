"""Bounds on what one ZonaProp search may cost in money and in time.

Both defaults were unbounded in ways that hurt in production:

* `ZONAPROP_MAX_RESULTS = 0` meant "paginate until the portal runs dry". The
  cost is no longer paid actor runs but proxy bandwidth and, above all, TIME:
  a La Plata search is 67 pages ≈ 87 MB and 4½ minutes of this portal alone,
  in parallel with six others.
* The HTTP client used a flat 30s for every Apify API call. A `ReadTimeout`
  mid-pagination killed a run that had already scraped (and been billed for)
  30 listings.
"""
import httpx
import pytest

from app.core.config import settings
from app.services.apify import _HTTP_TIMEOUT, ApifyService

# These exercise the Apify actor path, kept as the documented fallback
# (`ZONAPROP_USE_APIFY=true`). Production reads ZonaProp directly.
pytestmark = pytest.mark.usefixtures('apify_zonaprop')



def test_zonaprop_is_capped_by_default() -> None:
    """A cap the operator never set is the one that has to be sane."""
    assert settings.ZONAPROP_MAX_RESULTS == 800


def test_the_cap_is_a_whole_number_of_pages_worth_of_headroom() -> None:
    """The cap becomes a page ceiling via `ceil(cap / 30)`. 800 buys 27 pages
    — about 35 MB of residential proxy and ~110 s at the ~1.3 MB / ~4 s per
    page measured in production, against 87 MB and 4½ minutes uncapped."""
    pages = -(-settings.ZONAPROP_MAX_RESULTS // ApifyService._ZP_PAGE_SIZE)
    assert pages == 27


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
