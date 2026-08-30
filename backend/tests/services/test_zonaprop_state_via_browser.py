"""Ask the browser for the object, not for the HTML that contains it.

Production, with the Cloudflare fallback working:

    desafiado por Cloudflare — lo intento con navegador
    resuelto con navegador
    WARNING ... no trajo __PRELOADED_STATE__ (934 KB)

The challenge WAS solved — 934 KB of real page, not the 5 KB interstitial —
but scraping the marker out of `page.content()` found nothing, while the same
call locally returns 1361 KB with it. Serialised DOM is not a reliable place
to look for a value the page assigned to `window`: hydration rewrites the
document, and `content()` returns whatever it looks like at that instant.

The browser is holding the object. Asking JavaScript for it removes the
marker, the brace matching and the timing in one go.
"""
from typing import Any


from app.services.apify import _read_zonaprop_state_from_page


class _Page:
    """The slice of Playwright's Page that this code touches."""

    def __init__(self, state: Any, *, appears: bool = True) -> None:
        self._state = state
        self._appears = appears
        self.waited_for: list[str] = []

    async def wait_for_function(self, expr: str, **kw: Any) -> None:
        self.waited_for.append(expr)
        if not self._appears:
            raise TimeoutError('nunca aparecio')

    async def evaluate(self, expr: str) -> Any:
        return self._state


class TestItAsksJavaScript:
    async def test_it_returns_the_state_object(self):
        state = await _read_zonaprop_state_from_page(
            _Page({'listStore': {'paging': {'total': 422}}}))

        assert state == {'listStore': {'paging': {'total': 422}}}

    async def test_it_waits_for_the_value_not_a_fixed_delay(self):
        """A sleep races hydration; waiting on the value itself does not."""
        page = _Page({'listStore': {}})

        await _read_zonaprop_state_from_page(page)

        assert any('__PRELOADED_STATE__' in e for e in page.waited_for)

    async def test_a_page_that_never_defines_it_returns_none(self):
        assert await _read_zonaprop_state_from_page(
            _Page(None, appears=False)) is None

    async def test_a_non_mapping_is_rejected(self):
        """`evaluate` hands back whatever JS held; only a mapping is usable."""
        assert await _read_zonaprop_state_from_page(_Page('not an object')) is None

    async def test_an_empty_object_is_rejected(self):
        assert await _read_zonaprop_state_from_page(_Page({})) is None
