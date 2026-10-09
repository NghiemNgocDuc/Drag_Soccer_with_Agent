-- Run this in your Supabase SQL Editor once (idempotent) to enable:
--  1. profiles.avatar_url column (profile photo public URL)
--  2. `avatars` storage bucket (public read, owner-only write)
--  3. RLS policies that scope each user's avatar writes to avatars/{user_id}/*
--
-- NOTE: bucket creation is also attempted at runtime by db/profile_photos.py
-- (idempotent), but RLS policies can only be applied via SQL — apply this file.

-- 1) Avatar URL column
alter table public.profiles
add column if not exists avatar_url text;

-- 2) Public-read `avatars` bucket
insert into storage.buckets (id, name, public)
values ('avatars', 'avatars', true)
on conflict (id) do nothing;

-- 3) RLS policies — public can read; only the owner can write under their own folder
create policy "avatars_public_read"
on storage.objects for select
using (bucket_id = 'avatars');

create policy "avatars_owner_insert"
on storage.objects for insert
with check (
  bucket_id = 'avatars'
  and (storage.foldername(name))[1] = auth.uid()::text
);

create policy "avatars_owner_update"
on storage.objects for update
using (
  bucket_id = 'avatars'
  and (storage.foldername(name))[1] = auth.uid()::text
);

create policy "avatars_owner_delete"
on storage.objects for delete
using (
  bucket_id = 'avatars'
  and (storage.foldername(name))[1] = auth.uid()::text
);
