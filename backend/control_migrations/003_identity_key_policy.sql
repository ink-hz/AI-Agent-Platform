create table platform_control.provider_identity_key_policies (
  provider text primary key check (provider = 'dingtalk'),
  lookup_transition_versions integer[] not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (array_ndims(lookup_transition_versions) = 1),
  check (array_lower(lookup_transition_versions, 1) = 1),
  check (cardinality(lookup_transition_versions) between 1 and 3),
  check (lookup_transition_versions[1] > 0),
  check (
    cardinality(lookup_transition_versions) < 2
    or (
      lookup_transition_versions[2] is not null
      and lookup_transition_versions[2] = lookup_transition_versions[1] + 1
    )
  ),
  check (
    cardinality(lookup_transition_versions) < 3
    or (
      lookup_transition_versions[3] is not null
      and lookup_transition_versions[3] = lookup_transition_versions[2] + 1
    )
  )
);

create function platform_control.set_provider_identity_key_policy(
  selected_provider text,
  selected_versions integer[]
) returns void
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_provider <> 'dingtalk'
     or selected_versions is null
     or array_ndims(selected_versions) <> 1
     or array_lower(selected_versions, 1) <> 1
     or cardinality(selected_versions) not between 1 and 3
     or selected_versions[1] is null
     or selected_versions[1] <= 0
     or (
       cardinality(selected_versions) >= 2
       and (
         selected_versions[2] is null
         or selected_versions[2] <> selected_versions[1] + 1
       )
     )
     or (
       cardinality(selected_versions) >= 3
       and (
         selected_versions[3] is null
         or selected_versions[3] <> selected_versions[2] + 1
       )
     )
  then
    raise check_violation using message = 'provider identity key policy invalid';
  end if;

  perform pg_advisory_xact_lock(1229998928);
  insert into platform_control.provider_identity_key_policies (
    provider,
    lookup_transition_versions
  ) values (
    selected_provider,
    selected_versions
  )
  on conflict (provider) do update set
    lookup_transition_versions = excluded.lookup_transition_versions,
    updated_at = now();
end
$function$;

revoke all on function platform_control.set_provider_identity_key_policy(
  text, integer[]
) from public;
revoke all on platform_control.provider_identity_key_policies from public;

do $migration$
declare
  selected_suffix text;
  selected_app text;
  selected_maintenance text;
  role_name text;
begin
  case current_user
    when 'platform_control_owner' then
      selected_suffix := '';
    when 'platform_control_owner_preview' then
      selected_suffix := '_preview';
    else
      raise insufficient_privilege using
        message = 'control migration must run as an approved owner role';
  end case;

  selected_app := 'platform_control_app' || selected_suffix;
  selected_maintenance := 'platform_control_maintenance' || selected_suffix;

  foreach role_name in array array[
    'platform_control_migrator',
    'platform_control_app',
    'platform_directory_worker',
    'platform_stream_ingest',
    'platform_audit_append',
    'platform_control_maintenance',
    'platform_control_migrator_preview',
    'platform_control_app_preview',
    'platform_directory_worker_preview',
    'platform_stream_ingest_preview',
    'platform_audit_append_preview',
    'platform_control_maintenance_preview'
  ] loop
    execute format(
      'revoke all on platform_control.provider_identity_key_policies from %I',
      role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.set_provider_identity_key_policy(text, integer[]) '
      'from %I',
      role_name
    );
  end loop;

  execute format(
    'grant select, insert on '
    'platform_control.provider_identity_key_policies to %I',
    selected_app
  );
  execute format(
    'grant execute on function '
    'platform_control.set_provider_identity_key_policy(text, integer[]) to %I',
    selected_maintenance
  );
end
$migration$;
