"""Una búsqueda tiene un techo de gasto en Anthropic.

El loop de extracción corre una llamada a Haiku POR PÁGINA, y con 260
inmobiliarias el fan-in trae ~1500 páginas. Nadie fijaba ese número: lo fijaba
el tamaño de la zona. Medido en producción, una búsqueda venía costando ~USD 4
de tokens.

`llm_costs` ya sabía cuánto costaba cada llamada (`usage_cost_usd`) y ya la
anotaba (`record_llm_usage`), pero sólo contra la BASE — un ledger que nadie
consultaba durante la corrida. Servía para la factura de ayer, no para frenar
la de hoy. Esto le agrega el contador en memoria y el techo.

Mismo diseño que el tope de Apify, y por las mismas razones: contador en un
ContextVar instalado una vez por búsqueda (las tareas hijas heredan el mismo
dict, así que el fan-out entero suma en un solo lugar) y consulta ANTES de cada
llamada. Tope BLANDO: las llamadas ya en vuelo terminan.

La diferencia con Apify está en cómo se corta. Apify son pocos runs y ahí una
excepción por run se lee bien; acá son 1500 llamadas en `asyncio.gather`, y
1500 excepciones serían ruido, no información. Por eso el guard es una consulta
booleana: la página que encuentra el presupuesto agotado devuelve vacío y sale.
"""
import pytest

from app.services.llm_costs import (
    HAIKU_4_5,
    SCOPE_EXTRACT_WEBSITE,
    book_llm_cost,
    llm_budget_exhausted,
    llm_total_usd,
    record_llm_usage,
    use_llm_ledger,
)


@pytest.fixture()
def cap_one_usd(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'LLM_MAX_USD_PER_SEARCH', 1.0)


# ── El contador ───────────────────────────────────────────────────────────────

def test_el_ledger_suma_por_scope() -> None:
    """Por scope y no un total pelado: el loop de sitios web y el de Instagram
    comen del mismo dólar, y cuando se agota hay que poder decir cuál se lo
    llevó."""
    ledger: dict[str, float] = {}

    with use_llm_ledger(ledger):
        book_llm_cost(SCOPE_EXTRACT_WEBSITE, 0.4)
        book_llm_cost(SCOPE_EXTRACT_WEBSITE, 0.1)
        book_llm_cost('extract_instagram', 0.2)

    assert ledger == {'extract_website': 0.5, 'extract_instagram': 0.2}
    assert llm_total_usd(ledger) == pytest.approx(0.7)


def test_fuera_de_una_busqueda_anotar_no_explota() -> None:
    """Los caminos de CRM (ficha propio, enrich) llaman al mismo registro y
    corren sueltos, sin ledger instalado."""
    book_llm_cost(SCOPE_EXTRACT_WEBSITE, 0.4)  # no debe levantar


async def test_anotar_una_llamada_la_suma_al_ledger_aunque_no_haya_base() -> None:
    """`record_llm_usage` cortaba temprano con `sb=None`. Si el conteo en
    memoria viviera después de ese return, una instalación sin Supabase — o un
    test — gastaría sin tope y sin enterarse."""
    ledger: dict[str, float] = {}
    usage = {'input_tokens': 1_000_000, 'output_tokens': 0}

    with use_llm_ledger(ledger):
        await record_llm_usage(None, scope=SCOPE_EXTRACT_WEBSITE, model=HAIKU_4_5, usage=usage)

    # Haiku 4.5: USD 1.00 por millón de tokens de input.
    assert llm_total_usd(ledger) == pytest.approx(1.0)


# ── El techo ──────────────────────────────────────────────────────────────────

def test_con_presupuesto_disponible_no_corta(cap_one_usd: None) -> None:
    with use_llm_ledger({'extract_website': 0.4}):
        assert llm_budget_exhausted() is False


def test_alcanzado_el_techo_corta(cap_one_usd: None) -> None:
    with use_llm_ledger({'extract_website': 1.0}):
        assert llm_budget_exhausted() is True


def test_el_techo_mira_el_total_de_todos_los_scopes(cap_one_usd: None) -> None:
    """El presupuesto es de la BÚSQUEDA: parsear la query, extraer de sitios y
    extraer de Instagram salen del mismo dólar."""
    with use_llm_ledger({'search_parse': 0.05, 'extract_website': 0.6, 'extract_instagram': 0.4}):
        assert llm_budget_exhausted() is True


def test_un_tope_en_cero_significa_sin_tope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Misma convención que el resto de los knobs."""
    from app.core.config import settings
    monkeypatch.setattr(settings, 'LLM_MAX_USD_PER_SEARCH', 0.0)

    with use_llm_ledger({'extract_website': 99.0}):
        assert llm_budget_exhausted() is False


def test_fuera_de_una_busqueda_no_hay_tope(cap_one_usd: None) -> None:
    """Sin ledger instalado son los caminos de CRM, que no tienen contra qué
    acumular. Leer un ledger inexistente como presupuesto agotado los dejaría
    sin poder hacer una sola llamada."""
    assert llm_budget_exhausted() is False


def test_el_ledger_no_se_filtra_fuera_del_bloque(cap_one_usd: None) -> None:
    """Dos búsquedas en el mismo proceso no comparten presupuesto."""
    with use_llm_ledger({'extract_website': 5.0}):
        assert llm_budget_exhausted() is True

    assert llm_budget_exhausted() is False
