create or replace function platform_control.revoke_platform_admin(
  selected_operation_id uuid,
  selected_actor_id uuid,
  selected_target_id uuid,
  selected_expected_row_version bigint,
  selected_audit_event_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  replay jsonb;
  prior_role text;
  current_version bigint;
  session_count bigint;
  result_snapshot jsonb;
begin
  if selected_operation_id is null or selected_actor_id is null
     or selected_target_id is null or selected_audit_event_id is null
     or selected_expected_row_version is null
     or selected_expected_row_version < 0
  then
    raise check_violation using message = 'admin revocation precondition invalid';
  end if;
  perform platform_control.lock_dingtalk_identity_directory();
  perform pg_advisory_xact_lock(hashtextextended(selected_operation_id::text, 0));
  perform platform_control.require_platform_owner(selected_actor_id);
  replay := platform_control.replay_management_mutation(
    selected_operation_id, 'revoke_admin', selected_actor_id,
    selected_target_id, null, null, selected_expected_row_version, 0,
    null, selected_audit_event_id
  );
  if replay is not null then return replay; end if;
  perform platform_control.require_requested_audit(
    selected_audit_event_id, selected_operation_id, selected_actor_id,
    'admin_role_revocation_requested', 'internal_user',
    selected_target_id::text, 'admin_access_revoked'
  );
  if not exists (
    select 1 from platform_control.audit_events event
    where event.audit_event_id = selected_audit_event_id
      and event.sanitized_before_after->>'previous_role' = 'platform_admin'
      and event.sanitized_before_after->>'new_role' = 'member'
      and (event.sanitized_before_after->>'expected_row_version')::bigint
          = selected_expected_row_version
  ) then
    raise check_violation using message = 'audit payload mismatch';
  end if;
  select users.role::text, users.row_version into prior_role, current_version
  from platform_control.internal_users users
  join platform_control.directory_state state on state.singleton
  join platform_control.directory_generations generation
    on generation.generation_id = state.active_generation_id
   and generation.status = 'complete'
  where users.internal_user_id = selected_target_id
    and state.last_complete_at > clock_timestamp() - interval '24 hours'
  for update of users;
  if not found or prior_role <> 'platform_admin'
     or current_version <> selected_expected_row_version
  then
    raise check_violation using message = 'admin revocation precondition failed';
  end if;
  update platform_control.internal_users
  set role = 'member',
      role_audit_event_id = selected_audit_event_id,
      row_version = row_version + 1,
      updated_at = clock_timestamp()
  where internal_user_id = selected_target_id;
  update platform_control.web_sessions
  set revoked_at = clock_timestamp(), revoked_reason = 'admin_role_revoked'
  where internal_user_id = selected_target_id and revoked_at is null;
  get diagnostics session_count = row_count;
  result_snapshot := jsonb_build_object(
    'operation_id', selected_operation_id::text,
    'previous_role', prior_role,
    'new_role', 'member',
    'row_version', current_version + 1,
    'session_revocation_count', session_count,
    'previous_scopes', '[]'::jsonb,
    'new_scopes', '[]'::jsonb
  );
  insert into platform_control.management_mutations (
    operation_id, action, actor_internal_user_id, target_internal_user_id,
    expected_target_row_version, expected_causal_row_version,
    requested_audit_event_id, requested_audit_id_copy, applied_result
  ) values (
    selected_operation_id, 'revoke_admin', selected_actor_id,
    selected_target_id, selected_expected_row_version, 0,
    selected_audit_event_id, selected_audit_event_id, result_snapshot
  );
  return result_snapshot;
end
$function$;

revoke all on function platform_control.revoke_platform_admin(
  uuid, uuid, uuid, bigint, uuid
) from public;

do $migration$
declare
  selected_suffix text;
  selected_app text;
  role_name text;
begin
  case current_user
    when 'platform_control_owner' then selected_suffix := '';
    when 'platform_control_owner_preview' then selected_suffix := '_preview';
    else
      raise insufficient_privilege using
        message = 'control migration must run as an approved owner role';
  end case;
  selected_app := 'platform_control_app' || selected_suffix;

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
      'revoke all on function platform_control.revoke_platform_admin(uuid,uuid,uuid,bigint,uuid) from %I',
      role_name
    );
  end loop;

  execute format(
    'grant execute on function platform_control.revoke_platform_admin(uuid,uuid,uuid,bigint,uuid) to %I',
    selected_app
  );
end
$migration$;
