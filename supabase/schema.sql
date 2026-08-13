create extension if not exists vector;
create extension if not exists pgcrypto;

create table if not exists public.name_groups (
  id uuid primary key default gen_random_uuid(),
  canonical_title text not null check (btrim(canonical_title) <> ''),
  canonical_key text not null unique check (btrim(canonical_key) <> ''),
  title_embedding vector(384),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.name_mappings (
  cleaned_name text primary key check (btrim(cleaned_name) <> ''),
  lookup_key text not null unique check (btrim(lookup_key) <> ''),
  group_id uuid not null references public.name_groups(id),
  member_embedding vector(384),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists name_mappings_group_id_idx on public.name_mappings(group_id);
create unique index if not exists name_groups_canonical_key_idx on public.name_groups(canonical_key);
create unique index if not exists name_mappings_lookup_key_idx on public.name_mappings(lookup_key);
create index if not exists name_groups_title_embedding_hnsw
  on public.name_groups using hnsw (title_embedding vector_cosine_ops)
  where title_embedding is not null;
create index if not exists name_mappings_member_embedding_hnsw
  on public.name_mappings using hnsw (member_embedding vector_cosine_ops)
  where member_embedding is not null;

alter table public.name_groups enable row level security;
alter table public.name_mappings enable row level security;
revoke all on table public.name_groups from anon, authenticated;
revoke all on table public.name_mappings from anon, authenticated;

create or replace function public.set_updated_at()
returns trigger language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists name_groups_set_updated_at on public.name_groups;
create trigger name_groups_set_updated_at before update on public.name_groups
for each row execute function public.set_updated_at();
drop trigger if exists name_mappings_set_updated_at on public.name_mappings;
create trigger name_mappings_set_updated_at before update on public.name_mappings
for each row execute function public.set_updated_at();

create or replace function public.valid_review_embedding(value jsonb)
returns boolean language sql immutable
security invoker
set search_path = pg_catalog, public
as $$
  select value is null or value = 'null'::jsonb or (
    jsonb_typeof(value) = 'array'
    and jsonb_array_length(value) = 384
    and not exists (
      select 1 from jsonb_array_elements(value) item
      where jsonb_typeof(item) <> 'number'
         or abs((item #>> '{}')::numeric) > 3.402823466e38
    )
  );
$$;

create or replace function public.review_lookup_key(name text)
returns text language plpgsql immutable
security invoker
set search_path = pg_catalog, public
as $$
declare
  normalized text;
begin
  normalized := regexp_replace(name, '[_|]+', ' ', 'g');
  normalized := regexp_replace(normalized, '[[:space:]]+', ' ', 'g');
  normalized := regexp_replace(
    normalized,
    '(^|[[:space:]])(co[.]?[[:space:]]*,?[[:space:]]*ltd[.]?|pte[[:space:]]+ltd[.]?|sdn[[:space:]]+bhd[.]?|limited[.]?|gmbh[.]?|ltd[.]?|pte[.]?)([[:space:][:punct:]].*)?$',
    '', 'i'
  );
  normalized := replace(lower(normalized), 'ß', 'ss');
  return btrim(regexp_replace(normalized, '[^[:alnum:]]+', ' ', 'g'));
end;
$$;

create or replace function public.submit_name_review(payload jsonb)
returns jsonb language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  group_item jsonb;
  mapping_item jsonb;
  unmap_item jsonb;
  resolved_id uuid;
  temp_map jsonb := '{}'::jsonb;
  groups_value jsonb;
  mappings_value jsonb;
  unmaps_value jsonb;
begin
  if payload is null or jsonb_typeof(payload) <> 'object' then
    raise exception using errcode = '22023', message = 'review payload must be an object';
  end if;
  if not (payload ? 'groups') or not (payload ? 'mappings') or not (payload ? 'unmap_names') then
    raise exception using errcode = '22023', message = 'review payload arrays are required';
  end if;
  groups_value := payload->'groups';
  mappings_value := payload->'mappings';
  unmaps_value := payload->'unmap_names';
  if jsonb_typeof(groups_value) <> 'array'
     or jsonb_typeof(mappings_value) <> 'array'
     or jsonb_typeof(unmaps_value) <> 'array' then
    raise exception using errcode = '22023', message = 'groups, mappings, and unmap_names must be arrays';
  end if;
  if exists (select 1 from jsonb_array_elements(groups_value) g
             group by g->>'id' having count(*) > 1) then
    raise exception using errcode = '23505', message = 'duplicate group id';
  end if;
  if exists (select 1 from jsonb_array_elements(groups_value) g
             group by lower(btrim(g->>'canonical_title')) having count(*) > 1) then
    raise exception using errcode = '23505', message = 'duplicate group title';
  end if;
  if exists (select 1 from jsonb_array_elements(groups_value) g
             group by public.review_lookup_key(coalesce(g->>'canonical_key', g->>'canonical_title'))
             having count(*) > 1) then
    raise exception using errcode = '23505', message = 'duplicate group key';
  end if;
  if exists (select 1 from jsonb_array_elements(mappings_value) m
             group by m->>'cleaned_name' having count(*) > 1) then
    raise exception using errcode = '23505', message = 'duplicate mapping name';
  end if;
  if exists (select 1 from jsonb_array_elements(mappings_value) m
             group by public.review_lookup_key(coalesce(m->>'lookup_key', m->>'cleaned_name'))
             having count(*) > 1) then
    raise exception using errcode = '23505', message = 'duplicate mapping key';
  end if;
  if exists (select 1 from jsonb_array_elements(unmaps_value) u
             group by u #>> '{}' having count(*) > 1) then
    raise exception using errcode = '23505', message = 'duplicate unmap name';
  end if;

  for group_item in select value from jsonb_array_elements(groups_value) loop
    if jsonb_typeof(group_item) <> 'object'
       or btrim(coalesce(group_item->>'id', '')) = ''
       or btrim(coalesce(group_item->>'canonical_title', '')) = ''
       or (group_item ? 'canonical_key' and btrim(coalesce(group_item->>'canonical_key', '')) = '')
       or public.review_lookup_key(coalesce(group_item->>'canonical_key', group_item->>'canonical_title')) = ''
       or not public.valid_review_embedding(group_item->'title_embedding') then
      raise exception using errcode = '22023', message = 'invalid group';
    end if;
    if coalesce((group_item->>'existing')::boolean, false) then
      begin resolved_id := (group_item->>'id')::uuid;
      exception when invalid_text_representation then
        raise exception using errcode = '22023', message = 'invalid existing group id';
      end;
      update public.name_groups set
        canonical_title = btrim(group_item->>'canonical_title'),
        canonical_key = public.review_lookup_key(coalesce(group_item->>'canonical_key', group_item->>'canonical_title')),
        title_embedding = case when group_item ? 'title_embedding' and group_item->'title_embedding' <> 'null'::jsonb
          then (group_item->'title_embedding')::text::vector else title_embedding end
      where id = resolved_id;
      if not found then
        raise exception using errcode = 'P0002', message = 'existing group does not exist';
      end if;
    else
      insert into public.name_groups(canonical_title, canonical_key, title_embedding)
      values (btrim(group_item->>'canonical_title'),
              public.review_lookup_key(coalesce(group_item->>'canonical_key', group_item->>'canonical_title')),
              case when group_item ? 'title_embedding' and group_item->'title_embedding' <> 'null'::jsonb
                then (group_item->'title_embedding')::text::vector else null end)
      returning id into resolved_id;
      temp_map := temp_map || jsonb_build_object(group_item->>'id', resolved_id::text);
    end if;
  end loop;

  for unmap_item in select value from jsonb_array_elements(unmaps_value) loop
    if jsonb_typeof(unmap_item) <> 'string' or btrim(unmap_item #>> '{}') = '' then
      raise exception using errcode = '22023', message = 'invalid unmap name';
    end if;
    delete from public.name_mappings where cleaned_name = unmap_item #>> '{}';
  end loop;

  for mapping_item in select value from jsonb_array_elements(mappings_value) loop
    if jsonb_typeof(mapping_item) <> 'object'
       or btrim(coalesce(mapping_item->>'cleaned_name', '')) = ''
       or btrim(coalesce(mapping_item->>'group_id', '')) = ''
       or (mapping_item ? 'lookup_key' and btrim(coalesce(mapping_item->>'lookup_key', '')) = '')
       or public.review_lookup_key(coalesce(mapping_item->>'lookup_key', mapping_item->>'cleaned_name')) = ''
       or not public.valid_review_embedding(mapping_item->'member_embedding') then
      raise exception using errcode = '22023', message = 'invalid mapping';
    end if;
    begin
      resolved_id := coalesce(temp_map ->> (mapping_item->>'group_id'), mapping_item->>'group_id')::uuid;
    exception when invalid_text_representation then
      raise exception using errcode = '22023', message = 'mapping references unknown group';
    end;
    if exists (select 1 from public.name_mappings n
               where n.cleaned_name = mapping_item->>'cleaned_name'
               and n.group_id <> resolved_id) then
      raise exception using errcode = '23505', message = 'mapping conflict requires explicit unmap';
    end if;
    insert into public.name_mappings(cleaned_name, lookup_key, group_id, member_embedding)
    values (btrim(mapping_item->>'cleaned_name'),
            public.review_lookup_key(coalesce(mapping_item->>'lookup_key', mapping_item->>'cleaned_name')),
            resolved_id,
            case when mapping_item ? 'member_embedding' and mapping_item->'member_embedding' <> 'null'::jsonb
              then (mapping_item->'member_embedding')::text::vector else null end)
    on conflict (cleaned_name) do update set
      lookup_key = excluded.lookup_key, group_id = excluded.group_id,
      member_embedding = case when mapping_item ? 'member_embedding'
        then excluded.member_embedding else public.name_mappings.member_embedding end;
  end loop;
  return temp_map;
end;
$$;

revoke all on function public.set_updated_at() from public, anon, authenticated;
revoke all on function public.valid_review_embedding(jsonb) from public, anon, authenticated;
revoke all on function public.review_lookup_key(text) from public, anon, authenticated;
revoke execute on function public.submit_name_review(jsonb) from public, anon, authenticated;
grant execute on function public.valid_review_embedding(jsonb) to service_role;
grant execute on function public.review_lookup_key(text) to service_role;
grant execute on function public.submit_name_review(jsonb) to service_role;
