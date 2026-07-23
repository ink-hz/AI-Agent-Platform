\set ON_ERROR_STOP on

begin;

create schema if not exists platform_identity authorization flywheel_owner;

create table if not exists platform_identity.dingtalk_directory_members (
  staff_id text primary key,
  display_name text not null check (btrim(display_name) <> ''),
  normalized_name text not null check (btrim(normalized_name) <> ''),
  departments jsonb not null default '[]'::jsonb
    check (jsonb_typeof(departments) = 'array'),
  active boolean not null default true,
  source_updated_at timestamptz,
  source_synced_at timestamptz not null
);

create index if not exists idx_dingtalk_directory_active_name
  on platform_identity.dingtalk_directory_members (normalized_name)
  where active;

create or replace function platform_identity.refresh_dingtalk_matches()
returns integer
language plpgsql
security definer
set search_path = pg_catalog, platform_identity, flywheel_identity
as $$
declare
  matched integer;
begin
  with active_name_counts as (
    select normalized_name, count(*) as member_count
    from platform_identity.dingtalk_directory_members
    where active
    group by normalized_name
  ), unique_members as (
    select member.*
    from platform_identity.dingtalk_directory_members member
    join active_name_counts counts using (normalized_name)
    where member.active and counts.member_count = 1
  )
  update flywheel_identity.external_identities identity
  set department = nullif(
        (select string_agg(value, ' / ' order by ordinal)
         from jsonb_array_elements_text(member.departments) with ordinality item(value, ordinal)),
        ''
      ),
      attributes = coalesce(identity.attributes, '{}'::jsonb) || jsonb_build_object(
        'dingtalk_match', jsonb_build_object(
          'staff_id', member.staff_id,
          'active', true,
          'match_method', 'exact_unique_name',
          'source_synced_at', member.source_synced_at
        )
      )
  from unique_members member
  where identity.provider = 'feishu'
    and nullif(btrim(identity.display_name), '') is not null
    and normalize(btrim(identity.display_name), NFKC) = member.normalized_name;

  get diagnostics matched = row_count;
  return matched;
end;
$$;

alter table platform_identity.dingtalk_directory_members owner to flywheel_owner;
alter function platform_identity.refresh_dingtalk_matches() owner to flywheel_owner;

revoke all on schema platform_identity from public;
revoke all on all tables in schema platform_identity from public;
revoke all on function platform_identity.refresh_dingtalk_matches() from public;

grant usage on schema platform_identity to platform_sync_writer;
grant select, insert, update on platform_identity.dingtalk_directory_members
  to platform_sync_writer;
grant execute on function platform_identity.refresh_dingtalk_matches()
  to platform_sync_writer;

commit;
