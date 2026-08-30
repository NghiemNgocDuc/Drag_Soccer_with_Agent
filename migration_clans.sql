-- migration_clans.sql — Clans: create, open/request joins, leader transfer
-- Run once in Supabase SQL editor. In-memory fallback handles DEV_MODE.

create table if not exists public.clans (
  id          uuid primary key default gen_random_uuid(),
  name        text not null unique,
  description text default '',
  leader_id   uuid references auth.users(id) on delete set null,
  join_type   text not null check (join_type in ('open','request')), -- open = free to join, request = leader approval
  member_limit integer not null default 20 check (member_limit between 2 and 100),
  created_at  timestamp with time zone default now() not null
);
alter table public.clans enable row level security;
drop policy if exists "Anyone can read clans" on public.clans;
create policy "Anyone can read clans" on public.clans for select using (true);
drop policy if exists "Authenticated can create clans" on public.clans;
create policy "Authenticated can create clans" on public.clans for insert with check (auth.uid() = leader_id);
drop policy if exists "Leader can update clan" on public.clans;
create policy "Leader can update clan" on public.clans for update using (auth.uid() = leader_id);
drop policy if exists "Leader can delete clan" on public.clans;
create policy "Leader can delete clan" on public.clans for delete using (auth.uid() = leader_id);
create index if not exists clans_leader_id_idx on public.clans(leader_id);
create index if not exists clans_name_idx on public.clans(name);

create table if not exists public.clan_members (
  clan_id   uuid references public.clans(id) on delete cascade not null,
  user_id   uuid references auth.users(id) on delete cascade not null,
  joined_at timestamp with time zone default now() not null,
  primary key (clan_id, user_id)
);
alter table public.clan_members enable row level security;
drop policy if exists "Anyone can read clan_members" on public.clan_members;
create policy "Anyone can read clan_members" on public.clan_members for select using (true);
drop policy if exists "Members can manage" on public.clan_members;
create policy "Members can manage" on public.clan_members for all using (auth.uid() = user_id or exists (select 1 from public.clans where clans.id = clan_id and clans.leader_id = auth.uid()));
create index if not exists clan_members_user_id_idx on public.clan_members(user_id);

create table if not exists public.clan_requests (
  id        uuid primary key default gen_random_uuid(),
  clan_id   uuid references public.clans(id) on delete cascade not null,
  user_id   uuid references auth.users(id) on delete cascade not null,
  message   text default '',
  status    text not null default 'pending' check (status in ('pending','approved','declined')),
  created_at timestamp with time zone default now() not null,
  unique (clan_id, user_id)
);
alter table public.clan_requests enable row level security;
drop policy if exists "Anyone can read clan_requests" on public.clan_requests;
create policy "Anyone can read clan_requests" on public.clan_requests for select using (true);
drop policy if exists "Authenticated can request" on public.clan_requests;
create policy "Authenticated can request" on public.clan_requests for insert with check (auth.uid() = user_id);
drop policy if exists "Leader or owner can manage requests" on public.clan_requests;
create policy "Leader or owner can manage requests" on public.clan_requests for all using (auth.uid() = user_id or exists (select 1 from public.clans where clans.id = clan_id and clans.leader_id = auth.uid()));
create index if not exists clan_requests_clan_id_idx on public.clan_requests(clan_id);
