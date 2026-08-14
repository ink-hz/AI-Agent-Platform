alter table platform_control.management_mutations
  drop constraint management_mutations_action_check,
  add constraint management_mutations_action_check check (action in (
    'assign_viewer', 'revoke_viewer',
    'grant_scope', 'revoke_scope',
    'assign_admin', 'revoke_admin',
    'bind_owner', 'replace_owner'
  ));

create or replace function platform_control.require_management_actor(actor_id uuid)
returns void
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if actor_id is null or not exists (
    select 1 from platform_control.internal_users
    where internal_user_id = actor_id
      and role in ('platform_owner', 'platform_admin')
      and status = 'active'
      and locally_invalidated_at is null
  ) then
    raise insufficient_privilege using
      message = 'active platform management actor required';
  end if;
end
$function$;

create function platform_control.require_platform_owner(actor_id uuid)
returns void
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if actor_id is null or not exists (
    select 1 from platform_control.internal_users
    where internal_user_id = actor_id
      and role = 'platform_owner'
      and status = 'active'
      and locally_invalidated_at is null
  ) then
    raise insufficient_privilege using message = 'active platform owner required';
  end if;
end
$function$;

create function platform_control.validate_admin_audit_event_v25(
  actor_id uuid,
  event_name text,
  target_name text,
  target_id text,
  correlation_id uuid,
  event_result text,
  reason text,
  details jsonb
) returns void
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  expected_reason text;
  expected_keys text[];
  expected_previous_role text;
  expected_new_role text;
  actual_keys text[];
  key_name text;
begin
  case event_name
    when 'admin_role_assignment_requested' then
      expected_reason := 'admin_access_approved';
      expected_previous_role := 'member';
      expected_new_role := 'platform_admin';
      expected_keys := array[
        'expected_row_version','new_role','operation_id','previous_role','result'
      ];
    when 'admin_role_revocation_requested' then
      expected_reason := 'admin_access_revoked';
      expected_previous_role := 'platform_admin';
      expected_new_role := 'member';
      expected_keys := array[
        'expected_row_version','new_role','operation_id','previous_role','result'
      ];
    when 'admin_role_assignment_completed' then
      expected_reason := 'admin_access_approved';
      expected_previous_role := 'member';
      expected_new_role := 'platform_admin';
      expected_keys := array[
        'linked_audit_event_id','new_role','new_scopes','operation_id',
        'previous_role','previous_scopes','result','row_version',
        'session_revocation_count'
      ];
    when 'admin_role_revocation_completed' then
      expected_reason := 'admin_access_revoked';
      expected_previous_role := 'platform_admin';
      expected_new_role := 'member';
      expected_keys := array[
        'linked_audit_event_id','new_role','new_scopes','operation_id',
        'previous_role','previous_scopes','result','row_version',
        'session_revocation_count'
      ];
    when 'admin_role_assignment_failed' then
      expected_reason := 'admin_access_approved';
      expected_keys := array[
        'error_code','linked_audit_event_id','operation_id','result'
      ];
    when 'admin_role_revocation_failed' then
      expected_reason := 'admin_access_revoked';
      expected_keys := array[
        'error_code','linked_audit_event_id','operation_id','result'
      ];
    else
      raise check_violation using message = 'audit event invalid';
  end case;

  if actor_id is null or target_id is null or correlation_id is null
     or target_name <> 'internal_user'
     or event_result <> split_part(
       event_name, '_', array_length(string_to_array(event_name, '_'), 1)
     )
     or reason <> expected_reason
     or jsonb_typeof(details) <> 'object'
  then
    raise check_violation using message = 'audit event invalid';
  end if;
  perform target_id::uuid;
  select array_agg(value order by value) into actual_keys
  from jsonb_object_keys(details) key(value);
  if actual_keys is distinct from expected_keys
     or details->>'operation_id' <> correlation_id::text
     or details->>'result' <> event_result
  then
    raise check_violation using message = 'audit event invalid';
  end if;
  foreach key_name in array expected_keys loop
    if jsonb_typeof(details->key_name) = 'null' then
      raise check_violation using message = 'audit metadata invalid';
    end if;
  end loop;
  perform (details->>'operation_id')::uuid;

  if event_result in ('requested', 'completed') and (
    details->>'previous_role' <> expected_previous_role
    or details->>'new_role' <> expected_new_role
  ) then
    raise check_violation using message = 'audit metadata invalid';
  end if;
  if event_result = 'requested' and (
    jsonb_typeof(details->'expected_row_version') <> 'number'
    or details->>'expected_row_version' !~ '^[0-9]+$'
  ) then
    raise check_violation using message = 'audit metadata invalid';
  end if;
  if event_result in ('completed', 'failed') then
    if jsonb_typeof(details->'linked_audit_event_id') <> 'string' then
      raise check_violation using message = 'audit metadata invalid';
    end if;
    perform (details->>'linked_audit_event_id')::uuid;
  end if;
  if event_result = 'completed' then
    foreach key_name in array array['row_version','session_revocation_count'] loop
      if jsonb_typeof(details->key_name) <> 'number'
         or details->>key_name !~ '^[0-9]+$'
      then
        raise check_violation using message = 'audit metadata invalid';
      end if;
    end loop;
    foreach key_name in array array['previous_scopes','new_scopes'] loop
      if jsonb_typeof(details->key_name) <> 'array'
         or jsonb_array_length(details->key_name) > 256
         or exists (
           select 1 from jsonb_array_elements(details->key_name) item(value)
           where jsonb_typeof(item.value) <> 'string'
              or item.value #>> '{}' !~
                 '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
         )
         or details->key_name <> coalesce((
           select jsonb_agg(to_jsonb(canonical.value) order by canonical.value)
           from (
             select distinct item.value #>> '{}' as value
             from jsonb_array_elements(details->key_name) item(value)
           ) canonical
         ), '[]'::jsonb)
      then
        raise check_violation using message = 'audit metadata invalid';
      end if;
    end loop;
  elsif event_result = 'failed' and (
    jsonb_typeof(details->'error_code') <> 'string'
    or details->>'error_code' not in ('business_rejected','control_unavailable')
  ) then
    raise check_violation using message = 'audit metadata invalid';
  end if;
