create function platform_control.validate_fae_session_read_audit_v60(
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
  actual_keys text[];
  key_name text;
begin
  case event_name
    when 'fae_session_detail_read_requested' then
      expected_keys := array['operation_id','result'];
    when 'fae_session_detail_read_completed' then
      expected_keys := array['linked_audit_event_id','operation_id','result'];
    when 'fae_session_detail_read_failed' then
      expected_keys := array[
        'error_code','linked_audit_event_id','operation_id','result'
      ];
    else
      raise check_violation using message = 'audit event invalid';
  end case;

  if actor_id is null or correlation_id is null
     or target_name <> 'fae_session'
     or target_id !~ '^[0-9a-f]{64}$'
     or reason <> 'privileged_read'
     or event_result <> split_part(
       event_name, '_', array_length(string_to_array(event_name, '_'), 1)
     )
     or jsonb_typeof(details) <> 'object'
  then
    raise check_violation using message = 'audit event invalid';
  end if;

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

  if event_result in ('completed', 'failed') then
    if jsonb_typeof(details->'linked_audit_event_id') <> 'string' then
      raise check_violation using message = 'audit metadata invalid';
    end if;
    perform (details->>'linked_audit_event_id')::uuid;
  end if;
  if event_result = 'failed' and (
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

revoke all on function platform_control.validate_fae_session_read_audit_v60(
  uuid,text,text,text,uuid,text,text,jsonb
) from public;

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
  if event_name like 'fae_session_detail_read_%' then
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
