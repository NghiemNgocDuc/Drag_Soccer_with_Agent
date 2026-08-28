-- migration_friends.sql — persistent friends + friend_requests (replaces ephemeral Redis-only)
-- Run once in Supabase SQL editor. Redis remains for presence cache only.
-- Cap 32 friends (FIFA Mobile) enforced app-side.

create table if not exists public.friends (
  user_id    uuid references auth.users(id) on delete cascade not null,
  friend_id  uuid references auth.users(id) on delete cascade not null,
  since      timestamp with time zone default now() not null,
  nickname   text default null,
  favorite   boolean default false not null,
  primary key (user_id, friend_id),
  check (user_id <> friend_id)
);
alter table public.friends enable row level security;
drop policy if exists "Users can manage own friends" on public.friends;
create policy "Users can manage own friends" on public.friends for all using (auth.uid() = user_id);
create index if not exists friends_user_id_idx on public.friends(user_id);
create index if not exists friends_friend_id_idx on public.friends(friend_id);

create table if not exists public.friend_requests (
  id          text primary key, -- hex id app-generated
  from_id     uuid references auth.users(id) on delete cascade not null,
  to_id       uuid references auth.users(id) on delete cascade not null,
  created_at  timestamp with time zone default now() not null,
  unique (from_id, to_id),
  check (from_id <> to_id)
);
alter table public.friend_requests enable row level security;
drop policy if exists "Users can manage own friend requests" on public.friend_requests;
create policy "Users can manage own friend requests" on public.friend_requests for all using (auth.uid() = from_id or auth.uid() = to_id);
create index if not exists friend_requests_to_id_idx on public.friend_requests(to_id);
create index if not exists friend_requests_from_id_idx on public.friend_requests(from_id);

-- recently_met is Redis-only (30 max) — no table needed
