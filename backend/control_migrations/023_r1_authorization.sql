create function platform_control.has_observation_scope_v23(
  selected_actor_id uuid,
  selected_agent_id text
) returns boolean
language sql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
  select exists (
    select 1 from platform_control.internal_users users
    join platform_control.observation_grants grant_row
      on grant_row.viewer_internal_user_id=users.internal_user_id
     and grant_row.agent_id=selected_agent_id
     and grant_row.revoked_at is null
    where users.internal_user_id=selected_actor_id
      and users.status='active'
      and users.locally_invalidated_at is null
      and users.role='management_viewer'
      and selected_agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
  )
$function$;

create function platform_control.append_authorized_read_v23(
  selected_event_id uuid,
  selected_actor_id uuid,
  selected_agent_id text,
  selected_target text,
  selected_request_id uuid
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare selected_role platform_control.user_role;
begin
  select role into selected_role from platform_control.internal_users
  where internal_user_id=selected_actor_id and status='active'
    and locally_invalidated_at is null;
  if not found or selected_role <> 'management_viewer'
     or selected_target not in ('governance_audit','management_projection')
     or (selected_target='governance_audit' and selected_agent_id is not null)
     or (
       selected_target='management_projection' and not
       platform_control.has_observation_scope_v23(
         selected_actor_id,selected_agent_id
       )
     )
  then raise check_violation using message='authorized read audit rejected'; end if;
  insert into platform_control.audit_events(
    audit_event_id,actor_internal_user_id,event_type,target_type,
    target_internal_id,request_id,result,reason_code,sanitized_before_after
  ) values (
    selected_event_id,selected_actor_id,'scoped_management_read_completed',
    'platform_management',coalesce(selected_agent_id,'governance_audit'),
    selected_request_id,'completed','privileged_read',
    jsonb_build_object(
      'scope_kind',case when selected_agent_id is null
        then 'governance' else 'exact_agent' end,
      'agent_id',selected_agent_id
    )
  );
  return selected_event_id;
end
$function$;

revoke all on function platform_control.has_observation_scope_v23(uuid,text)
from public;
revoke all on function platform_control.append_authorized_read_v23(
  uuid,uuid,text,text,uuid
) from public;

do $migration$
declare selected_app name; selected_audit name; role_name name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner'
  then selected_app:='platform_control_app';
       selected_audit:='platform_audit_append';
  elsif current_database()='agent_platform_control_preview'
     and current_user='platform_control_owner_preview'
  then selected_app:='platform_control_app_preview';
       selected_audit:='platform_audit_append_preview';
  else raise insufficient_privilege using
    message='R1 authorization owner/environment mismatch';
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
      'revoke all on function platform_control.has_observation_scope_v23('
      'uuid,text) from %I',role_name
    );
    execute format(
      'revoke all on function platform_control.append_authorized_read_v23('
      'uuid,uuid,text,text,uuid) from %I',role_name
    );
  end loop;
  execute format(
    'grant execute on function platform_control.has_observation_scope_v23('
    'uuid,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_control.append_authorized_read_v23('
    'uuid,uuid,text,text,uuid) to %I',selected_audit
  );
end
$migration$;
