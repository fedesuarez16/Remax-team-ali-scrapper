from __future__ import annotations

from datetime import datetime
from typing import Literal, Union

from pydantic import BaseModel, Field

Fuente = Literal['zonaprop', 'mercadolibre', 'googlemaps', 'instagram', 'argenprop', 'manual']
TipoOperacion = Literal['venta', 'alquiler', 'alquiler_temp']
TipoPropiedad = Literal['departamento', 'casa', 'ph', 'local', 'oficina', 'terreno', 'otro']
Moneda = Literal['USD', 'ARS']


class RawProperty(BaseModel):
    """Source-shaped payload from a scraper. Loose by design."""
    fuente: Fuente
    titulo: str | None = None
    direccion: str
    precio: float | None = None
    moneda: Moneda = 'USD'
    tipo_operacion: TipoOperacion | None = None
    tipo_propiedad: TipoPropiedad | None = None
    ambientes: int | None = None
    m2_total: float | None = None
    m2_cubiertos: float | None = None
    antiguedad: int | None = None
    amenities: list[str] = Field(default_factory=list)
    imagenes: list[str] = Field(default_factory=list)
    url_origen: str | None = None
    raw: dict = Field(default_factory=dict)


class NormalizedProperty(BaseModel):
    """Unified schema; 1:1 with the properties table columns."""
    titulo: str | None = None
    direccion: str
    direccion_norm: str | None = None
    precio: float | None = None
    moneda: Moneda = 'USD'
    tipo_operacion: TipoOperacion
    tipo_propiedad: TipoPropiedad = 'otro'
    ambientes: int | None = None
    m2_total: float | None = None
    m2_cubiertos: float | None = None
    antiguedad: int | None = None
    amenities: list[str] = Field(default_factory=list)
    imagenes: list[str] = Field(default_factory=list)
    fuente: Fuente
    url_origen: str | None = None
    confianza_extraccion: float = 0.8


class ScrapingFilters(BaseModel):
    """Parsed from natural language by Claude tool_use."""
    zona: str | None = None
    tipo_operacion: TipoOperacion | None = None
    tipo_propiedad: TipoPropiedad | None = None
    precio_min: float | None = None
    precio_max: float | None = None
    ambientes_min: int | None = None
    ambientes_max: int | None = None
    m2_min: float | None = None
    m2_max: float | None = None


class ScrapingJob(BaseModel):
    id: str
    query_raw: str
    zona: str | None = None
    fuentes: list[str] = Field(default_factory=list)
    filters: dict | None = None
    estado: Literal['pending', 'running', 'done', 'error'] = 'pending'
    prop_count: int = 0
    error_msg: str | None = None
    creado_at: datetime | None = None
    completado_at: datetime | None = None


# ---- SSE wire events (discriminated union on `event`) ----
class ProgressEvent(BaseModel):
    event: Literal['progress'] = 'progress'
    source: str
    status: Literal['running', 'done', 'error']
    count: int = 0
    message: str = ''


class PropertyBatchEvent(BaseModel):
    event: Literal['property_batch'] = 'property_batch'
    source: str
    count: int
    properties: list[NormalizedProperty]


class DoneEvent(BaseModel):
    event: Literal['done'] = 'done'
    job_id: str
    total_count: int
    sources: list[str]


class ErrorEvent(BaseModel):
    event: Literal['error'] = 'error'
    source: str | None = None
    message: str
    recoverable: bool = True


class ClarificationEvent(BaseModel):
    event: Literal['clarification'] = 'clarification'
    message: str


SSEEvent = Union[
    ProgressEvent, PropertyBatchEvent, DoneEvent, ErrorEvent, ClarificationEvent
]
