"""Test-first para `cleaner.delete_dead_links` — el paso que faltaba.

`check_links` sólo CLASIFICA: te dice qué links están rotos y ahí termina. El
usuario se quedaba con la lista de rotos en pantalla y ninguna forma de sacarlos
de la base. Esta función es el botón "borrar": recibe las urls que el usuario
eligió y elimina las propiedades correspondientes.

La invariante que ordena todo el archivo: **la lista que manda el front es una
INTENCIÓN, no una orden de borrado**. Antes de tocar una fila se vuelve a
verificar el aviso, y sólo un veredicto `dead` borra. Una lista vieja, un doble
click o un payload manipulado no pueden vaciar la base — exactamente la misma
razón por la que el veredicto del bot es ternario y no booleano.
"""
from __future__ import annotations

import pytest

from app.services import cleaner
from app.services.cleaner import CheckResult

from tests.services.test_cleaner_run import _FakeSupabase, _prop, _verdicts


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    cleaner.reset_state()


def _urls(bucket: list[dict]) -> list[str]:
    return [item.get('url') or item.get('url_origen') for item in bucket]


# ── sólo borra lo que vuelve a dar muerto ────────────────────────────────────


async def test_deletes_the_property_behind_a_dead_link() -> None:
    dead = _prop('https://portal.com/muerta')
    alive = _prop('https://portal.com/viva')
    sb = _FakeSupabase(properties=[dead, alive])

    result = await cleaner.delete_dead_links(
        sb,
        ['https://portal.com/muerta'],
        checker=_verdicts({'https://portal.com/muerta': 'dead'}),
    )

    assert _urls(result['eliminadas']) == ['https://portal.com/muerta']
    assert [r['id'] for r in sb.store('properties')] == [alive['id']]


async def test_a_link_that_came_back_alive_is_never_deleted() -> None:
    """La lista puede estar vieja: el aviso se republicó entre verificar y borrar."""
    row = _prop('https://portal.com/revivida')
    sb = _FakeSupabase(properties=[row])

    result = await cleaner.delete_dead_links(
        sb,
        ['https://portal.com/revivida'],
        checker=_verdicts({}),  # todo alive
    )

    assert result['eliminadas'] == []
    assert _urls(result['conservadas']) == ['https://portal.com/revivida']
    assert len(sb.store('properties')) == 1


async def test_an_unverifiable_link_is_never_deleted() -> None:
    """Un 403 del portal no prueba nada — la duda jamás borra."""
    row = _prop('https://portal.com/bloqueada')
    sb = _FakeSupabase(properties=[row])

    result = await cleaner.delete_dead_links(
        sb,
        ['https://portal.com/bloqueada'],
        checker=_verdicts({'https://portal.com/bloqueada': 'unknown'}),
    )

    assert result['eliminadas'] == []
    assert _urls(result['conservadas']) == ['https://portal.com/bloqueada']
    assert len(sb.store('properties')) == 1


async def test_a_link_that_is_not_in_the_base_is_reported_not_deleted() -> None:
    """Verificar acepta links sueltos; borrar sólo alcanza a lo que está guardado."""
    sb = _FakeSupabase(properties=[_prop('https://portal.com/otra')])

    result = await cleaner.delete_dead_links(
        sb,
        ['https://portal.com/nunca-scrapeada'],
        checker=_verdicts({'https://portal.com/nunca-scrapeada': 'dead'}),
    )

    assert result['no_encontradas'] == ['https://portal.com/nunca-scrapeada']
    assert result['eliminadas'] == []
    assert len(sb.store('properties')) == 1


async def test_deletes_every_row_sharing_the_dead_url() -> None:
    """El mismo aviso puede haber entrado dos veces: se van las dos filas."""
    a = _prop('https://portal.com/duplicada')
    b = _prop('https://portal.com/duplicada')
    sb = _FakeSupabase(properties=[a, b])

    result = await cleaner.delete_dead_links(
        sb,
        ['https://portal.com/duplicada'],
        checker=_verdicts({'https://portal.com/duplicada': 'dead'}),
    )

    assert len(result['eliminadas']) == 2
    assert sb.store('properties') == []


async def test_mixed_list_deletes_only_the_dead_ones() -> None:
    dead = _prop('https://portal.com/muerta')
    alive = _prop('https://portal.com/viva')
    blocked = _prop('https://portal.com/bloqueada')
    sb = _FakeSupabase(properties=[dead, alive, blocked])

    result = await cleaner.delete_dead_links(
        sb,
        [p['url_origen'] for p in (dead, alive, blocked)],
        checker=_verdicts({
            'https://portal.com/muerta': 'dead',
            'https://portal.com/bloqueada': 'unknown',
        }),
    )

    assert _urls(result['eliminadas']) == ['https://portal.com/muerta']
    assert sorted(_urls(result['conservadas'])) == [
        'https://portal.com/bloqueada',
        'https://portal.com/viva',
    ]
    assert sorted(r['url_origen'] for r in sb.store('properties')) == [
        'https://portal.com/bloqueada',
        'https://portal.com/viva',
    ]


