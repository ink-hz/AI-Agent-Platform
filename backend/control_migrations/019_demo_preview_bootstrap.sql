create table platform_control.demo_directory_bootstraps (
  generation_id uuid primary key references
    platform_control.directory_generations(generation_id) on delete cascade,
  declared_member_count integer not null
    check (declared_member_count between 1 and 3),
  protected_digest bytea not null
    check (octet_length(protected_digest) = 32),
  created_at timestamptz not null default now()
);

create function platform_control.begin_demo_directory_generation(
  selected_generation_id uuid,
  selected_member_count integer,
  selected_protected_digest bytea
) returns void
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if current_database() <> 'agent_platform_control_preview'
     or selected_generation_id is null
     or selected_member_count is null
     or selected_member_count not between 1 and 3
     or selected_protected_digest is null
     or octet_length(selected_protected_digest) <> 32
  then
    raise check_violation using message = 'demo directory generation invalid';
  end if;

  if not pg_try_advisory_xact_lock(1229998928) then
    raise check_violation using message = 'directory staging already in progress';
  end if;
  if exists (
    select 1
    from platform_control.directory_generations generation
    where generation.status = 'staging'
  ) then
    raise check_violation using message = 'directory staging already in progress';
  end if;

  insert into platform_control.directory_generations (
    generation_id, status, member_count, department_count, content_sha256
  ) values (
    selected_generation_id, 'staging', selected_member_count, 0,
    encode(selected_protected_digest, 'hex')
  );
  insert into platform_control.demo_directory_bootstraps (
    generation_id, declared_member_count, protected_digest
  ) values (
    selected_generation_id, selected_member_count, selected_protected_digest
  );
end
$function$;

revoke all on table platform_control.demo_directory_bootstraps from public;
revoke all on function platform_control.begin_demo_directory_generation(
  uuid,integer,bytea
) from public;

do $migration$
declare
  role_name name;
begin
  if not (
    (current_database() = 'agent_platform_control'
      and current_user = 'platform_control_owner')
    or
    (current_database() = 'agent_platform_control_preview'
      and current_user = 'platform_control_owner_preview')
  ) then
    raise insufficient_privilege using
      message = 'demo preview bootstrap migration owner/environment mismatch';
  end if;

  foreach role_name in array array[
    'platform_control_migrator', 'platform_control_app',
    'platform_directory_worker', 'platform_stream_ingest',
    'platform_audit_append', 'platform_control_maintenance',
    'platform_control_migrator_preview', 'platform_control_app_preview',
    'platform_directory_worker_preview', 'platform_stream_ingest_preview',
    'platform_audit_append_preview', 'platform_control_maintenance_preview'
  ] loop
    execute format(
      'revoke all on function '
      'platform_control.begin_demo_directory_generation(uuid,integer,bytea) '
      'from %I', role_name
    );
    execute format(
      'revoke all on table platform_control.demo_directory_bootstraps from %I',
      role_name
    );
  end loop;

  if current_database() = 'agent_platform_control_preview' then
    grant select on platform_control.provider_identity_key_policies
      to platform_directory_worker_preview;
    grant execute on function
      platform_control.begin_demo_directory_generation(uuid,integer,bytea)
      to platform_directory_worker_preview;
  end if;
end
$migration$;
