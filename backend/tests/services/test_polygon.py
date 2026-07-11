"""Test-first for `point_in_polygon` (T-2.1/2.2) — pure ray-cast, no external
deps. Written BEFORE `app/services/polygon.py` exists, so this file MUST fail
on collection/import until T-2.2 lands."""
from app.services.polygon import point_in_polygon

# A simple 4-point square around Palermo-ish coords: lat in [-34.60,-34.58],
# lng in [-58.44,-58.42]. Polygon points are [lat, lng] pairs (matches the
# `[[lat,lng],...]` shape persisted on `scraping_jobs.polygon`).
_SQUARE = [
    [-34.60, -58.44],
    [-34.60, -58.42],
    [-34.58, -58.42],
    [-34.58, -58.44],
]


def test_point_inside_square_is_true() -> None:
    assert point_in_polygon(-34.59, -58.43, _SQUARE) is True


def test_point_outside_square_is_false() -> None:
    assert point_in_polygon(-34.50, -58.30, _SQUARE) is False


def test_point_far_outside_is_false() -> None:
    assert point_in_polygon(0.0, 0.0, _SQUARE) is False


def test_point_on_edge_does_not_raise_and_is_bool() -> None:
    result = point_in_polygon(-34.60, -58.43, _SQUARE)
    assert isinstance(result, bool)


def test_empty_polygon_returns_false() -> None:
    assert point_in_polygon(-34.59, -58.43, []) is False


def test_polygon_with_fewer_than_three_points_returns_false() -> None:
    assert point_in_polygon(-34.59, -58.43, [[-34.60, -58.44], [-34.58, -58.42]]) is False


def test_malformed_polygon_never_raises() -> None:
    malformed = [[-34.60], [None, None], 'not-a-point']  # type: ignore[list-item]
    assert point_in_polygon(-34.59, -58.43, malformed) is False


def test_none_polygon_returns_false() -> None:
    assert point_in_polygon(-34.59, -58.43, None) is False  # type: ignore[arg-type]


def test_triangle_inside_point() -> None:
    triangle = [[-34.60, -58.44], [-34.58, -58.44], [-34.59, -58.40]]
    assert point_in_polygon(-34.59, -58.42, triangle) is True


def test_triangle_outside_point() -> None:
    triangle = [[-34.60, -58.44], [-34.58, -58.44], [-34.59, -58.40]]
    assert point_in_polygon(-34.70, -58.60, triangle) is False
