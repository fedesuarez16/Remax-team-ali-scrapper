EXTRACT_FILTERS_TOOL = {
    'name': 'extract_search_filters',
    'description': (
        'Extrae parámetros de búsqueda inmobiliaria del mercado argentino a partir '
        'de una consulta en lenguaje natural. Devuelve null en campos no mencionados.'
    ),
    'input_schema': {
        'type': 'object',
        'properties': {
            'zona': {'type': ['string', 'null'],
                     'description': 'Barrio o ciudad, ej: Palermo, Belgrano, La Plata'},
            'tipo_operacion': {'type': ['string', 'null'],
                               'enum': ['venta', 'alquiler', 'alquiler_temp', None]},
            'tipo_propiedad': {'type': ['string', 'null'],
                               'enum': ['departamento', 'casa', 'ph', 'local',
                                        'oficina', 'terreno', 'otro', None]},
            'precio_min': {'type': ['number', 'null'], 'description': 'USD'},
            'precio_max': {'type': ['number', 'null'], 'description': 'USD'},
            'ambientes_min': {'type': ['integer', 'null']},
            'ambientes_max': {'type': ['integer', 'null']},
            'm2_min': {'type': ['number', 'null']},
            'm2_max': {'type': ['number', 'null']},
        },
        'required': ['zona'],
    },
}

SYSTEM_PROMPT = (
    'Sos un parser de búsquedas inmobiliarias para el mercado argentino. '
    'Interpretás lenguaje informal (ej: "2 amb", "depto", "para alquilar"). '
    'Si el precio viene en pesos (ARS), dejá precio en null salvo que sea claro. '
    'Llamá SIEMPRE a la herramienta extract_search_filters. '
    'Si no podés identificar la zona, devolvé zona=null.'
)
