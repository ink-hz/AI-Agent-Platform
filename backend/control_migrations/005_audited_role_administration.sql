alter table platform_control.internal_users
  add column role_audit_event_id uuid
  references platform_control.audit_events(audit_event_id);

alter table platform_control.observation_grants
  add column created_audit_event_id uuid
  references platform_control.audit_events(audit_event_id),
  add column revoked_audit_event_id uuid
  references platform_control.audit_events(audit_event_id);

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

create function platform_control.resolve_owner_binding_target(
  selected_generation uuid,
  selected_subject_kind text,
  selected_versions integer[],
  selected_lookups bytea[]
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  selected_user uuid;
begin
  if selected_generation is null
     or selected_subject_kind is null
     or selected_subject_kind = ''
     or selected_versions is null
     or selected_lookups is null
     or cardinality(selected_versions) = 0
     or cardinality(selected_versions) <> cardinality(selected_lookups)
  then
    raise check_violation using
      message = 'owner binding selection invalid';
  end if;

  select users.internal_user_id into strict selected_user
  from platform_control.directory_generations generation
  join platform_control.directory_members member
    on member.generation_id = generation.generation_id
  join platform_control.internal_users users
    on users.internal_user_id = member.internal_user_id
  join platform_control.provider_identities identity
    on identity.internal_user_id = users.internal_user_id
   and identity.subject_kind = member.subject_kind
  join unnest(selected_versions, selected_lookups)
    as candidate(key_version, lookup_value)
    on identity.lookup_key_version = candidate.key_version
   and identity.lookup_hmac = candidate.lookup_value
   and member.lookup_key_version = candidate.key_version
   and member.lookup_hmac = candidate.lookup_value
  where generation.generation_id = selected_generation
    and generation.status = 'complete'
    and member.subject_kind = selected_subject_kind
    and member.status = 'active'
    and users.status = 'active';

  return selected_user;
exception
  when no_data_found or too_many_rows then
    raise check_violation using
      message = 'target unavailable in selected generation';
end
$function$;

create function platform_control.change_platform_owner(
  operation_name text,
  target_user uuid,
  selected_generation uuid,
  requested_audit_event uuid
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  existing_owner uuid;
begin
  if operation_name not in ('bind', 'replace')
     or target_user is null
     or selected_generation is null
     or requested_audit_event is null
     or not exists (
       select 1 from platform_control.audit_events audit
       where audit.audit_event_id = requested_audit_event
         and audit.target_internal_id = target_user::text
         and audit.event_type = case operation_name
           when 'bind' then 'owner_binding_requested'
           when 'replace' then 'owner_replacement_requested'
         end
         and audit.result = 'requested'
         and audit.sanitized_before_after->>'directory_generation_id'
             = selected_generation::text
     )
  then
    raise check_violation using message = 'owner role change invalid';
  end if;

  perform pg_advisory_xact_lock(1331121733);
  perform 1
  from platform_control.directory_generations generation
  join platform_control.directory_members member
    on member.generation_id = generation.generation_id
  join platform_control.provider_identities identity
    on identity.internal_user_id = member.internal_user_id
   and identity.subject_kind = member.subject_kind
   and identity.lookup_key_version = member.lookup_key_version
   and identity.lookup_hmac = member.lookup_hmac
  join platform_control.internal_users users
    on users.internal_user_id = member.internal_user_id
  where generation.generation_id = selected_generation
    and generation.status = 'complete'
    and member.internal_user_id = target_user
    and member.status = 'active'
    and users.status = 'active';
  if not found then
    raise check_violation using
      message = 'target unavailable in selected generation';
  end if;

  select internal_user_id into existing_owner
  from platform_control.internal_users
  where role = 'platform_owner' and status = 'active'
  for update;

  if operation_name = 'bind'
     and existing_owner is not null
     and existing_owner <> target_user
  then
    raise check_violation using message = 'active owner already bound';
  end if;

  if operation_name = 'replace' and existing_owner is null then
    raise check_violation using message = 'active owner unavailable';
  end if;

  if existing_owner is not null and existing_owner <> target_user then
    update platform_control.internal_users
    set role = 'member', role_audit_event_id = requested_audit_event,
        updated_at = now()
    where internal_user_id = existing_owner;
  end if;

  update platform_control.internal_users
  set role = 'platform_owner', role_audit_event_id = requested_audit_event,
      updated_at = now()
  where internal_user_id = target_user;

  update platform_control.web_sessions
  set revoked_at = now(), revoked_reason = 'owner role changed'
  where internal_user_id in (target_user, existing_owner)
    and revoked_at is null;

  return target_user;
end
$function$;

create function platform_control.show_directory_generation()
returns table (
  generation_id uuid,
  status text,
  completed_at timestamptz,
  is_active boolean
)
language sql
security definer
set search_path = pg_catalog, platform_control
as $function$
  select generation.generation_id, generation.status,
         generation.completed_at,
         state.active_generation_id = generation.generation_id
  from platform_control.directory_generations generation
  cross join platform_control.directory_state state
  where generation.status = 'complete'
  order by generation.completed_at desc nulls last, generation.generation_id
  limit 1
$function$;

create function platform_control.purge_expired_control_state()
returns table (
  audit_events bigint,
  login_attempts bigint,
  web_sessions bigint,
  rate_buckets bigint
)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  audit_events := platform_control.retain_audit_events(
    clock_timestamp() - interval '365 days'
  );
  delete from platform_control.login_attempts where expires_at < now();
  get diagnostics login_attempts = row_count;
  delete from platform_control.web_sessions where absolute_expires_at < now();
  get diagnostics web_sessions = row_count;
  delete from platform_control.auth_rate_buckets
  where updated_at < now() - interval '1 day';
  get diagnostics rate_buckets = row_count;
  return next;
end
$function$;

revoke all on function platform_control.resolve_owner_binding_target(
  uuid, text, integer[], bytea[]
) from public;
revoke all on function platform_control.change_platform_owner(
  text, uuid, uuid, uuid
) from public;
revoke all on function platform_control.show_directory_generation()
from public;
revoke all on function platform_control.purge_expired_control_state()
from public;

do $migration$
declare
  selected_suffix text;
  selected_migrator text;
  selected_audit text;
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
  selected_audit := 'platform_audit_append' || selected_suffix;

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
      'revoke all on function '
      'platform_control.resolve_owner_binding_target('
      'uuid, text, integer[], bytea[]) from %I', role_name
    );
    execute format(
      'revoke all on function platform_control.change_platform_owner('
      'text, uuid, uuid, uuid) from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.show_directory_generation() from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.purge_expired_control_state() from %I', role_name
    );
    execute format(
      'revoke all on function platform_control.append_audit_event('
      'uuid, uuid, text, text, text, uuid, text, text, jsonb) from %I',
      role_name
    );
  end loop;

  execute format(
    'grant usage on schema platform_control to %I', selected_migrator
  );
  execute format(
    'grant execute on function '
    'platform_control.resolve_owner_binding_target('
    'uuid, text, integer[], bytea[]) to %I', selected_migrator
  );
  execute format(
    'grant execute on function platform_control.change_platform_owner('
    'text, uuid, uuid, uuid) to %I', selected_migrator
  );
  execute format(
    'grant execute on function '
    'platform_control.show_directory_generation() to %I',
    selected_migrator
  );
  execute format(
    'grant execute on function platform_control.append_audit_event('
    'uuid, uuid, text, text, text, uuid, text, text, jsonb) to %I',
    selected_audit
  );
  execute format(
    'grant execute on function '
    'platform_control.purge_expired_control_state() to %I',
    'platform_control_maintenance' || selected_suffix
  );
end
$migration$;
