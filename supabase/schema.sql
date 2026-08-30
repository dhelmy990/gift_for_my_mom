create table if not exists public.company_aliases (
  alias_key text primary key check (btrim(alias_key) <> ''),
  cleaned_alias text not null check (btrim(cleaned_alias) <> ''),
  canonical_name text not null check (btrim(canonical_name) <> ''),
  updated_at timestamptz not null default now()
);

alter table public.company_aliases enable row level security;
revoke all on table public.company_aliases from public, anon, authenticated;
grant select, insert, update on table public.company_aliases to service_role;

create or replace function public.set_company_alias_updated_at()
returns trigger language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists company_aliases_set_updated_at on public.company_aliases;
create trigger company_aliases_set_updated_at
before update on public.company_aliases
for each row execute function public.set_company_alias_updated_at();

revoke all on function public.set_company_alias_updated_at()
from public, anon, authenticated;
