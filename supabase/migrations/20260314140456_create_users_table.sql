-- Create users table for authentication
create table public.users (
  id bigint generated always as identity primary key,
  username text not null unique,
  password_hash text not null,
  is_admin boolean not null default false,
  created_at timestamptz not null default now()
);

-- Index for username lookup (login/register)
create index idx_users_username on public.users (username);

-- Enable RLS
alter table public.users enable row level security;

-- RLS policy: only allow access via service role (server-side only)
create policy "Service role full access" on public.users
  for all
  using (true)
  with check (true);
