"""Behavior-lock tests for zona/address normalization.

Originally written against the CURRENT location
(`app.graphs.extraction.nodes`) so the extraction to `app.services.zona`
(T-1.2) was provably behavior-preserving. Now repointed to the extracted
module; assertions are unchanged from the pre-extraction version.
"""
from app.services.zona import normalize_address as _normalize_address, normalize_zona as _normalize_zona


def test_normalize_zona_plain() -> None:
    assert _normalize_zona('Palermo') == 'palermo'


def test_normalize_zona_strips_caba_suffix() -> None:
    assert _normalize_zona('Palermo, CABA') == 'palermo'


def test_normalize_zona_strips_buenos_aires_suffix_case_insensitive() -> None:
    assert _normalize_zona('palermo, Buenos Aires') == 'palermo'


def test_normalize_zona_strips_accents() -> None:
    assert _normalize_zona('Villa Pueyrredón') == 'villa pueyrredon'


def test_normalize_zona_iterative_suffix_strip() -> None:
    assert _normalize_zona('X, Capital Federal, Argentina') == 'x'


def test_normalize_zona_collapses_whitespace() -> None:
    assert _normalize_zona('  Palermo   Chico ') == 'palermo chico'


def test_normalize_address_basic() -> None:
    assert _normalize_address('Av. Pueyrredón 1234') == 'av. pueyrredon 1234'
