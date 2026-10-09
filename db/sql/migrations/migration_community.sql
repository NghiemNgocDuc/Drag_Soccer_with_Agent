-- migration_community.sql — share gallery: likes + comments for public models
-- Public models are user_models where is_public=true; likes/comments reference them.

create table if not exists public.model_likes (
  model_id uuid references public.user_models(id) on delete cascade not null,
  user_id uuid references auth.users(id) on delete cascade not null,
  created_at timestamp with time zone default now() not null,
  primary key (model_id, user_id)
);
alter table public.model_likes enable row level security;
drop policy if exists "Anyone can read likes" on public.model_likes;
create policy "Anyone can read likes" on public.model_likes for select using (true);
drop policy if exists "Users can like" on public.model_likes;
create policy "Users can like" on public.model_likes for insert with check (auth.uid() = user_id);
drop policy if exists "Users can unlike own" on public.model_likes;
create policy "Users can unlike own" on public.model_likes for delete using (auth.uid() = user_id);
create index if not exists model_likes_model_id_idx on public.model_likes(model_id);

create table if not exists public.model_comments (
  id uuid primary key default gen_random_uuid(),
  model_id uuid references public.user_models(id) on delete cascade not null,
  user_id uuid references auth.users(id) on delete cascade not null,
  body text not null check (char_length(body) between 1 and 500),
  created_at timestamp with time zone default now() not null
);
alter table public.model_comments enable row level security;
drop policy if exists "Anyone can read comments" on public.model_comments;
create policy "Anyone can read comments" on public.model_comments for select using (true);
drop policy if exists "Users can comment" on public.model_comments;
create policy "Users can comment" on public.model_comments for insert with check (auth.uid() = user_id);
drop policy if exists "Users can delete own comment" on public.model_comments;
create policy "Users can delete own comment" on public.model_comments for delete using (auth.uid() = user_id);
create index if not exists model_comments_model_id_idx on public.model_comments(model_id);
create index if not exists model_comments_created_at_idx on public.model_comments(created_at desc);
