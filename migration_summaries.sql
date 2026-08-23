-- ============================================================
-- Post-match shareable summary cards
-- Run in Supabase SQL Editor (after migration_ranked.sql)
-- ============================================================

-- One row per finished online match, keyed by the Redis room id (the
-- only stable id a casual room has). `data` is the full snapshot:
-- result, ranked deltas, both players' saved lineups, season number.
create table if not exists public.match_summaries (
  room_id     text primary key,
  data        jsonb not null,
  created_at  timestamp with time zone default now()
);

alter table public.match_summaries enable row level security;

create policy "Anyone can read match summaries"
  on public.match_summaries for select using (true);

create policy "Service can manage match summaries"
  on public.match_summaries for all using (true);