alter table platform_control.internal_users
  add column row_version bigint not null default 0
  check (row_version >= 0);

alter table platform_control.observation_grants
  add column row_version bigint not null default 0
  check (row_version >= 0);

alter table platform_control.internal_users
  drop constraint internal_users_role_audit_event_id_fkey,
  add constraint internal_users_role_audit_event_id_fkey
    foreign key (role_audit_event_id)
    references platform_control.audit_events(audit_event_id)
    on delete set null;

alter table platform_control.observation_grants
  drop constraint observation_grants_created_audit_event_id_fkey,
  drop constraint observation_grants_revoked_audit_event_id_fkey,
  add constraint observation_grants_created_audit_event_id_fkey
    foreign key (created_audit_event_id)
    references platform_control.audit_events(audit_event_id)
    on delete set null,
  add constraint observation_grants_revoked_audit_event_id_fkey
    foreign key (revoked_audit_event_id)
    references platform_control.audit_events(audit_event_id)
    on delete set null;

drop index platform_control.one_platform_owner;
create unique index one_platform_owner
  on platform_control.internal_users ((role))
  where role = 'platform_owner';

create table platform_control.management_mutations (
  operation_id uuid primary key,
  action text not null check (action in (
    'assign_viewer', 'revoke_viewer',
    'grant_scope', 'revoke_scope',
    'bind_owner', 'replace_owner'
  )),
  actor_internal_user_id uuid,
  target_internal_user_id uuid not null,
  agent_id text,
  generation_id uuid,
  expected_target_row_version bigint not null check (
    expected_target_row_version >= 0
  ),
  expected_causal_row_version bigint not null check (
    expected_causal_row_version >= 0
  ),
  expected_owner_internal_user_id uuid,
  requested_audit_event_id uuid
    references platform_control.audit_events(audit_event_id)
    on delete set null,
  requested_audit_id_copy uuid not null,
  applied_result jsonb not null check (jsonb_typeof(applied_result) = 'object'),
  applied_at timestamptz not null default clock_timestamp()
);

revoke all on platform_control.management_mutations from public;

