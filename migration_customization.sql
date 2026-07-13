-- Run this in your Supabase SQL Editor to add the customization column
alter table public.profiles
add column if not exists customization jsonb default '{}'::jsonb;
