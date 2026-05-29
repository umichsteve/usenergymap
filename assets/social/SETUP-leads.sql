-- US Energy Map — lead capture table
-- Run once in your Supabase project: Dashboard → SQL Editor → paste → Run.
-- Then put the project's URL + anon (publishable) key into the gate config
-- at the top of map.html.

create table if not exists public.leads (
  id          uuid primary key default gen_random_uuid(),
  email       text not null,
  name        text not null,
  source      text default 'map_gate',
  user_agent  text,
  created_at  timestamptz not null default now()
);

-- Lock it down: the public (anon) key may INSERT only — never read.
alter table public.leads enable row level security;

drop policy if exists "anon can insert leads" on public.leads;
create policy "anon can insert leads"
  on public.leads
  for insert
  to anon
  with check (true);

-- (Intentionally no SELECT policy for anon — you read leads from the
--  dashboard or with the service-role key, never from the browser.)

-- Optional: prevent duplicate emails piling up.
create unique index if not exists leads_email_key on public.leads (lower(email));
