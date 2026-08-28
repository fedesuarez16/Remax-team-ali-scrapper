"""One out-of-range number must not cost the whole batch.

Real backend log:

    upsert collision: filas=420 ...
    ERROR property upsert failed (420 rows, job c557c4ec-...)
    postgrest.exceptions.APIError: {'message': 'numeric field overflow',
      'code': '22003', 'details': 'A field with precision 14, scale 2 must
      round to an absolute value less than 10^12.'}

Postgres rejects the INSERT as a whole, so a single garbage price — a mis-read
ARS figure, a parser slip — silently took 420 properties down with it. The
scrapers had already been paid for.

`properties` declares `precio numeric(14,2)`, `expensas`/`m2_total`/
`m2_cubiertos` `numeric(10,2)`, `confianza_extraccion numeric(4,3)` and
smallint counters. A value that cannot fit its column is not data; dropping
that FIELD costs one attribute, dropping the batch costs everything.
"""
import logging

import pytest

from app.graphs.extraction.nodes import _prop_to_dict
from app.models.property import NormalizedProperty


def _prop(**over) -> NormalizedProperty:
    data = dict(
        direccion='Calle 7 500', precio=380_000.0, moneda='USD',
        tipo_operacion='venta', tipo_propiedad='casa', fuente='zonaprop',
        url_origen='https://zp/1',
    )
    data.update(over)
    return NormalizedProperty(**data)


class TestPrecio:
    def test_a_sane_price_is_untouched(self):
        assert _prop_to_dict(_prop(precio=380_000.0), 'job')['precio'] == 380_000.0

    def test_an_overflowing_price_becomes_null(self):
        """numeric(14,2) tops out below 10^12."""
        row = _prop_to_dict(_prop(precio=1e13), 'job')
        assert row['precio'] is None

    def test_the_boundary_itself_is_rejected(self):
        assert _prop_to_dict(_prop(precio=1e12), 'job')['precio'] is None

    def test_just_under_the_boundary_survives(self):
        assert _prop_to_dict(_prop(precio=999_999_999_999.0), 'job')['precio'] is not None


class TestTheSmallerColumns:
    @pytest.mark.parametrize('field', ['m2_total', 'm2_cubiertos', 'expensas'])
    def test_numeric_10_2_tops_out_below_10_8(self, field):
        assert _prop_to_dict(_prop(**{field: 1e9}), 'job')[field] is None
        assert _prop_to_dict(_prop(**{field: 120.5}), 'job')[field] == 120.5

    @pytest.mark.parametrize('field', ['ambientes', 'banos', 'cocheras', 'piso', 'antiguedad'])
    def test_smallint_counters_are_bounded(self, field):
        assert _prop_to_dict(_prop(**{field: 999_999}), 'job')[field] is None
        assert _prop_to_dict(_prop(**{field: 3}), 'job')[field] == 3


class TestItIsSayable:
    def test_dropping_a_value_is_logged(self, caplog: pytest.LogCaptureFixture):
        """Silently nulling a price would be its own kind of data loss."""
        with caplog.at_level(logging.WARNING, logger='app.graphs.extraction.nodes'):
            _prop_to_dict(_prop(precio=1e13), 'job')

        blob = ' '.join(r.getMessage() for r in caplog.records)
        assert 'precio' in blob
        assert 'Calle 7 500' in blob

    def test_a_clean_row_logs_nothing(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger='app.graphs.extraction.nodes'):
            _prop_to_dict(_prop(), 'job')

        assert not caplog.records