create function platform_control.validate_audit_event_v2(
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
  expected_target text;
  expected_keys text[];
  actual_keys text[];
  key_name text;
begin
  if actor_id is null or event_name is null or target_name is null
     or target_id is null or correlation_id is null or event_result is null
     or reason is null or details is null
  then
    raise check_violation using message = 'audit event invalid';
  end if;
  if actor_id is null or correlation_id is null
     or jsonb_typeof(details) <> 'object'
  then
    raise check_violation using message = 'audit event invalid';
  end if;

  case event_name
    when 'viewer_role_assignment_requested' then
      expected_reason := 'access_approved'; expected_target := 'internal_user';
      expected_keys := array['expected_row_version','new_role','operation_id','previous_role','result'];
    when 'viewer_role_revocation_requested' then
      expected_reason := 'access_revoked'; expected_target := 'internal_user';
      expected_keys := array['expected_row_version','new_role','operation_id','previous_role','result'];
    when 'observation_scope_assignment_requested' then
      expected_reason := 'scope_approved'; expected_target := 'agent_observation_scope';
      expected_keys := array['agent_id','expected_scope_row_version','expected_user_row_version','operation_id','result'];
    when 'observation_scope_revocation_requested' then
      expected_reason := 'scope_revoked'; expected_target := 'agent_observation_scope';
      expected_keys := array['agent_id','expected_scope_row_version','expected_user_row_version','operation_id','result'];
    when 'owner_binding_requested' then
      expected_reason := 'initial_owner_binding'; expected_target := 'internal_user';
      expected_keys := array['approver_a','approver_b','backup_reference','directory_generation_digest','directory_generation_id','expected_owner_row_version','expected_target_row_version','incident_reference','operation_id','os_operator','protected_target_lookup_hash','protected_target_lookup_version','result'];
    when 'owner_replacement_requested' then
      expected_reason := 'owner_departure'; expected_target := 'internal_user';
      expected_keys := array['approver_a','approver_b','backup_reference','directory_generation_digest','directory_generation_id','expected_owner_row_version','expected_target_row_version','incident_reference','operation_id','os_operator','previous_owner_internal_user_id','protected_target_lookup_hash','protected_target_lookup_version','result'];
    when 'management_user_list_read_requested' then
      expected_reason := 'privileged_read'; expected_target := 'management_user_directory';
      expected_keys := array['operation_id','result'];
    when 'governance_audit_read_requested' then
      expected_reason := 'privileged_read'; expected_target := 'governance_audit';
      expected_keys := array['operation_id','result'];
    when 'viewer_role_assignment_completed' then
      expected_reason := 'access_approved'; expected_target := 'internal_user';
      expected_keys := array['linked_audit_event_id','new_role','new_scopes','operation_id','previous_role','previous_scopes','result','row_version','session_revocation_count'];
    when 'viewer_role_revocation_completed' then
      expected_reason := 'access_revoked'; expected_target := 'internal_user';
      expected_keys := array['linked_audit_event_id','new_role','new_scopes','operation_id','previous_role','previous_scopes','result','row_version','session_revocation_count'];
    when 'observation_scope_assignment_completed' then
      expected_reason := 'scope_approved'; expected_target := 'agent_observation_scope';
      expected_keys := array['after_scope','agent_id','before_scope','linked_audit_event_id','new_scopes','operation_id','previous_scopes','result','row_version'];
    when 'observation_scope_revocation_completed' then
      expected_reason := 'scope_revoked'; expected_target := 'agent_observation_scope';
      expected_keys := array['after_scope','agent_id','before_scope','linked_audit_event_id','new_scopes','operation_id','previous_scopes','result','row_version'];
    when 'owner_binding_completed' then
      expected_reason := 'initial_owner_binding'; expected_target := 'internal_user';
      expected_keys := array['linked_audit_event_id','new_owner_internal_user_id','new_owner_role','new_owner_row_version','operation_id','result','session_revocation_count'];
    when 'owner_replacement_completed' then
      expected_reason := 'owner_departure'; expected_target := 'internal_user';
      expected_keys := array['linked_audit_event_id','new_owner_internal_user_id','new_owner_role','new_owner_row_version','operation_id','previous_owner_internal_user_id','previous_owner_role','previous_owner_row_version','result','session_revocation_count'];
    when 'management_user_list_read_completed' then
      expected_reason := 'privileged_read'; expected_target := 'management_user_directory';
      expected_keys := array['item_count','linked_audit_event_id','operation_id','result'];
    when 'governance_audit_read_completed' then
      expected_reason := 'privileged_read'; expected_target := 'governance_audit';
      expected_keys := array['item_count','linked_audit_event_id','operation_id','result'];
    when 'viewer_role_assignment_failed',
         'viewer_role_revocation_failed' then
      expected_reason := case when event_name like '%assignment%' then 'access_approved' else 'access_revoked' end;
      expected_target := 'internal_user';
      expected_keys := array['error_code','linked_audit_event_id','operation_id','result'];
    when 'observation_scope_assignment_failed',
         'observation_scope_revocation_failed' then
      expected_reason := case when event_name like '%assignment%' then 'scope_approved' else 'scope_revoked' end;
      expected_target := 'agent_observation_scope';
      expected_keys := array['error_code','linked_audit_event_id','operation_id','result'];
    when 'owner_binding_failed' then
      expected_reason := 'initial_owner_binding'; expected_target := 'internal_user';
      expected_keys := array['error_code','linked_audit_event_id','operation_id','result'];
    when 'owner_replacement_failed' then
      expected_reason := 'owner_departure'; expected_target := 'internal_user';
      expected_keys := array['error_code','linked_audit_event_id','operation_id','result'];
    when 'management_user_list_read_failed',
         'governance_audit_read_failed' then
      expected_reason := 'privileged_read';
      expected_target := case when event_name like 'management_%' then 'management_user_directory' else 'governance_audit' end;
      expected_keys := array['error_code','linked_audit_event_id','operation_id','result'];
    else
      raise check_violation using message = 'audit event invalid';
  end case;

  select array_agg(value order by value) into actual_keys
  from jsonb_object_keys(details) key(value);
  if actual_keys is distinct from expected_keys
     or target_name <> expected_target
     or reason <> expected_reason
     or event_result <> split_part(event_name, '_', array_length(string_to_array(event_name, '_'), 1))
     or details->>'result' <> event_result
     or details->>'operation_id' <> correlation_id::text
  then
    raise check_violation using message = 'audit event invalid';
  end if;

  foreach key_name in array expected_keys loop
    if jsonb_typeof(details->key_name) = 'null' then
      raise check_violation using message = 'audit metadata invalid';
    end if;
  end loop;

  if expected_target = 'internal_user' then
    perform target_id::uuid;
  elsif expected_target = 'agent_observation_scope' then
    if target_id !~ '^[0-9a-f-]{36}:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' then
      raise check_violation using message = 'audit target invalid';
    end if;
    perform split_part(target_id, ':', 1)::uuid;
  elsif expected_target = 'management_user_directory' and target_id <> 'all' then
    raise check_violation using message = 'audit target invalid';
  elsif expected_target = 'governance_audit' and target_id <> 'sanitized' then
    raise check_violation using message = 'audit target invalid';
  end if;

  foreach key_name in array array[
    'operation_id','linked_audit_event_id','directory_generation_id',
    'previous_owner_internal_user_id','new_owner_internal_user_id'
  ] loop
    if details ? key_name then
      if jsonb_typeof(details->key_name) <> 'string' then
        raise check_violation using message = 'audit metadata invalid';
      end if;
      perform (details->>key_name)::uuid;
    end if;
  end loop;
  foreach key_name in array array[
    'expected_row_version','expected_user_row_version','expected_scope_row_version',
    'expected_owner_row_version','expected_target_row_version','row_version',
    'session_revocation_count','previous_owner_row_version','new_owner_row_version',
    'protected_target_lookup_version','item_count'
  ] loop
    if details ? key_name and (
      jsonb_typeof(details->key_name) <> 'number'
      or details->>key_name !~ '^[0-9]+$'
    ) then raise check_violation using message = 'audit metadata invalid'; end if;
  end loop;
  foreach key_name in array array['before_scope','after_scope'] loop
    if details ? key_name and jsonb_typeof(details->key_name) <> 'boolean' then
      raise check_violation using message = 'audit metadata invalid';
    end if;
  end loop;
  foreach key_name in array array['previous_role','new_role','previous_owner_role','new_owner_role'] loop
    if details ? key_name and (
      jsonb_typeof(details->key_name) <> 'string'
      or details->>key_name not in ('member','management_viewer','platform_owner')
    ) then
      raise check_violation using message = 'audit metadata invalid';
    end if;
  end loop;
  if details ? 'agent_id' and (
    jsonb_typeof(details->'agent_id') <> 'string'
    or details->>'agent_id' !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
  ) then
    raise check_violation using message = 'audit metadata invalid';
  end if;
  foreach key_name in array array['os_operator','approver_a','approver_b'] loop
    if details ? key_name and (
      jsonb_typeof(details->key_name) <> 'string'
      or details->>key_name !~ '^(uid:[0-9]{1,10}|[a-z_][a-z0-9_.-]{0,31}|[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$'
    ) then
      raise check_violation using message = 'audit metadata invalid';
    end if;
  end loop;
  foreach key_name in array array['backup_reference','incident_reference'] loop
    if details ? key_name and (
      jsonb_typeof(details->key_name) <> 'string'
      or details->>key_name !~ '^[A-Z][A-Z0-9_-]{2,63}$'
    ) then
      raise check_violation using message = 'audit metadata invalid';
    end if;
  end loop;
  foreach key_name in array array['directory_generation_digest','protected_target_lookup_hash'] loop
    if details ? key_name and (
      jsonb_typeof(details->key_name) <> 'string'
      or details->>key_name !~ '^[0-9a-f]{64}$'
    ) then
      raise check_violation using message = 'audit metadata invalid';
    end if;
  end loop;
  if details ? 'error_code' and (
    jsonb_typeof(details->'error_code') <> 'string'
    or details->>'error_code' not in ('business_rejected','control_unavailable')
  ) then
    raise check_violation using message = 'audit metadata invalid';
  end if;
  foreach key_name in array array['previous_scopes','new_scopes'] loop
    if details ? key_name and (
      jsonb_typeof(details->key_name) <> 'array'
      or jsonb_array_length(details->key_name) > 256
      or exists (
        select 1 from jsonb_array_elements(details->key_name) item(value)
        where jsonb_typeof(item.value) <> 'string'
           or item.value #>> '{}' !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
      )
      or details->key_name <> coalesce((
        select jsonb_agg(to_jsonb(canonical.value) order by canonical.value)
        from (
          select distinct item.value #>> '{}' as value
          from jsonb_array_elements(details->key_name) item(value)
        ) canonical
      ), '[]'::jsonb)
    ) then raise check_violation using message = 'audit metadata invalid'; end if;
  end loop;
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
begin
  perform platform_control.validate_audit_event_v2(
    actor_id, event_name, target_name, target_id, correlation_id,
    event_result, reason, details
  );
  insert into platform_control.audit_events (
    audit_event_id, actor_internal_user_id, event_type, target_type,
    target_internal_id, request_id, result, reason_code,
    sanitized_before_after
  ) values (
    event_id, actor_id, event_name, target_name, target_id,
    correlation_id, event_result, reason, details
  ) on conflict (audit_event_id) do nothing;
  select * into strict stored from platform_control.audit_events
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

create function platform_control.require_management_actor(actor_id uuid)
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
  ) then
    raise insufficient_privilege using message = 'active platform owner required';
  end if;
