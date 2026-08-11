-- Team-wide editable texts for the Ficha Propio. These used to be module
-- constants in `frontend/lib/ficha.ts`; making them editable means giving them
-- a home, and their semantics are global — one set of texts for every ficha,
-- published and future — so this is a settings row, not a collection.
--
-- Singleton enforced by `check (id = 1)`: a second row would make "which texts
-- does the public page render?" ambiguous, and that page has no way to choose.
--
-- RLS enabled + default-deny: backend uses service_role (bypasses RLS); no
-- frontend/anon-key access is expected in this phase (no auth yet).

create table public.ficha_settings (
    id               smallint primary key default 1 check (id = 1),
    -- Contact blurb shown above the agent card on the public ficha.
    texto_seleccion  text not null,
    -- Normative notice. Legally required on every published ficha — the API
    -- refuses to store it blank, and falls back to the built-in text on read.
    disclaimer_legal text not null,
    -- Footer signature: responsible broker + their registration number.
    firma            text not null,
    colegiatura      text not null,
    pie_publicacion  text not null,
    updated_at       timestamptz not null default now()
);
alter table public.ficha_settings enable row level security;

create policy "default deny" on public.ficha_settings
    as restrictive for all using (false);

-- Seed with the texts that were hardcoded in the frontend, so an existing
-- deployment keeps rendering exactly what it rendered before this migration.
-- Kept in sync with `DEFAULT_TEXTOS` in `backend/app/api/v1/ficha_settings.py`,
-- which is also the read-time fallback if this row is ever missing.
insert into public.ficha_settings (
    id, texto_seleccion, disclaimer_legal, firma, colegiatura, pie_publicacion
) values (
    1,
    'Esta selección de propiedades reúne las oportunidades relevadas en el mercado que mejor se ajustan a tus criterios de búsqueda. Si alguna opción resulta de tu interés, comunícate para coordinar una visita o solicitar más información.',
    '⚖️ En cumplimiento de las normas legales aplicables, informamos que los Agentes NO ejercen el Corretaje Inmobiliario. Todas las operaciones inmobiliarias son concluidas por los Corredores Matriculados responsables en cada oficina.',
    'Andrés Alí | Diagonal II',
    'C.D.C.P.D.J.L.P. 7428',
    'Publicación generada por RE/MAX Diagonal II. La información puede estar sujeta a modificaciones sin previo aviso.'
);

-- Bump `updated_at` on every edit so it reflects the last change, not the seed.
create or replace function public.touch_ficha_settings_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

create trigger trg_ficha_settings_updated_at
    before update on public.ficha_settings
    for each row execute function public.touch_ficha_settings_updated_at();
