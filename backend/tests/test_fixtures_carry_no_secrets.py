"""Captured fixtures must not smuggle credentials into the repo.

`tests/fixtures/zonaprop_city_bell.json` is real portal output, and real portal
output contains ZonaProp's own Google Static Maps key inside every posting's
`urlStaticMap`. It reached GitHub and tripped secret scanning — a third-party
key, nothing of ours to rotate, but published all the same.

Capturing live data is worth it: these tests fail against the portal's ACTUAL
shape rather than one I imagined. The cost is this guard.
"""
import pathlib
import re

import pytest

_FIXTURES = pathlib.Path(__file__).parent / 'fixtures'

# Deliberately broad: a fixture has no legitimate reason to hold any of these.
_SECRET = re.compile(
    r'AIza[0-9A-Za-z_-]{10,}'          # Google API key
    r'|sk-[A-Za-z0-9]{20,}'            # OpenAI-style
    r'|apify_api_[A-Za-z0-9]{10,}'     # Apify token
    r'|eyJ[A-Za-z0-9_-]{20,}\.'        # JWT
    r'|(?:api[_-]?key|access[_-]?token|client[_-]?secret)=[^&"\s]{8,}',
    re.I,
)


def _fixture_files() -> list[pathlib.Path]:
    return sorted(p for p in _FIXTURES.rglob('*') if p.is_file())


def test_there_are_fixtures_to_check() -> None:
    """A guard that silently checks nothing is worse than no guard."""
    assert _fixture_files()


@pytest.mark.parametrize(
    'path', _fixture_files(), ids=lambda p: p.name,
)
def test_no_credentials_in_fixture(path: pathlib.Path) -> None:
    hit = _SECRET.search(path.read_text(errors='ignore'))
    assert hit is None, (
        f'{path.name} parece llevar una credencial ({hit.group()[:12]}...). '
        'Los fixtures capturados en vivo tienen que limpiarse antes de commitear.'
    )
