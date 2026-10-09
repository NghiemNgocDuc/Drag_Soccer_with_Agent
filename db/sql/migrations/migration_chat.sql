-- ============================================================
-- Run this once in your Supabase SQL Editor (idempotent)
-- Dashboard → SQL Editor → New query → paste → Run
--
-- Enables chat:
--  1. `messages` — persisted friend DMs. Match chat and
--     tournament-lobby chat are EPHEMERAL and live in Redis
--     with TTLs (like rooms/tournaments), so no table for them.
--  2. `reported_messages` — moderation queue. Nothing user-facing
--     reads it; review happens via the service role / SQL editor.
--  3. `blocks` — per-user block lists (server-side filtering).
--
-- sender_id / scope_id are `text`, not uuid FKs, because guests
-- (guest:*) and Clerk users (clerk:*) participate in chat too.
-- DM conversation ids are "uidA|uidB" (sorted, '|' separator).
-- ============================================================

-- 1. Persisted friend DMs
create table if not exists public.messages (
  id          uuid default gen_random_uuid() primary key,
  sender_id   text not null,
  sender_name text not null,
  scope       text not null check (scope = 'dm'),
  scope_id    text not null,           -- sorted "uidA|uidB" conversation id
  body        text not null,
  emoji_only  boolean default false,
  seq         bigint generated always as identity,
  created_at  timestamp with time zone default now()
);

alter table public.messages enable row level security;

create policy "DM participants can read their conversation"
  on public.messages for select
  using (
    scope = 'dm'
    and (
      auth.uid()::text = split_part(scope_id, '|', 1)
      or auth.uid()::text = split_part(scope_id, '|', 2)
    )
  );

create policy "Service can insert messages"
  on public.messages for insert with check (true);

create index if not exists messages_scope_idx on public.messages (scope, scope_id, seq);
create index if not exists messages_sender_idx on public.messages (sender_id);


-- 2. Moderation queue (reports)
create table if not exists public.reported_messages (
  id           uuid default gen_random_uuid() primary key,
  reporter_id  text not null,
  scope        text not null,          -- match | tournament | dm
  scope_id     text not null,
  mid          text not null,          -- message id as the reporter saw it
  sender_id    text not null,
  sender_name  text not null,
  body         text not null,
  reason       text default '',
  created_at   timestamp with time zone default now()
);

alter table public.reported_messages enable row level security;

create policy "Users can insert reports"
  on public.reported_messages for insert with check (auth.uid() is not null);

create index if not exists reported_messages_created_idx on public.reported_messages (created_at);


-- 3. Block lists
create table if not exists public.blocks (
  blocker_id  text not null,
  blocked_id  text not null,
  created_at  timestamp with time zone default now(),
  primary key (blocker_id, blocked_id)
);

alter table public.blocks enable row level security;

create policy "Users manage their own blocks"
  on public.blocks for all using (blocker_id = auth.uid()::text)
  with check (blocker_id = auth.uid()::text);
