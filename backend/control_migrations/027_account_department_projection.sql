create function platform_control.read_account_departments_v27(
  selected_internal_user_id uuid
) returns text[]
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  selected_departments text[];
begin
  if selected_internal_user_id is null then
    raise check_violation using message='account department internal user invalid';
  end if;

  if exists (
    select 1
    from platform_control.directory_state state
    join platform_control.directory_generations generation
      on generation.generation_id=state.active_generation_id
     and generation.status='complete'
    join platform_control.directory_members member
      on member.generation_id=generation.generation_id
     and member.internal_user_id=selected_internal_user_id
     and member.status='active'
    join platform_control.member_departments membership
      on membership.generation_id=member.generation_id
     and membership.member_key=member.member_key
    join platform_control.directory_departments department
      on department.generation_id=membership.generation_id
     and department.department_key=membership.department_key
    where state.singleton
      and (
        department.display_name is null
        or length(btrim(department.display_name)) not between 1 and 256
      )
  ) then
    raise check_violation using message='account department display invalid';
  end if;

  select coalesce(
    array_agg(distinct btrim(department.display_name)
              order by btrim(department.display_name)),
    array[]::text[]
  ) into selected_departments
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
   and generation.status='complete'
  join platform_control.directory_members member
    on member.generation_id=generation.generation_id
   and member.internal_user_id=selected_internal_user_id
   and member.status='active'
  join platform_control.member_departments membership
    on membership.generation_id=member.generation_id
   and membership.member_key=member.member_key
  join platform_control.directory_departments department
    on department.generation_id=membership.generation_id
   and department.department_key=membership.department_key
  where state.singleton;

  return selected_departments;
end
$function$;

revoke all on function platform_control.read_account_departments_v27(uuid)
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
      message='account department projection owner/environment mismatch';
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
      'revoke all on function platform_control.read_account_departments_v27(uuid) from %I',
      role_name
    );
  end loop;

  execute format(
    'grant execute on function platform_control.read_account_departments_v27(uuid) to %I',
    selected_app
  );
end
$migration$;
