create function platform_control.enforce_active_scope_limit()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  perform pg_advisory_xact_lock(hashtextextended(
    new.viewer_internal_user_id::text, 256
  ));
  if new.revoked_at is null
     and (
       tg_op = 'INSERT'
       or old.revoked_at is not null
       or old.viewer_internal_user_id is distinct from new.viewer_internal_user_id
       or old.agent_id is distinct from new.agent_id
     )
     and (
       select count(*)
       from platform_control.observation_grants active_grant
       where active_grant.viewer_internal_user_id = new.viewer_internal_user_id
         and active_grant.revoked_at is null
     ) >= 256
  then
    raise check_violation using message = 'scope limit reached';
  end if;
  return new;
end
$function$;

create trigger enforce_active_scope_limit
before insert or update on platform_control.observation_grants
for each row execute function platform_control.enforce_active_scope_limit();

create function platform_control.canonicalize_management_result()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  scope_count bigint;
  scope_digest text;
begin
  if new.action = 'revoke_viewer'
     and jsonb_typeof(new.applied_result->'previous_scopes') = 'array'
     and jsonb_array_length(new.applied_result->'previous_scopes') > 256
  then
    select count(*), encode(sha256(convert_to(
      coalesce(string_agg(item.value, E'\n' order by item.value), ''), 'UTF8'
    )), 'hex')
    into scope_count, scope_digest
    from jsonb_array_elements_text(new.applied_result->'previous_scopes') item(value);
    new.applied_result := (new.applied_result - 'previous_scopes' - 'new_scopes')
      || jsonb_build_object(
        'previous_scope_count', scope_count,
        'previous_scope_sha256', scope_digest,
        'new_scope_count', 0,
        'new_scope_sha256', encode(sha256(convert_to('', 'UTF8')), 'hex')
      );
  end if;
  return new;
end
$function$;

create trigger canonicalize_management_result
before insert or update on platform_control.management_mutations
for each row execute function platform_control.canonicalize_management_result();

create function platform_control.validate_viewer_revocation_summary(
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
  expected_keys constant text[] := array[
    'linked_audit_event_id','new_role','new_scope_count','new_scope_sha256',
    'operation_id','previous_role','previous_scope_count',
    'previous_scope_sha256','result','row_version','session_revocation_count'
  ];
  actual_keys text[];
begin
  select array_agg(value order by value) into actual_keys
  from jsonb_object_keys(details) key(value);
  if actor_id is null or correlation_id is null
     or event_name <> 'viewer_role_revocation_completed'
     or target_name <> 'internal_user'
     or target_id is null
     or event_result <> 'completed'
     or reason <> 'access_revoked'
     or jsonb_typeof(details) <> 'object'
     or actual_keys is distinct from expected_keys
     or details->>'operation_id' <> correlation_id::text
     or details->>'result' <> 'completed'
     or details->>'previous_role' <> 'management_viewer'
     or details->>'new_role' <> 'member'
  then
    raise check_violation using message = 'audit event invalid';
  end if;
  perform target_id::uuid;
  perform (details->>'operation_id')::uuid;
  perform (details->>'linked_audit_event_id')::uuid;
  if details->>'previous_scope_sha256' !~ '^[0-9a-f]{64}$'
     or details->>'new_scope_sha256' !~ '^[0-9a-f]{64}$'
     or exists (
       select 1 from unnest(array[
         'previous_scope_count','new_scope_count','row_version',
         'session_revocation_count'
       ]) key_name
       where jsonb_typeof(details->key_name) <> 'number'
          or details->>key_name !~ '^[0-9]+$'
     )
  then
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
  if event_name = 'viewer_role_revocation_completed'
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

create function platform_control.reconcile_platform_owner_v2(
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
  replay jsonb;
begin
  if selected_operation not in ('bind', 'replace') then
    raise check_violation using message = 'owner change precondition invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(selected_operation_id::text, 0));
  replay := platform_control.replay_management_mutation(
    selected_operation_id, selected_operation || '_owner', null,
    selected_target_id, null, selected_generation_id,
    selected_expected_target_version, selected_expected_owner_version,
    selected_expected_owner_id, selected_audit_event_id
  );
  return replay;
end
$function$;

revoke all on function platform_control.enforce_active_scope_limit() from public;
revoke all on function platform_control.canonicalize_management_result() from public;
revoke all on function platform_control.validate_viewer_revocation_summary(
  uuid, text, text, text, uuid, text, text, jsonb
) from public;
revoke all on function platform_control.reconcile_platform_owner_v2(
  uuid, text, uuid, uuid, uuid, bigint, bigint, uuid
) from public;

do $migration$
declare
  selected_suffix text;
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
  selected_migrator := 'platform_control_migrator' || selected_suffix;
  foreach role_name in array array[
    'platform_control_migrator','platform_control_app',
    'platform_directory_worker','platform_stream_ingest',
    'platform_audit_append','platform_control_maintenance',
    'platform_control_migrator_preview','platform_control_app_preview',
    'platform_directory_worker_preview','platform_stream_ingest_preview',
    'platform_audit_append_preview','platform_control_maintenance_preview'
  ] loop
    execute format(
      'revoke all on function platform_control.reconcile_platform_owner_v2(uuid,text,uuid,uuid,uuid,bigint,bigint,uuid) from %I',
      role_name
    );
  end loop;
  execute format(
    'grant execute on function platform_control.reconcile_platform_owner_v2(uuid,text,uuid,uuid,uuid,bigint,bigint,uuid) to %I',
    selected_migrator
  );
end
$migration$;
