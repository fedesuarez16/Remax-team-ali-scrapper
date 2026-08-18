"""Splitting `.in_()` value lists so the request URL stays under the limit.

PostgREST serialises `.in_(col, values)` into the QUERY STRING, so a long list
becomes a long URL. Past roughly 39 KB of encoded parameter, Supabase answers:

    {'message': 'JSON could not be generated', 'code': 400,
     'details': "b'Bad Request'"}

Measured live by bisection: 635 typical addresses (~38 849 B) pass, 636
(~38 908 B) fail. That is a BYTE limit, not an item-count limit — 800 real
addresses (~30 KB) pass while 800 longer synthetic ones (~49 KB) do not, so
chunking has to budget encoded size, never `len(values)`.

This is what silently broke job bb382a74: `_fill_missing_images` and
`_link_job_properties` both raised it, both swallowed it into a warning, and
the run persisted 757 properties while writing only 30 links.
"""
import urllib.parse

import pytest

from app.core.database import IN_FILTER_MAX_BYTES, chunk_for_in_filter


def _encoded_size(values: list[str]) -> int:
    """Mirror how PostgREST encodes an `in.(...)` parameter."""
    return len(urllib.parse.quote(','.join(f'"{v}"' for v in values)))


ADDRESS = 'Calle 47 E/ 12 y 13 N 1234 Depto 5'


class TestEveryChunkFitsTheBudget:
    @pytest.mark.parametrize('count', [1, 50, 500, 2000, 10000])
    def test_no_chunk_exceeds_the_limit(self, count):
        values = [f'{ADDRESS} #{i}' for i in range(count)]
        for chunk in chunk_for_in_filter(values):
            assert _encoded_size(chunk) <= IN_FILTER_MAX_BYTES

    def test_budget_leaves_headroom_under_the_measured_ceiling(self):
        """~38.9 KB is where it broke; the budget must sit clearly below."""
        assert IN_FILTER_MAX_BYTES < 30_000

    def test_very_long_single_value_still_yields_a_chunk(self):
        """A value bigger than the budget cannot be split further — emit it
        alone rather than dropping it or looping forever."""
        huge = 'x' * (IN_FILTER_MAX_BYTES * 2)
        chunks = list(chunk_for_in_filter([huge]))
        assert chunks == [[huge]]


class TestNothingIsLostOrDuplicated:
    @pytest.mark.parametrize('count', [0, 1, 999, 5000])
    def test_concatenating_chunks_rebuilds_the_input(self, count):
        values = [f'{ADDRESS} #{i}' for i in range(count)]
        rebuilt = [v for chunk in chunk_for_in_filter(values) for v in chunk]
        assert rebuilt == values

    def test_empty_input_yields_no_chunks(self):
        assert list(chunk_for_in_filter([])) == []

    def test_no_empty_chunks(self):
        values = [f'{ADDRESS} #{i}' for i in range(3000)]
        assert all(chunk for chunk in chunk_for_in_filter(values))


class TestSizeNotCountDrivesTheSplit:
    def test_short_values_pack_more_per_chunk_than_long_ones(self):
        short = [f'{i}' for i in range(4000)]
        long = [f'{ADDRESS} muy larga con relleno #{i}' for i in range(4000)]
        assert len(list(chunk_for_in_filter(short))) < len(list(chunk_for_in_filter(long)))

    def test_a_small_list_stays_in_one_request(self):
        """The common case must not pay for extra round-trips."""
        assert len(list(chunk_for_in_filter([f'{ADDRESS} #{i}' for i in range(100)]))) == 1
