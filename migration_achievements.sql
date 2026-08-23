-- ============================================================
-- Achievements / badges (permanent, account-bound recognition)
-- Run in Supabase SQL Editor after supabase_schema.sql
-- ============================================================

-- 1. Achievement catalog (seeded idempotently from db/achievements.py;
--    the seed upsert runs lazily on first award / page view)
create table if not exists public.achievement_definitions (
  key         text primary key,
  category    text not null,
  name        text not null,
  description text not null default '',
  emoji       text not null default '🏆',
  sort_order  integer not null default 0
);

alter table public.achievement_definitions enable row level security;

create policy "Anyone can read achievement definitions"
  on public.achievement_definitions for select using (true);

create policy "Service can manage achievement definitions"
  on public.achievement_definitions for all using (true);

-- 2. Earned badges (unique (user_id, key) = DB-level double-award guard)
create table if not exists public.user_achievements (
  id               uuid default gen_random_uuid() primary key,
  user_id          uuid references auth.users on delete cascade not null,
  achievement_key  text references public.achievement_definitions (key) not null,
  awarded_at       timestamp with time zone default now(),
  unique (user_id, achievement_key)
);

alter table public.user_achievements enable row level security;

create policy "Users can read own achievements"
  on public.user_achievements for select using (auth.uid() = user_id);

create policy "Service can manage achievements"
  on public.user_achievements for all using (true);

create index if not exists user_achievements_user_idx
  on public.user_achievements (user_id, awarded_at desc);
