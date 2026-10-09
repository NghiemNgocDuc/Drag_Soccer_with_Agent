-- ============================================================
-- Ranked matchmaking: ELO ratings for human players
-- Run in Supabase SQL Editor after supabase_schema.sql
-- ============================================================

-- 1. Ratings (one row per user)
create table if not exists public.ratings (
  user_id      uuid references auth.users on delete cascade primary key,
  rating       integer not null default 1200,
  games_played integer not null default 0,
  wins         integer not null default 0,
  losses       integer not null default 0,
  peak_rating  integer not null default 1200,
  updated_at   timestamp with time zone default now()
);

alter table public.ratings enable row level security;

create policy "Anyone can read ratings"
  on public.ratings for select using (true);

create policy "Service can manage ratings"
  on public.ratings for all using (true);

create index if not exists ratings_rating_idx on public.ratings (rating desc);

-- 2. Ranked matches (one row per rated match; room_id unique -> idempotent apply)
create table if not exists public.ranked_matches (
  id              uuid default gen_random_uuid() primary key,
  room_id         text unique,
  player_a        uuid references auth.users not null,
  player_b        uuid references auth.users not null,
  winner          text not null check (winner in ('A', 'B')),
  score_a         integer not null default 0,
  score_b         integer not null default 0,
  rating_a_before integer not null,
  rating_a_after  integer not null,
  delta_a         integer not null,
  k_a             integer not null,
  rating_b_before integer not null,
  rating_b_after  integer not null,
  delta_b         integer not null,
  k_b             integer not null,
  created_at      timestamp with time zone default now()
);

alter table public.ranked_matches enable row level security;

create policy "Anyone can read ranked matches"
  on public.ranked_matches for select using (true);

create policy "Service can manage ranked matches"
  on public.ranked_matches for all using (true);

create index if not exists ranked_matches_created_idx on public.ranked_matches (created_at desc);

-- 3. Rating history (full log per change; feeds rating-over-time charts / seasons)
create table if not exists public.rating_history (
  id             uuid default gen_random_uuid() primary key,
  user_id        uuid references auth.users on delete cascade not null,
  match_id       uuid references public.ranked_matches on delete set null,
  rating_before  integer not null,
  rating_after   integer not null,
  delta          integer not null,
  created_at     timestamp with time zone default now()
);

alter table public.rating_history enable row level security;

create policy "Users can read own rating history"
  on public.rating_history for select using (auth.uid() = user_id);

create policy "Service can manage rating history"
  on public.rating_history for all using (true);

create index if not exists rating_history_user_idx on public.rating_history (user_id, created_at desc);

-- 4. Atomic result application (single transaction: match row + history + both ratings)
--    p_winner: 1 = A wins, 0 = B wins
create or replace function public.submit_ranked_result(
  p_room_id        text,
  p_player_a       uuid,
  p_player_b       uuid,
  p_winner         integer,
  p_score_a        integer,
  p_score_b        integer,
  p_rating_a_before integer,
  p_rating_a_after  integer,
  p_delta_a        integer,
  p_k_a            integer,
  p_rating_b_before integer,
  p_rating_b_after  integer,
  p_delta_b        integer,
  p_k_b            integer
) returns jsonb
language plpgsql security definer
as $$
declare
  v_match_id uuid;
begin
  insert into public.ranked_matches (
    room_id, player_a, player_b, winner, score_a, score_b,
    rating_a_before, rating_a_after, delta_a, k_a,
    rating_b_before, rating_b_after, delta_b, k_b
  ) values (
    p_room_id, p_player_a, p_player_b, case when p_winner = 1 then 'A' else 'B' end,
    p_score_a, p_score_b,
    p_rating_a_before, p_rating_a_after, p_delta_a, p_k_a,
    p_rating_b_before, p_rating_b_after, p_delta_b, p_k_b
  )
  returning id into v_match_id;

  insert into public.rating_history (user_id, match_id, rating_before, rating_after, delta)
  values (p_player_a, v_match_id, p_rating_a_before, p_rating_a_after, p_delta_a),
         (p_player_b, v_match_id, p_rating_b_before, p_rating_b_after, p_delta_b);

  insert into public.ratings (user_id, rating, games_played, wins, losses, peak_rating)
  values (
    p_player_a, p_rating_a_after, 1,
    case when p_winner = 1 then 1 else 0 end,
    case when p_winner = 1 then 0 else 1 end,
    greatest(p_rating_a_before, p_rating_a_after)
  )
  on conflict (user_id) do update set
    rating       = excluded.rating,
    games_played = public.ratings.games_played + 1,
    wins         = public.ratings.wins + excluded.wins,
    losses       = public.ratings.losses + excluded.losses,
    peak_rating  = greatest(public.ratings.peak_rating, excluded.peak_rating),
    updated_at   = now();

  insert into public.ratings (user_id, rating, games_played, wins, losses, peak_rating)
  values (
    p_player_b, p_rating_b_after, 1,
    case when p_winner = 1 then 0 else 1 end,
    case when p_winner = 1 then 1 else 0 end,
    greatest(p_rating_b_before, p_rating_b_after)
  )
  on conflict (user_id) do update set
    rating       = excluded.rating,
    games_played = public.ratings.games_played + 1,
    wins         = public.ratings.wins + excluded.wins,
    losses       = public.ratings.losses + excluded.losses,
    peak_rating  = greatest(public.ratings.peak_rating, excluded.peak_rating),
    updated_at   = now();

  return jsonb_build_object('match_id', v_match_id);
end $$;
