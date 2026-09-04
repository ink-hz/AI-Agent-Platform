create table platform_control.voc_workbench_grants (
  grant_id uuid primary key,
  internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  permission text not null check (permission = 'manager'),
  created_by_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  created_at timestamptz not null default clock_timestamp(),
  created_audit_event_id uuid not null
    references platform_control.audit_events(audit_event_id),
  revoked_at timestamptz,
  revoked_by_internal_user_id uuid
    references platform_control.internal_users(internal_user_id),
  revoked_audit_event_id uuid
    references platform_control.audit_events(audit_event_id),
  row_version bigint not null default 0 check (row_version >= 0),
  constraint voc_workbench_grant_revocation_complete check (
    num_nonnulls(
      revoked_at, revoked_by_internal_user_id, revoked_audit_event_id
    ) in (0, 3)
  )
);

create unique index one_active_voc_workbench_grant
  on platform_control.voc_workbench_grants(internal_user_id)
  where revoked_at is null;

revoke all on platform_control.voc_workbench_grants from public;

alter table platform_control.management_mutations
  drop constraint management_mutations_action_check,
  add constraint management_mutations_action_check check (action in (
    'assign_viewer', 'revoke_viewer',
    'grant_scope', 'revoke_scope',
    'assign_admin', 'revoke_admin',
    'bind_owner', 'replace_owner',
    'grant_fae_workbench', 'revoke_fae_workbench',
    'grant_voc_workbench', 'revoke_voc_workbench'
  ));