exception
  when invalid_text_representation then
    raise check_violation using message = 'audit metadata invalid';
end
$function$;

create or replace function platform_control.append_audit_event(
  event_id uuid,
  actor_id uuid,
  event_name text,
  target_name text,
  target_id text,
  correlation_id uuid,
  event_result text,
  reason text,
  details jsonb
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  stored platform_control.audit_events%rowtype;
  summary_keys constant text[] := array[
    'linked_audit_event_id','new_role','new_scope_count','new_scope_sha256',
    'operation_id','previous_role','previous_scope_count',
    'previous_scope_sha256','result','row_version','session_revocation_count'
  ];
  actual_keys text[];
begin
  select array_agg(value order by value) into actual_keys
  from jsonb_object_keys(details) key(value);
  if event_name like 'admin_role_%' then
    perform platform_control.validate_admin_audit_event_v25(
      actor_id, event_name, target_name, target_id, correlation_id,
      event_result, reason, details
    );
  elsif event_name = 'viewer_role_revocation_completed'
     and actual_keys = summary_keys
  then
    perform platform_control.validate_viewer_revocation_summary(
      actor_id, event_name, target_name, target_id, correlation_id,
      event_result, reason, details
    );
  else
    perform platform_control.validate_audit_event_v2(
      actor_id, event_name, target_name, target_id, correlation_id,
      event_result, reason, details
    );
  end if;
  insert into platform_control.audit_events (
    audit_event_id, actor_internal_user_id, event_type, target_type,
    target_internal_id, request_id, result, reason_code,
    sanitized_before_after
  ) values (
    event_id, actor_id, event_name, target_name, target_id,
    correlation_id, event_result, reason, details
  ) on conflict (audit_event_id) do nothing;
  select * into strict stored
  from platform_control.audit_events
  where audit_event_id = event_id;
  if stored.actor_internal_user_id is distinct from actor_id
     or stored.event_type is distinct from event_name
     or stored.target_type is distinct from target_name
     or stored.target_internal_id is distinct from target_id
     or stored.request_id is distinct from correlation_id
     or stored.result is distinct from event_result
     or stored.reason_code is distinct from reason
     or stored.sanitized_before_after is distinct from details
  then
    raise unique_violation using message = 'audit event identity collision';
  end if;
  return event_id;
end
$function$;

create function platform_control.assign_platform_admin(
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
  result_snapshot jsonb;
begin
  if selected_operation_id is null or selected_actor_id is null
     or selected_target_id is null or selected_audit_event_id is null
     or selected_expected_row_version is null
     or selected_expected_row_version < 0
  then
    raise check_violation using message = 'admin assignment precondition invalid';
  end if;
  perform platform_control.lock_dingtalk_identity_directory();
  perform pg_advisory_xact_lock(hashtextextended(selected_operation_id::text, 0));
  perform platform_control.require_platform_owner(selected_actor_id);
  replay := platform_control.replay_management_mutation(
    selected_operation_id, 'assign_admin', selected_actor_id,
    selected_target_id, null, null, selected_expected_row_version, 0,
    null, selected_audit_event_id
  );
  if replay is not null then return replay; end if;
  perform platform_control.require_requested_audit(
    selected_audit_event_id, selected_operation_id, selected_actor_id,
    'admin_role_assignment_requested', 'internal_user',
    selected_target_id::text, 'admin_access_approved'
  );
  if not exists (
    select 1 from platform_control.audit_events event
    where event.audit_event_id = selected_audit_event_id
      and event.sanitized_before_after->>'previous_role' = 'member'
      and event.sanitized_before_after->>'new_role' = 'platform_admin'
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
    and users.status = 'active'
    and users.locally_invalidated_at is null
    and state.last_complete_at > clock_timestamp() - interval '24 hours'
    and users.last_confirmed_generation_id = generation.generation_id
  for update of users;
  if not found or prior_role <> 'member'
     or current_version <> selected_expected_row_version
  then
    raise check_violation using message = 'admin assignment precondition failed';
  end if;
  update platform_control.internal_users
  set role = 'platform_admin',
      role_audit_event_id = selected_audit_event_id,
      row_version = row_version + 1,
      updated_at = clock_timestamp()
  where internal_user_id = selected_target_id;
  result_snapshot := jsonb_build_object(
    'operation_id', selected_operation_id::text,
    'previous_role', prior_role,
    'new_role', 'platform_admin',
    'row_version', current_version + 1,
    'session_revocation_count', 0,
    'previous_scopes', '[]'::jsonb,
    'new_scopes', '[]'::jsonb
  );
  insert into platform_control.management_mutations (
    operation_id, action, actor_internal_user_id, target_internal_user_id,
    expected_target_row_version, expected_causal_row_version,
    requested_audit_event_id, requested_audit_id_copy, applied_result
  ) values (
    selected_operation_id, 'assign_admin', selected_actor_id,
    selected_target_id, selected_expected_row_version, 0,
    selected_audit_event_id, selected_audit_event_id, result_snapshot
  );
  return result_snapshot;
end
$function$;

create function platform_control.revoke_platform_admin(
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
    and users.status = 'active'
    and users.locally_invalidated_at is null
    and state.last_complete_at > clock_timestamp() - interval '24 hours'
    and users.last_confirmed_generation_id = generation.generation_id
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

revoke all on function platform_control.require_platform_owner(uuid) from public;
revoke all on function platform_control.validate_admin_audit_event_v25(
  uuid, text, text, text, uuid, text, text, jsonb
) from public;
revoke all on function platform_control.assign_platform_admin(
  uuid, uuid, uuid, bigint, uuid
) from public;
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
      'revoke all on function platform_control.require_platform_owner(uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.validate_admin_audit_event_v25(uuid,text,text,text,uuid,text,text,jsonb) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.assign_platform_admin(uuid,uuid,uuid,bigint,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.revoke_platform_admin(uuid,uuid,uuid,bigint,uuid) from %I',
      role_name
    );
  end loop;

  execute format(
    'grant execute on function platform_control.assign_platform_admin(uuid,uuid,uuid,bigint,uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.revoke_platform_admin(uuid,uuid,uuid,bigint,uuid) to %I',
    selected_app
  );
end
$migration$;
