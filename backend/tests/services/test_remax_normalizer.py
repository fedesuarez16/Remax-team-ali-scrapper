"""`_norm_remax` against a real item captured from
api-ar.redremax.com/remaxweb-ar/api/listings/findAllWithEntrepreneurships.
"""
from app.services.apify import _norm_remax

_REAL_ITEM = {
    'id': 610152,
    'entityId': '983c4b47-16f6-4f73-958f-dc26f4ce39b3',
    'operation': {'id': 1.0, 'value': 'sale'},
    'currency': {'id': 1.0, 'value': 'USD'},
    'listingStatus': {'id': 1.0, 'value': 'active'},
    'type': {'id': 2.0, 'value': 'departamento_estandar'},
    'title': 'EN VENTA 3 DORMIT C/DEP.COCH. FTE PQUE CHACABUCO',
    'slug': 'en-venta-3-dormit-c-dep-coch-fte-pque-chacabuco',
    'totalRooms': 5,
    'bathrooms': 2,
    'bedrooms': 3,
    'price': 245000.0,
    'expensesPrice': 247000.0,
    'displayAddress': 'Avenida Asamblea 1400',
    'dimensionLand': 0.0,
    'dimensionTotalBuilt': 114.47,
    'dimensionCovered': 103.38,
    'geoLabel': 'Parque Chacabuco, Capital Federal',
}


def test_maps_core_fields() -> None:
    prop = _norm_remax(_REAL_ITEM, 'Parque Chacabuco')
    assert prop is not None
    assert prop.fuente == 'remax'
    assert prop.precio == 245000.0
    assert prop.moneda == 'USD'
    assert prop.tipo_operacion == 'venta'
    assert prop.tipo_propiedad == 'departamento'
    assert prop.ambientes == 5
    assert prop.banos == 2
    assert prop.m2_total == 114.47
    assert prop.m2_cubiertos == 103.38
    assert prop.direccion == 'Avenida Asamblea 1400'


def test_builds_listing_detail_url_from_slug_and_id() -> None:
    prop = _norm_remax(_REAL_ITEM, 'Parque Chacabuco')
    assert prop is not None
    assert prop.url_origen == (
        'https://www.remax.com.ar/listing/en-venta-3-dormit-c-dep-coch-fte-pque-chacabuco-610152'
    )


def test_rent_operation_maps_to_alquiler() -> None:
    item = {**_REAL_ITEM, 'operation': {'id': 2.0, 'value': 'rent'}}
    prop = _norm_remax(item, 'Parque Chacabuco')
    assert prop is not None
    assert prop.tipo_operacion == 'alquiler'


def test_falls_back_to_geo_label_when_no_display_address() -> None:
    item = {**_REAL_ITEM, 'displayAddress': None}
    prop = _norm_remax(item, 'Parque Chacabuco')
    assert prop is not None
    assert prop.direccion == 'Parque Chacabuco, Capital Federal'


def test_unpriced_listing_is_skipped() -> None:
    item = {**_REAL_ITEM, 'price': None}
    assert _norm_remax(item, 'Parque Chacabuco') is None


def test_ph_type_maps_correctly() -> None:
    item = {**_REAL_ITEM, 'type': {'id': 12.0, 'value': 'ph'}}
    prop = _norm_remax(item, 'Parque Chacabuco')
    assert prop is not None
    assert prop.tipo_propiedad == 'ph'
