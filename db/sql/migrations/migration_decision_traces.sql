-- ============================================================
-- Decision traces — per-turn AI decision log for loss analysis
-- Run in Supabase SQL Editor after supabase_schema.sql
--
-- One row per turn of a traced match. Rows are written by the
-- server (service role) from the sim call sites whenever a
-- logged-in user's own model participates (arena / leaderboard
-- bench / tournament). Owners read their own; RLS is a second
-- gate for the anon key (reads go through the service client).
--
-- Retention (confirmed tradeoff): age-pruned at 30 days and
-- capped at ~200 recent matches per owner by db/decision_traces.
-- ============================================================

create table if not exists public.decision_traces (
  id              uuid default gen_random_uuid() primary key,
  owner_id        text not null,           -- user id whose model was traced
  match_id        text not null,           -- "arena:{uid}:{ts}:{side}" | "tournament:{tid}:{mid}" | "bench:{model_id}:{ts}:{opp}"
  model_id        text not null,           -- "user_model:<uuid>" (the traced model)
  model_label     text not null default '',-- display name of the traced model
  opponent        text not null default '',-- opponent display name
  result          text not null default '',-- win | loss | draw (traced model's perspective)
  score_for       integer not null default 0,
  score_against   integer not null default 0,
  turn            integer not null,        -- 0-based kick index in the match
  mover           text not null,           -- "a" | "b"
  decision        jsonb not null,          -- {player_idx, angle, power}
  state_snapshot  jsonb not null,          -- pruned state dict the model saw
  outcome_tag     text not null,           -- goal | good_chance | neutral | poor | own_goal_risk
  created_at      timestamp with time zone default now(),
  unique (match_id, turn)                  -- idempotent re-saves
);

alter table public.decision_traces enable row level security;

create policy "Owners can read own decision traces"
  on public.decision_traces for select using (owner_id = auth.uid()::text);

create policy "Service can manage decision traces"
  on public.decision_traces for all using (true);

create index if not exists decision_traces_owner_match_idx
  on public.decision_traces (owner_id, match_id);

create index if not exists decision_traces_owner_created_idx
  on public.decision_traces (owner_id, created_at desc);
