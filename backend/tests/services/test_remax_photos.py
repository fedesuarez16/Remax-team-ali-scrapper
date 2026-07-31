"""RE/MAX gallery extraction.

The listings API DOES return photos — verified live against
`api-ar.redremax.com/remaxweb-ar/api/listings/findAllWithEntrepreneurships`,
each item carries:

    "photos": [{"rawValue": "listings/<listingId>/<photoId>"}, ...]

`rawValue` is a CDN path with no size segment and no extension. The real
browsable URL, lifted from the rendered ficha of that same listing, inserts a
size segment between the listing directory and the photo id:

    https://d1acdg20u0pmxj.cloudfront.net/listings/<listingId>/1080xAUTO/<photoId>.jpg

Verified with a live request: that URL 307s to the signed asset and resolves
`200 image/jpg`. `_norm_remax` used to hardcode `imagenes=[]`, so every RE/MAX
result reached the UI photoless even though the payload had the photos all
along.
"""
from app.services.apify import _norm_remax

_LISTING_ID = '359e2303-d971-4411-b530-3fa205740d47'

_REAL_ITEM = {
    'id': 610152,
    'operation': {'id': 1.0, 'value': 'sale'},
    'currency': {'id': 1.0, 'value': 'USD'},
    'type': {'id': 2.0, 'value': 'departamento_estandar'},
    'title': 'VENTA DEPTO 2 DORM EN FONTANAS DEL SUR',
    'slug': 'venta-depto-2-dorm-en-fontanas-del-sur',
    'totalRooms': 3,
    'bathrooms': 2,
    'price': 245000.0,
    'displayAddress': 'Avenida Asamblea 1400',
    'geoLabel': 'Parque Chacabuco, Capital Federal',
    'photos': [
        {'rawValue': f'listings/{_LISTING_ID}/24722cee-6b21-4bc4-afd5-52164f350e22'},
        {'rawValue': f'listings/{_LISTING_ID}/0db60610-4652-425b-bc99-fec7af2ba849'},
        {'rawValue': f'listings/{_LISTING_ID}/5f102734-5536-4744-bd9f-5d858d946b44'},
    ],
}

_CDN = 'https://d1acdg20u0pmxj.cloudfront.net'


def test_builds_cdn_urls_from_photos_raw_value() -> None:
    prop = _norm_remax(_REAL_ITEM, 'Parque Chacabuco')
    assert prop is not None
    assert prop.imagenes == [
        f'{_CDN}/listings/{_LISTING_ID}/1080xAUTO/24722cee-6b21-4bc4-afd5-52164f350e22.jpg',
        f'{_CDN}/listings/{_LISTING_ID}/1080xAUTO/0db60610-4652-425b-bc99-fec7af2ba849.jpg',
        f'{_CDN}/listings/{_LISTING_ID}/1080xAUTO/5f102734-5536-4744-bd9f-5d858d946b44.jpg',
    ]


def test_photo_order_is_preserved() -> None:
    prop = _norm_remax(_REAL_ITEM, 'Parque Chacabuco')
    assert prop is not None
    assert prop.imagenes[0].endswith('24722cee-6b21-4bc4-afd5-52164f350e22.jpg')


def test_missing_photos_field_yields_empty_gallery() -> None:
    item = {k: v for k, v in _REAL_ITEM.items() if k != 'photos'}
    prop = _norm_remax(item, 'Parque Chacabuco')
    assert prop is not None
    assert prop.imagenes == []


def test_null_photos_field_yields_empty_gallery() -> None:
    prop = _norm_remax({**_REAL_ITEM, 'photos': None}, 'Parque Chacabuco')
    assert prop is not None
    assert prop.imagenes == []


def test_entries_without_raw_value_are_skipped() -> None:
    item = {**_REAL_ITEM, 'photos': [
        {'rawValue': ''},
        {'value': 'something-else'},
        {'rawValue': f'listings/{_LISTING_ID}/aaaa'},
    ]}
    prop = _norm_remax(item, 'Parque Chacabuco')
    assert prop is not None
    assert prop.imagenes == [f'{_CDN}/listings/{_LISTING_ID}/1080xAUTO/aaaa.jpg']


def test_raw_value_without_directory_segment_is_skipped() -> None:
    # No "<dir>/<file>" split means no place to insert the size segment —
    # emitting a guessed URL would only produce broken <img> tags.
    prop = _norm_remax({**_REAL_ITEM, 'photos': [{'rawValue': 'orphan'}]}, 'z')
    assert prop is not None
    assert prop.imagenes == []


def test_absolute_raw_value_is_passed_through_untouched() -> None:
    url = 'https://cdn.example.com/already/absolute.jpg'
    prop = _norm_remax({**_REAL_ITEM, 'photos': [{'rawValue': url}]}, 'z')
    assert prop is not None
    assert prop.imagenes == [url]


def test_gallery_is_capped_at_twenty_photos() -> None:
    item = {**_REAL_ITEM, 'photos': [
        {'rawValue': f'listings/{_LISTING_ID}/photo-{i}'} for i in range(40)
    ]}
    prop = _norm_remax(item, 'Parque Chacabuco')
    assert prop is not None
    assert len(prop.imagenes) == 20
