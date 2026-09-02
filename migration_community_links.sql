-- Links for shared models — paper, doc, or any URL
alter table public.user_models
  add column if not exists links jsonb default '[]'::jsonb;

-- also allow links in model_leaderboard for fast display (denormalized, optional)
alter table public.model_leaderboard
  add column if not exists links jsonb default null;
