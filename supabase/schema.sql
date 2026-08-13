create extension if not exists vector;
create extension if not exists pgcrypto;

create table if not exists public.submission_ledger (
  request_id uuid primary key,
  payload_fingerprint text not null,
  result jsonb not null,
  created_at timestamptz not null default now()
);

alter table public.submission_ledger enable row level security;
revoke all on table public.submission_ledger from public, anon, authenticated;
grant select, insert on table public.submission_ledger to service_role;

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
  select case
    when value is null or value = 'null'::jsonb then true
    when jsonb_typeof(value) <> 'array' then false
    when jsonb_array_length(value) <> 384 then false
    else not exists (
      select 1 from jsonb_array_elements(value) item
      where case
        when jsonb_typeof(item) <> 'number' then true
        else abs((item #>> '{}')::numeric) > 3.402823466e38
      end
    )
  end;
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
  group_item record;
  mapping_item record;
  unmap_item record;
  resolved_id uuid;
  temp_map jsonb := '{}'::jsonb;
  groups_value jsonb;
  mappings_value jsonb;
  unmaps_value jsonb;
  request_uuid uuid;
  payload_fingerprint text;
  prior_fingerprint text;
  prior_result jsonb;
begin
  if payload is null or jsonb_typeof(payload) <> 'object' then
    raise exception using errcode = '22023', message = 'review payload must be an object';
  end if;
  if exists (
    select 1 from jsonb_object_keys(payload) field_name
    where field_name not in ('groups', 'mappings', 'unmap_names', 'request_id')
  ) then
    raise exception using errcode = '22023', message = 'unknown review payload field';
  end if;
  if not (payload ? 'groups') or not (payload ? 'mappings') or not (payload ? 'unmap_names')
     or not (payload ? 'request_id') or jsonb_typeof(payload->'request_id') <> 'string' then
    raise exception using errcode = '22023', message = 'review payload arrays are required';
  end if;
  begin
    request_uuid := btrim(payload->>'request_id')::uuid;
  exception when invalid_text_representation then
    raise exception using errcode = '22023', message = 'invalid request id';
  end;
  payload_fingerprint := encode(digest((payload - 'request_id')::text, 'sha256'), 'hex');
  perform pg_advisory_xact_lock(hashtextextended(request_uuid::text, 0));
  select ledger.payload_fingerprint, ledger.result
    into prior_fingerprint, prior_result
    from public.submission_ledger ledger where ledger.request_id = request_uuid;
  if found then
    if prior_fingerprint <> payload_fingerprint then
      raise exception using errcode = '23505', message = 'request id payload conflict';
    end if;
    return prior_result;
  end if;
  groups_value := payload->'groups';
  mappings_value := payload->'mappings';
  unmaps_value := payload->'unmap_names';
  if jsonb_typeof(groups_value) <> 'array'
     or jsonb_typeof(mappings_value) <> 'array'
     or jsonb_typeof(unmaps_value) <> 'array' then
    raise exception using errcode = '22023', message = 'groups, mappings, and unmap_names must be arrays';
  end if;

  drop table if exists pg_temp._review_groups;
  drop table if exists pg_temp._review_mappings;
  drop table if exists pg_temp._review_unmaps;
  create temporary table _review_groups (
    raw jsonb not null,
    temp_id text,
    canonical_title text,
    canonical_key text
  ) on commit drop;
  create temporary table _review_mappings (
    raw jsonb not null,
    cleaned_name text,
    lookup_key text,
    group_ref text
  ) on commit drop;
  create temporary table _review_unmaps (
    raw jsonb not null,
    cleaned_name text
  ) on commit drop;

  if exists (
    select 1 from jsonb_array_elements(groups_value) group_value
    where jsonb_typeof(group_value) <> 'object'
  ) then
    raise exception using errcode = '22023', message = 'invalid group';
  end if;
  if exists (
    select 1 from jsonb_array_elements(groups_value) group_value
    where not (group_value ? 'id')
       or jsonb_typeof(group_value->'id') <> 'string'
       or not (group_value ? 'canonical_title')
       or jsonb_typeof(group_value->'canonical_title') <> 'string'
       or not (group_value ? 'existing')
       or jsonb_typeof(group_value->'existing') <> 'boolean'
       or (group_value ? 'canonical_key'
           and jsonb_typeof(group_value->'canonical_key') <> 'string')
       or (group_value ? 'title_embedding'
           and not public.valid_review_embedding(group_value->'title_embedding'))
  ) then
    raise exception using errcode = '22023', message = 'invalid group';
  end if;
  if exists (
    select 1
    from jsonb_array_elements(groups_value) group_value,
         lateral jsonb_object_keys(group_value) field_name
    where field_name not in ('id', 'canonical_title', 'canonical_key', 'existing', 'title_embedding')
  ) then
    raise exception using errcode = '22023', message = 'unknown group field';
  end if;

  if exists (
    select 1 from jsonb_array_elements(mappings_value) mapping_value
    where jsonb_typeof(mapping_value) <> 'object'
  ) then
    raise exception using errcode = '22023', message = 'invalid mapping';
  end if;
  if exists (
    select 1 from jsonb_array_elements(mappings_value) mapping_value
    where not (mapping_value ? 'cleaned_name')
       or jsonb_typeof(mapping_value->'cleaned_name') <> 'string'
       or not (mapping_value ? 'group_id')
       or jsonb_typeof(mapping_value->'group_id') <> 'string'
       or (mapping_value ? 'lookup_key'
           and jsonb_typeof(mapping_value->'lookup_key') <> 'string')
       or (mapping_value ? 'member_embedding'
           and not public.valid_review_embedding(mapping_value->'member_embedding'))
  ) then
    raise exception using errcode = '22023', message = 'invalid mapping';
  end if;
  if exists (
    select 1
    from jsonb_array_elements(mappings_value) mapping_value,
         lateral jsonb_object_keys(mapping_value) field_name
    where field_name not in ('cleaned_name', 'lookup_key', 'group_id', 'member_embedding')
  ) then
    raise exception using errcode = '22023', message = 'unknown mapping field';
  end if;
  if exists (
    select 1 from jsonb_array_elements(unmaps_value) unmap_value
    where jsonb_typeof(unmap_value) <> 'string'
  ) then
    raise exception using errcode = '22023', message = 'invalid unmap name';
  end if;

  insert into _review_groups(raw, temp_id, canonical_title, canonical_key)
  select group_value,
         btrim(group_value->>'id'),
         btrim(group_value->>'canonical_title'),
         public.review_lookup_key(coalesce(group_value->>'canonical_key', group_value->>'canonical_title'))
  from jsonb_array_elements(groups_value) group_value;
  insert into _review_mappings(raw, cleaned_name, lookup_key, group_ref)
  select mapping_value,
         btrim(mapping_value->>'cleaned_name'),
         public.review_lookup_key(coalesce(mapping_value->>'lookup_key', mapping_value->>'cleaned_name')),
         btrim(mapping_value->>'group_id')
  from jsonb_array_elements(mappings_value) mapping_value;
  insert into _review_unmaps(raw, cleaned_name)
  select unmap_value, btrim(unmap_value #>> '{}')
  from jsonb_array_elements(unmaps_value) unmap_value;

  if exists (select 1 from _review_groups group by temp_id having count(*) > 1) then
    raise exception using errcode = '23505', message = 'duplicate group id';
  end if;
  if exists (select 1 from _review_groups
             group by lower(canonical_title) having count(*) > 1) then
    raise exception using errcode = '23505', message = 'duplicate group title';
  end if;
  if exists (select 1 from _review_groups group by canonical_key having count(*) > 1) then
    raise exception using errcode = '23505', message = 'duplicate group key';
  end if;
  if exists (select 1 from _review_mappings group by cleaned_name having count(*) > 1) then
    raise exception using errcode = '23505', message = 'duplicate mapping name';
  end if;
  if exists (select 1 from _review_mappings group by lookup_key having count(*) > 1) then
    raise exception using errcode = '23505', message = 'duplicate mapping key';
  end if;
  if exists (select 1 from _review_unmaps group by cleaned_name having count(*) > 1) then
    raise exception using errcode = '23505', message = 'duplicate unmap name';
  end if;

  for group_item in select * from _review_groups loop
    if jsonb_typeof(group_item.raw) <> 'object'
       or coalesce(group_item.temp_id, '') = ''
       or coalesce(group_item.canonical_title, '') = ''
       or (group_item.raw ? 'canonical_key' and btrim(coalesce(group_item.raw->>'canonical_key', '')) = '')
       or coalesce(group_item.canonical_key, '') = ''
       or not public.valid_review_embedding(group_item.raw->'title_embedding') then
      raise exception using errcode = '22023', message = 'invalid group';
    end if;
    if coalesce((group_item.raw->>'existing')::boolean, false) then
      begin resolved_id := group_item.temp_id::uuid;
      exception when invalid_text_representation then
        raise exception using errcode = '22023', message = 'invalid existing group id';
      end;
      update public.name_groups set
        canonical_title = group_item.canonical_title,
        canonical_key = group_item.canonical_key,
        title_embedding = case when group_item.raw ? 'title_embedding' and group_item.raw->'title_embedding' <> 'null'::jsonb
          then (group_item.raw->'title_embedding')::text::vector else title_embedding end
      where id = resolved_id;
      if not found then
        raise exception using errcode = 'P0002', message = 'existing group does not exist';
      end if;
    else
      insert into public.name_groups(canonical_title, canonical_key, title_embedding)
      values (group_item.canonical_title, group_item.canonical_key,
              case when group_item.raw ? 'title_embedding' and group_item.raw->'title_embedding' <> 'null'::jsonb
                then (group_item.raw->'title_embedding')::text::vector else null end)
      returning id into resolved_id;
      temp_map := temp_map || jsonb_build_object(group_item.temp_id, resolved_id::text);
    end if;
  end loop;

  for unmap_item in select * from _review_unmaps loop
    if jsonb_typeof(unmap_item.raw) <> 'string' or coalesce(unmap_item.cleaned_name, '') = '' then
      raise exception using errcode = '22023', message = 'invalid unmap name';
    end if;
    delete from public.name_mappings where cleaned_name = unmap_item.cleaned_name;
  end loop;

  for mapping_item in select * from _review_mappings loop
    if jsonb_typeof(mapping_item.raw) <> 'object'
       or coalesce(mapping_item.cleaned_name, '') = ''
       or coalesce(mapping_item.group_ref, '') = ''
       or (mapping_item.raw ? 'lookup_key' and btrim(coalesce(mapping_item.raw->>'lookup_key', '')) = '')
       or coalesce(mapping_item.lookup_key, '') = ''
       or not public.valid_review_embedding(mapping_item.raw->'member_embedding') then
      raise exception using errcode = '22023', message = 'invalid mapping';
    end if;
    begin
      resolved_id := coalesce(temp_map ->> mapping_item.group_ref, mapping_item.group_ref)::uuid;
    exception when invalid_text_representation then
      raise exception using errcode = '22023', message = 'mapping references unknown group';
    end;
    if exists (select 1 from public.name_mappings n
               where n.cleaned_name = mapping_item.cleaned_name
               and n.group_id <> resolved_id) then
      raise exception using errcode = '23505', message = 'mapping conflict requires explicit unmap';
    end if;
    insert into public.name_mappings(cleaned_name, lookup_key, group_id, member_embedding)
    values (mapping_item.cleaned_name, mapping_item.lookup_key,
            resolved_id,
            case when mapping_item.raw ? 'member_embedding' and mapping_item.raw->'member_embedding' <> 'null'::jsonb
              then (mapping_item.raw->'member_embedding')::text::vector else null end)
    on conflict (cleaned_name) do update set
      lookup_key = excluded.lookup_key, group_id = excluded.group_id,
      member_embedding = case when mapping_item.raw ? 'member_embedding'
        then excluded.member_embedding else public.name_mappings.member_embedding end;
  end loop;
  insert into public.submission_ledger(request_id, payload_fingerprint, result)
  values (request_uuid, payload_fingerprint, temp_map);
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
