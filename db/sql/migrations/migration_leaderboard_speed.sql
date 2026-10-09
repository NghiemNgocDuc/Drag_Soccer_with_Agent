-- Speed column for model leaderboard — average latency per move (ms)
alter table public.model_leaderboard
  add column if not exists avg_latency numeric default null,
  add column if not exists speed_rank integer default null;

create index if not exists model_leaderboard_latency_idx on public.model_leaderboard (avg_latency asc) where avg_latency is not null;
