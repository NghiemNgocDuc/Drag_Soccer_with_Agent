-- ============================================================
-- Ranked seasons: periodic soft-reset, archived standings, rewards
-- Run in Supabase SQL Editor after migration_ranked.sql
-- ============================================================

-- 1. Seasons (one row per season; number is unique)
create table if not exists public.seasons (
  id                  serial primary key,
  number              integer unique not null,
  starts_at           timestamp with time zone not null,
  ends_at             timestamp with time zone not null,
  status              text not null default 'active' check (status in ('active', 'completed')),
  leaderboard_snapshot jsonb,
  created_at          timestamp with time zone default now()
);

alter table public.seasons enable row level security;

create policy "Anyone can read seasons"
  on public.seasons for select using (true);

create policy "Service can manage seasons"
  on public.seasons for all using (true);

create index if not exists seasons_number_idx on public.seasons (number desc);

-- 2. Per-season ratings (the number that soft-resets; persists as season history)
create table if not exists public.season_ratings (
  id            serial primary key,
  user_id       uuid references auth.users on delete cascade not null,
  season_id     int references public.seasons (id) not null,
  rating_start  integer not null default 1200,
  rating        integer not null default 1200,
  games_played  integer not null default 0,
  wins          integer not null default 0,
  losses        integer not null default 0,
  peak_rating   integer not null default 1200,
  updated_at    timestamp with time zone default now(),
  unique (user_id, season_id)
);

alter table public.season_ratings enable row level security;

create policy "Anyone can read season ratings"
  on public.season_ratings for select using (true);

create policy "Service can manage season ratings"
  on public.season_ratings for all using (true);

create index if not exists season_ratings_board_idx
  on public.season_ratings (season_id, rating desc);

create index if not exists season_ratings_user_idx
  on public.season_ratings (user_id, season_id desc);

-- 3. Soft-reset entries surface in the rating log (match_id stays null)
alter table public.rating_history
  add column if not exists season_id int references public.seasons (id);