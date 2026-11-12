"""Test-first: los filtros de categoría de /properties aceptan varios valores.

El front manda `tipo_propiedad=casa,ph` (lista separada por comas) cuando el
usuario tilda más de una opción. Un solo valor sigue siendo un `eq`; varios
son un `in_`. Varias zonas se combinan con OR, y cada una conserva sus
exclusiones (La Plata NO incluye City Bell, pero City Bell tildada sí entra).
"""
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, data: Any, count: int | None = None) -> None:
        self.data = data
        self.count = count


class _Query:
    """Graba cada filtro aplicado en `log`; todas las ops devuelven self."""

    def __init__(self, log: dict[str, list[Any]]) -> None:
        self._log = log
        self._negate = False

    def _rec(self, op: str, *args: Any) -> '_Query':
        name = f'not_{op}' if self._negate else op
        self._negate = False
        self._log.setdefault(name, []).append(args)
        return self

    def select(self, *args: Any, **kwargs: Any) -> '_Query':
        return self

    def order(self, *args: Any, **kwargs: Any) -> '_Query':
        return self

    def range(self, *args: Any, **kwargs: Any) -> '_Query':
        return self

    def eq(self, column: str, value: Any) -> '_Query':
        return self._rec('eq', column, value)

    def in_(self, column: str, values: list[str]) -> '_Query':
        return self._rec('in_', column, list(values))

    def or_(self, expr: str) -> '_Query':
        return self._rec('or_', expr)

    def ilike(self, column: str, pattern: str) -> '_Query':
        return self._rec('ilike', column, pattern)

    def gte(self, *args: Any) -> '_Query':
        return self

    def lte(self, *args: Any) -> '_Query':
        return self

    def is_(self, *args: Any) -> '_Query':
        return self

    @property
    def not_(self) -> '_Query':
        self._negate = True
        return self

    async def execute(self) -> _Res:
        return _Res([], 0)


class _FakeSupabase:
    def __init__(self) -> None:
        self.log: dict[str, list[Any]] = {}

    def table(self, name: str) -> _Query:
        return _Query(self.log)


async def _get(params: dict[str, str]) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    from app.api.v1 import properties

    app = FastAPI()
    app.include_router(properties.router, prefix='/properties')
    fake = _FakeSupabase()
    app.state.supabase = fake
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        resp = await c.get('/properties', params=params)
    assert resp.status_code == 200
    return resp.json(), fake.log


async def test_single_value_stays_an_exact_match() -> None:
    _, log = await _get({'tipo_propiedad': 'casa'})

    assert log['eq'] == [('tipo_propiedad', 'casa')]
    assert 'in_' not in log


async def test_comma_list_becomes_in_filter() -> None:
    _, log = await _get({'tipo_propiedad': 'casa,ph', 'tipo_operacion': 'venta,alquiler'})

    assert ('tipo_propiedad', ['casa', 'ph']) in log['in_']
    assert ('tipo_operacion', ['venta', 'alquiler']) in log['in_']
    assert 'eq' not in log


async def test_blank_items_in_list_are_ignored() -> None:
    _, log = await _get({'fuente': 'zonaprop, ,'})

    assert log['eq'] == [('fuente', 'zonaprop')]


async def test_single_zona_keeps_its_exclusions() -> None:
    _, log = await _get({'zona': 'la_plata'})

    (expr,), = log['or_']
    assert 'direccion.ilike.*la plata*' in expr
    assert 'direccion.not.ilike.*city bell*' in expr


async def test_several_zonas_are_ored_each_with_its_own_exclusions() -> None:
    _, log = await _get({'zona': 'la_plata,city_bell'})

    (expr,), = log['or_']
    # La Plata sigue excluyendo City Bell dentro de SU grupo…
    assert 'and(or(direccion.ilike.*la plata*' in expr
    # …pero City Bell entra por su propio grupo del OR.
    assert 'direccion.ilike.*city bell*' in expr
    # Nada de exclusiones globales que maten a City Bell.
    assert 'not_ilike' not in log


async def test_unknown_zona_in_list_is_reported() -> None:
    body, _ = await _get({'zona': 'la_plata,atlantida'})

    assert body['error'] == 'zona desconocida: atlantida'
