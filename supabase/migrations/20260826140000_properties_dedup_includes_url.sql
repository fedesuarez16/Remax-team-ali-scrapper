-- `properties_dedup_idx` was `unique (direccion, precio, tipo_operacion)`, and
-- `_upsert_properties` writes insert-ignore against it. That triple is not an
-- identity: `_norm_zonaprop` falls back to `neighborhood` and then to the zona
-- whenever a portal publishes no street address, so a whole barrio's listings
-- share the string "La Plata" and collapse on price alone. Measured on a live
-- search: the graph handed over 54 distinct properties, 11 rows survived the
-- write, and the results view could only ever show those 11.
--
-- `url_origen` is the listing's identity and every scraped row carries one, so
-- adding it to the key lets distinct listings coexist. Two properties: the new
-- key is strictly MORE permissive than the old one, so no currently-valid row
-- can violate it; and with default NULL semantics a row without a URL keeps
-- deduplicating on the old triple. Verified against production before applying
-- (5937 rows): zero colliding groups, so nothing is deleted or rewritten.
--
-- `NULLS NOT DISTINCT` was considered and rejected: it would also collapse the
-- rows whose `precio` is null, which today coexist legitimately — 61 groups,
-- 150 rows, all of which would have had to be deleted.

drop index if exists public.properties_dedup_idx;

create unique index properties_dedup_idx
    on public.properties (direccion, precio, tipo_operacion, url_origen);
