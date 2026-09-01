create function platform_control.read_current_inactive_staff_member_v61(
  selected_lookup_version integer,
  selected_lookup_hmac bytea,
  selected_union_lookup_version integer,
  selected_union_lookup_hmac bytea
) returns table(member_status text)
language sql
security definer
set search_path = pg_catalog, platform_control
as $function$
  select member.status
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
   and generation.status='complete'
  join platform_control.directory_members member
    on member.generation_id=generation.generation_id
  where state.singleton
    and member.status in ('inactive','disabled')
    and member.lookup_key_version=selected_lookup_version
    and member.lookup_hmac=selected_lookup_hmac
    and member.union_lookup_key_version=selected_union_lookup_version
    and member.union_lookup_hmac=selected_union_lookup_hmac;
$function$;

revoke all on function platform_control.read_current_inactive_staff_member_v61(
  integer,bytea,integer,bytea
) from public;

do $migration$
declare
  selected_app name;
  role_name name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner'
  then
    selected_app := 'platform_control_app';
  elsif current_database()='agent_platform_control_preview'
     and current_user='platform_control_owner_preview'
  then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using
      message='VOC inactive staff projection owner/environment mismatch';
  end if;

  foreach role_name in array array[
    'platform_control_migrator','platform_control_app',
    'platform_directory_worker','platform_stream_ingest',
    'platform_audit_append','platform_control_maintenance',
    'platform_brain_worker','platform_control_migrator_preview',
    'platform_control_app_preview','platform_directory_worker_preview',
    'platform_stream_ingest_preview','platform_audit_append_preview',
    'platform_control_maintenance_preview','platform_brain_worker_preview'
  ] loop
    execute format(
      'revoke all on function '
      'platform_control.read_current_inactive_staff_member_v61(integer,bytea,integer,bytea) from %I',
      role_name
    );
  end loop;

  execute format(
    'revoke select on platform_control.directory_members from %I',
    selected_app
  );
  execute format(
    'grant execute on function '
    'platform_control.read_current_inactive_staff_member_v61(integer,bytea,integer,bytea) to %I',
    selected_app
  );
end
$migration$;
