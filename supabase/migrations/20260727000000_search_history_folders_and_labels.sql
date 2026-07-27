-- Adds folder grouping + editable labels to sidebar search history.
-- Forward-only migration: 20260718010000_search_history.sql is already
-- committed/applied and must not be rewritten (immutability).
--
-- Ordering matters: search_history_folders must exist before the
-- search_history.folder_id FK is declared.

create table public.search_history_folders (
    id         uuid primary key default gen_random_uuid(),
    name       text not null,
    created_at timestamptz not null default now()
);
alter table public.search_history_folders enable row level security;

create policy "default deny" on public.search_history_folders
    as restrictive for all using (false);

alter table public.search_history
    add column label text,
    add column folder_id uuid references public.search_history_folders(id) on delete set null;

create index idx_search_history_folder_id
    on public.search_history (folder_id);
