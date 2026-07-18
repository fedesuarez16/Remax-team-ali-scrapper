EXTRACT_FILTERS_TOOL = {
    'name': 'extract_search_filters',
    'description': (
        'Extrae parámetros de búsqueda inmobiliaria del mercado argentino a partir '
        'de una consulta en lenguaje natural. Devuelve null en campos no mencionados.'
    ),
    'input_schema': {
        'type': 'object',
        'properties': {
            'zonas': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': (
                    'TODAS las zonas mencionadas (barrios o ciudades). Una entrada por zona. '
                    'Ej: ["Los Hornos", "Tolosa", "City Bell"]. Si menciona una sola, devolvé una lista de un elemento.'
                ),
            },
            'tipo_operacion': {'type': ['string', 'null'],
                               'enum': ['venta', 'alquiler', 'alquiler_temp', None]},
            'tipos_propiedad': {
                'type': 'array',
                'items': {'type': 'string', 'enum': ['departamento', 'casa', 'ph', 'local', 'oficina', 'terreno', 'otro']},
                'description': 'Tipos de propiedad aceptados. Puede ser más de uno, ej: ["casa", "ph"]',
            },
            'precio_min': {'type': ['number', 'null'], 'description': 'USD'},
            'precio_max': {'type': ['number', 'null'], 'description': 'USD'},
            'ambientes_min': {
                'type': ['integer', 'null'],
                'description': (
                    'Mínimo de ambientes. Dormitorios NO son ambientes: "N dormitorios" '
                    'implica al menos N ambientes → ambientes_min=N y ambientes_max=null '
                    '(una casa de 3 dormitorios suele tener 4 o más ambientes).'
                ),
            },
            'ambientes_max': {
                'type': ['integer', 'null'],
                'description': (
                    'Máximo de ambientes. SOLO si el usuario fija un tope explícito '
                    '("hasta 3 ambientes", "máximo 3 amb", "como mucho 3"). '
                    'NUNCA lo infieras de "N ambientes" ni de "N dormitorios" a secas.'
                ),
            },
            'm2_min': {'type': ['number', 'null']},
            'm2_max': {'type': ['number', 'null']},
        },
        'required': ['zonas'],
    },
}

INSTAGRAM_EXTRACT_TOOL = {
    'name': 'extract_property_from_instagram',
    'description': (
        'Extrae datos estructurados de una propiedad inmobiliaria a partir del caption '
        'de un post de Instagram de una inmobiliaria argentina.'
    ),
    'input_schema': {
        'type': 'object',
        'properties': {
            'es_propiedad': {
                'type': 'boolean',
                'description': 'True si el post anuncia una propiedad inmobiliaria concreta',
            },
            'precio': {'type': ['number', 'null'], 'description': 'Precio numérico sin símbolo'},
            'moneda': {'type': ['string', 'null'], 'enum': ['USD', 'ARS', None]},
            'tipo_operacion': {'type': ['string', 'null'],
                               'enum': ['venta', 'alquiler', None]},
            'tipo_propiedad': {'type': ['string', 'null'],
                               'enum': ['departamento', 'casa', 'ph', 'local',
                                        'oficina', 'terreno', 'otro', None]},
            'ambientes': {'type': ['integer', 'null']},
            'banos': {'type': ['integer', 'null']},
            'cocheras': {'type': ['integer', 'null']},
            'piso': {'type': ['integer', 'null']},
            'expensas': {'type': ['number', 'null']},
            'm2': {'type': ['number', 'null']},
            'direccion_zona': {'type': ['string', 'null'],
                               'description': 'Dirección o barrio mencionado'},
            'amenities': {'type': 'array', 'items': {'type': 'string'},
                          'description': 'Ej: cochera, pileta, balcón'},
            'descripcion': {'type': ['string', 'null'],
                            'description': 'Descripción limpia sin precio ni dirección'},
        },
        'required': ['es_propiedad'],
    },
}

INSTAGRAM_SYSTEM_PROMPT = (
    'Sos un extractor de datos inmobiliarios de posts de Instagram argentinos. '
    'Analizás captions informales con abreviaturas (amb, dpto, m2, USD, $$) y emojis. '
    'Si el post no es sobre una propiedad concreta (ej: post institucional), devolvé es_propiedad=false. '
    'Siempre llamá a extract_property_from_instagram.'
)

SYSTEM_PROMPT = (
    'Sos un parser de búsquedas inmobiliarias para el mercado argentino. '
    'Interpretás lenguaje informal (ej: "2 amb", "depto", "para alquilar"). '
    'Dormitorios y ambientes son cosas distintas: "3 dormitorios" → ambientes_min=3, ambientes_max=null. '
    'Fijá ambientes_max únicamente si el usuario pone un tope explícito ("hasta", "máximo", "como mucho"). '
    'Si el precio viene en pesos (ARS), dejá precio en null salvo que sea claro. '
    'Llamá SIEMPRE a la herramienta extract_search_filters. '
    'Extraé TODAS las zonas mencionadas como una lista en "zonas" (una entrada por barrio o ciudad). '
    'Si no podés identificar ninguna zona, devolvé zonas=[]. '
    'Al extraer cada zona, normalizá el nombre con espacios correctos tal como aparece '
    'en portales inmobiliarios argentinos: "citybell" → "City Bell", '
    '"sanandrés" → "San Andrés", "villacrespo" → "Villa Crespo", etc.'
)
