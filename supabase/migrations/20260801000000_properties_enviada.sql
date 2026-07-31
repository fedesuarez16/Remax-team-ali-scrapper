-- Marca de "propiedad enviada al cliente".
--
-- Cuando el usuario selecciona propiedades en /properties (o en los resultados
-- de una búsqueda, /properties?job_id=…) y toca "Preparar y enviar", esas filas
-- quedan marcadas. Así, al volver a la misma búsqueda, se distinguen a simple
-- vista las que ya mandó de las que todavía no.
--
-- Es timestamptz y no boolean a propósito: NULL = no enviada, y cuando está
-- enviada además sabemos CUÁNDO, sin necesitar una segunda columna.
alter table public.properties add column if not exists enviada_at timestamptz;

-- El filtro "Enviadas / No enviadas" del listado pega contra esta columna.
create index if not exists properties_enviada_at_idx
  on public.properties (enviada_at);
