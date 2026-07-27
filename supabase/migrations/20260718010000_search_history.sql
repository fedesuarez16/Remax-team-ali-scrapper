-- Server-side persistence for the sidebar search history (was localStorage-only).
-- RLS enabled + default-deny: backend uses service_role (bypasses RLS); no
-- frontend/anon-key access is expected in this phase (no auth yet).

create table public.search_history (
    id         uuid primary key default gen_random_uuid(),
    query      text not null,
    zona       text,
    job_id     text,
    created_at timestamptz not null default now()
);
alter table public.search_history enable row level security;

create policy "default deny" on public.search_history
    as restrictive for all using (false);

-- Dedupe backstop for the delete-then-insert upsert done by the backend.
create unique index uq_search_history_query
    on public.search_history (lower(query));
create index idx_search_history_created_at
    on public.search_history (created_at desc);
