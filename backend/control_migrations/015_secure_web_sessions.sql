alter table platform_control.login_attempts
  add column environment text,
  add column state_hash_key_version integer,
  add column challenge_hash_key_version integer,
  add column verifier_ciphertext bytea,
  add column exchange_started_at timestamptz,
  add column failed_at timestamptz,
  add column failure_reason text,
  add constraint login_attempt_environment_valid
    check (environment is null or environment in ('production','preview')),
  add constraint login_attempt_state_key_version_valid
    check (state_hash_key_version is null or state_hash_key_version > 0),
  add constraint login_attempt_challenge_key_version_valid
    check (challenge_hash_key_version is null or challenge_hash_key_version > 0),
  add constraint login_attempt_failure_shape_valid
    check ((failed_at is null) = (failure_reason is null));

create unique index login_attempt_state_digest_unique
  on platform_control.login_attempts (state_hash_key_version, state_hash)
  where state_hash_key_version is not null;

alter table platform_control.web_sessions
  add column token_hash_key_version integer,
  add column csrf_hash_key_version integer,
  add constraint web_session_token_key_version_valid
    check (token_hash_key_version is null or token_hash_key_version > 0),
  add constraint web_session_csrf_key_version_valid
    check (csrf_hash_key_version is null or csrf_hash_key_version > 0);