# ── auditoría ────────────────────────────────────────────────────────────────


async def test_records_the_deletion_in_the_run_history() -> None:
    """Un borrado a mano deja el mismo rastro que una corrida del bot."""
    dead = _prop('https://portal.com/muerta', titulo='Depto Palermo')
    sb = _FakeSupabase(properties=[dead])

    await cleaner.delete_dead_links(
        sb,
        ['https://portal.com/muerta'],
        checker=_verdicts({'https://portal.com/muerta': 'dead'}),
    )

    runs = sb.store('cleanup_runs')
    assert len(runs) == 1
    run = runs[0]
    assert run['origen'] == 'manual'
    assert run['dry_run'] is False
    assert run['eliminadas_count'] == 1
    snapshot = run['eliminadas'][0]
    assert snapshot['titulo'] == 'Depto Palermo'
    assert snapshot['url_origen'] == 'https://portal.com/muerta'
    assert snapshot['motivo']


async def test_every_deleted_row_carries_the_reason() -> None:
    sb = _FakeSupabase(properties=[_prop('https://portal.com/muerta')])

    async def checker(url: str, *, client: object) -> CheckResult:
        return CheckResult('dead', 'la ficha dice "publicacion finalizada"')

    result = await cleaner.delete_dead_links(
        sb, ['https://portal.com/muerta'], checker=checker,
    )

    assert result['eliminadas'][0]['motivo'] == 'la ficha dice "publicacion finalizada"'


async def test_does_not_disturb_the_live_run_counters() -> None:
    """Borrar a mano no es una corrida del bot: el `/status` que sondea el front
    no puede quedar mostrando contadores de otra cosa."""
    sb = _FakeSupabase(properties=[_prop('https://portal.com/muerta')])

    await cleaner.delete_dead_links(
        sb,
        ['https://portal.com/muerta'],
        checker=_verdicts({'https://portal.com/muerta': 'dead'}),
    )

    assert cleaner.cleanup_state() == cleaner._blank_state()


# ── entradas inválidas ───────────────────────────────────────────────────────


async def test_rejects_an_empty_list() -> None:
    with pytest.raises(ValueError):
        await cleaner.delete_dead_links(_FakeSupabase(), [], checker=_verdicts({}))


async def test_rejects_more_than_the_cap() -> None:
    urls = [f'https://portal.com/{i}' for i in range(cleaner.MAX_LINKS + 1)]

    with pytest.raises(ValueError):
        await cleaner.delete_dead_links(_FakeSupabase(), urls, checker=_verdicts({}))


async def test_text_that_is_not_a_link_is_ignored_not_deleted() -> None:
    """`check_links` manda "no es un link válido" a rotos; acá no hay nada que borrar."""
    sb = _FakeSupabase(properties=[_prop('https://portal.com/viva')])

    result = await cleaner.delete_dead_links(
        sb, ['esto no es un link', 'https://portal.com/viva'], checker=_verdicts({}),
    )

    assert result['eliminadas'] == []
    assert 'esto no es un link' in result['no_encontradas']
    assert len(sb.store('properties')) == 1


async def test_without_supabase_it_deletes_nothing() -> None:
    result = await cleaner.delete_dead_links(None, ['https://portal.com/a'], checker=_verdicts({}))

    assert result['eliminadas'] == []
    assert result['error']


# ── resiliencia ──────────────────────────────────────────────────────────────


async def test_one_failing_delete_does_not_abort_the_rest() -> None:
    a = _prop('https://portal.com/muerta-a')
    b = _prop('https://portal.com/muerta-b')
    sb = _FakeSupabase(properties=[a, b])

    original_table = sb.table
    exploded: list[str] = []

    def table(name: str):
        query = original_table(name)
        if name != 'properties':
            return query
        original_delete = query.delete

        def delete():
            q = original_delete()
            original_execute = q.execute

            async def execute():
                # Sólo la primera fila explota; la segunda tiene que borrarse igual.
                if not exploded:
                    exploded.append('boom')
                    raise RuntimeError('delete exploded')
                return await original_execute()

            q.execute = execute  # type: ignore[method-assign]
            return q

        query.delete = delete  # type: ignore[method-assign]
        return query

    sb.table = table  # type: ignore[method-assign]

    result = await cleaner.delete_dead_links(
        sb,
        ['https://portal.com/muerta-a', 'https://portal.com/muerta-b'],
        checker=_verdicts({
            'https://portal.com/muerta-a': 'dead',
            'https://portal.com/muerta-b': 'dead',
        }),
    )

    assert len(result['eliminadas']) == 1
    assert len(sb.store('properties')) == 1
