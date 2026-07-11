"""ZonaProp redirects unknown zona slugs to a nationwide listing — the guard
`_item_matches_zona` must drop scraped items that never mention the requested
zona (in neighborhood, address, title or description).

Signature is `Iterable[str]` (T-5.1/ADR-1: phrase-SET, union of barrios ∪
localidad) — callers pass a set/list of phrases, an item is kept if the slug
of ANY phrase matches. The chat path (no localidades) passes a single-item
set, preserving today's behavior exactly.
"""
from app.services.apify import _item_matches_zona


def test_matches_via_neighborhood() -> None:
    item = {'neighborhood': 'Monte Grande', 'address': 'Las Heras 100'}
    assert _item_matches_zona(item, {'Monte Grande'}) is True


def test_matches_via_description() -> None:
    item = {'neighborhood': None, 'description': 'Hermoso PH en Monte Grande, cerca de la estación'}
    assert _item_matches_zona(item, {'Monte Grande'}) is True


def test_rejects_off_zona_item() -> None:
    item = {
        'neighborhood': 'Villa Catella',
        'address': 'Calle 124',
        'title': 'Casa en Ensenada',
        'description': 'Magnífica casa en venta sobre la Calle 125 en Ensenada.',
    }
    assert _item_matches_zona(item, {'Monte Grande'}) is False


def test_accent_insensitive() -> None:
    item = {'neighborhood': 'Núñez'}
    assert _item_matches_zona(item, {'Nunez'}) is True


def test_empty_zona_keeps_everything() -> None:
    assert _item_matches_zona({'neighborhood': 'Villa Catella'}, set()) is True


def test_multiword_zona_not_split() -> None:
    # 'Monte Grande' must match as a phrase — a lone 'Grande' elsewhere is not enough
    item = {'description': 'Casa grande en Villa Elisa con monte al fondo'}
    assert _item_matches_zona(item, {'Monte Grande'}) is False


# ── phrase-SET signature (T-5.1): Iterable[str], ANY match keeps the item ────

def test_phrase_set_any_match_keeps_item() -> None:
    item = {'description': 'Hermoso PH en Chacarita, cerca de la estación'}
    assert _item_matches_zona(item, {'Palermo', 'Chacarita', 'CABA'}) is True


def test_phrase_set_no_match_drops_item() -> None:
    item = {'neighborhood': 'Mendoza', 'address': 'Calle 9', 'title': '', 'description': ''}
    assert _item_matches_zona(item, {'Palermo', 'Chacarita', 'CABA'}) is False


def test_single_phrase_set_matches_today_chat_path_behavior() -> None:
    item = {'neighborhood': 'Monte Grande', 'address': 'Las Heras 100'}
    assert _item_matches_zona(item, {'Monte Grande'}) is True


def test_empty_phrase_set_keeps_everything() -> None:
    assert _item_matches_zona({'neighborhood': 'Villa Catella'}, set()) is True


def test_phrase_set_as_list_also_works() -> None:
    item = {'description': 'Depto en Belgrano'}
    assert _item_matches_zona(item, ['Palermo', 'Belgrano']) is True


# ── composite "Localidad, Partido" phrases: ALL comma-parts must appear, and
# the item's `city` field (ZonaProp = partido) counts toward the haystack. ────

def test_composite_phrase_matches_when_city_field_has_partido() -> None:
    item = {'neighborhood': 'Villa Elisa', 'city': 'La Plata', 'address': 'Calle 425 e/ 135 y 136'}
    assert _item_matches_zona(item, {'Villa Elisa, La Plata'}) is True


def test_composite_phrase_rejects_same_name_in_other_province() -> None:
    # Villa Elisa, Entre Ríos — mentions the localidad but never the partido
    item = {
        'neighborhood': 'Villa Elisa', 'city': 'Villa Elisa',
        'title': '2 casas en venta en Villa Elisa E.R.',
        'description': 'Casa en Villa Elisa, Colón, Entre Ríos.',
    }
    assert _item_matches_zona(item, {'Villa Elisa, La Plata'}) is False


def test_composite_phrase_matches_via_description_text() -> None:
    item = {'description': 'Venta de casa en Villa Elisa, La Plata. Amplio jardín.'}
    assert _item_matches_zona(item, {'Villa Elisa, La Plata'}) is True


def test_plain_phrase_still_matches_via_city_field() -> None:
    item = {'neighborhood': None, 'city': 'La Plata', 'address': 'Calle 7 n 1234'}
    assert _item_matches_zona(item, {'La Plata'}) is True


# ── guard phrase-set ASSEMBLY (verify CRITICAL-1): chat path must stay scoped
# to the branch's own zona; only the map path (localidades present) unions. ──

def test_guard_phrases_chat_path_scoped_to_branch_zona() -> None:
    from app.models.property import ScrapingFilters
    from app.services.apify import _guard_phrases
    f = ScrapingFilters(zonas=['Palermo', 'Belgrano'], zona='Palermo')
    assert _guard_phrases(f) == {'Palermo'}


def test_guard_phrases_map_path_unions_zonas_and_localidades() -> None:
    from app.models.property import ScrapingFilters
    from app.services.apify import _guard_phrases
    f = ScrapingFilters(
        zonas=['Barrio Montana', 'El Jagüel'], zona='Monte Grande',
        localidades=['Monte Grande'],
    )
    assert _guard_phrases(f) == {'Barrio Montana', 'El Jagüel', 'Monte Grande'}


def test_guard_phrases_empty_zona_chat_path() -> None:
    from app.models.property import ScrapingFilters
    from app.services.apify import _guard_phrases
    f = ScrapingFilters(zona=None)
    assert _guard_phrases(f) == set()