end
$function$;

create function platform_control.require_requested_audit(
  selected_audit_event_id uuid,
  selected_operation_id uuid,
  selected_actor_id uuid,
  selected_event_type text,
  selected_target_type text,
  selected_target_id text,
  selected_reason_code text
) returns void
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_audit_event_id is null
     or selected_operation_id is null
     or selected_actor_id is null
     or not exists (
       select 1 from platform_control.audit_events event
       where event.audit_event_id = selected_audit_event_id
         and event.actor_internal_user_id = selected_actor_id
         and event.event_type = selected_event_type
         and event.target_type = selected_target_type
         and event.target_internal_id = selected_target_id
         and event.request_id = selected_operation_id
         and event.result = 'requested'
         and event.reason_code = selected_reason_code
         and event.sanitized_before_after->>'operation_id'
             = selected_operation_id::text
         and event.sanitized_before_after->>'result' = 'requested'
     )
  then
    raise check_violation using message = 'matching audit intent required';
  end if;
end
$function$;

create function platform_control.replay_management_mutation(
  selected_operation_id uuid,
  selected_action text,
  selected_actor_id uuid,
  selected_target_id uuid,
  selected_agent_id text,
  selected_generation_id uuid,
  selected_expected_target_version bigint,
  selected_expected_causal_version bigint,
  selected_expected_owner_id uuid,
  selected_audit_event_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  stored platform_control.management_mutations%rowtype;
begin
  select * into stored
  from platform_control.management_mutations
  where operation_id = selected_operation_id;
  if not found then
    return null;
  end if;
  if stored.action is distinct from selected_action
     or stored.actor_internal_user_id is distinct from selected_actor_id
     or stored.target_internal_user_id is distinct from selected_target_id
     or stored.agent_id is distinct from selected_agent_id
     or stored.generation_id is distinct from selected_generation_id
     or stored.expected_target_row_version
        is distinct from selected_expected_target_version
     or stored.expected_causal_row_version
        is distinct from selected_expected_causal_version
     or stored.expected_owner_internal_user_id
        is distinct from selected_expected_owner_id
     or stored.requested_audit_id_copy
        is distinct from selected_audit_event_id
  then
    raise unique_violation using message = 'operation identity collision';
  end if;
  return stored.applied_result;
end
$function$;

create function platform_control.create_internal_member(
  selected_user_id uuid,
  selected_display_name text
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_user_id is null
     or selected_display_name is null
     or selected_display_name <> btrim(selected_display_name)
     or length(selected_display_name) not between 1 and 256
     or selected_display_name ~ '[[:cntrl:]]'
  then
    raise check_violation using message = 'internal member invalid';
  end if;
  insert into platform_control.internal_users (
    internal_user_id, role, display_name, status
  ) values (
    selected_user_id, 'member', selected_display_name, 'active'
  );
  return selected_user_id;
end
$function$;

create function platform_control.assign_management_viewer(
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
  if selected_expected_row_version is null or selected_expected_row_version < 0 then
    raise check_violation using message = 'viewer assignment precondition invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(selected_operation_id::text, 0));
  replay := platform_control.replay_management_mutation(
    selected_operation_id, 'assign_viewer', selected_actor_id,
    selected_target_id, null, null, selected_expected_row_version, 0,
    null, selected_audit_event_id
  );
  if replay is not null then return replay; end if;
  perform platform_control.require_management_actor(selected_actor_id);
  perform platform_control.require_requested_audit(
    selected_audit_event_id, selected_operation_id, selected_actor_id,
    'viewer_role_assignment_requested', 'internal_user',
    selected_target_id::text, 'access_approved'
  );
  if not exists (
    select 1 from platform_control.audit_events event
    where event.audit_event_id = selected_audit_event_id
      and event.sanitized_before_after->>'previous_role' = 'member'
      and event.sanitized_before_after->>'new_role' = 'management_viewer'
      and (event.sanitized_before_after->>'expected_row_version')::bigint
          = selected_expected_row_version
  ) then
    raise check_violation using message = 'audit payload mismatch';
  end if;
  select role::text, row_version into prior_role, current_version
  from platform_control.internal_users
  where internal_user_id = selected_target_id and status = 'active'
  for update;
  if not found or prior_role <> 'member'
     or current_version <> selected_expected_row_version
  then
    raise check_violation using message = 'viewer assignment precondition failed';
  end if;
  update platform_control.internal_users
  set role = 'management_viewer',
      role_audit_event_id = selected_audit_event_id,
      row_version = row_version + 1,
      updated_at = clock_timestamp()
  where internal_user_id = selected_target_id;
  result_snapshot := jsonb_build_object(
    'operation_id', selected_operation_id::text,
    'previous_role', prior_role,
    'new_role', 'management_viewer',
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
    selected_operation_id, 'assign_viewer', selected_actor_id,
    selected_target_id, selected_expected_row_version, 0,
    selected_audit_event_id, selected_audit_event_id, result_snapshot
  );
  return result_snapshot;
end
$function$;

create function platform_control.revoke_management_viewer(
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
  scopes_before jsonb;
  result_snapshot jsonb;
begin
  if selected_expected_row_version is null or selected_expected_row_version < 0 then
    raise check_violation using message = 'viewer revocation precondition invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(selected_operation_id::text, 0));
  replay := platform_control.replay_management_mutation(
    selected_operation_id, 'revoke_viewer', selected_actor_id,
    selected_target_id, null, null, selected_expected_row_version, 0,
    null, selected_audit_event_id
  );
  if replay is not null then return replay; end if;
  perform platform_control.require_management_actor(selected_actor_id);
  perform platform_control.require_requested_audit(
    selected_audit_event_id, selected_operation_id, selected_actor_id,
    'viewer_role_revocation_requested', 'internal_user',
    selected_target_id::text, 'access_revoked'
  );
  if not exists (
    select 1 from platform_control.audit_events event
    where event.audit_event_id = selected_audit_event_id
      and event.sanitized_before_after->>'previous_role' = 'management_viewer'
      and event.sanitized_before_after->>'new_role' = 'member'
      and (event.sanitized_before_after->>'expected_row_version')::bigint
          = selected_expected_row_version
  ) then
    raise check_violation using message = 'audit payload mismatch';
  end if;
  select role::text, row_version into prior_role, current_version
  from platform_control.internal_users
  where internal_user_id = selected_target_id
  for update;
  if not found or prior_role <> 'management_viewer'
     or current_version <> selected_expected_row_version
  then
    raise check_violation using message = 'viewer revocation precondition failed';
  end if;
  select coalesce(jsonb_agg(agent_id order by agent_id), '[]'::jsonb)
  into scopes_before
  from platform_control.observation_grants
  where viewer_internal_user_id = selected_target_id and revoked_at is null;
  update platform_control.observation_grants
  set revoked_at = clock_timestamp(), revoked_by = selected_actor_id,
      revoked_audit_event_id = selected_audit_event_id,
      row_version = row_version + 1
  where viewer_internal_user_id = selected_target_id and revoked_at is null;
  update platform_control.internal_users
  set role = 'member', role_audit_event_id = selected_audit_event_id,
      row_version = row_version + 1, updated_at = clock_timestamp()
  where internal_user_id = selected_target_id;
  update platform_control.web_sessions
  set revoked_at = clock_timestamp(), revoked_reason = 'viewer_role_revoked'
  where internal_user_id = selected_target_id and revoked_at is null;
  get diagnostics session_count = row_count;
  result_snapshot := jsonb_build_object(
    'operation_id', selected_operation_id::text,
    'previous_role', prior_role,
    'new_role', 'member',
    'row_version', current_version + 1,
    'session_revocation_count', session_count,
    'previous_scopes', scopes_before,
    'new_scopes', '[]'::jsonb
  );
  insert into platform_control.management_mutations (
    operation_id, action, actor_internal_user_id, target_internal_user_id,
    expected_target_row_version, expected_causal_row_version,
    requested_audit_event_id, requested_audit_id_copy, applied_result
  ) values (
    selected_operation_id, 'revoke_viewer', selected_actor_id,
    selected_target_id, selected_expected_row_version, 0,
    selected_audit_event_id, selected_audit_event_id, result_snapshot
  );
  return result_snapshot;
end
$function$;

create function platform_control.grant_observation_scope(
  selected_operation_id uuid,
  selected_actor_id uuid,
  selected_target_id uuid,
  selected_agent_id text,
  selected_expected_user_version bigint,
  selected_expected_scope_version bigint,
  selected_audit_event_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  replay jsonb;
  user_version bigint;
  current_scope_version bigint;
  scopes_before jsonb;
  scopes_after jsonb;
  result_snapshot jsonb;
begin
  if selected_agent_id is null
     or selected_agent_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
     or selected_expected_user_version is null
     or selected_expected_user_version < 0
     or selected_expected_scope_version is null
     or selected_expected_scope_version < 0
  then
    raise check_violation using message = 'scope assignment precondition invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(selected_operation_id::text, 0));
  replay := platform_control.replay_management_mutation(
    selected_operation_id, 'grant_scope', selected_actor_id,
    selected_target_id, selected_agent_id, null,
    selected_expected_user_version, selected_expected_scope_version,
    null, selected_audit_event_id
  );
  if replay is not null then return replay; end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_target_id::text || ':' || selected_agent_id, 0
  ));
  perform platform_control.require_management_actor(selected_actor_id);
  perform platform_control.require_requested_audit(
    selected_audit_event_id, selected_operation_id, selected_actor_id,
    'observation_scope_assignment_requested', 'agent_observation_scope',
    selected_target_id::text || ':' || selected_agent_id, 'scope_approved'
  );
  if not exists (
    select 1 from platform_control.audit_events event
    where event.audit_event_id = selected_audit_event_id
      and event.sanitized_before_after->>'agent_id' = selected_agent_id
      and (event.sanitized_before_after->>'expected_user_row_version')::bigint
          = selected_expected_user_version
      and (event.sanitized_before_after->>'expected_scope_row_version')::bigint
          = selected_expected_scope_version
  ) then
    raise check_violation using message = 'audit payload mismatch';
  end if;
  select row_version into user_version
  from platform_control.internal_users
  where internal_user_id = selected_target_id
    and status = 'active' and role = 'management_viewer'
  for update;
  select coalesce(max(row_version), 0) into current_scope_version
  from platform_control.observation_grants
  where viewer_internal_user_id = selected_target_id
    and agent_id = selected_agent_id;
  if user_version is null or user_version <> selected_expected_user_version
     or current_scope_version <> selected_expected_scope_version
     or exists (
       select 1 from platform_control.observation_grants
       where viewer_internal_user_id = selected_target_id
         and agent_id = selected_agent_id and revoked_at is null
     )
  then
    raise check_violation using message = 'scope assignment precondition failed';
  end if;
  select coalesce(jsonb_agg(agent_id order by agent_id), '[]'::jsonb)
  into scopes_before from platform_control.observation_grants
  where viewer_internal_user_id = selected_target_id and revoked_at is null;
  insert into platform_control.observation_grants (
    observation_grant_id, agent_id, viewer_internal_user_id, created_by,
    created_audit_event_id, row_version
  ) values (
    selected_operation_id, selected_agent_id, selected_target_id,
    selected_actor_id, selected_audit_event_id, current_scope_version + 1
  );
  select coalesce(jsonb_agg(agent_id order by agent_id), '[]'::jsonb)
  into scopes_after from platform_control.observation_grants
  where viewer_internal_user_id = selected_target_id and revoked_at is null;
  result_snapshot := jsonb_build_object(
    'operation_id', selected_operation_id::text,
    'agent_id', selected_agent_id,
    'before_scope', false,
    'after_scope', true,
    'row_version', current_scope_version + 1,
    'previous_scopes', scopes_before,
    'new_scopes', scopes_after
  );
  insert into platform_control.management_mutations (
    operation_id, action, actor_internal_user_id, target_internal_user_id,
    agent_id, expected_target_row_version, expected_causal_row_version,
    requested_audit_event_id, requested_audit_id_copy, applied_result
  ) values (
    selected_operation_id, 'grant_scope', selected_actor_id,
    selected_target_id, selected_agent_id, selected_expected_user_version,
    selected_expected_scope_version, selected_audit_event_id,
    selected_audit_event_id, result_snapshot
  );
  return result_snapshot;
end
$function$;

create function platform_control.revoke_observation_scope(
  selected_operation_id uuid,
  selected_actor_id uuid,
  selected_target_id uuid,
  selected_agent_id text,
  selected_expected_user_version bigint,
  selected_expected_scope_version bigint,
  selected_audit_event_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  replay jsonb;
  user_version bigint;
  grant_id uuid;
  current_scope_version bigint;
  scopes_before jsonb;
  scopes_after jsonb;
  result_snapshot jsonb;
begin
  if selected_agent_id is null
     or selected_agent_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
     or selected_expected_user_version is null
     or selected_expected_user_version < 0
     or selected_expected_scope_version is null
     or selected_expected_scope_version < 1
  then
    raise check_violation using message = 'scope revocation precondition invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(selected_operation_id::text, 0));
  replay := platform_control.replay_management_mutation(
    selected_operation_id, 'revoke_scope', selected_actor_id,
    selected_target_id, selected_agent_id, null,
    selected_expected_user_version, selected_expected_scope_version,
    null, selected_audit_event_id
  );
  if replay is not null then return replay; end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_target_id::text || ':' || selected_agent_id, 0
  ));
  perform platform_control.require_management_actor(selected_actor_id);
  perform platform_control.require_requested_audit(
    selected_audit_event_id, selected_operation_id, selected_actor_id,
    'observation_scope_revocation_requested', 'agent_observation_scope',
    selected_target_id::text || ':' || selected_agent_id, 'scope_revoked'
  );
  if not exists (
    select 1 from platform_control.audit_events event
    where event.audit_event_id = selected_audit_event_id
      and event.sanitized_before_after->>'agent_id' = selected_agent_id
      and (event.sanitized_before_after->>'expected_user_row_version')::bigint
          = selected_expected_user_version
      and (event.sanitized_before_after->>'expected_scope_row_version')::bigint
          = selected_expected_scope_version
  ) then
    raise check_violation using message = 'audit payload mismatch';
  end if;
  select row_version into user_version
  from platform_control.internal_users
  where internal_user_id = selected_target_id
    and status = 'active' and role = 'management_viewer'
  for update;
  if not found or user_version <> selected_expected_user_version then
    raise check_violation using message = 'scope revocation precondition failed';
  end if;
  select observation_grant_id, row_version
  into grant_id, current_scope_version
  from platform_control.observation_grants
  where viewer_internal_user_id = selected_target_id
    and agent_id = selected_agent_id and revoked_at is null
  for update;
  if not found or current_scope_version <> selected_expected_scope_version then
    raise check_violation using message = 'scope revocation precondition failed';
  end if;
  select coalesce(jsonb_agg(agent_id order by agent_id), '[]'::jsonb)
  into scopes_before from platform_control.observation_grants
  where viewer_internal_user_id = selected_target_id and revoked_at is null;
  update platform_control.observation_grants
  set revoked_at = clock_timestamp(), revoked_by = selected_actor_id,
      revoked_audit_event_id = selected_audit_event_id,
      row_version = row_version + 1
  where observation_grant_id = grant_id;
  select coalesce(jsonb_agg(agent_id order by agent_id), '[]'::jsonb)
  into scopes_after from platform_control.observation_grants
  where viewer_internal_user_id = selected_target_id and revoked_at is null;
  result_snapshot := jsonb_build_object(
    'operation_id', selected_operation_id::text,
    'agent_id', selected_agent_id,
    'before_scope', true,
    'after_scope', false,
    'row_version', current_scope_version + 1,
    'previous_scopes', scopes_before,
    'new_scopes', scopes_after
  );
  insert into platform_control.management_mutations (
    operation_id, action, actor_internal_user_id, target_internal_user_id,
    agent_id, expected_target_row_version, expected_causal_row_version,
    requested_audit_event_id, requested_audit_id_copy, applied_result
  ) values (
    selected_operation_id, 'revoke_scope', selected_actor_id,
    selected_target_id, selected_agent_id, selected_expected_user_version,
    selected_expected_scope_version, selected_audit_event_id,
    selected_audit_event_id, result_snapshot
  );
  return result_snapshot;
end
$function$;

create function platform_control.change_platform_owner_v2(
  selected_operation_id uuid,
  selected_operation text,
  selected_target_id uuid,
  selected_generation_id uuid,
  selected_expected_owner_id uuid,
  selected_expected_owner_version bigint,
  selected_expected_target_version bigint,
  selected_audit_event_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  selected_action text;
  replay jsonb;
  existing_owner_id uuid;
  existing_owner_version bigint;
  target_version bigint;
  session_count bigint;
  result_snapshot jsonb;
begin
  if selected_operation not in ('bind', 'replace')
     or selected_expected_owner_version is null
     or selected_expected_owner_version < 0
     or selected_expected_target_version is null
     or selected_expected_target_version < 0
  then
    raise check_violation using message = 'owner change precondition invalid';
  end if;
  selected_action := selected_operation || '_owner';
  perform pg_advisory_xact_lock(hashtextextended(selected_operation_id::text, 0));
  replay := platform_control.replay_management_mutation(
    selected_operation_id, selected_action, null, selected_target_id,
    null, selected_generation_id, selected_expected_target_version,
    selected_expected_owner_version, selected_expected_owner_id,
    selected_audit_event_id
  );
  if replay is not null then return replay; end if;
  perform platform_control.require_requested_audit(
    selected_audit_event_id, selected_operation_id, selected_target_id,
    case selected_operation when 'bind' then 'owner_binding_requested'
      else 'owner_replacement_requested' end,
    'internal_user', selected_target_id::text,
    case selected_operation when 'bind' then 'initial_owner_binding'
      else 'owner_departure' end
  );
  if not exists (
    select 1
    from platform_control.audit_events event
    join platform_control.directory_generations generation
      on generation.generation_id = selected_generation_id
    join platform_control.directory_members member
      on member.generation_id = generation.generation_id
     and member.internal_user_id = selected_target_id
    where event.audit_event_id = selected_audit_event_id
      and event.sanitized_before_after->>'directory_generation_id'
          = selected_generation_id::text
      and event.sanitized_before_after->>'directory_generation_digest'
          = generation.content_sha256
      and event.sanitized_before_after->>'protected_target_lookup_hash'
          = encode(member.lookup_hmac, 'hex')
      and (event.sanitized_before_after->>'protected_target_lookup_version')::integer
          = member.lookup_key_version
      and (event.sanitized_before_after->>'expected_owner_row_version')::bigint
          = selected_expected_owner_version
      and (event.sanitized_before_after->>'expected_target_row_version')::bigint
          = selected_expected_target_version
      and (
        selected_operation = 'bind'
        or event.sanitized_before_after->>'previous_owner_internal_user_id'
           = selected_expected_owner_id::text
      )
  ) then
    raise check_violation using message = 'audit payload mismatch';
  end if;
  if not exists (
    select 1 from platform_control.directory_generations generation
    join platform_control.directory_members member
      on member.generation_id = generation.generation_id
    join platform_control.internal_users selected_user
      on selected_user.internal_user_id = member.internal_user_id
    where generation.generation_id = selected_generation_id
      and generation.status = 'complete'
      and member.internal_user_id = selected_target_id
      and member.status = 'active'
      and selected_user.status = 'active'
  ) then
    raise check_violation using message = 'target unavailable in selected generation';
  end if;
  select internal_user_id, row_version
  into existing_owner_id, existing_owner_version
  from platform_control.internal_users
  where role = 'platform_owner'
  for update;
  if selected_operation = 'bind' and existing_owner_id is not null then
    raise check_violation using message = 'owner already bound';
  end if;
  if selected_operation = 'replace' and (
    existing_owner_id is null
    or existing_owner_id is distinct from selected_expected_owner_id
    or existing_owner_version <> selected_expected_owner_version
  ) then
    raise check_violation using message = 'owner replacement precondition failed';
  end if;
  select row_version into target_version
  from platform_control.internal_users
  where internal_user_id = selected_target_id and status = 'active'
  for update;
  if not found or target_version <> selected_expected_target_version
     or selected_target_id is not distinct from existing_owner_id
  then
    raise check_violation using message = 'owner target precondition failed';
  end if;
  if existing_owner_id is not null then
    update platform_control.internal_users
    set role = 'member', role_audit_event_id = selected_audit_event_id,
        row_version = row_version + 1, updated_at = clock_timestamp()
    where internal_user_id = existing_owner_id;
  end if;
  update platform_control.internal_users
  set role = 'platform_owner', role_audit_event_id = selected_audit_event_id,
      row_version = row_version + 1, updated_at = clock_timestamp()
  where internal_user_id = selected_target_id;
  update platform_control.web_sessions
  set revoked_at = clock_timestamp(), revoked_reason = 'owner_role_changed'
  where internal_user_id in (selected_target_id, existing_owner_id)
    and revoked_at is null;
  get diagnostics session_count = row_count;
  result_snapshot := jsonb_build_object(
    'operation_id', selected_operation_id::text,
    'previous_owner_internal_user_id', existing_owner_id,
    'new_owner_internal_user_id', selected_target_id,
    'previous_owner_role', case when existing_owner_id is null then null else 'platform_owner' end,
    'new_owner_role', 'platform_owner',
    'session_revocation_count', session_count,
    'previous_owner_row_version', case when existing_owner_id is null then 0 else existing_owner_version + 1 end,
    'new_owner_row_version', target_version + 1
  );
  insert into platform_control.management_mutations (
    operation_id, action, target_internal_user_id, generation_id,
    expected_target_row_version, expected_causal_row_version,
    expected_owner_internal_user_id, requested_audit_event_id,
    requested_audit_id_copy, applied_result
  ) values (
    selected_operation_id, selected_action, selected_target_id,
    selected_generation_id, selected_expected_target_version,
    selected_expected_owner_version, selected_expected_owner_id,
    selected_audit_event_id, selected_audit_event_id, result_snapshot
  );
  return result_snapshot;
end
$function$;

create function platform_control.owner_change_precondition(
  selected_generation_id uuid,
  selected_target_id uuid
) returns table (
  directory_generation_digest text,
  protected_target_lookup_hash text,
  protected_target_lookup_version integer,
  current_owner_internal_user_id uuid,
  current_owner_row_version bigint,
  target_row_version bigint
)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  return query
  select generation.content_sha256,
         encode(member.lookup_hmac, 'hex'),
         member.lookup_key_version,
         owner_row.internal_user_id,
         coalesce(owner_row.row_version, 0),
         target.row_version
  from platform_control.directory_generations generation
  join platform_control.directory_members member
    on member.generation_id = generation.generation_id
   and member.internal_user_id = selected_target_id
  join platform_control.internal_users target
    on target.internal_user_id = member.internal_user_id
  left join lateral (
    select internal_user_id, row_version
    from platform_control.internal_users
    where role = 'platform_owner'
  ) owner_row on true
  where generation.generation_id = selected_generation_id
    and generation.status = 'complete'
    and generation.content_sha256 ~ '^[0-9a-f]{64}$'
    and member.status = 'active'
    and target.status = 'active';
  if not found then
    raise check_violation using message = 'owner precondition unavailable';
  end if;
end
$function$;

revoke all on function platform_control.validate_audit_event_v2(
  uuid, text, text, text, uuid, text, text, jsonb
) from public;
revoke all on function platform_control.require_management_actor(uuid) from public;
revoke all on function platform_control.require_requested_audit(
  uuid, uuid, uuid, text, text, text, text
) from public;
revoke all on function platform_control.replay_management_mutation(
  uuid, text, uuid, uuid, text, uuid, bigint, bigint, uuid, uuid
) from public;
revoke all on function platform_control.create_internal_member(uuid, text) from public;
revoke all on function platform_control.assign_management_viewer(
  uuid, uuid, uuid, bigint, uuid
) from public;
revoke all on function platform_control.revoke_management_viewer(
  uuid, uuid, uuid, bigint, uuid
) from public;
revoke all on function platform_control.grant_observation_scope(
  uuid, uuid, uuid, text, bigint, bigint, uuid
) from public;
revoke all on function platform_control.revoke_observation_scope(
  uuid, uuid, uuid, text, bigint, bigint, uuid
) from public;
revoke all on function platform_control.change_platform_owner_v2(
  uuid, text, uuid, uuid, uuid, bigint, bigint, uuid
) from public;
revoke all on function platform_control.owner_change_precondition(uuid, uuid)
from public;

do $migration$
declare
  selected_suffix text;
  selected_app text;
  selected_migrator text;
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
  selected_migrator := 'platform_control_migrator' || selected_suffix;

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
      'revoke insert, update, delete on platform_control.internal_users from %I',
      role_name
    );
    execute format(
      'revoke update (display_name,status,last_confirmed_generation_id,'
      'locally_invalidated_at,updated_at,role,role_audit_event_id,row_version) '
      'on platform_control.internal_users from %I',
      role_name
    );
    execute format(
      'revoke insert, update, delete on platform_control.observation_grants from %I',
      role_name
    );
    execute format(
      'revoke all on platform_control.management_mutations from %I', role_name
    );
    execute format(
      'revoke all on function platform_control.create_internal_member(uuid,text) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.assign_management_viewer(uuid,uuid,uuid,bigint,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.revoke_management_viewer(uuid,uuid,uuid,bigint,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.grant_observation_scope(uuid,uuid,uuid,text,bigint,bigint,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.revoke_observation_scope(uuid,uuid,uuid,text,bigint,bigint,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.change_platform_owner_v2(uuid,text,uuid,uuid,uuid,bigint,bigint,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.owner_change_precondition(uuid,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.change_platform_owner(text,uuid,uuid,uuid) from %I',
      role_name
    );
  end loop;

  execute format(
    'grant execute on function platform_control.create_internal_member(uuid,text) to %I',
    selected_app
  );
  execute format(
    'grant select on platform_control.management_mutations to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.assign_management_viewer(uuid,uuid,uuid,bigint,uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.revoke_management_viewer(uuid,uuid,uuid,bigint,uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.grant_observation_scope(uuid,uuid,uuid,text,bigint,bigint,uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.revoke_observation_scope(uuid,uuid,uuid,text,bigint,bigint,uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.change_platform_owner_v2(uuid,text,uuid,uuid,uuid,bigint,bigint,uuid) to %I',
    selected_migrator
  );
  execute format(
    'grant execute on function platform_control.owner_change_precondition(uuid,uuid) to %I',
    selected_migrator
  );
end
$migration$;
