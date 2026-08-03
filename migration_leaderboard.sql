-- Leaderboard for custom AI models — public ranking of submitted models.
-- Run against Supabase (psql / Dashboard SQL editor). Idempotent.

-- Owner-visible opt-in + last run time on the existing models table
alter table public.user_models
  add column if not exists submitted_to_leaderboard boolean default false,
  add column if not exists last_benchmarked_at timestamp with time zone;

-- Public ranking rows. Kept as a SEPARATE table (not columns on user_models)
-- so a ranked model's private `code` is never exposed through read-by-public
-- row-level policies; the leaderboard row contains only rankable data.
create table if not exists public.model_leaderboard (
  model_id           uuid references public.user_models (id) on delete cascade primary key,
  user_id            uuid references auth.users not null,
  model_name         text not null,
  score              numeric not null,                   -- mean win rate vs 7 built-ins (0-100)
  games_per_opponent integer not null default 5,
  details            jsonb not null default '[]'::jsonb, -- per-opponent wins/losses/draws/win_rate
  benchmarked_at     timestamp with time zone default now()
);

alter table public.model_leaderboard enable row level security;

-- Anyone logged in can read the ranking (matches /leaderboard privacy: login_required page).
create policy "Anyone can read model leaderboard"
  on public.model_leaderboard for select using (true);

-- Owners manage their own row (insert on submit, update on re-benchmark, delete with model).
create policy "Users can manage own leaderboard entries"
  on public.model_leaderboard for all using (auth.uid() = user_id);

create index if not exists model_leaderboard_score_idx on public.model_leaderboard (score desc);
create index if not exists model_leaderboard_user_idx  on public.model_leaderboard (user_id);