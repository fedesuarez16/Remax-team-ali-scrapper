-- Ficha Propio: cada ficha se genera A NOMBRE DE UN AGENTE del equipo.
-- Se persiste el email (identificador estable del agente en el frontend);
-- NULL = fichas viejas, que caen en el titular por defecto.
alter table properties add column if not exists agente_email text;

comment on column properties.agente_email is
  'Email del agente del equipo a cuyo nombre se generó la Ficha Propio. NULL = titular por defecto.';