create function platform_control.create_web_login_attempt(
  selected_attempt_id uuid,
  selected_kind text,
  selected_state_hash bytea,
  selected_state_key_version integer,
  selected_challenge_hash bytea,
  selected_challenge_key_version integer,
  selected_verifier_ciphertext bytea,
  selected_return_path text,
  selected_environment text,
  selected_ttl_seconds integer
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  required_environment text;
begin
  required_environment := case current_database()
    when 'agent_platform_control' then 'production'
    when 'agent_platform_control_preview' then 'preview'
    else null
  end;
  if required_environment is null
     or selected_environment <> required_environment
     or selected_attempt_id is null
     or selected_kind not in ('qr','in_client')
     or octet_length(selected_state_hash) <> 32
     or selected_state_key_version is null or selected_state_key_version <= 0
     or octet_length(selected_challenge_hash) <> 32
     or selected_challenge_key_version is null or selected_challenge_key_version <= 0
     or octet_length(selected_verifier_ciphertext) < 29
     or selected_ttl_seconds <> 300
     or selected_return_path is null
     or selected_return_path !~ '^/'
     or selected_return_path ~ E'[\\r\\n\\x00]'
     or selected_return_path ~ '[\\\\%?#]'
     or selected_return_path ~ '(^|/)\\.{1,2}(/|$)'
     or selected_return_path ~ '^//'
     or (
       required_environment='preview'
       and selected_return_path <> '/_preview/dingtalk-r1'
       and selected_return_path !~ '^/_preview/dingtalk-r1/'
     )
  then
    raise check_violation using message = 'web login attempt invalid';
  end if;
  insert into platform_control.login_attempts (
    login_attempt_id,attempt_kind,state_hash,state_hash_key_version,
    challenge_hash,challenge_hash_key_version,verifier_ciphertext,
    return_path,environment,expires_at
  ) values (
    selected_attempt_id,selected_kind,selected_state_hash,selected_state_key_version,
    selected_challenge_hash,selected_challenge_key_version,
    selected_verifier_ciphertext,selected_return_path,
    selected_environment,clock_timestamp() + interval '300 seconds'
  );
  return selected_attempt_id;
end
$function$;

create function platform_control.claim_web_login_attempt(
  selected_state_hash bytea,
  selected_state_key_version integer,
  selected_environment text,
  selected_kind text
) returns table(
  attempt_id uuid,
  return_path text,
  expires_at timestamptz,
  challenge_hash bytea,
  challenge_hash_key_version integer,
  verifier_ciphertext bytea
)
language sql
security definer
set search_path = pg_catalog, platform_control
as $function$
  update platform_control.login_attempts attempt
  set exchange_started_at = clock_timestamp()
  where attempt.state_hash = selected_state_hash
    and attempt.state_hash_key_version = selected_state_key_version
    and attempt.environment = selected_environment
    and attempt.attempt_kind = selected_kind
    and attempt.consumed_at is null
    and attempt.failed_at is null
    and attempt.exchange_started_at is null
    and attempt.expires_at > clock_timestamp()
    and selected_environment = case current_database()
      when 'agent_platform_control' then 'production'
      when 'agent_platform_control_preview' then 'preview'
      else ''
    end
  returning attempt.login_attempt_id,attempt.return_path,attempt.expires_at,
    attempt.challenge_hash,attempt.challenge_hash_key_version,
    attempt.verifier_ciphertext
$function$;

create function platform_control.fail_web_login_attempt(
  selected_attempt_id uuid,
  selected_reason text
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_reason not in ('provider_exchange_failed','identity_resolution_failed') then
    raise check_violation using message = 'login failure reason invalid';
  end if;
  update platform_control.login_attempts attempt
  set failed_at=clock_timestamp(), consumed_at=clock_timestamp(), failure_reason=selected_reason
  where attempt.login_attempt_id=selected_attempt_id
    and attempt.exchange_started_at is not null
    and attempt.consumed_at is null
    and attempt.failed_at is null;
  return found;
end
$function$;

create function platform_control.consume_attempt_and_issue_session(
  selected_attempt_id uuid,
  selected_internal_user_id uuid,
  selected_session_id uuid,
  selected_token_hash bytea,
  selected_token_key_version integer,
  selected_csrf_hash bytea,
  selected_csrf_key_version integer,
  selected_idle_seconds integer,
  selected_absolute_seconds integer
) returns table(
  session_id uuid,
  idle_expires_at timestamptz,
  absolute_expires_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  selected_generation_id uuid;
begin
  if selected_attempt_id is null or selected_internal_user_id is null
     or selected_session_id is null
     or octet_length(selected_token_hash) <> 32
     or octet_length(selected_csrf_hash) <> 32
     or selected_token_key_version is null or selected_token_key_version <= 0
     or selected_csrf_key_version is null or selected_csrf_key_version <= 0
     or selected_idle_seconds <> 28800
     or selected_absolute_seconds <> 86400
  then
    raise check_violation using message = 'web session input invalid';
  end if;

  perform platform_control.lock_dingtalk_identity_directory();
  perform 1 from platform_control.login_attempts attempt
  where attempt.login_attempt_id=selected_attempt_id
    and attempt.exchange_started_at is not null
    and attempt.failed_at is null
    and attempt.consumed_at is null
    and attempt.expires_at > database_now
    and attempt.environment = case current_database()
      when 'agent_platform_control' then 'production'
      when 'agent_platform_control_preview' then 'preview'
      else ''
    end
  for update;
  if not found then return; end if;

  select generation.generation_id into selected_generation_id
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
   and generation.status='complete'
  where state.singleton;
  if selected_generation_id is null
     or not exists (
       select 1 from platform_control.internal_users users
       join platform_control.directory_members member
         on member.generation_id=selected_generation_id
        and member.internal_user_id=users.internal_user_id
        and member.status='active'
       where users.internal_user_id=selected_internal_user_id
         and users.status='active'
         and users.locally_invalidated_at is null
         and users.last_confirmed_generation_id=selected_generation_id
     )
  then
    return;
  end if;

  insert into platform_control.web_sessions (
    session_id,internal_user_id,token_hash,token_hash_key_version,
    csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at
  ) values (
    selected_session_id,selected_internal_user_id,selected_token_hash,
    selected_token_key_version,selected_csrf_hash,selected_csrf_key_version,
    database_now + interval '28800 seconds',
    database_now + interval '86400 seconds'
  );
  update platform_control.login_attempts attempt
  set consumed_at=database_now
  where attempt.login_attempt_id=selected_attempt_id;
  return query select selected_session_id,
    database_now + interval '28800 seconds',
    database_now + interval '86400 seconds';
end
$function$;

create function platform_control.authenticate_web_session(
  selected_token_hash bytea,
  selected_token_key_version integer,
  selected_idle_seconds integer
) returns table(
  session_id uuid,
  internal_user_id uuid,
  role text,
  hard_stale_read_only boolean,
  csrf_hash bytea,
  csrf_hash_key_version integer
)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
begin
  if octet_length(selected_token_hash) <> 32
     or selected_token_key_version is null or selected_token_key_version <= 0
     or selected_idle_seconds <> 28800
  then return; end if;
  perform platform_control.lock_dingtalk_identity_directory();
  return query
  update platform_control.web_sessions session
  set last_seen_at=database_now,
      idle_expires_at=least(database_now + interval '28800 seconds',session.absolute_expires_at)
  from platform_control.internal_users users,
       platform_control.directory_state state,
       platform_control.directory_generations generation,
       platform_control.directory_members member
  where session.token_hash=selected_token_hash
    and session.token_hash_key_version=selected_token_key_version
    and session.revoked_at is null
    and session.idle_expires_at > database_now
    and session.absolute_expires_at > database_now
    and users.internal_user_id=session.internal_user_id
    and users.status='active'
    and users.locally_invalidated_at is null
    and state.singleton
    and generation.generation_id=state.active_generation_id
    and generation.status='complete'
    and users.last_confirmed_generation_id=generation.generation_id
    and member.generation_id=generation.generation_id
    and member.internal_user_id=users.internal_user_id
    and member.status='active'
  returning session.session_id,session.internal_user_id,users.role::text,
    session.hard_stale_read_only,session.csrf_hash,session.csrf_hash_key_version;
end
$function$;

create function platform_control.revoke_web_session(
  selected_session_id uuid,
  selected_reason text
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_reason <> 'logout' then
    raise check_violation using message = 'web session revocation invalid';
  end if;
  update platform_control.web_sessions session
  set revoked_at=clock_timestamp(),revoked_reason=selected_reason
  where session.session_id=selected_session_id and session.revoked_at is null;
  return found;
end
$function$;

create function platform_control.append_system_health_read(
  selected_event_id uuid,
  selected_actor_id uuid,
  selected_request_id uuid
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_event_id is null or selected_actor_id is null
     or selected_request_id is null
     or not exists (
       select 1 from platform_control.internal_users users
       where users.internal_user_id=selected_actor_id
         and users.role='platform_owner'
         and users.status='active'
         and users.locally_invalidated_at is null
     )
  then
    raise insufficient_privilege using message='system health audit rejected';
  end if;
  insert into platform_control.audit_events (
    audit_event_id,actor_internal_user_id,event_type,target_type,
    target_internal_id,request_id,result,reason_code,sanitized_before_after
  ) values (
    selected_event_id,selected_actor_id,'system_health_read_completed',
    'platform_system','sanitized',selected_request_id,'completed',
    'privileged_read',jsonb_build_object(
      'operation_id',selected_request_id::text,'result','completed'
    )
  );
  return selected_event_id;
end
$function$;

revoke all on function platform_control.create_web_login_attempt(uuid,text,bytea,integer,bytea,integer,bytea,text,text,integer) from public;
revoke all on function platform_control.claim_web_login_attempt(bytea,integer,text,text) from public;
revoke all on function platform_control.fail_web_login_attempt(uuid,text) from public;
revoke all on function platform_control.consume_attempt_and_issue_session(uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer) from public;
revoke all on function platform_control.authenticate_web_session(bytea,integer,integer) from public;
revoke all on function platform_control.revoke_web_session(uuid,text) from public;
revoke all on function platform_control.append_system_health_read(uuid,uuid,uuid) from public;

do $migration$
declare
  selected_app name;
  selected_audit name;
  role_name text;
begin
  selected_app := case current_database()
    when 'agent_platform_control' then 'platform_control_app'
    when 'agent_platform_control_preview' then 'platform_control_app_preview'
    else null
  end;
  if selected_app is null then
    raise insufficient_privilege using message='unsupported control environment';
  end if;
  selected_audit := case current_database()
    when 'agent_platform_control' then 'platform_audit_append'
    when 'agent_platform_control_preview' then 'platform_audit_append_preview'
    else null
  end;
  foreach role_name in array array[
    'platform_control_migrator','platform_control_app','platform_directory_worker',
    'platform_stream_ingest','platform_audit_append','platform_control_maintenance',
    'platform_control_migrator_preview','platform_control_app_preview',
    'platform_directory_worker_preview','platform_stream_ingest_preview',
    'platform_audit_append_preview','platform_control_maintenance_preview'
  ] loop
    execute format('revoke insert,update,delete on platform_control.login_attempts from %I',role_name);
    execute format('revoke insert,update,delete on platform_control.web_sessions from %I',role_name);
    execute format('revoke all on function platform_control.create_web_login_attempt(uuid,text,bytea,integer,bytea,integer,bytea,text,text,integer) from %I',role_name);
    execute format('revoke all on function platform_control.claim_web_login_attempt(bytea,integer,text,text) from %I',role_name);
    execute format('revoke all on function platform_control.fail_web_login_attempt(uuid,text) from %I',role_name);
    execute format('revoke all on function platform_control.consume_attempt_and_issue_session(uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer) from %I',role_name);
    execute format('revoke all on function platform_control.authenticate_web_session(bytea,integer,integer) from %I',role_name);
    execute format('revoke all on function platform_control.revoke_web_session(uuid,text) from %I',role_name);
    execute format('revoke all on function platform_control.append_system_health_read(uuid,uuid,uuid) from %I',role_name);
  end loop;
  execute format('grant execute on function platform_control.create_web_login_attempt(uuid,text,bytea,integer,bytea,integer,bytea,text,text,integer) to %I',selected_app);
  execute format('grant execute on function platform_control.claim_web_login_attempt(bytea,integer,text,text) to %I',selected_app);
  execute format('grant execute on function platform_control.fail_web_login_attempt(uuid,text) to %I',selected_app);
  execute format('grant execute on function platform_control.consume_attempt_and_issue_session(uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer) to %I',selected_app);
  execute format('grant execute on function platform_control.authenticate_web_session(bytea,integer,integer) to %I',selected_app);
  execute format('grant execute on function platform_control.revoke_web_session(uuid,text) to %I',selected_app);
  execute format('grant execute on function platform_control.append_system_health_read(uuid,uuid,uuid) to %I',selected_audit);
end
$migration$;
