create function platform_control.read_account_gender_v29(
  selected_internal_user_id uuid
) returns text
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  selected_count bigint;
  selected_gender text;
begin
  if selected_internal_user_id is null then
    raise check_violation using message='account gender projection invalid';
  end if;

  select count(*),min(member.gender)
    into selected_count,selected_gender
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
   and generation.status='complete'
  join platform_control.directory_members member
    on member.generation_id=generation.generation_id
   and member.internal_user_id=selected_internal_user_id
   and member.status='active'
  where state.singleton;

  if selected_count > 1
     or (selected_gender is not null
         and selected_gender not in ('male','female'))
  then
    raise check_violation using message='account gender projection invalid';
  end if;

  return selected_gender;
end
$function$;

revoke all on function platform_control.read_account_gender_v29(uuid)
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
      message='account gender projection owner/environment mismatch';
  end if;

  foreach role_name in array array[
    'platform_control_migrator','platform_control_app',
    'platform_directory_worker','platform_stream_ingest',
    'platform_audit_append','platform_control_maintenance',
    'platform_control_migrator_preview','platform_control_app_preview',
    'platform_directory_worker_preview','platform_stream_ingest_preview',
    'platform_audit_append_preview','platform_control_maintenance_preview'
  ] loop
    execute format(
      'revoke all on function platform_control.read_account_gender_v29(uuid) from %I',
      role_name
    );
  end loop;

  execute format(
    'revoke select on platform_control.directory_members from %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.read_account_gender_v29(uuid) to %I',
    selected_app
  );
end
$migration$;
