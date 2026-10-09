-- ============================================================
-- Tutorial curriculum — per-user lesson progress
-- Run in Supabase SQL Editor after supabase_schema.sql
--
-- The Learn page is a guided, sequentially-unlocked curriculum
-- for new AI builders (7 lessons). This table records which
-- machine-checked milestones a user has completed. Rows are
-- written by the server (service role) from the background
-- check thread once a milestone passes.
--
-- One row per (user, lesson). The primary key is the
-- double-complete guard (idempotent re-checks).
-- ============================================================

create table if not exists public.tutorial_progress (
  user_id      text not null,          -- user id ("dev:*" | supabase auth uid)
  lesson_id    integer not null,       -- 1..7 (see services/tutorial.py LESSONS)
  completed_at double precision not null, -- unix epoch seconds
  primary key (user_id, lesson_id)
);

alter table public.tutorial_progress enable row level security;

create policy "Owners can read own tutorial progress"
  on public.tutorial_progress for select using (user_id = auth.uid()::text);

create policy "Service can manage tutorial progress"
  on public.tutorial_progress for all using (true);

create index if not exists tutorial_progress_user_idx
  on public.tutorial_progress (user_id);