create function platform_control.validate_voc_workbench_audit_v75(
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
  expected_keys text[];
  expected_reason text;
  expected_target text;
  requested_event_name text;
  actual_keys text[];
  key_name text;
begin
  case event_name
    when 'voc_workbench_grant_requested' then
      expected_reason := 'voc_workbench_access_approved';
      expected_target := 'directory_member';
      expected_keys := array[
        'expected_generation_id','expected_member_key','operation_id','result'
      ];
    when 'voc_workbench_revoke_requested' then
      expected_reason := 'voc_workbench_access_revoked';
      expected_target := 'internal_user';
      expected_keys := array['expected_row_version','operation_id','result'];
    when 'voc_workbench_grant_completed' then
      expected_reason := 'voc_workbench_access_approved';
      expected_target := 'directory_member';
      expected_keys := array[
        'grant_id','internal_user_id','linked_audit_event_id','operation_id',
        'permission','result','row_version'
      ];
    when 'voc_workbench_revoke_completed' then
      expected_reason := 'voc_workbench_access_revoked';
      expected_target := 'internal_user';
      expected_keys := array[
        'grant_id','internal_user_id','linked_audit_event_id','operation_id',
        'permission','result','row_version'
      ];
    when 'voc_workbench_grant_failed' then
      expected_reason := 'voc_workbench_access_approved';
      expected_target := 'directory_member';
      expected_keys := array[
        'error_code','linked_audit_event_id','operation_id','result'
      ];
    when 'voc_workbench_revoke_failed' then
      expected_reason := 'voc_workbench_access_revoked';
      expected_target := 'internal_user';
      expected_keys := array[
        'error_code','linked_audit_event_id','operation_id','result'
      ];
    else
      raise check_violation using message = 'audit event invalid';
  end case;

  if actor_id is null or correlation_id is null
     or target_name <> expected_target
     or reason <> expected_reason
     or event_result <> split_part(
       event_name, '_', array_length(string_to_array(event_name, '_'), 1)
     )
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

  if event_result = 'requested' and event_name like '%grant_%' then
    if jsonb_typeof(details->'expected_generation_id') <> 'string'
       or jsonb_typeof(details->'expected_member_key') <> 'string'
    then
      raise check_violation using message = 'audit metadata invalid';
    end if;
    perform (details->>'expected_generation_id')::uuid;
    perform (details->>'expected_member_key')::uuid;
    if details->>'expected_member_key' <> target_id then
      raise check_violation using message = 'audit metadata invalid';
    end if;
  elsif event_result = 'requested' and (
    jsonb_typeof(details->'expected_row_version') <> 'number'
    or details->>'expected_row_version' !~ '^[0-9]+$'
  ) then
    raise check_violation using message = 'audit metadata invalid';
  elsif event_result = 'completed' then
    if jsonb_typeof(details->'grant_id') <> 'string'
       or jsonb_typeof(details->'internal_user_id') <> 'string'
       or jsonb_typeof(details->'linked_audit_event_id') <> 'string'
       or details->>'permission' <> 'manager'
       or jsonb_typeof(details->'row_version') <> 'number'
       or details->>'row_version' !~ '^[0-9]+$'
    then
      raise check_violation using message = 'audit metadata invalid';
    end if;
    perform (details->>'grant_id')::uuid;
    perform (details->>'internal_user_id')::uuid;
    perform (details->>'linked_audit_event_id')::uuid;
    if event_name like '%revoke_%'
       and details->>'internal_user_id' <> target_id
    then
      raise check_violation using message = 'audit metadata invalid';
    end if;
  elsif event_result = 'failed' then
    if jsonb_typeof(details->'linked_audit_event_id') <> 'string'
       or jsonb_typeof(details->'error_code') <> 'string'
       or details->>'error_code' not in (
         'business_rejected', 'control_unavailable'
       )
    then
      raise check_violation using message = 'audit metadata invalid';
    end if;
    perform (details->>'linked_audit_event_id')::uuid;
  end if;

  if event_result in ('completed', 'failed') then
    requested_event_name := regexp_replace(
      event_name, '_(completed|failed)$', '_requested'
    );
    if not exists (
      select 1 from platform_control.audit_events requested
      where requested.audit_event_id
            = (details->>'linked_audit_event_id')::uuid
        and requested.actor_internal_user_id = actor_id
        and requested.event_type = requested_event_name
        and requested.target_type = target_name
        and requested.target_internal_id = target_id
        and requested.request_id = correlation_id
        and requested.result = 'requested'
        and requested.reason_code = reason
        and requested.sanitized_before_after->>'operation_id'
            = correlation_id::text
        and requested.sanitized_before_after->>'result' = 'requested'
    ) then
      raise check_violation using message = 'matching audit intent required';
    end if;
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
  if event_name like 'voc_workbench_grant_%'
     or event_name like 'voc_workbench_revoke_%'
  then
    perform platform_control.validate_voc_workbench_audit_v75(
      actor_id, event_name, target_name, target_id, correlation_id,
      event_result, reason, details
    );
  elsif event_name like 'fae_workbench_grant_%'
     or event_name like 'fae_workbench_revoke_%'
  then
    perform platform_control.validate_fae_workbench_audit_v63(
      actor_id, event_name, target_name, target_id, correlation_id,
      event_result, reason, details
    );
  elsif event_name like 'fae_session_detail_read_%' then
    perform platform_control.validate_fae_session_read_audit_v60(
      actor_id, event_name, target_name, target_id, correlation_id,
      event_result, reason, details
    );
  elsif event_name like 'partner_%' then
    perform platform_control.validate_partner_audit_event_v54(
      actor_id, event_name, target_name, target_id, correlation_id,
      event_result, reason, details
    );
  elsif event_name like 'admin_role_%' then
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

create function platform_control.grant_voc_workbench_access_v75(
  selected_operation_id uuid,
  selected_actor_id uuid,
  selected_display_name text,
  selected_expected_generation_id uuid,
  selected_expected_member_key uuid,
  selected_new_user_id uuid,
  selected_corporate_identity_id uuid,
  selected_union_identity_id uuid,
  selected_requested_audit_event_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  active_generation_id uuid;
  active_match_count bigint;
  all_match_count bigint;
  selected_member platform_control.directory_members%rowtype;
  resolved_user_id uuid;
  existing_grant platform_control.voc_workbench_grants%rowtype;
  stored_mutation platform_control.management_mutations%rowtype;
  request_fingerprint text;
  result_snapshot jsonb;
  resolver_error text;
begin
  if selected_operation_id is null or selected_actor_id is null
     or selected_display_name is null
     or selected_display_name <> btrim(selected_display_name)
     or length(selected_display_name) not between 1 and 256
     or selected_display_name ~ '[[:cntrl:]]'
     or selected_expected_generation_id is null
     or selected_expected_member_key is null
     or selected_new_user_id is null
     or selected_corporate_identity_id is null
     or selected_union_identity_id is null
     or selected_requested_audit_event_id is null
     or selected_corporate_identity_id = selected_union_identity_id
  then
    raise check_violation using message = 'directory_member_not_found';
  end if;

  perform platform_control.lock_dingtalk_identity_directory();
  perform pg_advisory_xact_lock(
    hashtextextended(selected_operation_id::text, 0)
  );
  perform platform_control.require_platform_owner(selected_actor_id);

  request_fingerprint := encode(
    sha256(convert_to(selected_display_name, 'UTF8')), 'hex'
  );
  select * into stored_mutation
  from platform_control.management_mutations
  where operation_id = selected_operation_id;
  if found then
    if stored_mutation.action is distinct from 'grant_voc_workbench'
       or stored_mutation.actor_internal_user_id
          is distinct from selected_actor_id
       or stored_mutation.agent_id is distinct from request_fingerprint
       or stored_mutation.generation_id
          is distinct from selected_expected_generation_id
       or stored_mutation.expected_target_row_version <> 0
       or stored_mutation.expected_causal_row_version <> 0
       or stored_mutation.expected_owner_internal_user_id is not null
       or stored_mutation.requested_audit_id_copy
          is distinct from selected_requested_audit_event_id
    then
      raise unique_violation using message = 'operation identity collision';
    end if;
    return stored_mutation.applied_result;
  end if;

  begin
    perform platform_control.require_requested_audit(
      selected_requested_audit_event_id, selected_operation_id,
      selected_actor_id, 'voc_workbench_grant_requested', 'directory_member',
      selected_expected_member_key::text, 'voc_workbench_access_approved'
    );
  exception when check_violation then
    raise check_violation using message = 'matching_audit_intent_required';
  end;
  if not exists (
    select 1 from platform_control.audit_events event
    where event.audit_event_id = selected_requested_audit_event_id
      and event.sanitized_before_after->>'expected_generation_id'
          = selected_expected_generation_id::text
      and event.sanitized_before_after->>'expected_member_key'
          = selected_expected_member_key::text
  ) then
    raise check_violation using message = 'matching_audit_intent_required';
  end if;

  select generation.generation_id into active_generation_id
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id = state.active_generation_id
   and generation.status = 'complete'
  where state.singleton
  for update of state, generation;
  if not found
     or active_generation_id <> selected_expected_generation_id
  then
    raise check_violation using message = 'directory_generation_changed';
  end if;

  select
    count(*) filter (where member.status = 'active'),
    count(*)
  into active_match_count, all_match_count
  from platform_control.directory_members member
  where member.generation_id = active_generation_id
    and member.subject_kind = 'employee'
    and member.display_name = selected_display_name;
  if all_match_count = 0 then
    raise check_violation using message = 'directory_member_not_found';
  elsif all_match_count > 1 then
    raise check_violation using message = 'directory_name_not_unique';
  elsif active_match_count = 0 then
    raise check_violation using message = 'directory_member_inactive';
  end if;

  select member.* into strict selected_member
  from platform_control.directory_members member
  where member.generation_id = active_generation_id
    and member.subject_kind = 'employee'
    and member.status = 'active'
    and member.display_name = selected_display_name
  for update;
  if selected_member.member_key <> selected_expected_member_key then
    raise check_violation using message = 'directory_generation_changed';
  end if;

  begin
    resolved_user_id := platform_control.resolve_verified_dingtalk_member(
      selected_new_user_id,
      selected_member.display_name,
      selected_corporate_identity_id,
      selected_member.lookup_hmac,
      selected_member.lookup_key_version,
      selected_member.encrypted_provider_id,
      selected_member.encryption_key_version,
      selected_union_identity_id,
      selected_member.union_lookup_hmac,
      selected_member.union_lookup_key_version,
      selected_member.union_encrypted_provider_id,
      selected_member.union_encryption_key_version
    );
  exception
    when unique_violation then
      raise check_violation using message = 'verified_identity_collision';
    when check_violation then
      get stacked diagnostics resolver_error = message_text;
      if resolver_error = 'active directory member unavailable' then
        raise check_violation using message = 'directory_generation_changed';
      end if;
      raise check_violation using message = 'verified_identity_collision';
  end;
  if not exists (
    select 1 from platform_control.internal_users users
    where users.internal_user_id = resolved_user_id
      and users.role = 'member'
  ) then
    raise check_violation using message = 'verified_identity_collision';
  end if;

  select * into existing_grant
  from platform_control.voc_workbench_grants grant_row
  where grant_row.internal_user_id = resolved_user_id
    and grant_row.revoked_at is null
  for update;
  if found then
    raise check_violation using message = 'voc_workbench_already_granted';
  end if;

  insert into platform_control.voc_workbench_grants (
    grant_id, internal_user_id, permission,
    created_by_internal_user_id, created_audit_event_id
  ) values (
    gen_random_uuid(), resolved_user_id, 'manager',
    selected_actor_id, selected_requested_audit_event_id
  ) returning * into existing_grant;

  result_snapshot := jsonb_build_object(
    'operation_id', selected_operation_id::text,
    'grant_id', existing_grant.grant_id::text,
    'internal_user_id', resolved_user_id::text,
    'permission', existing_grant.permission,
    'row_version', existing_grant.row_version
  );
  insert into platform_control.management_mutations (
    operation_id, action, actor_internal_user_id, target_internal_user_id,
    agent_id, generation_id, expected_target_row_version,
    expected_causal_row_version, requested_audit_event_id,
    requested_audit_id_copy, applied_result
  ) values (
    selected_operation_id, 'grant_voc_workbench', selected_actor_id,
    resolved_user_id, request_fingerprint, selected_expected_generation_id,
    0, 0, selected_requested_audit_event_id,
    selected_requested_audit_event_id, result_snapshot
  );
  return result_snapshot;
exception
  when no_data_found or too_many_rows then
    raise check_violation using message = 'directory_generation_changed';
end
$function$;

create function platform_control.replay_voc_workbench_grant_v75(
  selected_operation_id uuid,
  selected_actor_id uuid,
  selected_display_name text
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  stored_mutation platform_control.management_mutations%rowtype;
  requested_audit platform_control.audit_events%rowtype;
  request_fingerprint text;
begin
  if selected_operation_id is null or selected_actor_id is null
     or selected_display_name is null
     or selected_display_name <> btrim(selected_display_name)
     or length(selected_display_name) not between 1 and 256
     or selected_display_name ~ '[[:cntrl:]]'
  then
    raise check_violation using message = 'directory_member_not_found';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended(selected_operation_id::text, 0)
  );
  perform platform_control.require_platform_owner(selected_actor_id);

  select * into stored_mutation
  from platform_control.management_mutations
  where operation_id = selected_operation_id;
  if not found then
    return null;
  end if;

  request_fingerprint := encode(
    sha256(convert_to(selected_display_name, 'UTF8')), 'hex'
  );
  if stored_mutation.action is distinct from 'grant_voc_workbench'
     or stored_mutation.actor_internal_user_id
        is distinct from selected_actor_id
     or stored_mutation.agent_id is distinct from request_fingerprint
     or stored_mutation.generation_id is null
     or stored_mutation.expected_target_row_version <> 0
     or stored_mutation.expected_causal_row_version <> 0
     or stored_mutation.expected_owner_internal_user_id is not null
     or stored_mutation.requested_audit_id_copy is null
  then
    raise unique_violation using message = 'operation identity collision';
  end if;

  select * into requested_audit
  from platform_control.audit_events
  where audit_event_id = stored_mutation.requested_audit_id_copy;
  if not found
     or requested_audit.actor_internal_user_id
        is distinct from selected_actor_id
     or requested_audit.event_type
        is distinct from 'voc_workbench_grant_requested'
     or requested_audit.target_type is distinct from 'directory_member'
     or requested_audit.request_id is distinct from selected_operation_id
     or requested_audit.result is distinct from 'requested'
     or requested_audit.reason_code
        is distinct from 'voc_workbench_access_approved'
     or requested_audit.sanitized_before_after->>'operation_id'
        is distinct from selected_operation_id::text
     or requested_audit.sanitized_before_after->>'expected_generation_id'
        is distinct from stored_mutation.generation_id::text
     or requested_audit.sanitized_before_after->>'expected_member_key'
        is distinct from requested_audit.target_internal_id
  then
    raise check_violation using message = 'matching_audit_intent_required';
  end if;

  begin
    perform requested_audit.target_internal_id::uuid;
  exception when invalid_text_representation then
    raise check_violation using message = 'matching_audit_intent_required';
  end;

  return jsonb_build_object(
    'generation_id', stored_mutation.generation_id::text,
    'member_key', requested_audit.target_internal_id,
    'result', stored_mutation.applied_result
  );
end
$function$;

create function platform_control.revoke_voc_workbench_access_v75(
  selected_operation_id uuid,
  selected_actor_id uuid,
  selected_target_user_id uuid,
  selected_expected_grant_row_version bigint,
  selected_requested_audit_event_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  replay jsonb;
  selected_grant platform_control.voc_workbench_grants%rowtype;
  result_snapshot jsonb;
begin
  if selected_operation_id is null or selected_actor_id is null
     or selected_target_user_id is null
     or selected_expected_grant_row_version is null
     or selected_expected_grant_row_version < 0
     or selected_requested_audit_event_id is null
  then
    raise check_violation using message = 'voc_workbench_not_granted';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended(selected_operation_id::text, 0)
  );
  perform platform_control.require_platform_owner(selected_actor_id);
  replay := platform_control.replay_management_mutation(
    selected_operation_id, 'revoke_voc_workbench', selected_actor_id,
    selected_target_user_id, null, null,
    selected_expected_grant_row_version, 0, null,
    selected_requested_audit_event_id
  );
  if replay is not null then return replay; end if;

  begin
    perform platform_control.require_requested_audit(
      selected_requested_audit_event_id, selected_operation_id,
      selected_actor_id, 'voc_workbench_revoke_requested', 'internal_user',
      selected_target_user_id::text, 'voc_workbench_access_revoked'
    );
  exception when check_violation then
    raise check_violation using message = 'matching_audit_intent_required';
  end;
  if not exists (
    select 1 from platform_control.audit_events event
    where event.audit_event_id = selected_requested_audit_event_id
      and (event.sanitized_before_after->>'expected_row_version')::bigint
          = selected_expected_grant_row_version
  ) then
    raise check_violation using message = 'matching_audit_intent_required';
  end if;

  select * into selected_grant
  from platform_control.voc_workbench_grants grant_row
  where grant_row.internal_user_id = selected_target_user_id
    and grant_row.revoked_at is null
  for update;
  if not found
     or selected_grant.row_version <> selected_expected_grant_row_version
  then
    raise check_violation using message = 'voc_workbench_not_granted';
  end if;

  update platform_control.voc_workbench_grants
  set revoked_at = clock_timestamp(),
      revoked_by_internal_user_id = selected_actor_id,
      revoked_audit_event_id = selected_requested_audit_event_id,
      row_version = row_version + 1
  where grant_id = selected_grant.grant_id;
  result_snapshot := jsonb_build_object(
    'operation_id', selected_operation_id::text,
    'grant_id', selected_grant.grant_id::text,
    'internal_user_id', selected_target_user_id::text,
    'permission', selected_grant.permission,
    'row_version', selected_grant.row_version + 1
  );
  insert into platform_control.management_mutations (
    operation_id, action, actor_internal_user_id, target_internal_user_id,
    expected_target_row_version, expected_causal_row_version,
    requested_audit_event_id, requested_audit_id_copy, applied_result
  ) values (
    selected_operation_id, 'revoke_voc_workbench', selected_actor_id,
    selected_target_user_id, selected_expected_grant_row_version, 0,
    selected_requested_audit_event_id,
    selected_requested_audit_event_id, result_snapshot
  );
  return result_snapshot;
end
$function$;

create function platform_control.has_voc_workbench_access_v75(
  selected_internal_user_id uuid
) returns boolean
language sql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
  select selected_internal_user_id is not null and exists (
    select 1
    from platform_control.voc_workbench_grants grant_row
    join platform_control.internal_users users
      on users.internal_user_id = grant_row.internal_user_id
     and users.status = 'active'
     and users.locally_invalidated_at is null
    where grant_row.internal_user_id = selected_internal_user_id
      and grant_row.permission = 'manager'
      and grant_row.revoked_at is null
  );
$function$;

create function platform_control.read_voc_workbench_grants_v75()
returns table(
  grant_id uuid,
  internal_user_id uuid,
  display_name text,
  user_status text,
  permission text,
  created_at timestamptz,
  row_version bigint
)
language sql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
  select grant_row.grant_id, grant_row.internal_user_id,
    users.display_name, users.status, grant_row.permission,
    grant_row.created_at, grant_row.row_version
  from platform_control.voc_workbench_grants grant_row
  join platform_control.internal_users users
    on users.internal_user_id = grant_row.internal_user_id
  where grant_row.revoked_at is null
  order by users.display_name, grant_row.internal_user_id;
$function$;

revoke all on function platform_control.validate_voc_workbench_audit_v75(
  uuid,text,text,text,uuid,text,text,jsonb
) from public;
revoke all on function platform_control.grant_voc_workbench_access_v75(
  uuid,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid
) from public;
revoke all on function platform_control.replay_voc_workbench_grant_v75(
  uuid,uuid,text
) from public;
revoke all on function platform_control.revoke_voc_workbench_access_v75(
  uuid,uuid,uuid,bigint,uuid
) from public;
revoke all on function platform_control.has_voc_workbench_access_v75(uuid)
  from public;
revoke all on function platform_control.read_voc_workbench_grants_v75()
  from public;

do $migration$
declare
  selected_app name;
  role_name name;
begin
  if current_database() = 'agent_platform_control'
     and current_user = 'platform_control_owner'
  then
    selected_app := 'platform_control_app';
  elsif current_database() = 'agent_platform_control_preview'
        and current_user = 'platform_control_owner_preview'
  then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using
      message = 'VOC workbench access owner/environment mismatch';
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
      'revoke all on platform_control.voc_workbench_grants from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.validate_voc_workbench_audit_v75('
      'uuid,text,text,text,uuid,text,text,jsonb) from %I', role_name
    );
    execute format(
      'revoke all on function platform_control.grant_voc_workbench_access_v75('
      'uuid,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid) from %I', role_name
    );
    execute format(
      'revoke all on function platform_control.replay_voc_workbench_grant_v75('
      'uuid,uuid,text) from %I', role_name
    );
    execute format(
      'revoke all on function platform_control.revoke_voc_workbench_access_v75('
      'uuid,uuid,uuid,bigint,uuid) from %I', role_name
    );
    execute format(
      'revoke all on function platform_control.has_voc_workbench_access_v75('
      'uuid) from %I', role_name
    );
    execute format(
      'revoke all on function platform_control.read_voc_workbench_grants_v75() '
      'from %I', role_name
    );
  end loop;

  execute format(
    'grant execute on function platform_control.grant_voc_workbench_access_v75('
    'uuid,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid) to %I', selected_app
  );
  execute format(
    'grant execute on function platform_control.replay_voc_workbench_grant_v75('
    'uuid,uuid,text) to %I', selected_app
  );
  execute format(
    'grant execute on function platform_control.revoke_voc_workbench_access_v75('
    'uuid,uuid,uuid,bigint,uuid) to %I', selected_app
  );
  execute format(
    'grant execute on function platform_control.has_voc_workbench_access_v75('
    'uuid) to %I', selected_app
  );
  execute format(
    'grant execute on function platform_control.read_voc_workbench_grants_v75() '
    'to %I', selected_app
  );
end
$migration$;

create function platform_control.resolve_active_voc_workbench_member_v75(
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
  platform_control.resolve_active_voc_workbench_member_v75(text)
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
      message='VOC workbench member resolver owner/environment mismatch';
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
      'platform_control.resolve_active_voc_workbench_member_v75(text) from %I',
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
    'platform_control.resolve_active_voc_workbench_member_v75(text) to %I',
    selected_app
  );
end
$migration$;
