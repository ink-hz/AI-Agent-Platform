create function platform_control.resolve_active_fae_workbench_member_v73(
  selected_display_name text
) returns table(generation_id uuid, member_key uuid)
language sql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
  select state.active_generation_id,member.member_key
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
   and generation.status='complete'
  join platform_control.directory_members member
    on member.generation_id=generation.generation_id
  where state.singleton
    and selected_display_name is not null
    and member.subject_kind='employee'
    and member.status='active'
    and member.display_name=selected_display_name;
$function$;

revoke all on function
  platform_control.resolve_active_fae_workbench_member_v73(text)
from public;

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
      message='FAE workbench member resolver owner/environment mismatch';
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
      'platform_control.resolve_active_fae_workbench_member_v73(text) from %I',
      role_name
    );
  end loop;

  execute format(
    'revoke select on platform_control.directory_state, '
    'platform_control.directory_generations, '
    'platform_control.directory_members from %I',
    selected_app
  );
  execute format(
    'grant execute on function '
    'platform_control.resolve_active_fae_workbench_member_v73(text) to %I',
    selected_app
  );
end
$migration$;
