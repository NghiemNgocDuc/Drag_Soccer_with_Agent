-- ============================================================
-- Run this once in your Supabase SQL Editor
-- Dashboard → SQL Editor → New query → paste → Run
-- ============================================================

-- 1. Public profiles (one row per auth user)
create table if not exists public.profiles (
  id         uuid references auth.users on delete cascade primary key,
  username   text unique not null,
  created_at timestamp with time zone default now()
);

alter table public.profiles enable row level security;

create policy "Anyone can read profiles"
  on public.profiles for select using (true);

create policy "Users can update own profile"
  on public.profiles for update using (auth.uid() = id);

-- Service role inserts profiles on registration (bypass RLS)
create policy "Service can insert profiles"
  on public.profiles for insert with check (true);


-- 2. Games table
create table if not exists public.games (
  id          uuid default gen_random_uuid() primary key,
  user_id     uuid references auth.users not null,
  mode        text not null,          -- 'hvai' | 'hvh' | 'aivai'
  ai_model    text,
  winner      text,                   -- 'A' | 'B' | 'Draw'
  score_a     integer default 0,
  score_b     integer default 0,
  total_moves integer default 0,
  created_at  timestamp with time zone default now(),
  ended_at    timestamp with time zone
);

alter table public.games enable row level security;

create policy "Users can view own games"
  on public.games for select using (auth.uid() = user_id);

create policy "Service can insert games"
  on public.games for insert with check (true);

create index if not exists games_user_id_idx on public.games (user_id);


-- 3. User-uploaded AI models
create table if not exists public.user_models (
  id          uuid default gen_random_uuid() primary key,
  user_id     uuid references auth.users not null,
  name        text not null,
  description text default '',
  code        text not null,
  is_public   boolean default false,
  created_at  timestamp with time zone default now(),
  updated_at  timestamp with time zone default now()
);

alter table public.user_models enable row level security;

create policy "Users can manage own models"
  on public.user_models for all using (auth.uid() = user_id);

create policy "Anyone can read public models"
  on public.user_models for select using (is_public = true);

create index if not exists user_models_user_id_idx on public.user_models (user_id);


-- 4. Leaderboard function (called via supabase.rpc)
create or replace function public.get_leaderboard(limit_count integer default 20)
returns table (
  username    text,
  games_played bigint,
  wins        bigint,
  losses      bigint,
  draws       bigint,
  win_rate    numeric
)
language sql security definer
as $$
  select
    p.username,
    count(g.id)                                                            as games_played,
    count(g.id) filter (where g.winner = 'A' and g.mode = 'hvai')         as wins,
    count(g.id) filter (where g.winner = 'B' and g.mode = 'hvai')         as losses,
    count(g.id) filter (where g.winner = 'Draw' and g.mode = 'hvai')      as draws,
    round(
      100.0
      * count(g.id) filter (where g.winner = 'A' and g.mode = 'hvai')
      / nullif(count(g.id) filter (where g.mode = 'hvai'), 0),
      1
    )                                                                      as win_rate
  from public.profiles p
  left join public.games g on g.user_id = p.id
  group by p.id, p.username
  having count(g.id) > 0
  order by wins desc, games_played desc
  limit limit_count;
$$;

-- 5. Tournaments
create table if not exists public.tournaments (
  id          uuid default gen_random_uuid() primary key,
  creator_id  uuid references auth.users not null,
  name        text not null,
  status      text default 'pending', -- pending, active, completed
  created_at  timestamp with time zone default now()
);

alter table public.tournaments enable row level security;
create policy ""Users can manage own tournaments"" on public.tournaments for all using (auth.uid() = creator_id);
create policy ""Anyone can read tournaments"" on public.tournaments for select using (true);

create table if not exists public.tournament_participants (
  id            uuid default gen_random_uuid() primary key,
  tournament_id uuid references public.tournaments on delete cascade not null,
  participant_id text not null, -- can be a user_model id, built-in model name, or user id
  name          text not null
);
alter table public.tournament_participants enable row level security;
create policy ""Anyone can read participants"" on public.tournament_participants for select using (true);
create policy ""Service can insert participants"" on public.tournament_participants for insert with check (true);

create table if not exists public.tournament_matches (
  id            uuid default gen_random_uuid() primary key,
  tournament_id uuid references public.tournaments on delete cascade not null,
  round_num     integer not null,
  match_index   integer not null,
  participant_a uuid references public.tournament_participants(id),
  participant_b uuid references public.tournament_participants(id),
  winner        uuid references public.tournament_participants(id),
  status        text default 'pending',
  replay_data   jsonb
);
alter table public.tournament_matches enable row level security;
create policy ""Anyone can read matches"" on public.tournament_matches for select using (true);
create policy ""Service can update matches"" on public.tournament_matches for all using (true);
