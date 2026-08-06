"""Test-first para `cleaner.check_links` — verificar una LISTA pegada a mano.

Caso de uso: pegué 15 links que le mandé a un cliente hace un mes y quiero
saber cuáles siguen funcionando antes de reenviarlos.

A diferencia de `run_cleanup`, esto NO toca la base: no borra, no escribe, no
necesita Supabase. Es sólo verificación y clasificación.

El resultado son las dos listas pedidas (`activos` / `rotos`) más una tercera,
`sin_definir`, que existe por la misma razón que el veredicto es ternario: un
portal que nos bloquea con 403 no vuelve roto al link. Meter esos en `rotos`
haría que el usuario descarte links perfectamente vivos.
"""
from __future__ import annotations

import pytest

from app.services import cleaner
from app.services.cleaner import CheckResult


def _verdicts(mapping: dict[str, str]):
    async def checker(url: str, *, client: object) -> CheckResult:
        verdict = mapping.get(url, 'alive')
        return CheckResult(verdict=verdict, reason=f'fake:{verdict}')

    return checker


def _urls(bucket: list[dict]) -> list[str]:
    return [item['url'] for item in bucket]


# ── clasificación ────────────────────────────────────────────────────────────


async def test_splits_into_active_and_broken() -> None:
    result = await cleaner.check_links(
        ['https://portal.com/viva', 'https://portal.com/muerta'],
        checker=_verdicts({'https://portal.com/muerta': 'dead'}),
    )

    assert _urls(result['activos']) == ['https://portal.com/viva']
    assert _urls(result['rotos']) == ['https://portal.com/muerta']


async def test_unverifiable_links_are_not_reported_as_broken() -> None:
    """Un 403 del portal no vuelve roto al link: iría a la basura un link vivo."""
    result = await cleaner.check_links(
        ['https://portal.com/bloqueada'],
        checker=_verdicts({'https://portal.com/bloqueada': 'unknown'}),
    )

    assert result['rotos'] == []
    assert _urls(result['sin_definir']) == ['https://portal.com/bloqueada']


async def test_every_item_carries_the_reason() -> None:
    result = await cleaner.check_links(
        ['https://portal.com/muerta'],
        checker=_verdicts({'https://portal.com/muerta': 'dead'}),
    )

    assert result['rotos'][0]['motivo']


async def test_totals_are_reported() -> None:
    result = await cleaner.check_links(
        ['https://portal.com/a', 'https://portal.com/b', 'https://portal.com/c'],
        checker=_verdicts({'https://portal.com/b': 'dead', 'https://portal.com/c': 'unknown'}),
    )

    assert result['total'] == 3
    assert (len(result['activos']), len(result['rotos']), len(result['sin_definir'])) == (1, 1, 1)


async def test_input_order_is_preserved() -> None:
    urls = [f'https://portal.com/{i}' for i in range(6)]
    result = await cleaner.check_links(urls, checker=_verdicts({}))

    assert _urls(result['activos']) == urls


# ── higiene del input ────────────────────────────────────────────────────────


async def test_duplicates_are_checked_once() -> None:
    seen: list[str] = []

    async def checker(url: str, *, client: object) -> CheckResult:
        seen.append(url)
        return CheckResult('alive', 'ok')

    result = await cleaner.check_links(
        ['https://portal.com/a', 'https://portal.com/a', 'https://portal.com/a'],
        checker=checker,
    )

    assert seen == ['https://portal.com/a']
    assert result['total'] == 1


async def test_blank_lines_and_whitespace_are_dropped() -> None:
    result = await cleaner.check_links(
        ['  https://portal.com/a  ', '', '   ', '\n'], checker=_verdicts({}),
    )

    assert _urls(result['activos']) == ['https://portal.com/a']


async def test_a_bare_domain_gets_https_prepended() -> None:
    """Pegar desde WhatsApp suele traer el link sin esquema."""
    result = await cleaner.check_links(['www.zonaprop.com.ar/ficha-123.html'], checker=_verdicts({}))

    assert _urls(result['activos']) == ['https://www.zonaprop.com.ar/ficha-123.html']


async def test_text_that_is_not_a_url_counts_as_broken() -> None:
    """Es distinto de `sin_definir`: no hay nada que verificar acá, y el usuario
    igual tiene que sacarlo de la lista que le manda al cliente."""
    result = await cleaner.check_links(['hola que tal'], checker=_verdicts({}))

    assert _urls(result['rotos']) == ['hola que tal']
    assert result['sin_definir'] == []


async def test_malformed_input_never_reaches_the_network() -> None:
    called: list[str] = []

    async def checker(url: str, *, client: object) -> CheckResult:
        called.append(url)
        return CheckResult('alive', 'ok')

    await cleaner.check_links(['no soy un link'], checker=checker)

    assert called == []


async def test_an_empty_list_is_rejected() -> None:
    with pytest.raises(ValueError):
        await cleaner.check_links([])


async def test_a_list_of_only_blanks_is_rejected() -> None:
    with pytest.raises(ValueError):
        await cleaner.check_links(['', '   '])


async def test_too_many_links_are_rejected() -> None:
    with pytest.raises(ValueError):
        await cleaner.check_links([f'https://portal.com/{i}' for i in range(cleaner.MAX_LINKS + 1)])


async def test_the_cap_itself_is_accepted() -> None:
    urls = [f'https://portal.com/{i}' for i in range(cleaner.MAX_LINKS)]
    result = await cleaner.check_links(urls, checker=_verdicts({}))

    assert result['total'] == cleaner.MAX_LINKS


# ── aislamiento ──────────────────────────────────────────────────────────────


async def test_a_failing_check_degrades_to_unverifiable() -> None:
    async def exploding(url: str, *, client: object) -> CheckResult:
        raise RuntimeError('boom')

    result = await cleaner.check_links(['https://portal.com/a'], checker=exploding)

    assert _urls(result['sin_definir']) == ['https://portal.com/a']
    assert result['rotos'] == []


async def test_checking_a_list_does_not_touch_the_cleanup_state() -> None:
    """Verificar una lista pegada no es una corrida de limpieza de la base:
    no puede pisar los contadores que muestra la pantalla."""
    cleaner.reset_state()
    await cleaner.check_links(['https://portal.com/a'], checker=_verdicts({}))

    assert cleaner.cleanup_state()['checked'] == 0
